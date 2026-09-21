import os
import sys
import time
import json
import math
import threading
import argparse
import traceback
import csv
import copy

from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np


# ============================================================
# BTC ADAPTIVE TELEGRAM BOT V6
# Liquidity Sweep + Multi-Factor Confirmation
# ============================================================

PRODUCT = "BTC-USD"

REST = "https://api.exchange.coinbase.com"
WS_URL = "wss://ws-feed.exchange.coinbase.com"

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = "7500472109"

STATE_FILE = "btc_v5_state.json"
JOURNAL_FILE = "btc_v5_journal.jsonl"
CALIBRATION_FILE = "btc_v5_calibration.json"
CSV_LOG_FILE = "logs/btc_signals.csv"


# ============================================================
# CORE SETTINGS
# ============================================================

SCAN_SECONDS = 60
COOLDOWN_MIN = 45

MIN_SCORE = 62
STRONG_SCORE = 76

MIN_AGREEMENT = 0.58
STRONG_AGREEMENT = 0.68


# ============================================================
# ORDER FLOW SETTINGS
# ============================================================

ORDERFLOW_STALE = 8
WS_WARMUP_SECONDS = 5

REQUEST_TIMEOUT = 20

HISTORY_1H_BARS = 1200

FLOW_TRADE_LIMIT = 100
FLOW_LARGE_TRADE_BTC = 0.25


# ============================================================
# LIQUIDITY SWEEP SETTINGS
# ============================================================

# Swing/liquidity reference window on 1H.
SWEEP_LOOKBACK = 20

# A sweep must penetrate the previous liquidity level
# by at least this fraction of ATR to be considered meaningful.
SWEEP_MIN_PENETRATION_ATR = 0.05

# Sweep remains relevant for this many CLOSED 1H bars.
SWEEP_MAX_AGE = 3

# Minimum reclaim quality before a sweep is considered
# structurally meaningful.
SWEEP_MIN_RECLAIM = 0.35

# Minimum final confirmation required when a fresh sweep
# is present and is being used to validate a trade.
SWEEP_MIN_CONFIRMATION = 0.55

# Confirmation weights.
SWEEP_CONFIRM_WEIGHTS = {
    "sweep_quality": 0.20,
    "structure": 0.20,
    "momentum": 0.15,
    "orderflow": 0.15,
    "reversal": 0.10,
    "retest": 0.10,
    "volume": 0.10,
}


# ============================================================
# EXISTING V5 WEIGHTS
# ============================================================

WEIGHTS = {
    "trend": 15,
    "structure": 20,
    "price": 15,
    "volume": 10,
    "momentum": 10,
    "retest": 12,
    "orderflow": 13,
    "reversal": 10,
}


session = requests.Session()

session.headers.update({
    "User-Agent":
        "BTC-Adaptive-Telegram-Bot-V6-GitHub/1.0"
})


flow = {
    "bids": {},
    "asks": {},
    "delta": 0.0,
    "large": 0.0,
    "buy": 0.0,
    "sell": 0.0,
    "trade_count": 0,
    "updated": 0.0,
}

flow_lock = threading.Lock()

ws_thread = None


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts():
    return time.time()


def iso(ts=None):
    return datetime.fromtimestamp(
        ts or now_ts(),
        tz=timezone.utc,
    ).isoformat()


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


# ============================================================
# JSON STATE
# ============================================================

def load_json(path, default):
    try:
        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            obj,
            f,
            indent=2,
            ensure_ascii=False,
        )

    os.replace(tmp, path)


def load_state():
    s = load_json(
        STATE_FILE,
        {},
    )

    if not isinstance(s, dict):
        s = {}

    s.setdefault(
        "last_signal",
        None,
    )

    s.setdefault(
        "last_signal_ts",
        0,
    )

    s.setdefault(
        "last_direction",
        None,
    )

    s.setdefault(
        "impulse",
        None,
    )

    s.setdefault(
        "last_scan_ts",
        0,
    )

    s.setdefault(
        "flow_snapshot",
        {},
    )

    s.setdefault(
        "last_sweep",
        None,
    )

    return s


def save_state(s):
    save_json(
        STATE_FILE,
        s,
    )


# ============================================================
# JOURNAL
# ============================================================

def journal(event, **kwargs):
    rec = {
        "ts": iso(),
        "event": event,
        **kwargs,
    }

    try:
        with open(
            JOURNAL_FILE,
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                json.dumps(
                    rec,
                    ensure_ascii=False,
                )
                + "\n"
            )

    except Exception as e:
        print(
            "JOURNAL_ERROR",
            repr(e),
        )


# ============================================================
# CSV LOGGER
# ============================================================

def log_signal_csv(
    timestamp,
    direction,
    score,
    agreement,
    reason,
    plan,
    flow_data,
    sweep_data=None,
):
    try:
        os.makedirs(
            "logs",
            exist_ok=True,
        )

        file_exists = os.path.exists(
            CSV_LOG_FILE
        )

        file_empty = (
            file_exists
            and os.path.getsize(
                CSV_LOG_FILE
            ) == 0
        )

        with open(
            CSV_LOG_FILE,
            "a",
            newline="",
            encoding="utf-8",
        ) as f:

            writer = csv.writer(f)

            if not file_exists or file_empty:

                writer.writerow([
                    "timestamp",
                    "direction",
                    "score",
                    "agreement",
                    "reason",

                    "entry",
                    "sl",
                    "tp",
                    "rr",

                    "orderflow_imb",
                    "orderflow_delta",
                    "orderflow_large",
                    "orderflow_buy",
                    "orderflow_sell",
                    "trades",

                    "sweep_type",
                    "sweep_level",
                    "sweep_distance_atr",
                    "reclaim_strength",
                    "sweep_quality",
                    "sweep_age",
                    "sweep_confirmation",

                    "sweep_structure",
                    "sweep_momentum",
                    "sweep_orderflow",
                    "sweep_reversal",
                    "sweep_retest",
                    "sweep_volume",
                ])

            sweep_data = (
                sweep_data
                or {}
            )

            # ------------------------------------------------
            # These local variables preserve the same logging
            # values while avoiding invalid multiline
            # f-string syntax.
            # ------------------------------------------------

            sweep_distance_atr = safe_float(
                sweep_data.get(
                    "distance_atr"
                )
            )

            sweep_reclaim_strength = safe_float(
                sweep_data.get(
                    "reclaim_strength"
                )
            )

            sweep_quality = safe_float(
                sweep_data.get(
                    "quality"
                )
            )

            sweep_confirmation = safe_float(
                sweep_data.get(
                    "confirmation"
                )
            )

            sweep_structure = safe_float(
                sweep_data.get(
                    "structure"
                )
            )

            sweep_momentum = safe_float(
                sweep_data.get(
                    "momentum"
                )
            )

            sweep_orderflow = safe_float(
                sweep_data.get(
                    "orderflow"
                )
            )

            sweep_reversal = safe_float(
                sweep_data.get(
                    "reversal"
                )
            )

            sweep_retest = safe_float(
                sweep_data.get(
                    "retest"
                )
            )

            sweep_volume = safe_float(
                sweep_data.get(
                    "volume"
                )
            )

            writer.writerow([
                timestamp,
                direction,

                f"{safe_float(score):.2f}",

                f"{safe_float(agreement) * 100:.2f}%",

                reason,

                f"{safe_float(plan.get('entry')):.2f}",
                f"{safe_float(plan.get('stop')):.2f}",
                f"{safe_float(plan.get('target')):.2f}",
                f"{safe_float(plan.get('rr')):.2f}",

                f"{safe_float(flow_data.get('imb')):.4f}",
                f"{safe_float(flow_data.get('delta')):.4f}",
                f"{safe_float(flow_data.get('large')):.4f}",
                f"{safe_float(flow_data.get('buy')):.4f}",
                f"{safe_float(flow_data.get('sell')):.4f}",

                flow_data.get(
                    "trade_count",
                    0,
                ),

                sweep_data.get(
                    "type",
                    "NONE",
                ),

                f"{safe_float(sweep_data.get('level')):.2f}",

                f"{sweep_distance_atr:.4f}",

                f"{sweep_reclaim_strength:.4f}",

                f"{sweep_quality:.4f}",

                sweep_data.get(
                    "age",
                    "",
                ),

                f"{sweep_confirmation:.4f}",

                f"{sweep_structure:.4f}",

                f"{sweep_momentum:.4f}",

                f"{sweep_orderflow:.4f}",

                f"{sweep_reversal:.4f}",

                f"{sweep_retest:.4f}",

                f"{sweep_volume:.4f}",
            ])

    except Exception as e:
        print(
            "CSV_LOG_ERROR",
            repr(e),
        )

        journal(
            "CSV_LOG_ERROR",
            error=repr(e),
        )


# ============================================================
# TELEGRAM
# ============================================================

def telegram(text):

    if not TOKEN or not CHAT_ID:

        print(
            "TELEGRAM_NOT_CONFIGURED"
        )

        print(text)

        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/sendMessage"
    )

    try:

        r = session.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
            },
            timeout=REQUEST_TIMEOUT,
        )

        r.raise_for_status()

        return True

    except Exception as e:

        print(
            "TELEGRAM_ERROR",
            repr(e),
        )

        journal(
            "TELEGRAM_ERROR",
            error=repr(e),
        )

        return False


# ============================================================
# COINBASE REST
# ============================================================

def coinbase_get(
    path,
    params=None,
):

    r = session.get(
        REST + path,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    r.raise_for_status()

    return r.json()


# ============================================================
# CANDLES
# ============================================================

def candles(
    granularity,
    limit=300,
):

    data = coinbase_get(
        f"/products/{PRODUCT}/candles",
        params={
            "granularity": int(
                granularity
            )
        },
    )

    if not isinstance(
        data,
        list,
    ):
        raise RuntimeError(
            "Unexpected candle response"
        )

    rows = []

    for x in data[:limit]:

        if len(x) < 6:
            continue

        rows.append({
            "ts": pd.to_datetime(
                int(x[0]),
                unit="s",
                utc=True,
            ),

            "low": safe_float(x[1]),
            "high": safe_float(x[2]),
            "open": safe_float(x[3]),
            "close": safe_float(x[4]),
            "volume": safe_float(x[5]),
        })

    df = pd.DataFrame(rows)

    if df.empty:

        raise RuntimeError(
            f"No candles returned "
            f"for granularity={granularity}"
        )

    return (
        df.sort_values("ts")
        .drop_duplicates("ts")
        .reset_index(drop=True)
    )


def candles_1h_history(
    total=HISTORY_1H_BARS
):

    granularity = 3600

    per = 300

    need = int(
        math.ceil(
            total / per
        )
    )

    end = int(
        time.time()
        // granularity
        * granularity
    )

    chunks = []

    for _ in range(need):

        start = (
            end
            - per * granularity
        )

        data = coinbase_get(
            f"/products/{PRODUCT}/candles",
            params={
                "granularity": granularity,

                "start": datetime.fromtimestamp(
                    start,
                    tz=timezone.utc,
                ).isoformat(),

                "end": datetime.fromtimestamp(
                    end,
                    tz=timezone.utc,
                ).isoformat(),
            },
        )

        if not isinstance(
            data,
            list,
        ):
            raise RuntimeError(
                "Unexpected 1H candle response"
            )

        for x in data:

            if len(x) < 6:
                continue

            chunks.append({
                "ts": pd.to_datetime(
                    int(x[0]),
                    unit="s",
                    utc=True,
                ),

                "low": safe_float(x[1]),
                "high": safe_float(x[2]),
                "open": safe_float(x[3]),
                "close": safe_float(x[4]),
                "volume": safe_float(x[5]),
            })

        end = start - 1

    df = pd.DataFrame(
        chunks
    )

    if df.empty:

        raise RuntimeError(
            "No 1H history returned"
        )

    df = (
        df.sort_values("ts")
        .drop_duplicates("ts")
        .reset_index(drop=True)
    )

    cutoff = pd.Timestamp(
        int(
            time.time()
            // 3600
            * 3600
        ),
        unit="s",
        tz="UTC",
    )

    df = df[
        df["ts"] < cutoff
    ].copy()

    return (
        df.tail(total)
        .reset_index(drop=True)
    )


# ============================================================
# RESAMPLE
# ============================================================

def resample_ohlcv(
    df,
    hours,
):

    if df.empty:
        return df.copy()

    x = (
        df.copy()
        .set_index("ts")
        .sort_index()
    )

    rule = f"{hours}h"

    out = x.resample(
        rule,
        origin="epoch",
        label="left",
        closed="left",
    ).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })

    counts = (
        x["close"]
        .resample(
            rule,
            origin="epoch",
            label="left",
            closed="left",
        )
        .count()
    )

    out = (
        out[
            counts >= hours
        ]
        .dropna()
        .reset_index()
    )

    return out


# ============================================================
# CLOSED / LIVE
# ============================================================

def closed_live(df):

    if len(df) < 5:

        raise RuntimeError(
            "Insufficient candles"
        )

    d = df.copy()

    if not pd.api.types.is_datetime64_any_dtype(
        d["ts"]
    ):

        d["ts"] = pd.to_datetime(
            d["ts"],
            utc=True,
        )

    step = int(
        (
            d["ts"].iloc[-1]
            - d["ts"].iloc[-2]
        ).total_seconds()
    )

    if step > 0:

        end = (
            d["ts"].iloc[-1]
            .timestamp()
            + step
        )

        if time.time() < end:

            return (
                d.iloc[:-1].copy(),
                d.iloc[-1].copy(),
            )

    return (
        d.copy(),
        d.iloc[-1].copy(),
    )


# ============================================================
# FRAME BUILD
# ============================================================

def frames():

    h1 = candles_1h_history(
        HISTORY_1H_BARS
    )

    m15_raw = candles(
        900,
        300,
    )

    m15_closed, m15_live = closed_live(
        m15_raw
    )

    h2 = resample_ohlcv(
        h1,
        2,
    )

    h4 = resample_ohlcv(
        h1,
        4,
    )

    return {
        "15m": m15_closed,
        "15m_live": m15_live,
        "1h": h1,
        "2h": h2,
        "4h": h4,
    }


# ============================================================
# ATR
# ============================================================

def atr(
    df,
    n=14,
):

    h = df["high"]
    l = df["low"]
    c = df["close"]

    prev = c.shift(1)

    tr = pd.concat([
        h - l,
        (h - prev).abs(),
        (l - prev).abs(),
    ], axis=1).max(axis=1)

    return tr.rolling(n).mean()


# ============================================================
# RSI
# ============================================================

def rsi(
    s,
    n=14,
):

    d = s.diff()

    up = d.clip(
        lower=0
    )

    dn = -d.clip(
        upper=0
    )

    au = up.ewm(
        alpha=1 / n,
        adjust=False,
    ).mean()

    ad = dn.ewm(
        alpha=1 / n,
        adjust=False,
    ).mean()

    rs = (
        au
        / ad.replace(
            0,
            np.nan,
        )
    )

    out = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    return out.fillna(50)


# ============================================================
# INDICATORS
# ============================================================

def indicators(df):

    x = df.copy()

    x["atr"] = atr(
        x
    )

    x["rsi"] = rsi(
        x["close"],
        14,
    )

    for n in [
        9,
        21,
        50,
        200,
    ]:

        x[f"ema{n}"] = (
            x["close"]
            .ewm(
                span=n,
                adjust=False,
            )
            .mean()
        )

    x["vol_ma20"] = (
        x["volume"]
        .rolling(20)
        .mean()
    )

    x["vol_ratio"] = (
        x["volume"]
        /
        x["vol_ma20"].replace(
            0,
            np.nan,
        )
    )

    x["ret1"] = (
        x["close"]
        .pct_change()
    )

    x["ret8"] = (
        x["close"]
        .pct_change(8)
    )

    x["vol_z"] = (
        (
            x["volume"]
            - x["volume"]
            .rolling(30)
            .mean()
        )
        /
        x["volume"]
        .rolling(30)
        .std()
    )

    x["atr_pct"] = (
        x["atr"]
        .rank(
            pct=True
        )
    )

    return x


# ============================================================
# PREPARE
# ============================================================

def prepare(df):

    x = indicators(
        df
    )

    x["ema21_prev"] = (
        x["ema21"]
        .shift(3)
    )

    x["hh20"] = (
        x["high"]
        .rolling(20)
        .max()
    )

    x["ll20"] = (
        x["low"]
        .rolling(20)
        .min()
    )

    x["hh8"] = (
        x["high"]
        .rolling(8)
        .max()
    )

    x["ll8"] = (
        x["low"]
        .rolling(8)
        .min()
    )

    return (
        x.dropna()
        .reset_index(drop=True)
    )


# ============================================================
# CONTEXT
# ============================================================

def context(row):

    e21 = row["ema21"]
    e50 = row["ema50"]
    e200 = row["ema200"]

    close = row["close"]

    slope = (
        row["ema21"]
        - row["ema21_prev"]
        if "ema21_prev" in row
        else 0
    )

    if (
        close > e21
        > e50
        > e200
        and slope > 0
    ):

        label = "BULLISH"

    elif (
        close < e21
        < e50
        < e200
        and slope < 0
    ):

        label = "BEARISH"

    else:

        label = "NEUTRAL"

    return {
        "label": label,
        "slope": slope,
        "above21": close > e21,
    }


# ============================================================
# LIQUIDITY SWEEP DETECTOR
#
# Primary detector = CLOSED 1H candles.
#
# SELL_SIDE_SWEEP:
#   Current low breaks previous liquidity low
#   and closes back above that level.
#
# BUY_SIDE_SWEEP:
#   Current high breaks previous liquidity high
#   and closes back below that level.
#
# DOUBLE_SWEEP:
#   Both sides are swept/reclaimed on the same candle.
# ============================================================

def detect_liquidity_sweep(
    df,
):

    if len(df) < (
        SWEEP_LOOKBACK + 5
    ):

        return {
            "type": "NONE",
            "level": 0.0,
            "distance_atr": 0.0,
            "reclaim_strength": 0.0,
            "quality": 0.0,
            "age": None,
            "direction": None,
            "candle_ts": None,
            "penetration": 0.0,
            "rejection": 0.0,
        }

    d = prepare(
        df
    )

    if len(d) < (
        SWEEP_LOOKBACK + 3
    ):

        return {
            "type": "NONE",
            "level": 0.0,
            "distance_atr": 0.0,
            "reclaim_strength": 0.0,
            "quality": 0.0,
            "age": None,
            "direction": None,
            "candle_ts": None,
            "penetration": 0.0,
            "rejection": 0.0,
        }

    # Work only with the latest CLOSED 1H candle.
    r = d.iloc[-1]

    history = d.iloc[
        -SWEEP_LOOKBACK - 1:-1
    ]

    previous_high = float(
        history["high"].max()
    )

    previous_low = float(
        history["low"].min()
    )

    current_high = float(
        r["high"]
    )

    current_low = float(
        r["low"]
    )

    current_open = float(
        r["open"]
    )

    current_close = float(
        r["close"]
    )

    current_atr = max(
        safe_float(
            r["atr"]
        ),
        current_close * 0.0001,
    )

    candle_range = max(
        current_high - current_low,
        1e-9,
    )

    # --------------------------------------------------------
    # TOP / BUY-SIDE SWEEP
    # --------------------------------------------------------

    buy_side_break = (
        current_high
        > previous_high
    )

    buy_side_reclaim = (
        current_close
        < previous_high
    )

    buy_side_sweep = (
        buy_side_break
        and buy_side_reclaim
    )

    # --------------------------------------------------------
    # BOTTOM / SELL-SIDE SWEEP
    # --------------------------------------------------------

    sell_side_break = (
        current_low
        < previous_low
    )

    sell_side_reclaim = (
        current_close
        > previous_low
    )

    sell_side_sweep = (
        sell_side_break
        and sell_side_reclaim
    )

    # --------------------------------------------------------
    # NO SWEEP
    # --------------------------------------------------------

    if not (
        buy_side_sweep
        or sell_side_sweep
    ):

        return {
            "type": "NONE",
            "level": 0.0,
            "distance_atr": 0.0,
            "reclaim_strength": 0.0,
            "quality": 0.0,
            "age": 0,
            "direction": None,
            "candle_ts": r["ts"].isoformat(),
            "penetration": 0.0,
            "rejection": 0.0,
        }

    # --------------------------------------------------------
    # BOTH SIDES = DOUBLE SWEEP
    # --------------------------------------------------------

    if (
        buy_side_sweep
        and sell_side_sweep
    ):

        high_penetration = (
            current_high
            - previous_high
        )

        low_penetration = (
            previous_low
            - current_low
        )

        high_distance_atr = (
            high_penetration
            / current_atr
        )

        low_distance_atr = (
            low_penetration
            / current_atr
        )

        high_reclaim = (
            previous_high
            - current_close
        ) / candle_range

        low_reclaim = (
            current_close
            - previous_low
        ) / candle_range

        reclaim_strength = max(
            0.0,
            min(
                1.0,
                max(
                    high_reclaim,
                    low_reclaim,
                ),
            ),
        )

        penetration = max(
            high_distance_atr,
            low_distance_atr,
        )

        quality_pen = min(
            1.0,
            penetration
            / 0.50,
        )

        quality = (
            0.50
            * quality_pen
            +
            0.50
            * reclaim_strength
        )

        return {
            "type": "DOUBLE_SWEEP",
            "level": (
                previous_high
                if high_distance_atr
                >= low_distance_atr
                else previous_low
            ),
            "distance_atr": penetration,
            "reclaim_strength": reclaim_strength,
            "quality": max(
                0.0,
                min(
                    1.0,
                    quality,
                ),
            ),
            "age": 0,
            "direction": None,
            "candle_ts": r["ts"].isoformat(),
            "penetration": penetration,
            "rejection": reclaim_strength,
            "buy_side_level": previous_high,
            "sell_side_level": previous_low,
            "buy_side_distance_atr": high_distance_atr,
            "sell_side_distance_atr": low_distance_atr,
        }

    # --------------------------------------------------------
    # SELL-SIDE SWEEP
    # Potential LONG reversal.
    # --------------------------------------------------------

    if sell_side_sweep:

        penetration = (
            previous_low
            - current_low
        )

        distance_atr = (
            penetration
            / current_atr
        )

        # How far the close has reclaimed the
        # swept level relative to the candle range.
        reclaim_strength = (
            current_close
            - previous_low
        ) / candle_range

        reclaim_strength = max(
            0.0,
            min(
                1.0,
                reclaim_strength,
            ),
        )

        # Lower wick/rejection quality.
        lower_wick = (
            min(
                current_open,
                current_close,
            )
            - current_low
        )

        rejection = (
            lower_wick
            / candle_range
        )

        rejection = max(
            0.0,
            min(
                1.0,
                rejection,
            ),
        )

        quality_pen = min(
            1.0,
            distance_atr
            / 0.50,
        )

        quality = (
            0.40
            * quality_pen
            +
            0.40
            * reclaim_strength
            +
            0.20
            * rejection
        )

        return {
            "type": "SELL_SIDE_SWEEP",
            "level": previous_low,
            "distance_atr": distance_atr,
            "reclaim_strength": reclaim_strength,
            "quality": max(
                0.0,
                min(
                    1.0,
                    quality,
                ),
            ),
            "age": 0,
            "direction": "LONG",
            "candle_ts": r["ts"].isoformat(),
            "penetration": penetration,
            "rejection": rejection,
        }

    # --------------------------------------------------------
    # BUY-SIDE SWEEP
    # Potential SHORT reversal.
    # --------------------------------------------------------

    penetration = (
        current_high
        - previous_high
    )

    distance_atr = (
        penetration
        / current_atr
    )

    reclaim_strength = (
        previous_high
        - current_close
    ) / candle_range

    reclaim_strength = max(
        0.0,
        min(
            1.0,
            reclaim_strength,
        ),
    )

    upper_wick = (
        current_high
        - max(
            current_open,
            current_close,
        )
    )

    rejection = (
        upper_wick
        / candle_range
    )

    rejection = max(
        0.0,
        min(
            1.0,
            rejection,
        ),
    )

    quality_pen = min(
        1.0,
        distance_atr
        / 0.50,
    )

    quality = (
        0.40
        * quality_pen
        +
        0.40
        * reclaim_strength
        +
        0.20
        * rejection
    )

    return {
        "type": "BUY_SIDE_SWEEP",
        "level": previous_high,
        "distance_atr": distance_atr,
        "reclaim_strength": reclaim_strength,
        "quality": max(
            0.0,
            min(
                1.0,
                quality,
            ),
        ),
        "age": 0,
        "direction": "SHORT",
        "candle_ts": r["ts"].isoformat(),
        "penetration": penetration,
        "rejection": rejection,
    }


# ============================================================
# ORDER BOOK
# ============================================================

def book_depth():

    data = coinbase_get(
        f"/products/{PRODUCT}/book",
        {
            "level": 2
        },
    )

    bids = data.get(
        "bids",
        []
    )

    asks = data.get(
        "asks",
        []
    )

    with flow_lock:

        flow["bids"] = {
            safe_float(p):
                safe_float(s)

            for p, s, *_ in bids[:100]
        }

        flow["asks"] = {
            safe_float(p):
                safe_float(s)

            for p, s, *_ in asks[:100]
        }

        flow["updated"] = now_ts()


# ============================================================
# RECENT TRADES
# ============================================================

def recent_trades():

    data = coinbase_get(
        f"/products/{PRODUCT}/trades",
        {
            "limit":
                FLOW_TRADE_LIMIT
        },
    )

    if not isinstance(
        data,
        list,
    ):
        raise RuntimeError(
            "Unexpected trades response"
        )

    buy = 0.0
    sell = 0.0
    large = 0.0
    delta = 0.0

    trade_count = 0

    for t in data[
        :FLOW_TRADE_LIMIT
    ]:

        if not isinstance(
            t,
            dict,
        ):
            continue

        size = safe_float(
            t.get("size")
        )

        side = str(
            t.get(
                "side",
                "",
            )
        ).lower()

        trade_count += 1

        # Preserve existing V5 interpretation.
        #
        # Coinbase trade side semantics can be easy to
        # confuse with aggressor direction, so we retain
        # the V5 delta convention here.

        if side == "sell":

            buy += size
            delta += size

            if (
                size
                >= FLOW_LARGE_TRADE_BTC
            ):
                large += size

        elif side == "buy":

            sell += size
            delta -= size

            if (
                size
                >= FLOW_LARGE_TRADE_BTC
            ):
                large -= size

    with flow_lock:

        flow["buy"] = buy
        flow["sell"] = sell

        flow["delta"] = delta
        flow["large"] = large

        flow["trade_count"] = (
            trade_count
        )

        flow["updated"] = now_ts()


# ============================================================
# FLOW METRICS
# ============================================================

def flow_metrics():

    with flow_lock:

        bids = dict(
            flow["bids"]
        )

        asks = dict(
            flow["asks"]
        )

        delta = flow["delta"]
        large = flow["large"]

        buy = flow["buy"]
        sell = flow["sell"]

        trade_count = flow.get(
            "trade_count",
            0,
        )

        updated = flow["updated"]

    bid = sum(
        sorted(
            bids.values(),
            reverse=True,
        )[:20]
    )

    ask = sum(
        sorted(
            asks.values(),
            reverse=True,
        )[:20]
    )

    imb = (
        (bid - ask)
        /
        max(
            bid + ask,
            1e-9,
        )
    )

    return {
        "bid20": bid,
        "ask20": ask,
        "imb": imb,
        "delta": delta,
        "large": large,
        "buy": buy,
        "sell": sell,
        "trade_count": trade_count,
        "updated": updated,
    }


# ============================================================
# REST FLOW SNAPSHOT
# ============================================================

def rest_flow_snapshot(
    state=None,
):

    try:

        book_depth()
        recent_trades()

        current = flow_metrics()

        previous = (
            (state or {})
            .get(
                "flow_snapshot"
            )
            or {}
        )

        current["delta_change"] = (
            safe_float(
                current.get(
                    "delta"
                )
            )
            -
            safe_float(
                previous.get(
                    "delta"
                )
            )
        )

        current["imb_change"] = (
            safe_float(
                current.get(
                    "imb"
                )
            )
            -
            safe_float(
                previous.get(
                    "imb"
                )
            )
        )

        if state is not None:

            state["flow_snapshot"] = {
                "ts": now_ts(),
                "imb": current["imb"],
                "delta": current["delta"],
                "large": current["large"],
                "buy": current["buy"],
                "sell": current["sell"],
                "trade_count":
                    current[
                        "trade_count"
                    ],
            }

        return (
            True,
            current,
        )

    except Exception as e:

        journal(
            "FLOW_REST_ERROR",
            error=repr(e),
        )

        return (
            False,
            {
                "error":
                    repr(e)
            },
        )


# ============================================================
# ORDER FLOW EVALUATION
# ============================================================

def flow_ev(price):

    m = flow_metrics()

    stale = (
        now_ts()
        - m["updated"]
    ) > ORDERFLOW_STALE

    imb = m["imb"]
    delta = m["delta"]
    large = m["large"]

    buy = m["buy"]
    sell = m["sell"]

    if stale:

        return {
            "score": 0.0,
            "text":
                "order flow stale",
            "stale": True,
            "imb": imb,
        }

    score = 0.0

    if imb > 0.08:

        score += 0.5

    elif imb < -0.08:

        score -= 0.5

    if delta > 0:

        score += 0.35

    elif delta < 0:

        score -= 0.35

    if large > 0:

        score += 0.15

    elif large < 0:

        score -= 0.15

    return {
        "score":
            max(
                -1,
                min(
                    1,
                    score,
                ),
            ),

        "text": (
            f"imb={imb:.2f} "
            f"delta={delta:.3f} "
            f"large={large:.3f} "
            f"buy={buy:.3f} "
            f"sell={sell:.3f} "
            f"trades="
            f"{m.get('trade_count', 0)}"
        ),

        "stale": False,

        "imb": imb,
        "buy": buy,
        "sell": sell,
        "delta": delta,
        "large": large,

        "trade_count":
            m.get(
                "trade_count",
                0,
            ),
    }


# ============================================================
# TREND
# ============================================================

def trend_ev(
    df,
    direction,
):

    r = df.iloc[-1]

    bullish = (
        r.close
        > r.ema21
        > r.ema50
        > r.ema200
    )

    bearish = (
        r.close
        < r.ema21
        < r.ema50
        < r.ema200
    )

    slope = (
        r.ema21
        - r.ema21_prev
    )

    if direction == "LONG":

        return (
            1.0
            if bullish and slope > 0
            else 0.5
            if r.close > r.ema21
            else 0.0
        )

    return (
        1.0
        if bearish and slope < 0
        else 0.5
        if r.close < r.ema21
        else 0.0
    )


# ============================================================
# PRICE
# ============================================================

def price_ev(
    df,
    direction,
):

    r = df.iloc[-1]

    dist = (
        (r.close - r.ema21)
        /
        max(
            r.atr,
            1e-9,
        )
    )

    if direction == "LONG":

        return (
            1.0
            if -0.5 <= dist <= 1.5
            else 0.5
            if dist > 0
            else 0.25
        )

    return (
        1.0
        if -1.5 <= dist <= 0.5
        else 0.5
        if dist < 0
        else 0.25
    )


# ============================================================
# MOMENTUM
# ============================================================

def momentum_ev(
    df,
    direction,
):

    r = df.iloc[-1]

    if direction == "LONG":

        return (
            1.0
            if (
                52 <= r.rsi <= 72
                and r.ret8 > 0
            )
            else 0.5
            if r.ret8 > 0
            else 0
        )

    return (
        1.0
        if (
            28 <= r.rsi <= 48
            and r.ret8 < 0
        )
        else 0.5
        if r.ret8 < 0
        else 0
    )


# ============================================================
# VOLUME
# ============================================================

def volume_ev(df):

    r = df.iloc[-1]

    vr = safe_float(
        r.vol_ratio,
        1,
    )

    return (
        1.0
        if vr >= 1.25
        else 0.5
        if vr >= 0.9
        else 0.25
    )


# ============================================================
# STRUCTURE
# ============================================================

def structure_ev(
    df,
    direction,
):

    r = df.iloc[-1]

    if direction == "LONG":

        return (
            1.0
            if r.close
            > r.hh8 * 0.998

            else 0.5
            if r.close > r.ema21

            else 0.25
        )

    return (
        1.0
        if r.close
        < r.ll8 * 1.002

        else 0.5
        if r.close < r.ema21

        else 0.25
    )


# ============================================================
# REVERSAL
# ============================================================

def reversal_ev(
    df,
    direction,
):

    if len(df) < 3:
        return 0.0

    a, b, c = (
        df.iloc[-3],
        df.iloc[-2],
        df.iloc[-1],
    )

    if direction == "LONG":

        pin = (
            c.low < b.low
            and c.close > c.open
            and c.close > b.close
        )

        return (
            1.0
            if pin
            else 0.0
        )

    pin = (
        c.high > b.high
        and c.close < c.open
        and c.close < b.close
    )

    return (
        1.0
        if pin
        else 0.0
    )


# ============================================================
# IMPULSE / RETEST
# ============================================================

def impulse_retest_ev(
    df,
    direction,
    state,
):

    r = df.iloc[-1]

    imp = state.get(
        "impulse"
    )

    if (
        imp
        and imp.get(
            "direction"
        ) == direction
    ):

        age = int(
            imp.get(
                "bars",
                0,
            )
        )

        level = safe_float(
            imp.get(
                "level"
            )
        )

        state[
            "impulse"
        ]["bars"] = (
            age + 1
        )

        if (
            direction == "LONG"
            and r.low
            <= level * 1.002
            and r.close > level
        ):

            return 1.0

        if (
            direction == "SHORT"
            and r.high
            >= level * 0.998
            and r.close < level
        ):

            return 1.0

        if age > 8:

            state[
                "impulse"
            ] = None

    move = safe_float(
        r.ret8
    )

    if (
        direction == "LONG"
        and move > 0.025
    ):

        state["impulse"] = {
            "direction":
                "LONG",
            "level":
                float(r.close),
            "bars":
                0,
        }

    elif (
        direction == "SHORT"
        and move < -0.025
    ):

        state["impulse"] = {
            "direction":
                "SHORT",
            "level":
                float(r.close),
            "bars":
                0,
        }

    return 0.0


# ============================================================
# NORMALIZED ORDERFLOW CONFIRMATION
# ============================================================

def orderflow_confirmation(
    flow_data,
    direction,
):

    if not flow_data:
        return 0.0

    if flow_data.get(
        "stale",
        False,
    ):
        return 0.0

    score = safe_float(
        flow_data.get(
            "score"
        )
    )

    # Directional alignment.
    if direction == "LONG":

        return max(
            0.0,
            min(
                1.0,
                (
                    score + 1
                ) / 2,
            ),
        )

    return max(
        0.0,
        min(
            1.0,
            (
                -score + 1
            ) / 2,
        ),
    )


# ============================================================
# SWEEP CONFIRMATION
#
# Sweep is NOT an independent trade trigger.
#
# It asks:
#   1. Was there a quality sweep?
#   2. Did structure agree?
#   3. Did momentum agree?
#   4. Did orderflow agree?
#   5. Did reversal agree?
#   6. Did retest agree?
#   7. Did volume participate?
# ============================================================

def sweep_confirmation(
    F,
    direction,
    sweep,
    vals,
    flow_data,
):

    if not sweep:
        return {
            "active": False,
            "confirmation": 0.0,
            "reason": "no_sweep",
        }

    sweep_type = sweep.get(
        "type",
        "NONE",
    )

    # Double sweep is deliberately not assigned
    # to either direction automatically.
    if sweep_type == "DOUBLE_SWEEP":

        return {
            "active": True,
            "confirmation": 0.0,
            "reason":
                "double_sweep_direction_unclear",
            "structure": 0.0,
            "momentum": 0.0,
            "orderflow": 0.0,
            "reversal": 0.0,
            "retest": 0.0,
            "volume": 0.0,
        }

    expected_direction = sweep.get(
        "direction"
    )

    # A sweep in the opposite direction is not
    # confirmation for this trade.
    if (
        expected_direction
        != direction
    ):

        return {
            "active": True,
            "confirmation": 0.0,
            "reason":
                "opposite_sweep",
            "structure": 0.0,
            "momentum": 0.0,
            "orderflow": 0.0,
            "reversal": 0.0,
            "retest": 0.0,
            "volume": 0.0,
        }

    quality = safe_float(
        sweep.get(
            "quality"
        )
    )

    reclaim = safe_float(
        sweep.get(
            "reclaim_strength"
        )
    )

    distance_atr = safe_float(
        sweep.get(
            "distance_atr"
        )
    )

    if (
        distance_atr
        < SWEEP_MIN_PENETRATION_ATR
    ):

        return {
            "active": True,
            "confirmation": 0.0,
            "reason":
                "sweep_too_shallow",
            "structure": 0.0,
            "momentum": 0.0,
            "orderflow": 0.0,
            "reversal": 0.0,
            "retest": 0.0,
            "volume": 0.0,
        }

    if (
        reclaim
        < SWEEP_MIN_RECLAIM
    ):

        return {
            "active": True,
            "confirmation": 0.0,
            "reason":
                "reclaim_too_weak",
            "structure": 0.0,
            "momentum": 0.0,
            "orderflow": 0.0,
            "reversal": 0.0,
            "retest": 0.0,
            "volume": 0.0,
        }

    # Existing V5 evidence becomes the witness
    # for the liquidity event.

    structure = safe_float(
        vals.get(
            "structure"
        )
    )

    momentum = safe_float(
        vals.get(
            "momentum"
        )
    )

    reversal = safe_float(
        vals.get(
            "reversal"
        )
    )

    retest = safe_float(
        vals.get(
            "retest"
        )
    )

    volume = safe_float(
        vals.get(
            "volume"
        )
    )

    orderflow = orderflow_confirmation(
        flow_data,
        direction,
    )

    components = {
        "sweep_quality":
            quality,

        "structure":
            structure,

        "momentum":
            momentum,

        "orderflow":
            orderflow,

        "reversal":
            reversal,

        "retest":
            retest,

        "volume":
            volume,
    }

    confirmation = sum(
        SWEEP_CONFIRM_WEIGHTS[k]
        * components[k]

        for k in SWEEP_CONFIRM_WEIGHTS
    )

    confirmation = max(
        0.0,
        min(
            1.0,
            confirmation,
        ),
    )

    return {
        "active": True,
        "confirmation": confirmation,
        "reason":
            (
                "confirmed"
                if confirmation
                >= SWEEP_MIN_CONFIRMATION
                else "confirmation_low"
            ),

        **components,
    }


# ============================================================
# RISK PLAN
# ============================================================

def risk_plan(
    df,
    direction,
):

    r = df.iloc[-1]

    entry = float(
        r.close
    )

    a = max(
        float(r.atr),
        entry * 0.002,
    )

    if direction == "LONG":

        stop = (
            entry
            - 1.25 * a
        )

        target = (
            entry
            + 2.0 * a
        )

    else:

        stop = (
            entry
            + 1.25 * a
        )

        target = (
            entry
            - 2.0 * a
        )

    risk = abs(
        entry - stop
    )

    reward = abs(
        target - entry
    )

    rr = (
        reward
        /
        max(
            risk,
            1e-9,
        )
    )

    return {
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "atr": a,
    }


# ============================================================
# SCORE DIRECTION
# ============================================================

def score_direction(
    F,
    direction,
    state,
):

    d15 = prepare(
        F["15m"]
    )

    d1 = prepare(
        F["1h"]
    )

    d2 = prepare(
        F["2h"]
    )

    d4 = prepare(
        F["4h"]
    )

    r1 = d1.iloc[-1]

    vals = {
        "trend": (
            trend_ev(
                d4,
                direction,
            )
            +
            trend_ev(
                d2,
                direction,
            )
            +
            trend_ev(
                d1,
                direction,
            )
        ) / 3,

        "price":
            price_ev(
                d1,
                direction,
            ),

        "momentum":
            momentum_ev(
                d1,
                direction,
            ),

        "volume":
            volume_ev(
                d1,
            ),

        "structure": (
            structure_ev(
                d1,
                direction,
            )
            +
            structure_ev(
                d15,
                direction,
            )
        ) / 2,

        "reversal":
            reversal_ev(
                d15,
                direction,
            ),

        "retest":
            impulse_retest_ev(
                d1,
                direction,
                state,
            ),
    }

    fe = flow_ev(
        float(
            r1.close
        )
    )

    vals["orderflow"] = (
        fe["score"]
        if direction == "LONG"
        else -fe["score"]
    )

    total = sum(
        WEIGHTS[k]
        * vals[k]

        for k in WEIGHTS
    )

    agreement = (
        sum(
            v >= 0.5
            for v in vals.values()
        )
        /
        len(vals)
    )

    return (
        total,
        agreement,
        vals,
        fe,
    )


# ============================================================
# BUILD SIGNAL
# ============================================================

def build_signal(
    F,
    state,
):

    d1 = prepare(
        F["1h"]
    )

    if len(d1) < 210:

        raise RuntimeError(
            "Not enough 1H candles "
            "for EMA200"
        )

    # --------------------------------------------------------
    # Detect latest CLOSED 1H liquidity event.
    # --------------------------------------------------------

    sweep = detect_liquidity_sweep(
        F["1h"]
    )

    # --------------------------------------------------------
    # Store latest sweep for diagnostics.
    # --------------------------------------------------------

    state["last_sweep"] = copy.deepcopy(
        sweep
    )

    # --------------------------------------------------------
    # Independent states for LONG / SHORT.
    # --------------------------------------------------------

    long_state = copy.deepcopy(
        state
    )

    short_state = copy.deepcopy(
        state
    )

    long_score, long_ag, long_vals, long_flow = (
        score_direction(
            F,
            "LONG",
            long_state,
        )
    )

    short_score, short_ag, short_vals, short_flow = (
        score_direction(
            F,
            "SHORT",
            short_state,
        )
    )

    if long_score >= short_score:

        direction = "LONG"

        score = long_score
        agreement = long_ag
        vals = long_vals
        fe = long_flow

        selected_state = long_state

    else:

        direction = "SHORT"

        score = short_score
        agreement = short_ag
        vals = short_vals
        fe = short_flow

        selected_state = short_state

    # Keep selected impulse state.
    state["impulse"] = (
        selected_state.get(
            "impulse"
        )
    )

    # --------------------------------------------------------
    # Sweep confirmation for selected direction.
    # --------------------------------------------------------

    sweep_conf = sweep_confirmation(
        F,
        direction,
        sweep,
        vals,
        fe,
    )

    plan = risk_plan(
        d1,
        direction,
    )

    reasons = []

    # --------------------------------------------------------
    # Existing V5 gates.
    # --------------------------------------------------------

    if score < MIN_SCORE:

        reasons.append(
            "score_low"
        )

    if agreement < MIN_AGREEMENT:

        reasons.append(
            "agreement_low"
        )

    if plan["rr"] < 1.20:

        reasons.append(
            "rr_low"
        )

    # --------------------------------------------------------
    # NEW SWEEP GATE
    #
    # Only applies when a meaningful fresh sweep exists.
    #
    # No sweep:
    #   Existing V5 can still produce a signal.
    #
    # Valid sweep:
    #   It must be confirmed by core evidence.
    #
    # Opposite / weak / double sweep:
    #   No automatic trade.
    # --------------------------------------------------------

    if sweep_conf.get(
        "active",
        False,
    ):

        if (
            sweep.get(
                "type"
            ) == "DOUBLE_SWEEP"
        ):

            reasons.append(
                "double_sweep_unclear"
            )

        elif (
            sweep_conf.get(
                "confirmation",
                0.0,
            )
            < SWEEP_MIN_CONFIRMATION
        ):

            reasons.append(
                "sweep_not_confirmed"
            )

    if reasons:

        return None, {
            "direction": direction,
            "score": score,
            "agreement": agreement,
            "vals": vals,
            "flow": fe,
            "plan": plan,
            "reason": ",".join(
                reasons
            ),
            "sweep": sweep,
            "sweep_confirmation":
                sweep_conf,
        }

    strength = (
        "STRONG"
        if (
            score >= STRONG_SCORE
            and
            agreement
            >= STRONG_AGREEMENT
        )
        else "VALID"
    )

    # If a sweep is present and strongly confirmed,
    # expose that explicitly without altering the
    # original V5 numerical score.
    if (
        sweep_conf.get(
            "confirmation",
            0.0,
        )
        >= 0.75
        and
        sweep_conf.get(
            "active",
            False,
        )
    ):

        strength = (
            "SWEEP_CONFIRMED_"
            + strength
        )

    signal_id = (
        f"{direction}:"
        f"{round(plan['entry'], 2)}:"
        f"{d1.iloc[-1]['ts'].isoformat()}"
    )

    return {
        "id": signal_id,
        "direction": direction,
        "strength": strength,
        "score": score,
        "agreement": agreement,
        "plan": plan,
        "vals": vals,
        "flow": fe,
        "sweep": sweep,
        "sweep_confirmation":
            sweep_conf,
        "ts": iso(),
    }, {}


# ============================================================
# FORMAT SWEEP TEXT
# ============================================================

def format_sweep(
    sweep,
    confirmation,
):

    if not sweep:

        return (
            "Sweep: NONE"
        )

    sweep_type = sweep.get(
        "type",
        "NONE",
    )

    if sweep_type == "NONE":

        return (
            "Sweep: NONE"
        )

    level = safe_float(
        sweep.get(
            "level"
        )
    )

    distance = safe_float(
        sweep.get(
            "distance_atr"
        )
    )

    reclaim = safe_float(
        sweep.get(
            "reclaim_strength"
        )
    )

    quality = safe_float(
        sweep.get(
            "quality"
        )
    )

    conf = safe_float(
        confirmation.get(
            "confirmation"
        )
    )

    reason = confirmation.get(
        "reason",
        "n/a",
    )

    return (
        f"Sweep: {sweep_type}\n"
        f"Level: {level:.2f}\n"
        f"Distance: "
        f"{distance:.2f} ATR\n"
        f"Reclaim: "
        f"{reclaim * 100:.0f}%\n"
        f"Quality: "
        f"{quality * 100:.0f}%\n"
        f"Confirmation: "
        f"{conf * 100:.0f}%\n"
        f"Sweep status: {reason}"
    )


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    sig,
):

    p = sig["plan"]
    v = sig["vals"]

    sweep_text = format_sweep(
        sig.get(
            "sweep",
            {},
        ),
        sig.get(
            "sweep_confirmation",
            {},
        ),
    )

    return (
        f"₿ BTC V6 — "
        f"{sig['strength']} "
        f"{sig['direction']}\n\n"

        f"Entry: {p['entry']:.2f}\n"
        f"SL: {p['stop']:.2f}\n"
        f"TP: {p['target']:.2f}\n"
        f"RR: {p['rr']:.2f}\n\n"

        f"Score: "
        f"{sig['score']:.1f}/"
        f"{sum(WEIGHTS.values())}\n"

        f"Agreement: "
        f"{sig['agreement'] * 100:.0f}%\n\n"

        f"Trend {v['trend']:.2f} | "
        f"Structure "
        f"{v['structure']:.2f}\n"

        f"Price {v['price']:.2f} | "
        f"Vol {v['volume']:.2f}\n"

        f"Momentum "
        f"{v['momentum']:.2f} | "
        f"Retest "
        f"{v['retest']:.2f}\n"

        f"Reversal "
        f"{v['reversal']:.2f} | "
        f"OF "
        f"{v['orderflow']:.2f}\n\n"

        f"{sweep_text}\n\n"

        f"UTC: {sig['ts']}"
    )


# ============================================================
# RUN ONCE
# ============================================================

def run_once():

    state = load_state()

    state["last_scan_ts"] = (
        now_ts()
    )

    print(
        "BTC Adaptive Telegram "
        "Bot V6 — "
        "Liquidity Sweep "
        "Confirmation"
    )

    F = frames()

    flow_ok, flow_snapshot = (
        rest_flow_snapshot(
            state
        )
    )

    sig, det = build_signal(
        F,
        state,
    )

    # ========================================================
    # WAIT
    # ========================================================

    if sig is None:

        sweep = det.get(
            "sweep",
            {},
        )

        sweep_conf = det.get(
            "sweep_confirmation",
            {},
        )

        sweep_text = format_sweep(
            sweep,
            sweep_conf,
        )

        # ----------------------------------------------------
        # FIX:
        # Extract the flow text before constructing the
        # multiline Telegram f-string.
        #
        # This preserves the exact same output while avoiding
        # the SyntaxError caused by a multiline f-string
        # expression on the GitHub Python runner.
        # ----------------------------------------------------

        wait_flow_text = det["flow"].get(
            "text",
            "n/a",
        )

        msg = (
            f"₿ BTC V6 — WAIT\n"

            f"Best: "
            f"{det['direction']}\n"

            f"Score: "
            f"{det['score']:.1f}\n"

            f"Agreement: "
            f"{det['agreement'] * 100:.0f}%\n"

            f"Reason: "
            f"{det['reason']}\n\n"

            f"{sweep_text}\n\n"

            f"OrderFlow: "
            f"{wait_flow_text}"
        )

        telegram(
            msg
        )

        journal(
            "WAIT",
            flow_ok=flow_ok,
            flow_snapshot=
                flow_snapshot,
            **det,
        )

        log_signal_csv(
            timestamp=iso(),

            direction=
                det["direction"],

            score=
                det["score"],

            agreement=
                det["agreement"],

            reason=
                det["reason"],

            plan=
                det["plan"],

            flow_data=
                det["flow"],

            sweep_data=
                sweep,
        )

        save_state(
            state
        )

        return

    # ========================================================
    # DIRECTION-SPECIFIC COOLDOWN
    # ========================================================

    last_ts = safe_float(
        state.get(
            "last_signal_ts"
        )
    )

    last_direction = (
        state.get(
            "last_direction"
        )
    )

    same = (
        state.get(
            "last_signal"
        )
        == sig["id"]
    )

    cooldown = (
        last_direction
        == sig["direction"]
        and
        (
            now_ts()
            - last_ts
        )
        < COOLDOWN_MIN * 60
    )

    if same or cooldown:

        reason = (
            "same_signal"
            if same
            else
            "same_direction_cooldown"
        )

        journal(
            "SUPPRESSED",
            signal=sig,
            same=same,
            cooldown=cooldown,
            last_direction=
                last_direction,
        )

        telegram(
            f"₿ BTC V6 — "
            f"SIGNAL SUPPRESSED\n"
            f"{sig['direction']} "
            f"{sig['score']:.1f}\n"
            f"Reason: {reason}"
        )

        log_signal_csv(
            timestamp=sig["ts"],

            direction=
                sig["direction"],

            score=
                sig["score"],

            agreement=
                sig["agreement"],

            reason=reason,

            plan=
                sig["plan"],

            flow_data=
                sig["flow"],

            sweep_data=
                sig.get(
                    "sweep",
                    {},
                ),
        )

        save_state(
            state
        )

        return

    # ========================================================
    # SEND SIGNAL
    # ========================================================

    telegram(
        format_signal(
            sig
        )
    )

    journal(
        "SIGNAL",
        signal=sig,
        flow_ok=flow_ok,
        flow_snapshot=
            flow_snapshot,
    )

    log_signal_csv(
        timestamp=sig["ts"],

        direction=
            sig["direction"],

        score=
            sig["score"],

        agreement=
            sig["agreement"],

        reason=(
            "signal_"
            f"{sig['strength'].lower()}"
        ),

        plan=
            sig["plan"],

        flow_data=
            sig["flow"],

        sweep_data=
            sig.get(
                "sweep",
                {},
            ),
    )

    state["last_signal"] = (
        sig["id"]
    )

    state["last_signal_ts"] = (
        now_ts()
    )

    state["last_direction"] = (
        sig["direction"]
    )

    save_state(
        state
    )


# ============================================================
# CONTINUOUS RUNNER
# ============================================================

def run():

    state = load_state()

    while True:

        started = time.time()

        try:

            run_once()

        except KeyboardInterrupt:

            break

        except Exception as e:

            print(
                "ERROR",
                repr(e),
            )

            journal(
                "ERROR",
                error=repr(e),
                traceback=
                    traceback.format_exc(),
            )

        time.sleep(
            max(
                2,
                SCAN_SECONDS
                - (
                    time.time()
                    - started
                ),
            )
        )


# ============================================================
# MAIN
# ============================================================

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--once",
        action="store_true",
    )

    ap.add_argument(
        "--status",
        action="store_true",
    )

    args = ap.parse_args()

    if args.status:

        print(
            json.dumps(
                load_state(),
                indent=2,
                ensure_ascii=False,
            )
        )

        return

    if args.once:

        try:

            run_once()

        except Exception as e:

            print(
                "ERROR",
                repr(e),
            )

            journal(
                "ERROR",
                error=repr(e),
                traceback=
                    traceback.format_exc(),
            )

            raise

    else:

        run()


if __name__ == "__main__":

    main()

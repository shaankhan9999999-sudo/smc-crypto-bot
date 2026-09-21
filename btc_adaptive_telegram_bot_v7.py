import os, sys, time, json, math, argparse, traceback, csv, copy, re, hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import requests
import pandas as pd
import numpy as np

# ============================================================
# BTC ADAPTIVE TELEGRAM BOT V7
# Liquidity Sweep + News + Post-Impulse Continuation + Lifecycle
# Coinbase REST only. Designed for GitHub Actions --once.
# ============================================================
PRODUCT = "BTC-USD"
REST = "https://api.exchange.coinbase.com"
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7500472109")

STATE_FILE = "btc_v7_state.json"
JOURNAL_FILE = "btc_v7_journal.jsonl"
CALIBRATION_FILE = "btc_v7_calibration.json"
CSV_LOG_FILE = "logs/btc_v7_signals.csv"

SCAN_SECONDS = 60
COOLDOWN_MIN = 45
MIN_SCORE = 62
STRONG_SCORE = 76
MIN_AGREEMENT = 0.58
STRONG_AGREEMENT = 0.68
REQUEST_TIMEOUT = 20
HISTORY_1H_BARS = 1200
FLOW_TRADE_LIMIT = 100
FLOW_LARGE_TRADE_BTC = 0.25
ORDERFLOW_STALE = 8

SWEEP_LOOKBACK = 20
SWEEP_MAX_AGE = 3
SWEEP_MIN_PENETRATION_ATR = 0.05
SWEEP_MIN_RECLAIM = 0.35
SWEEP_MIN_CONFIRMATION = 0.55

SWEEP_CONFIRM_WEIGHTS = {
    "sweep_quality": 0.20,
    "structure": 0.20,
    "momentum": 0.15,
    "orderflow": 0.15,
    "reversal": 0.10,
    "retest": 0.10,
    "volume": 0.10,
}

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

# News
NEWS_ENABLED = os.environ.get("NEWS_ENABLED", "1") != "0"
NEWS_API_KEY = os.environ.get("CRYPTOPANIC_API_KEY", "")
NEWS_API_URL = os.environ.get(
    "NEWS_API_URL",
    "https://cryptopanic.com/api/developer/v2/posts/"
)
NEWS_LOOKBACK_MIN = int(os.environ.get("NEWS_LOOKBACK_MIN", "30"))
NEWS_MAX_ARTICLES = int(os.environ.get("NEWS_MAX_ARTICLES", "20"))
NEWS_MIN_RELEVANCE = float(os.environ.get("NEWS_MIN_RELEVANCE", ".60"))
NEWS_MIN_CONFIDENCE = float(os.environ.get("NEWS_MIN_CONFIDENCE", ".55"))
NEWS_CONFIRMATION_THRESHOLD = float(
    os.environ.get("NEWS_CONFIRMATION_THRESHOLD", ".60")
)

# Post-impulse continuation
CONT_LOOKBACK = 20
CONT_IMPULSE_BARS = 6
CONT_IMPULSE_ATR = 1.8
CONT_MIN_PULLBACK = 0.18
CONT_MAX_PULLBACK = 0.65
CONT_READY_SCORE = 0.62

# Trade lifecycle / target extension
MAX_TARGET_EXTENSIONS = 2
EXTENSION_COOLDOWN_MIN = 30
MAX_TRADE_AGE_HOURS = 36

session = requests.Session()
session.headers.update({
    "User-Agent": "BTC-Adaptive-Telegram-Bot-V7-GitHub/1.0"
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


def now_ts():
    return time.time()


def iso(ts=None):
    return datetime.fromtimestamp(
        ts or now_ts(),
        tz=timezone.utc
    ).isoformat()


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, safe_float(x)))


def coinbase_get(path, params=None):
    r = session.get(
        REST + path,
        params=params,
        timeout=REQUEST_TIMEOUT
    )
    r.raise_for_status()
    return r.json()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            obj,
            f,
            indent=2,
            ensure_ascii=False,
            default=str
        )
    os.replace(tmp, path)


def load_state():
    s = load_json(STATE_FILE, {})

    if not isinstance(s, dict):
        s = {}

    defaults = {
        "last_signal": None,
        "last_signal_ts": 0,
        "last_direction": None,
        "last_scan_ts": 0,
        "flow_snapshot": {},
        "last_sweep": None,
        "continuation": None,
        "news_events": [],
        "active_trades": {},
    }

    for k, v in defaults.items():
        s.setdefault(k, v)

    return s


def save_state(s):
    save_json(STATE_FILE, s)


def journal(event, **kwargs):
    rec = {
        "ts": iso(),
        "event": event,
        **kwargs
    }

    try:
        with open(
            JOURNAL_FILE,
            "a",
            encoding="utf-8"
        ) as f:
            f.write(
                json.dumps(
                    rec,
                    ensure_ascii=False,
                    default=str
                ) + "\n"
            )
    except Exception as e:
        print("JOURNAL_ERROR", repr(e))


def csv_log(rec):
    try:
        os.makedirs("logs", exist_ok=True)

        exists = os.path.exists(CSV_LOG_FILE)

        fields = [
            "timestamp",
            "event",
            "signal_id",
            "direction",
            "score",
            "agreement",
            "entry",
            "sl",
            "tp",
            "rr",
            "news_status",
            "news_direction",
            "news_confirmation",
            "news_impact",
            "news_confidence",
            "news_relevance",
            "news_event_count",
            "continuation_state",
            "continuation_score",
            "pullback_depth",
            "breakout_level",
            "reversal_risk",
            "lifecycle",
            "target_version",
            "previous_target",
            "new_target",
            "reason",
        ]

        with open(
            CSV_LOG_FILE,
            "a",
            newline="",
            encoding="utf-8"
        ) as f:
            w = csv.DictWriter(
                f,
                fieldnames=fields
            )

            if (
                not exists
                or os.path.getsize(CSV_LOG_FILE) == 0
            ):
                w.writeheader()

            w.writerow({
                k: rec.get(k, "")
                for k in fields
            })

    except Exception as e:
        journal(
            "CSV_LOG_ERROR",
            error=repr(e)
        )


def telegram(text, reply_to_message_id=None):
    if not TOKEN or not CHAT_ID:
        print(
            "TELEGRAM_NOT_CONFIGURED\n" + text
        )
        return None

    url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }

    if reply_to_message_id:
        payload["reply_parameters"] = {
            "message_id": int(reply_to_message_id)
        }

    try:
        r = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

        return data.get(
            "result",
            {}
        ).get(
            "message_id"
        )

    except Exception as e:

        if reply_to_message_id:
            try:
                r = session.post(
                    url,
                    json={
                        "chat_id": CHAT_ID,
                        "text": text
                    },
                    timeout=REQUEST_TIMEOUT
                )

                r.raise_for_status()

                return r.json().get(
                    "result",
                    {}
                ).get(
                    "message_id"
                )

            except Exception:
                pass

        journal(
            "TELEGRAM_ERROR",
            error=repr(e)
        )

        print(
            "TELEGRAM_ERROR",
            repr(e)
        )

        return None


def candles(granularity, limit=300):
    data = coinbase_get(
        f"/products/{PRODUCT}/candles",
        {
            "granularity": int(granularity)
        }
    )

    rows = [
        {
            "ts": pd.to_datetime(
                int(x[0]),
                unit="s",
                utc=True
            ),
            "low": safe_float(x[1]),
            "high": safe_float(x[2]),
            "open": safe_float(x[3]),
            "close": safe_float(x[4]),
            "volume": safe_float(x[5]),
        }
        for x in data[:limit]
        if len(x) >= 6
    ]

    df = pd.DataFrame(rows)

    if df.empty:
        raise RuntimeError(
            "No candles returned"
        )

    return (
        df
        .sort_values("ts")
        .drop_duplicates("ts")
        .reset_index(drop=True)
    )


def candles_1h_history(total=HISTORY_1H_BARS):
    g = 3600
    per = 300
    end = int(time.time() // g * g)
    chunks = []

    for _ in range(math.ceil(total / per)):
        start = end - per * g

        data = coinbase_get(
            f"/products/{PRODUCT}/candles",
            {
                "granularity": g,
                "start": iso(start),
                "end": iso(end),
            }
        )

        for x in data:
            if len(x) >= 6:
                chunks.append({
                    "ts": pd.to_datetime(
                        int(x[0]),
                        unit="s",
                        utc=True
                    ),
                    "low": safe_float(x[1]),
                    "high": safe_float(x[2]),
                    "open": safe_float(x[3]),
                    "close": safe_float(x[4]),
                    "volume": safe_float(x[5]),
                })

        end = start - 1

    df = pd.DataFrame(chunks)

    if df.empty:
        raise RuntimeError(
            "No 1H history"
        )

    cutoff = pd.Timestamp(
        int(time.time() // 3600 * 3600),
        unit="s",
        tz="UTC"
    )

    return (
        df
        .sort_values("ts")
        .drop_duplicates("ts")
        .query("ts < @cutoff")
        .tail(total)
        .reset_index(drop=True)
    )


def resample_ohlcv(df, hours):
    x = (
        df.copy()
        .set_index("ts")
        .sort_index()
    )

    rule = f"{hours}h"

    out = (
        x.resample(
            rule,
            origin="epoch",
            label="left",
            closed="left"
        )
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
    )

    cnt = (
        x["close"]
        .resample(
            rule,
            origin="epoch",
            label="left",
            closed="left"
        )
        .count()
    )

    return (
        out[cnt >= hours]
        .dropna()
        .reset_index()
    )


def closed_live(df):
    if len(df) < 5:
        raise RuntimeError(
            "Insufficient candles"
        )

    d = df.copy()

    d["ts"] = pd.to_datetime(
        d["ts"],
        utc=True
    )

    step = int(
        (
            d.ts.iloc[-1]
            - d.ts.iloc[-2]
        ).total_seconds()
    )

    if (
        step > 0
        and time.time()
        < d.ts.iloc[-1].timestamp() + step
    ):
        return (
            d.iloc[:-1].copy(),
            d.iloc[-1].copy()
        )

    return (
        d.copy(),
        d.iloc[-1].copy()
    )


def frames():
    h1 = candles_1h_history()

    m15, m15_live = closed_live(
        candles(900, 300)
    )

    return {
        "15m": m15,
        "15m_live": m15_live,
        "1h": h1,
        "2h": resample_ohlcv(h1, 2),
        "4h": resample_ohlcv(h1, 4),
    }


def atr(df, n=14):
    p = df.close.shift(1)

    tr = pd.concat(
        [
            df.high - df.low,
            (df.high - p).abs(),
            (df.low - p).abs(),
        ],
        axis=1
    ).max(axis=1)

    return tr.rolling(n).mean()


def rsi(s, n=14):
    d = s.diff()

    up = d.clip(lower=0)
    dn = -d.clip(upper=0)

    au = up.ewm(
        alpha=1 / n,
        adjust=False
    ).mean()

    ad = dn.ewm(
        alpha=1 / n,
        adjust=False
    ).mean()

    rs = au / ad.replace(
        0,
        np.nan
    )

    return (
        100
        - 100 / (1 + rs)
    ).fillna(50)


def prepare(df):
    x = df.copy()

    x["atr"] = atr(x)
    x["rsi"] = rsi(x.close)

    for n in [9, 21, 50, 200]:
        x[f"ema{n}"] = x.close.ewm(
            span=n,
            adjust=False
        ).mean()

    x["vol_ma20"] = (
        x.volume.rolling(20).mean()
    )

    x["vol_ratio"] = (
        x.volume
        / x.vol_ma20.replace(
            0,
            np.nan
        )
    )

    x["ret1"] = x.close.pct_change()
    x["ret8"] = x.close.pct_change(8)

    x["vol_z"] = (
        (
            x.volume
            - x.volume.rolling(30).mean()
        )
        / x.volume.rolling(30).std()
    )

    x["ema21_prev"] = x.ema21.shift(3)

    x["hh20"] = (
        x.high.rolling(20).max()
    )

    x["ll20"] = (
        x.low.rolling(20).min()
    )

    x["hh8"] = (
        x.high.rolling(8).max()
    )

    x["ll8"] = (
        x.low.rolling(8).min()
    )

    return (
        x
        .dropna()
        .reset_index(drop=True)
    )


def trend_ev(df, direction):
    r = df.iloc[-1]

    bull = (
        r.close > r.ema21 > r.ema50 > r.ema200
        and r.ema21 > r.ema21_prev
    )

    bear = (
        r.close < r.ema21 < r.ema50 < r.ema200
        and r.ema21 < r.ema21_prev
    )

    if direction == "LONG":
        return (
            1.0 if bull
            else .5 if r.close > r.ema21
            else 0.0
        )

    return (
        1.0 if bear
        else .5 if r.close < r.ema21
        else 0.0
    )


def price_ev(df, direction):
    r = df.iloc[-1]

    dist = (
        (r.close - r.ema21)
        / max(r.atr, 1e-9)
    )

    if direction == "LONG":
        return (
            1.0
            if -.5 <= dist <= 1.5
            else .5
            if dist > 0
            else .25
        )

    return (
        1.0
        if -1.5 <= dist <= .5
        else .5
        if dist < 0
        else .25
    )


def momentum_ev(df, direction):
    r = df.iloc[-1]

    if direction == "LONG":
        return (
            1.0
            if 52 <= r.rsi <= 72 and r.ret8 > 0
            else .5
            if r.ret8 > 0
            else 0.0
        )

    return (
        1.0
        if 28 <= r.rsi <= 48 and r.ret8 < 0
        else .5
        if r.ret8 < 0
        else 0.0
    )


def volume_ev(df):
    vr = safe_float(
        df.iloc[-1].vol_ratio,
        1
    )

    return (
        1.0 if vr >= 1.25
        else .5 if vr >= .9
        else .25
    )
def structure_ev(df, direction):
    r = df.iloc[-1]

    prev = df.iloc[-2]

    if direction == "LONG":
        higher_low = r.low >= prev.low
        higher_close = r.close >= prev.close
        breakout = r.close > df.high.iloc[-8:-1].max()

        if breakout and higher_low:
            return 1.0

        if higher_close or higher_low:
            return 0.65

        return 0.25

    lower_high = r.high <= prev.high
    lower_close = r.close <= prev.close
    breakdown = r.close < df.low.iloc[-8:-1].min()

    if breakdown and lower_high:
        return 1.0

    if lower_close or lower_high:
        return 0.65

    return 0.25


def reversal_ev(df, direction):
    r = df.iloc[-1]

    if direction == "LONG":
        if r.rsi < 42 and r.close < r.ema21:
            return 0.0

        if r.rsi < 50 or r.close < r.ema21:
            return 0.45

        return 1.0

    if r.rsi > 58 and r.close > r.ema21:
        return 0.0

    if r.rsi > 50 or r.close > r.ema21:
        return 0.45

    return 1.0


def retest_ev(df, direction):
    if len(df) < 12:
        return 0.5

    r = df.iloc[-1]

    recent_high = df.high.iloc[-9:-1].max()
    recent_low = df.low.iloc[-9:-1].min()

    if direction == "LONG":
        if r.close >= recent_high:
            return 1.0

        if (
            r.low <= recent_high
            and r.close > recent_high * .998
        ):
            return 0.85

        if r.close > r.ema21:
            return 0.6

        return 0.25

    if r.close <= recent_low:
        return 1.0

    if (
        r.high >= recent_low
        and r.close < recent_low * 1.002
    ):
        return 0.85

    if r.close < r.ema21:
        return 0.6

    return 0.25


def orderflow_metrics():
    age = (
        now_ts() - flow.get("updated", 0)
    )

    bids = flow.get("bids", {})
    asks = flow.get("asks", {})

    bid_sizes = sorted(
        [
            safe_float(v)
            for v in bids.values()
        ],
        reverse=True
    )[:20]

    ask_sizes = sorted(
        [
            safe_float(v)
            for v in asks.values()
        ],
        reverse=True
    )[:20]

    bid_sum = sum(bid_sizes)
    ask_sum = sum(ask_sizes)

    total = bid_sum + ask_sum

    imbalance = (
        (bid_sum - ask_sum) / total
        if total > 0
        else 0
    )

    delta = safe_float(
        flow.get("delta")
    )

    trade_total = (
        abs(safe_float(flow.get("buy")))
        + abs(safe_float(flow.get("sell")))
    )

    delta_ratio = (
        delta / trade_total
        if trade_total > 0
        else 0
    )

    return {
        "age": age,
        "bid_sum": bid_sum,
        "ask_sum": ask_sum,
        "imbalance": imbalance,
        "delta": delta,
        "delta_ratio": delta_ratio,
        "large": safe_float(
            flow.get("large")
        ),
        "trade_count": int(
            flow.get("trade_count", 0)
        ),
    }


def orderflow_ev(direction):
    m = orderflow_metrics()

    if m["age"] > ORDERFLOW_STALE:
        return 0.5

    imbalance = m["imbalance"]
    delta = m["delta_ratio"]

    if direction == "LONG":
        value = (
            0.5
            + 0.30 * clamp(
                (imbalance + 1) / 2
            )
            + 0.20 * clamp(
                (delta + 1) / 2
            )
        )
    else:
        value = (
            0.5
            + 0.30 * clamp(
                (1 - imbalance) / 2
            )
            + 0.20 * clamp(
                (1 - delta) / 2
            )
        )

    return clamp(value)


def recent_trades():
    """
    Coinbase trade interpretation intentionally follows
    the existing bot logic:

    side == sell  -> aggressive BUY / positive delta
    side == buy   -> aggressive SELL / negative delta
    """

    try:
        data = coinbase_get(
            f"/products/{PRODUCT}/trades"
        )

        bids = {}
        asks = {}

        buy = 0.0
        sell = 0.0
        delta = 0.0
        large = 0.0

        count = 0

        for t in data[:FLOW_TRADE_LIMIT]:
            price = safe_float(
                t.get("price")
            )

            size = safe_float(
                t.get("size")
            )

            side = str(
                t.get("side", "")
            ).lower()

            if price <= 0 or size <= 0:
                continue

            count += 1

            if side == "sell":
                buy += size
                delta += size
            elif side == "buy":
                sell += size
                delta -= size

            if size >= FLOW_LARGE_TRADE_BTC:
                large += size

        return {
            "buy": buy,
            "sell": sell,
            "delta": delta,
            "large": large,
            "trade_count": count,
        }

    except Exception as e:
        journal(
            "TRADES_ERROR",
            error=repr(e)
        )

        return {
            "buy": 0.0,
            "sell": 0.0,
            "delta": 0.0,
            "large": 0.0,
            "trade_count": 0,
        }


def ticker_price():
    data = coinbase_get(
        f"/products/{PRODUCT}/ticker"
    )

    return safe_float(
        data.get("price")
    )


def orderbook():
    return coinbase_get(
        f"/products/{PRODUCT}/book",
        {
            "level": 2
        }
    )


def update_flow():
    global flow

    book = orderbook()

    bids = {}
    asks = {}

    for row in book.get("bids", []):
        if len(row) >= 3:
            bids[
                safe_float(row[0])
            ] = safe_float(row[2])

    for row in book.get("asks", []):
        if len(row) >= 3:
            asks[
                safe_float(row[0])
            ] = safe_float(row[2])

    trades = recent_trades()

    flow = {
        "bids": bids,
        "asks": asks,
        "buy": trades["buy"],
        "sell": trades["sell"],
        "delta": trades["delta"],
        "large": trades["large"],
        "trade_count": trades["trade_count"],
        "updated": now_ts(),
    }

    return orderflow_metrics()


def detect_sweep(df, direction):
    """
    Sweep is confirmation/gate, not an independent signal.

    We inspect the latest closed candle and the previous
    SWEEP_MAX_AGE closed candles, so a valid recent sweep
    can still confirm a setup for up to a few candles.
    """

    x = prepare(df)

    if len(x) < SWEEP_LOOKBACK + 5:
        return {
            "type": "NONE",
            "quality": 0.0,
            "age": None,
            "level": None,
            "reason": "insufficient_history",
        }

    candidates = []

    max_age = min(
        SWEEP_MAX_AGE,
        len(x) - SWEEP_LOOKBACK - 2
    )

    for age in range(max_age + 1):
        idx = len(x) - 1 - age
        r = x.iloc[idx]

        before = x.iloc[
            idx - SWEEP_LOOKBACK:idx
        ]

        if len(before) < SWEEP_LOOKBACK:
            continue

        atr_value = safe_float(
            r.atr
        )

        if atr_value <= 0:
            continue

        prior_high = before.high.max()
        prior_low = before.low.min()

        candle_range = max(
            r.high - r.low,
            1e-9
        )

        # ----------------------------------------------------
        # LONG liquidity sweep:
        # price takes previous low and reclaims it
        # ----------------------------------------------------
        low_penetration = (
            prior_low - r.low
        )

        long_pen = (
            low_penetration / atr_value
        )

        long_reclaim = (
            r.close - r.low
        ) / candle_range

        long_reclaim_ok = (
            r.close > prior_low
            and long_reclaim >= SWEEP_MIN_RECLAIM
        )

        long_pen_ok = (
            long_pen >= SWEEP_MIN_PENETRATION_ATR
        )

        # ----------------------------------------------------
        # SHORT liquidity sweep:
        # price takes previous high and rejects it
        # ----------------------------------------------------
        high_penetration = (
            r.high - prior_high
        )

        short_pen = (
            high_penetration / atr_value
        )

        short_reclaim = (
            r.high - r.close
        ) / candle_range

        short_reclaim_ok = (
            r.close < prior_high
            and short_reclaim >= SWEEP_MIN_RECLAIM
        )

        short_pen_ok = (
            short_pen >= SWEEP_MIN_PENETRATION_ATR
        )

        long_quality = clamp(
            0.50 * clamp(
                long_pen / max(
                    SWEEP_MIN_PENETRATION_ATR * 3,
                    1e-9
                )
            )
            + 0.50 * long_reclaim
        )

        short_quality = clamp(
            0.50 * clamp(
                short_pen / max(
                    SWEEP_MIN_PENETRATION_ATR * 3,
                    1e-9
                )
            )
            + 0.50 * short_reclaim
        )

        if (
            long_pen_ok
            and long_reclaim_ok
        ):
            candidates.append({
                "type": "LONG",
                "quality": long_quality,
                "age": age,
                "level": prior_low,
                "penetration_atr": long_pen,
                "reclaim": long_reclaim,
                "timestamp": str(r.ts),
            })

        if (
            short_pen_ok
            and short_reclaim_ok
        ):
            candidates.append({
                "type": "SHORT",
                "quality": short_quality,
                "age": age,
                "level": prior_high,
                "penetration_atr": short_pen,
                "reclaim": short_reclaim,
                "timestamp": str(r.ts),
            })

    if not candidates:
        return {
            "type": "NONE",
            "quality": 0.0,
            "age": None,
            "level": None,
            "reason": "no_recent_sweep",
        }

    candidates.sort(
        key=lambda z: (
            z["quality"],
            -z["age"]
        ),
        reverse=True
    )

    best = candidates[0]

    # If the best two candidates are opposite-direction
    # sweeps of nearly equal quality, don't force a direction.
    if len(candidates) >= 2:
        a = candidates[0]
        b = candidates[1]

        if (
            a["type"] != b["type"]
            and abs(
                a["quality"] - b["quality"]
            ) < 0.08
        ):
            return {
                "type": "NONE",
                "quality": 0.0,
                "age": None,
                "level": None,
                "reason": "double_sweep_direction_unclear",
            }

    return best


def sweep_confirmation(
    sweep,
    technical,
    direction
):
    if not sweep:
        return 0.0

    if sweep.get("type") != direction:
        return 0.0

    quality = clamp(
        sweep.get("quality")
    )

    age = sweep.get(
        "age"
    )

    if age is None:
        return 0.0

    age_factor = max(
        0.0,
        1.0 - age * 0.22
    )

    structure = clamp(
        technical.get(
            "structure",
            0
        )
    )

    momentum = clamp(
        technical.get(
            "momentum",
            0
        )
    )

    orderflow = clamp(
        technical.get(
            "orderflow",
            0
        )
    )

    reversal = clamp(
        technical.get(
            "reversal",
            0
        )
    )

    retest = clamp(
        technical.get(
            "retest",
            0
        )
    )

    volume = clamp(
        technical.get(
            "volume",
            0
        )
    )

    confirmation = (
        SWEEP_CONFIRM_WEIGHTS["sweep_quality"]
        * quality
        + SWEEP_CONFIRM_WEIGHTS["structure"]
        * structure
        + SWEEP_CONFIRM_WEIGHTS["momentum"]
        * momentum
        + SWEEP_CONFIRM_WEIGHTS["orderflow"]
        * orderflow
        + SWEEP_CONFIRM_WEIGHTS["reversal"]
        * reversal
        + SWEEP_CONFIRM_WEIGHTS["retest"]
        * retest
        + SWEEP_CONFIRM_WEIGHTS["volume"]
        * volume
    )

    confirmation *= age_factor

    return clamp(
        confirmation
    )


def technical_snapshot(frameset, direction):
    f15 = prepare(
        frameset["15m"]
    )

    f1h = prepare(
        frameset["1h"]
    )

    f2h = prepare(
        frameset["2h"]
    )

    f4h = prepare(
        frameset["4h"]
    )

    trend = np.mean([
        trend_ev(f15, direction),
        trend_ev(f1h, direction),
        trend_ev(f2h, direction),
        trend_ev(f4h, direction),
    ])

    structure = np.mean([
        structure_ev(f15, direction),
        structure_ev(f1h, direction),
    ])

    price = price_ev(
        f15,
        direction
    )

    volume = volume_ev(
        f15
    )

    momentum = np.mean([
        momentum_ev(f15, direction),
        momentum_ev(f1h, direction),
    ])

    retest = retest_ev(
        f15,
        direction
    )

    orderflow = orderflow_ev(
        direction
    )

    reversal = reversal_ev(
        f15,
        direction
    )

    return {
        "trend": clamp(trend),
        "structure": clamp(structure),
        "price": clamp(price),
        "volume": clamp(volume),
        "momentum": clamp(momentum),
        "retest": clamp(retest),
        "orderflow": clamp(orderflow),
        "reversal": clamp(reversal),
    }
def technical_score(snapshot):
    total = 0.0

    for key, weight in WEIGHTS.items():
        total += (
            clamp(snapshot.get(key, 0))
            * weight
        )

    return total


def direction_agreement(snapshot, direction):
    values = [
        snapshot.get("trend", 0),
        snapshot.get("structure", 0),
        snapshot.get("price", 0),
        snapshot.get("volume", 0),
        snapshot.get("momentum", 0),
        snapshot.get("retest", 0),
        snapshot.get("orderflow", 0),
        snapshot.get("reversal", 0),
    ]

    if direction == "LONG":
        supportive = [
            v for v in values
            if v >= 0.55
        ]
    else:
        supportive = [
            v for v in values
            if v >= 0.55
        ]

    if not values:
        return 0.0

    return len(supportive) / len(values)


# ============================================================
# NEWS ENGINE
# ============================================================

NEWS_KEYWORDS = {
    "ETF": {
        "long": [
            "bitcoin etf inflow",
            "spot bitcoin etf",
            "bitcoin etf approval",
            "etf inflows",
            "etf demand",
            "institutional buying",
        ],
        "short": [
            "bitcoin etf outflow",
            "etf outflows",
            "etf rejection",
            "etf redemption",
        ],
        "weight": 1.00,
    },

    "REGULATION": {
        "long": [
            "crypto regulation clarity",
            "bitcoin regulation",
            "crypto friendly regulation",
            "regulatory approval",
            "regulatory clarity",
        ],
        "short": [
            "crypto ban",
            "bitcoin ban",
            "regulatory crackdown",
            "sec lawsuit",
            "crypto crackdown",
        ],
        "weight": 0.90,
    },

    "MACRO": {
        "long": [
            "fed rate cut",
            "rate cut",
            "dovish fed",
            "lower inflation",
            "liquidity boost",
        ],
        "short": [
            "fed rate hike",
            "rate hike",
            "hawkish fed",
            "higher inflation",
            "liquidity tightening",
        ],
        "weight": 0.90,
    },

    "INSTITUTIONAL": {
        "long": [
            "institutional bitcoin purchase",
            "company buys bitcoin",
            "corporate bitcoin purchase",
            "institutional demand",
            "treasury bitcoin",
        ],
        "short": [
            "institution sells bitcoin",
            "company sells bitcoin",
            "bitcoin liquidation",
            "institutional selling",
        ],
        "weight": 0.90,
    },

    "EXCHANGE": {
        "long": [
            "bitcoin listing",
            "btc listing",
            "bitcoin adoption",
            "bitcoin trading support",
        ],
        "short": [
            "bitcoin delisting",
            "btc delisting",
            "exchange halt",
            "exchange outage",
        ],
        "weight": 0.75,
    },

    "SECURITY": {
        "long": [
            "security recovered",
            "funds recovered",
            "hack recovered",
        ],
        "short": [
            "bitcoin hack",
            "crypto hack",
            "exchange hack",
            "security breach",
            "exploit",
            "stolen bitcoin",
        ],
        "weight": 1.00,
    },

    "STABLECOIN": {
        "long": [
            "stablecoin inflows",
            "stablecoin liquidity",
            "usdt issuance",
            "usdc issuance",
        ],
        "short": [
            "stablecoin depeg",
            "stablecoin outflows",
            "usdt depeg",
            "usdc depeg",
        ],
        "weight": 0.75,
    },

    "MINING": {
        "long": [
            "bitcoin mining difficulty",
            "bitcoin hash rate",
            "miner accumulation",
        ],
        "short": [
            "miner selling",
            "miner capitulation",
            "hash rate collapse",
        ],
        "weight": 0.65,
    },

    "LEGAL": {
        "long": [
            "crypto lawsuit dismissed",
            "bitcoin lawsuit dismissed",
            "legal victory bitcoin",
        ],
        "short": [
            "bitcoin lawsuit",
            "crypto lawsuit",
            "criminal charges crypto",
            "bitcoin seizure",
        ],
        "weight": 0.80,
    },
}


def normalize_text(text):
    text = str(text or "").lower()

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def news_fingerprint(title, source=""):
    raw = (
        normalize_text(title)
        + "|"
        + normalize_text(source)
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]


def parse_news_timestamp(value):
    if not value:
        return None

    try:
        if isinstance(value, (int, float)):
            return float(value)

        s = str(value).replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(s)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.timestamp()

    except Exception:
        return None


def classify_news(headline, description=""):
    text = normalize_text(
        f"{headline} {description}"
    )

    scores = {
        "LONG": 0.0,
        "SHORT": 0.0,
    }

    categories = []

    for category, cfg in NEWS_KEYWORDS.items():

        long_hits = sum(
            1
            for k in cfg["long"]
            if k in text
        )

        short_hits = sum(
            1
            for k in cfg["short"]
            if k in text
        )

        if long_hits:
            scores["LONG"] += (
                long_hits
                * cfg["weight"]
            )
            categories.append(
                category
            )

        if short_hits:
            scores["SHORT"] += (
                short_hits
                * cfg["weight"]
            )
            categories.append(
                category
            )

    categories = list(
        dict.fromkeys(categories)
    )

    total = (
        scores["LONG"]
        + scores["SHORT"]
    )

    if total <= 0:
        return {
            "direction": "NEUTRAL",
            "impact": 0.0,
            "confidence": 0.0,
            "categories": [],
        }

    if abs(
        scores["LONG"]
        - scores["SHORT"]
    ) < 0.35:

        return {
            "direction": "NEUTRAL",
            "impact": clamp(
                total / 5
            ),
            "confidence": 0.35,
            "categories": categories,
        }

    direction = (
        "LONG"
        if scores["LONG"]
        > scores["SHORT"]
        else "SHORT"
    )

    dominant = max(
        scores["LONG"],
        scores["SHORT"]
    )

    opposing = min(
        scores["LONG"],
        scores["SHORT"]
    )

    confidence = clamp(
        (
            dominant - opposing
        )
        / max(
            dominant,
            1
        )
    )

    impact = clamp(
        dominant / 4
    )

    return {
        "direction": direction,
        "impact": impact,
        "confidence": confidence,
        "categories": categories,
    }


def news_source_quality(source):
    s = normalize_text(source)

    high_quality = [
        "reuters",
        "bloomberg",
        "coindesk",
        "cointelegraph",
        "the block",
        "financial times",
        "cnbc",
        "wsj",
    ]

    medium_quality = [
        "decrypt",
        "bitcoin magazine",
        "forbes",
        "marketwatch",
        "yahoo finance",
    ]

    if any(
        x in s
        for x in high_quality
    ):
        return 0.90

    if any(
        x in s
        for x in medium_quality
    ):
        return 0.75

    return 0.55


def news_relevance(headline, description=""):
    text = normalize_text(
        f"{headline} {description}"
    )

    direct = [
        "bitcoin",
        "btc",
        "crypto",
        "cryptocurrency",
        "digital asset",
        "bitcoin etf",
    ]

    hits = sum(
        1
        for x in direct
        if x in text
    )

    if hits >= 3:
        return 1.0

    if hits == 2:
        return 0.9

    if hits == 1:
        return 0.75

    return 0.45


def fetch_news(state):
    if not NEWS_ENABLED:
        return {
            "status": "DISABLED",
            "events": [],
        }

    params = {
        "currencies": "BTC",
        "kind": "news",
        "public": "true",
    }

    if NEWS_API_KEY:
        params["auth_token"] = NEWS_API_KEY

    try:
        response = session.get(
            NEWS_API_URL,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        payload = response.json()

        if isinstance(
            payload,
            dict
        ):
            items = (
                payload.get("results")
                or payload.get("data")
                or payload.get("posts")
                or []
            )
        elif isinstance(
            payload,
            list
        ):
            items = payload
        else:
            items = []

    except Exception as e:
        journal(
            "NEWS_FETCH_ERROR",
            error=repr(e)
        )

        return {
            "status": "UNAVAILABLE",
            "events": [],
            "error": repr(e),
        }

    seen = {
        x.get("fingerprint")
        for x in state.get(
            "news_events",
            []
        )
        if x.get("fingerprint")
    }

    events = []

    cutoff = (
        now_ts()
        - NEWS_LOOKBACK_MIN * 60
    )

    for item in items[:NEWS_MAX_ARTICLES]:

        if not isinstance(
            item,
            dict
        ):
            continue

        title = (
            item.get("title")
            or item.get("headline")
            or ""
        )

        description = (
            item.get("description")
            or item.get("body")
            or item.get("summary")
            or ""
        )

        source_data = item.get(
            "source"
        )

        if isinstance(
            source_data,
            dict
        ):
            source = (
                source_data.get("title")
                or source_data.get("name")
                or ""
            )
        else:
            source = (
                str(source_data)
                if source_data
                else ""
            )

        published = (
            item.get("published_at")
            or item.get("published")
            or item.get("created_at")
            or item.get("date")
        )

        ts = parse_news_timestamp(
            published
        )

        if ts is None:
            ts = now_ts()

        if ts < cutoff:
            continue

        if not title:
            continue

        fp = news_fingerprint(
            title,
            source
        )

        if fp in seen:
            continue

        classified = classify_news(
            title,
            description
        )

        relevance = news_relevance(
            title,
            description
        )

        if relevance < NEWS_MIN_RELEVANCE:
            continue

        confidence = (
            classified["confidence"]
        )

        if confidence < NEWS_MIN_CONFIDENCE:
            continue

        freshness = clamp(
            1
            - (
                max(
                    0,
                    now_ts() - ts
                )
                / (
                    NEWS_LOOKBACK_MIN
                    * 60
                )
            )
        )

        quality = news_source_quality(
            source
        )

        event = {
            "direction": classified[
                "direction"
            ],
            "impact": classified[
                "impact"
            ],
            "confidence": confidence,
            "freshness": freshness,
            "relevance": relevance,
            "source_quality": quality,
            "event_id": fp,
            "fingerprint": fp,
            "headline": title,
            "source": source,
            "published_at": iso(ts),
            "url": (
                item.get("url")
                or item.get("link")
                or ""
            ),
            "categories": classified[
                "categories"
            ],
        }

        events.append(event)

    # Save fingerprints so the same article does not
    # repeatedly count as a fresh independent event.
    history = state.get(
        "news_events",
        []
    )

    history.extend(events)

    cutoff_history = (
        now_ts() - 24 * 3600
    )

    state["news_events"] = [
        x
        for x in history
        if parse_news_timestamp(
            x.get("published_at")
        ) is not None
        and parse_news_timestamp(
            x.get("published_at")
        ) >= cutoff_history
    ][-100:]

    return {
        "status": "OK",
        "events": events,
    }


def news_alignment(news, direction):
    if not news:
        return {
            "status": "NEUTRAL",
            "confirmation": 0.0,
            "event_count": 0,
            "impact": 0.0,
            "confidence": 0.0,
            "headline": "",
        }

    if news.get("status") in (
        "DISABLED",
        "UNAVAILABLE"
    ):
        return {
            "status": news["status"],
            "confirmation": 0.0,
            "event_count": 0,
            "impact": 0.0,
            "confidence": 0.0,
            "headline": "",
        }

    events = news.get(
        "events",
        []
    )

    if not events:
        return {
            "status": "NEUTRAL",
            "confirmation": 0.0,
            "event_count": 0,
            "impact": 0.0,
            "confidence": 0.0,
            "headline": "",
        }

    aligned = []
    conflicts = []

    for e in events:
        strength = (
            e["impact"]
            * e["confidence"]
            * e["freshness"]
            * e["relevance"]
            * e["source_quality"]
        )

        if e["direction"] == direction:
            aligned.append(
                (strength, e)
            )

        elif e["direction"] != "NEUTRAL":
            conflicts.append(
                (strength, e)
            )

    aligned.sort(
        key=lambda x: x[0],
        reverse=True
    )

    conflicts.sort(
        key=lambda x: x[0],
        reverse=True
    )

    aligned_strength = (
        aligned[0][0]
        if aligned
        else 0.0
    )

    conflict_strength = (
        conflicts[0][0]
        if conflicts
        else 0.0
    )

    confirmation = clamp(
        aligned_strength
        - 0.55 * conflict_strength
    )

    if confirmation >= NEWS_CONFIRMATION_THRESHOLD:
        status = "SUPPORTIVE"

    elif conflict_strength >= (
        NEWS_CONFIRMATION_THRESHOLD
    ):
        status = "CONFLICT"

    else:
        status = "NEUTRAL"

    best = (
        aligned[0][1]
        if aligned
        else conflicts[0][1]
        if conflicts
        else None
    )

    return {
        "status": status,
        "confirmation": confirmation,
        "event_count": len(events),
        "impact": (
            best["impact"]
            if best
            else 0.0
        ),
        "confidence": (
            best["confidence"]
            if best
            else 0.0
        ),
        "headline": (
            best["headline"]
            if best
            else ""
        ),
    }


# ============================================================
# POST-IMPULSE CONTINUATION ENGINE
# ============================================================

def continuation_engine(
    df,
    direction,
    state=None,
    mutate=False
):
    x = prepare(df)

    if len(x) < 30:
        return {
            "state": "NONE",
            "score": 0.0,
            "direction": direction,
            "breakout_level": None,
            "impulse_high": None,
            "impulse_low": None,
            "pullback_depth": None,
            "reversal_risk": 1.0,
            "age": None,
        }

    r = x.iloc[-1]

    atr_value = safe_float(
        r.atr
    )

    if atr_value <= 0:
        return {
            "state": "NONE",
            "score": 0.0,
            "direction": direction,
            "breakout_level": None,
            "impulse_high": None,
            "impulse_low": None,
            "pullback_depth": None,
            "reversal_risk": 1.0,
            "age": None,
        }

    recent = x.iloc[
        -CONT_LOOKBACK:
    ]

    base_high = (
        recent.high.iloc[
            :-CONT_IMPULSE_BARS
        ].max()
    )

    base_low = (
        recent.low.iloc[
            :-CONT_IMPULSE_BARS
        ].min()
    )

    impulse_window = x.iloc[
        -CONT_IMPULSE_BARS:
    ]

    impulse_high = (
        impulse_window.high.max()
    )

    impulse_low = (
        impulse_window.low.min()
    )

    if direction == "LONG":
        impulse_move = (
            impulse_high
            - base_high
        ) / atr_value

        breakout_level = base_high

        if impulse_move < CONT_IMPULSE_ATR:
            return {
                "state": "NONE",
                "score": 0.0,
                "direction": direction,
                "breakout_level": breakout_level,
                "impulse_high": impulse_high,
                "impulse_low": impulse_low,
                "pullback_depth": None,
                "reversal_risk": 0.35,
                "age": 0,
            }

        pullback_depth = (
            impulse_high - r.close
        ) / max(
            impulse_high - breakout_level,
            atr_value
        )

        level_hold = (
            r.close >= breakout_level
        )

        structure_hold = (
            r.close >= r.ema21
            or r.low >= breakout_level
        )

        momentum_cooling = (
            48 <= r.rsi <= 68
        )

        reacceleration = (
            r.close > r.open
            and r.ret1 > 0
        )

    else:
        impulse_move = (
            base_low
            - impulse_low
        ) / atr_value

        breakout_level = base_low

        if impulse_move < CONT_IMPULSE_ATR:
            return {
                "state": "NONE",
                "score": 0.0,
                "direction": direction,
                "breakout_level": breakout_level,
                "impulse_high": impulse_high,
                "impulse_low": impulse_low,
                "pullback_depth": None,
                "reversal_risk": 0.35,
                "age": 0,
            }

        pullback_depth = (
            r.close - impulse_low
        ) / max(
            breakout_level - impulse_low,
            atr_value
        )

        level_hold = (
            r.close <= breakout_level
        )

        structure_hold = (
            r.close <= r.ema21
            or r.high <= breakout_level
        )

        momentum_cooling = (
            32 <= r.rsi <= 52
        )

        reacceleration = (
            r.close < r.open
            and r.ret1 < 0
        )

    if not (
        CONT_MIN_PULLBACK
        <= pullback_depth
        <= CONT_MAX_PULLBACK
    ):
        if (
            pullback_depth
            < CONT_MIN_PULLBACK
        ):
            state_name = "IMPULSE"

        elif (
            pullback_depth
            > CONT_MAX_PULLBACK
        ):
            state_name = "INVALIDATED"

        else:
            state_name = "NONE"

        reversal_risk = clamp(
            pullback_depth
            if pullback_depth > .65
            else .20
        )

        return {
            "state": state_name,
            "score": 0.0,
            "direction": direction,
            "breakout_level":# ============================================================
# SIGNAL / TRADE LIFECYCLE
# ============================================================

def make_signal_id(direction):
    return (
        f"{direction}-"
        f"{int(now_ts())}-"
        f"{hashlib.sha1(str(now_ts()).encode()).hexdigest()[:8]}"
    )


def calculate_levels(price, direction, atr_value):
    atr_value = max(
        safe_float(atr_value),
        price * 0.002
    )

    if direction == "LONG":
        stop = price - 1.25 * atr_value
        target = price + 2.25 * atr_value
    else:
        stop = price + 1.25 * atr_value
        target = price - 2.25 * atr_value

    risk = abs(price - stop)
    reward = abs(target - price)

    rr = (
        reward / risk
        if risk > 0
        else 0
    )

    return {
        "entry": price,
        "stop": stop,
        "target": target,
        "rr": rr,
        "atr": atr_value,
    }


def build_signal(
    frameset,
    state,
    news_data
):
    candidates = []

    for direction in [
        "LONG",
        "SHORT"
    ]:
        technical = technical_snapshot(
            frameset,
            direction
        )

        score = technical_score(
            technical
        )

        agreement = direction_agreement(
            technical,
            direction
        )

        sweep = detect_sweep(
            frameset["1h"],
            direction
        )

        sweep_conf = sweep_confirmation(
            sweep,
            technical,
            direction
        )

        continuation = continuation_engine(
            frameset["15m"],
            direction,
            state,
            mutate=False
        )

        # Continuation is an enhancement of the retest
        # component, not a brand-new weighted score.
        technical_for_score = dict(
            technical
        )

        if (
            continuation["state"]
            == "CONTINUATION_READY"
        ):
            technical_for_score["retest"] = max(
                technical_for_score["retest"],
                continuation["score"]
            )

        adjusted_score = technical_score(
            technical_for_score
        )

        news = news_alignment(
            news_data,
            direction
        )

        # News cannot create a signal.
        # It only provides a small confirmation boost
        # when technical conditions already pass.
        news_boost = 0.0

        if (
            adjusted_score >= MIN_SCORE
            and news["status"] == "SUPPORTIVE"
        ):
            news_boost = min(
                3.0,
                news["confirmation"] * 3
            )

        final_score = min(
            100.0,
            adjusted_score + news_boost
        )

        candidates.append({
            "direction": direction,
            "technical": technical_for_score,
            "raw_score": adjusted_score,
            "score": final_score,
            "agreement": agreement,
            "sweep": sweep,
            "sweep_confirmation": sweep_conf,
            "continuation": continuation,
            "news": news,
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    best = candidates[0]

    # A valid sweep is a confirmation gate.
    # If there is a recent sweep in the opposite direction,
    # don't force a signal.
    if (
        best["score"] < MIN_SCORE
        or best["agreement"] < MIN_AGREEMENT
    ):
        return None, candidates

    sweep = best["sweep"]

    if (
        sweep.get("type") != "NONE"
        and sweep.get("type")
        != best["direction"]
    ):
        return None, candidates

    # If a sweep exists for the direction, require
    # sufficient confirmation quality.
    if (
        sweep.get("type")
        == best["direction"]
        and best["sweep_confirmation"]
        < SWEEP_MIN_CONFIRMATION
    ):
        return None, candidates

    return best, candidates


def signal_text(signal):
    d = signal["direction"]
    score = signal["score"]
    agreement = signal["agreement"]

    levels = signal["levels"]

    news = signal["news"]

    continuation = signal[
        "continuation"
    ]

    sweep = signal[
        "sweep"
    ]

    label = (
        "🔥 STRONG"
        if score >= STRONG_SCORE
        and agreement >= STRONG_AGREEMENT
        else "📊 VALID"
    )

    text = (
        f"{label} BTC {d}\n"
        f"Score: {score:.1f}/100\n"
        f"Agreement: {agreement:.0%}\n\n"
        f"Entry: {levels['entry']:.2f}\n"
        f"Stop: {levels['stop']:.2f}\n"
        f"Target: {levels['target']:.2f}\n"
        f"RR: {levels['rr']:.2f}\n\n"
        f"Sweep: {sweep.get('type', 'NONE')}"
    )

    if sweep.get("type") != "NONE":
        text += (
            f" | age={sweep.get('age')}"
            f" | quality={sweep.get('quality', 0):.2f}"
        )

    text += (
        f"\nContinuation: "
        f"{continuation.get('state', 'NONE')}"
        f" ({continuation.get('score', 0):.2f})"
    )

    if news["status"] == "SUPPORTIVE":
        text += (
            "\n📰 News: SUPPORTIVE"
            f" ({news['confirmation']:.2f})"
        )

        if news.get("headline"):
            text += (
                f"\n{news['headline'][:180]}"
            )

    elif news["status"] == "CONFLICT":
        text += (
            "\n⚠️ News: CONFLICTING"
        )

    elif news["status"] == "UNAVAILABLE":
        text += (
            "\n📰 News: unavailable"
        )

    return text


def register_trade(
    state,
    signal,
    message_id=None
):
    signal_id = signal[
        "signal_id"
    ]

    trade = {
        "signal_id": signal_id,
        "direction": signal["direction"],
        "entry": signal["levels"]["entry"],
        "stop": signal["levels"]["stop"],
        "target": signal["levels"]["target"],
        "initial_target": signal["levels"]["target"],
        "target_version": 0,
        "status": "ACTIVE",
        "created_at": now_ts(),
        "last_update_at": now_ts(),
        "last_update_price": signal["levels"]["entry"],
        "telegram_message_id": message_id,
        "continuation": signal[
            "continuation"
        ],
        "reversal_risk": signal[
            "continuation"
        ].get(
            "reversal_risk",
            1.0
        ),
        "extension_count": 0,
    }

    state.setdefault(
        "active_trades",
        {}
    )[signal_id] = trade

    return trade


def extension_candidate(
    trade,
    frameset,
    news_data
):
    if trade.get(
        "status"
    ) not in (
        "ACTIVE",
        "TARGET_EXTENDED",
        "TARGET_EXTENDED_2",
    ):
        return None

    if trade.get(
        "target_version",
        0
    ) >= MAX_TARGET_EXTENSIONS:
        return None

    created = safe_float(
        trade.get("created_at")
    )

    if (
        now_ts() - created
        > MAX_TRADE_AGE_HOURS * 3600
    ):
        return None

    last_update = safe_float(
        trade.get("last_update_at")
    )

    if (
        now_ts() - last_update
        < EXTENSION_COOLDOWN_MIN * 60
    ):
        return None

    direction = trade[
        "direction"
    ]

    continuation = continuation_engine(
        frameset["15m"],
        direction,
        mutate=False
    )

    if (
        continuation["state"]
        != "CONTINUATION_READY"
    ):
        return None

    if (
        continuation["reversal_risk"]
        > 0.35
    ):
        return None

    news = news_alignment(
        news_data,
        direction
    )

    # Strong conflicting news can block an extension,
    # but news alone can never create one.
    if (
        news["status"] == "CONFLICT"
        and news["confirmation"]
        >= 0.70
    ):
        return None

    price = ticker_price()

    old_target = safe_float(
        trade["target"]
    )

    atr_value = safe_float(
        prepare(
            frameset["15m"]
        ).iloc[-1].atr
    )

    minimum_move = max(
        atr_value * 0.35,
        price * 0.0015
    )

    if direction == "LONG":
        if price < (
            old_target - minimum_move
        ):
            return None

        new_target = (
            old_target
            + 1.25 * atr_value
        )

        if new_target <= old_target:
            return None

    else:
        if price > (
            old_target + minimum_move
        ):
            return None

        new_target = (
            old_target
            - 1.25 * atr_value
        )

        if new_target >= old_target:
            return None

    return {
        "price": price,
        "old_target": old_target,
        "new_target": new_target,
        "continuation": continuation,
        "news": news,
    }


def manage_active_trades(
    state,
    frameset,
    news_data
):
    trades = state.get(
        "active_trades",
        {}
    )

    if not trades:
        return

    try:
        price = ticker_price()
    except Exception as e:
        journal(
            "TRADE_PRICE_ERROR",
            error=repr(e)
        )
        return

    changed = False

    for signal_id, trade in list(
        trades.items()
    ):
        direction = trade[
            "direction"
        ]

        stop = safe_float(
            trade["stop"]
        )

        target = safe_float(
            trade["target"]
        )

        expired = (
            now_ts()
            - safe_float(
                trade.get("created_at")
            )
            > MAX_TRADE_AGE_HOURS * 3600
        )

        hit_stop = (
            price <= stop
            if direction == "LONG"
            else price >= stop
        )

        hit_target = (
            price >= target
            if direction == "LONG"
            else price <= target
        )

        if hit_stop:
            trade["status"] = "SL_HIT"
            trade["closed_at"] = now_ts()
            trade["close_price"] = price
            trade["result"] = "LOSS"

            telegram(
                (
                    f"🛑 BTC {direction} setup "
                    f"STOP HIT\n"
                    f"Entry: {trade['entry']:.2f}\n"
                    f"Stop: {stop:.2f}\n"
                    f"Close: {price:.2f}\n"
                    f"Signal ID: {signal_id}"
                ),
                reply_to_message_id=trade.get(
                    "telegram_message_id"
                )
            )

            journal(
                "TRADE_CLOSED",
                signal_id=signal_id,
                result="LOSS",
                close_price=price,
            )

            csv_log({
                "event": "TRADE_CLOSED",
                "signal_id": signal_id,
                "direction": direction,
                "entry": trade["entry"],
                "sl": stop,
                "tp": target,
                "reason": "STOP",
            })

            del trades[
                signal_id
            ]

            changed = True
            continue

        if hit_target:
            trade["status"] = "TP_HIT"
            trade["closed_at"] = now_ts()
            trade["close_price"] = price
            trade["result"] = "WIN"

            telegram(
                (
                    f"🎯 BTC {direction} "
                    f"TARGET HIT\n"
                    f"Entry: {trade['entry']:.2f}\n"
                    f"Target: {target:.2f}\n"
                    f"Close: {price:.2f}\n"
                    f"Signal ID: {signal_id}"
                ),
                reply_to_message_id=trade.get(
                    "telegram_message_id"
                )
            )

            journal(
                "TRADE_CLOSED",
                signal_id=signal_id,
                result="WIN",
                close_price=price,
            )

            csv_log({
                "event": "TRADE_CLOSED",
                "signal_id": signal_id,
                "direction": direction,
                "entry": trade["entry"],
                "sl": stop,
                "tp": target,
                "reason": "TARGET",
            })

            del trades[
                signal_id
            ]

            changed = True
            continue

        if expired:
            trade["status"] = "EXPIRED"
            trade["closed_at"] = now_ts()
            trade["close_price"] = price
            trade["result"] = "EXPIRED"

            telegram(
                (
                    f"⌛ BTC {direction} setup "
                    f"EXPIRED\n"
                    f"Current price: {price:.2f}\n"
                    f"Signal ID: {signal_id}\n\n"
                    f"This setup is no longer active."
                ),
                reply_to_message_id=trade.get(
                    "telegram_message_id"
                )
            )

            journal(
                "TRADE_CLOSED",
                signal_id=signal_id,
                result="EXPIRED",
                close_price=price,
            )

            del trades[
                signal_id
            ]

            changed = True
            continue

        candidate = extension_candidate(
            trade,
            frameset,
            news_data
        )

        if candidate:
            old_target = candidate[
                "old_target"
            ]

            new_target = candidate[
                "new_target"
            ]

            version = (
                int(
                    trade.get(
                        "target_version",
                        0
                    )
                )
                + 1
            )

            trade["target"] = new_target
            trade[
                "target_version"
            ] = version

            trade[
                "status"
            ] = (
                "TARGET_EXTENDED"
                if version == 1
                else "TARGET_EXTENDED_2"
            )

            trade[
                "last_update_at"
            ] = now_ts()

            trade[
                "last_update_price"
            ] = price

            trade[
                "continuation"
            ] = candidate[
                "continuation"
            ]

            trade[
                "reversal_risk"
            ] = candidate[
                "continuation"
            ].get(
                "reversal_risk",
                1.0
            )

            update_text = (
                f"🔄 BTC {direction} "
                f"TARGET EXTENDED #{version}\n\n"
                f"Old target: {old_target:.2f}\n"
                f"New target: {new_target:.2f}\n"
                f"Current price: {price:.2f}\n"
                f"Continuation: READY\n"
                f"Reversal risk: "
                f"{trade['reversal_risk']:.2f}\n\n"
                f"⚠️ This is NOT a new signal.\n"
                f"It is an update to the existing "
                f"{signal_id} setup."
            )

            message_id = telegram(
                update_text,
                reply_to_message_id=trade.get(
                    "telegram_message_id"
                )
            )

            if message_id:
                trade[
                    "last_update_message_id"
                ] = message_id

            journal(
                "TARGET_EXTENDED",
                signal_id=signal_id,
                version=version,
                old_target=old_target,
                new_target=new_target,
                price=price,
            )

            csv_log({
                "event": "TARGET_EXTENDED",
                "signal_id": signal_id,
                "direction": direction,
                "entry": trade["entry"],
                "sl": trade["stop"],
                "tp": new_target,
                "previous_target": old_target,
                "new_target": new_target,
                "target_version": version,
                "lifecycle": trade["status"],
                "continuation_state": "CONTINUATION_READY",
            })

            changed = True

    if changed:
        state[
            "active_trades"
        ] = trades


def calibration_update(
    state,
    signal,
    outcome=None
):
    data = load_json(
        CALIBRATION_FILE,
        {
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "expired": 0,
            "score_buckets": {},
        }
    )

    if outcome is None:
        data["signals"] += 1

        bucket = str(
            int(
                signal["score"] // 5
            ) * 5
        )

        data[
            "score_buckets"
        ].setdefault(
            bucket,
            {
                "signals": 0,
                "wins": 0,
                "losses": 0,
            }
        )

        data[
            "score_buckets"
        ][bucket][
            "signals"
        ] += 1

    else:
        if outcome == "WIN":
            data["wins"] += 1
        elif outcome == "LOSS":
            data["losses"] += 1
        elif outcome == "EXPIRED":
            data["expired"] += 1

    save_json(
        CALIBRATION_FILE,
        data
    )


def run_once():
    state = load_state()

    state[
        "last_scan_ts"
    ] = now_ts()

    try:
        # --------------------------------------------
        # Market data
        # --------------------------------------------
        frameset = frames()

        # --------------------------------------------
        # Order flow
        # --------------------------------------------
        try:
            flow_metrics_now = update_flow()

            state[
                "flow_snapshot"
            ] = flow_metrics_now

        except Exception as e:
            journal(
                "FLOW_UPDATE_ERROR",
                error=repr(e)
            )

        # --------------------------------------------
        # News
        # --------------------------------------------
        news_data = fetch_news(
            state
        )

        # --------------------------------------------
        # Existing active trades
        # --------------------------------------------
        manage_active_trades(
            state,
            frameset,
            news_data
        )

        # --------------------------------------------
        # New signal
        # --------------------------------------------
        signal, candidates = build_signal(
            frameset,
            state,
            news_data
        )

        if signal is None:
            journal(
                "NO_SIGNAL",
                candidates=[
                    {
                        "direction": c["direction"],
                        "score": c["score"],
                        "agreement": c["agreement"],
                        "sweep": c["sweep"].get(
                            "type"
                        ),
                    }
                    for c in candidates
                ],
                news_status=news_data.get(
                    "status"
                ),
            )

            save_state(state)

            return {
                "status": "NO_SIGNAL"
            }

        direction = signal[
            "direction"
        ]

        # --------------------------------------------
        # Cooldown
        # Same direction blocked for COOLDOWN_MIN.
        # Opposite direction is allowed.
        # --------------------------------------------
        last_direction = state.get(
            "last_direction"
        )

        last_signal_ts = safe_float(
            state.get(
                "last_signal_ts"
            )
        )

        cooldown_active = (
            last_direction == direction
            and (
                now_ts()
                - last_signal_ts
                < COOLDOWN_MIN * 60
            )
        )

        if cooldown_active:
            journal(
                "SIGNAL_COOLDOWN",
                direction=direction,
   # ============================================================
# SIGNAL / TRADE LIFECYCLE
# ============================================================

def make_signal_id(direction):
    return (
        f"{direction}-"
        f"{int(now_ts())}-"
        f"{hashlib.sha1(str(now_ts()).encode()).hexdigest()[:8]}"
    )


def calculate_levels(price, direction, atr_value):
    atr_value = max(
        safe_float(atr_value),
        price * 0.002
    )

    if direction == "LONG":
        stop = price - 1.25 * atr_value
        target = price + 2.25 * atr_value
    else:
        stop = price + 1.25 * atr_value
        target = price - 2.25 * atr_value

    risk = abs(price - stop)
    reward = abs(target - price)

    rr = (
        reward / risk
        if risk > 0
        else 0
    )

    return {
        "entry": price,
        "stop": stop,
        "target": target,
        "rr": rr,
        "atr": atr_value,
    }


def build_signal(
    frameset,
    state,
    news_data
):
    candidates = []

    for direction in [
        "LONG",
        "SHORT"
    ]:
        technical = technical_snapshot(
            frameset,
            direction
        )

        score = technical_score(
            technical
        )

        agreement = direction_agreement(
            technical,
            direction
        )

        sweep = detect_sweep(
            frameset["1h"],
            direction
        )

        sweep_conf = sweep_confirmation(
            sweep,
            technical,
            direction
        )

        continuation = continuation_engine(
            frameset["15m"],
            direction,
            state,
            mutate=False
        )

        # Continuation is an enhancement of the retest
        # component, not a brand-new weighted score.
        technical_for_score = dict(
            technical
        )

        if (
            continuation["state"]
            == "CONTINUATION_READY"
        ):
            technical_for_score["retest"] = max(
                technical_for_score["retest"],
                continuation["score"]
            )

        adjusted_score = technical_score(
            technical_for_score
        )

        news = news_alignment(
            news_data,
            direction
        )

        # News cannot create a signal.
        # It only provides a small confirmation boost
        # when technical conditions already pass.
        news_boost = 0.0

        if (
            adjusted_score >= MIN_SCORE
            and news["status"] == "SUPPORTIVE"
        ):
            news_boost = min(
                3.0,
                news["confirmation"] * 3
            )

        final_score = min(
            100.0,
            adjusted_score + news_boost
        )

        candidates.append({
            "direction": direction,
            "technical": technical_for_score,
            "raw_score": adjusted_score,
            "score": final_score,
            "agreement": agreement,
            "sweep": sweep,
            "sweep_confirmation": sweep_conf,
            "continuation": continuation,
            "news": news,
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    best = candidates[0]

    # A valid sweep is a confirmation gate.
    # If there is a recent sweep in the opposite direction,
    # don't force a signal.
    if (
        best["score"] < MIN_SCORE
        or best["agreement"] < MIN_AGREEMENT
    ):
        return None, candidates

    sweep = best["sweep"]

    if (
        sweep.get("type") != "NONE"
        and sweep.get("type")
        != best["direction"]
    ):
        return None, candidates

    # If a sweep exists for the direction, require
    # sufficient confirmation quality.
    if (
        sweep.get("type")
        == best["direction"]
        and best["sweep_confirmation"]
        < SWEEP_MIN_CONFIRMATION
    ):
        return None, candidates

    return best, candidates


def signal_text(signal):
    d = signal["direction"]
    score = signal["score"]
    agreement = signal["agreement"]

    levels = signal["levels"]

    news = signal["news"]

    continuation = signal[
        "continuation"
    ]

    sweep = signal[
        "sweep"
    ]

    label = (
        "🔥 STRONG"
        if score >= STRONG_SCORE
        and agreement >= STRONG_AGREEMENT
        else "📊 VALID"
    )

    text = (
        f"{label} BTC {d}\n"
        f"Score: {score:.1f}/100\n"
        f"Agreement: {agreement:.0%}\n\n"
        f"Entry: {levels['entry']:.2f}\n"
        f"Stop: {levels['stop']:.2f}\n"
        f"Target: {levels['target']:.2f}\n"
        f"RR: {levels['rr']:.2f}\n\n"
        f"Sweep: {sweep.get('type', 'NONE')}"
    )

    if sweep.get("type") != "NONE":
        text += (
            f" | age={sweep.get('age')}"
            f" | quality={sweep.get('quality', 0):.2f}"
        )

    text += (
        f"\nContinuation: "
        f"{continuation.get('state', 'NONE')}"
        f" ({continuation.get('score', 0):.2f})"
    )

    if news["status"] == "SUPPORTIVE":
        text += (
            "\n📰 News: SUPPORTIVE"
            f" ({news['confirmation']:.2f})"
        )

        if news.get("headline"):
            text += (
                f"\n{news['headline'][:180]}"
            )

    elif news["status"] == "CONFLICT":
        text += (
            "\n⚠️ News: CONFLICTING"
        )

    elif news["status"] == "UNAVAILABLE":
        text += (
            "\n📰 News: unavailable"
        )

    return text


def register_trade(
    state,
    signal,
    message_id=None
):
    signal_id = signal[
        "signal_id"
    ]

    trade = {
        "signal_id": signal_id,
        "direction": signal["direction"],
        "entry": signal["levels"]["entry"],
        "stop": signal["levels"]["stop"],
        "target": signal["levels"]["target"],
        "initial_target": signal["levels"]["target"],
        "target_version": 0,
        "status": "ACTIVE",
        "created_at": now_ts(),
        "last_update_at": now_ts(),
        "last_update_price": signal["levels"]["entry"],
        "telegram_message_id": message_id,
        "continuation": signal[
            "continuation"
        ],
        "reversal_risk": signal[
            "continuation"
        ].get(
            "reversal_risk",
            1.0
        ),
        "extension_count": 0,
    }

    state.setdefault(
        "active_trades",
        {}
    )[signal_id] = trade

    return trade


def extension_candidate(
    trade,
    frameset,
    news_data
):
    if trade.get(
        "status"
    ) not in (
        "ACTIVE",
        "TARGET_EXTENDED",
        "TARGET_EXTENDED_2",
    ):
        return None

    if trade.get(
        "target_version",
        0
    ) >= MAX_TARGET_EXTENSIONS:
        return None

    created = safe_float(
        trade.get("created_at")
    )

    if (
        now_ts() - created
        > MAX_TRADE_AGE_HOURS * 3600
    ):
        return None

    last_update = safe_float(
        trade.get("last_update_at")
    )

    if (
        now_ts() - last_update
        < EXTENSION_COOLDOWN_MIN * 60
    ):
        return None

    direction = trade[
        "direction"
    ]

    continuation = continuation_engine(
        frameset["15m"],
        direction,
        mutate=False
    )

    if (
        continuation["state"]
        != "CONTINUATION_READY"
    ):
        return None

    if (
        continuation["reversal_risk"]
        > 0.35
    ):
        return None

    news = news_alignment(
        news_data,
        direction
    )

    # Strong conflicting news can block an extension,
    # but news alone can never create one.
    if (
        news["status"] == "CONFLICT"
        and news["confirmation"]
        >= 0.70
    ):
        return None

    price = ticker_price()

    old_target = safe_float(
        trade["target"]
    )

    atr_value = safe_float(
        prepare(
            frameset["15m"]
        ).iloc[-1].atr
    )

    minimum_move = max(
        atr_value * 0.35,
        price * 0.0015
    )

    if direction == "LONG":
        if price < (
            old_target - minimum_move
        ):
            return None

        new_target = (
            old_target
            + 1.25 * atr_value
        )

        if new_target <= old_target:
            return None

    else:
        if price > (
            old_target + minimum_move
        ):
            return None

        new_target = (
            old_target
            - 1.25 * atr_value
        )

        if new_target >= old_target:
            return None

    return {
        "price": price,
        "old_target": old_target,
        "new_target": new_target,
        "continuation": continuation,
        "news": news,
    }


def manage_active_trades(
    state,
    frameset,
    news_data
):
    trades = state.get(
        "active_trades",
        {}
    )

    if not trades:
        return

    try:
        price = ticker_price()
    except Exception as e:
        journal(
            "TRADE_PRICE_ERROR",
            error=repr(e)
        )
        return

    changed = False

    for signal_id, trade in list(
        trades.items()
    ):
        direction = trade[
            "direction"
        ]

        stop = safe_float(
            trade["stop"]
        )

        target = safe_float(
            trade["target"]
        )

        expired = (
            now_ts()
            - safe_float(
                trade.get("created_at")
            )
            > MAX_TRADE_AGE_HOURS * 3600
        )

        hit_stop = (
            price <= stop
            if direction == "LONG"
            else price >= stop
        )

        hit_target = (
            price >= target
            if direction == "LONG"
            else price <= target
        )

        if hit_stop:
            trade["status"] = "SL_HIT"
            trade["closed_at"] = now_ts()
            trade["close_price"] = price
            trade["result"] = "LOSS"

            telegram(
                (
                    f"🛑 BTC {direction} setup "
                    f"STOP HIT\n"
                    f"Entry: {trade['entry']:.2f}\n"
                    f"Stop: {stop:.2f}\n"
                    f"Close: {price:.2f}\n"
                    f"Signal ID: {signal_id}"
                ),
                reply_to_message_id=trade.get(
                    "telegram_message_id"
                )
            )

            journal(
                "TRADE_CLOSED",
                signal_id=signal_id,
                result="LOSS",
                close_price=price,
            )

            csv_log({
                "event": "TRADE_CLOSED",
                "signal_id": signal_id,
                "direction": direction,
                "entry": trade["entry"],
                "sl": stop,
                "tp": target,
                "reason": "STOP",
            })

            del trades[
                signal_id
            ]

            changed = True
            continue

        if hit_target:
            trade["status"] = "TP_HIT"
            trade["closed_at"] = now_ts()
            trade["close_price"] = price
            trade["result"] = "WIN"

            telegram(
                (
                    f"🎯 BTC {direction} "
                    f"TARGET HIT\n"
                    f"Entry: {trade['entry']:.2f}\n"
                    f"Target: {target:.2f}\n"
                    f"Close: {price:.2f}\n"
                    f"Signal ID: {signal_id}"
                ),
                reply_to_message_id=trade.get(
                    "telegram_message_id"
                )
            )

            journal(
                "TRADE_CLOSED",
                signal_id=signal_id,
                result="WIN",
                close_price=price,
            )

            csv_log({
                "event": "TRADE_CLOSED",
                "signal_id": signal_id,
                "direction": direction,
                "entry": trade["entry"],
                "sl": stop,
                "tp": target,
                "reason": "TARGET",
            })

            del trades[
                signal_id
            ]

            changed = True
            continue

        if expired:
            trade["status"] = "EXPIRED"
            trade["closed_at"] = now_ts()
            trade["close_price"] = price
            trade["result"] = "EXPIRED"

            telegram(
                (
                    f"⌛ BTC {direction} setup "
                    f"EXPIRED\n"
                    f"Current price: {price:.2f}\n"
                    f"Signal ID: {signal_id}\n\n"
                    f"This setup is no longer active."
                ),
                reply_to_message_id=trade.get(
                    "telegram_message_id"
                )
            )

            journal(
                "TRADE_CLOSED",
                signal_id=signal_id,
                result="EXPIRED",
                close_price=price,
            )

            del trades[
                signal_id
            ]

            changed = True
            continue

        candidate = extension_candidate(
            trade,
            frameset,
            news_data
        )

        if candidate:
            old_target = candidate[
                "old_target"
            ]

            new_target = candidate[
                "new_target"
            ]

            version = (
                int(
                    trade.get(
                        "target_version",
                        0
                    )
                )
                + 1
            )

            trade["target"] = new_target
            trade[
                "target_version"
            ] = version

            trade[
                "status"
            ] = (
                "TARGET_EXTENDED"
                if version == 1
                else "TARGET_EXTENDED_2"
            )

            trade[
                "last_update_at"
            ] = now_ts()

            trade[
                "last_update_price"
            ] = price

            trade[
                "continuation"
            ] = candidate[
                "continuation"
            ]

            trade[
                "reversal_risk"
            ] = candidate[
                "continuation"
            ].get(
                "reversal_risk",
                1.0
            )

            update_text = (
                f"🔄 BTC {direction} "
                f"TARGET EXTENDED #{version}\n\n"
                f"Old target: {old_target:.2f}\n"
                f"New target: {new_target:.2f}\n"
                f"Current price: {price:.2f}\n"
                f"Continuation: READY\n"
                f"Reversal risk: "
                f"{trade['reversal_risk']:.2f}\n\n"
                f"⚠️ This is NOT a new signal.\n"
                f"It is an update to the existing "
                f"{signal_id} setup."
            )

            message_id = telegram(
                update_text,
                reply_to_message_id=trade.get(
                    "telegram_message_id"
                )
            )

            if message_id:
                trade[
                    "last_update_message_id"
                ] = message_id

            journal(
                "TARGET_EXTENDED",
                signal_id=signal_id,
                version=version,
                old_target=old_target,
                new_target=new_target,
                price=price,
            )

            csv_log({
                "event": "TARGET_EXTENDED",
                "signal_id": signal_id,
                "direction": direction,
                "entry": trade["entry"],
                "sl": trade["stop"],
                "tp": new_target,
                "previous_target": old_target,
                "new_target": new_target,
                "target_version": version,
                "lifecycle": trade["status"],
                "continuation_state": "CONTINUATION_READY",
            })

            changed = True

    if changed:
        state[
            "active_trades"
        ] = trades


def calibration_update(
    state,
    signal,
    outcome=None
):
    data = load_json(
        CALIBRATION_FILE,
        {
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "expired": 0,
            "score_buckets": {},
        }
    )

    if outcome is None:
        data["signals"] += 1

        bucket = str(
            int(
                signal["score"] // 5
            ) * 5
        )

        data[
            "score_buckets"
        ].setdefault(
            bucket,
            {
                "signals": 0,
                "wins": 0,
                "losses": 0,
            }
        )

        data[
            "score_buckets"
        ][bucket][
            "signals"
        ] += 1

    else:
        if outcome == "WIN":
            data["wins"] += 1
        elif outcome == "LOSS":
            data["losses"] += 1
        elif outcome == "EXPIRED":
            data["expired"] += 1

    save_json(
        CALIBRATION_FILE,
        data
    )


def run_once():
    state = load_state()

    state[
        "last_scan_ts"
    ] = now_ts()

    try:
        # --------------------------------------------
        # Market data
        # --------------------------------------------
        frameset = frames()

        # --------------------------------------------
        # Order flow
        # --------------------------------------------
        try:
            flow_metrics_now = update_flow()

            state[
                "flow_snapshot"
            ] = flow_metrics_now

        except Exception as e:
            journal(
                "FLOW_UPDATE_ERROR",
                error=repr(e)
            )

        # --------------------------------------------
        # News
        # --------------------------------------------
        news_data = fetch_news(
            state
        )

        # --------------------------------------------
        # Existing active trades
        # --------------------------------------------
        manage_active_trades(
            state,
            frameset,
            news_data
        )

        # --------------------------------------------
        # New signal
        # --------------------------------------------
        signal, candidates = build_signal(
            frameset,
            state,
            news_data
        )

        if signal is None:
            journal(
                "NO_SIGNAL",
                candidates=[
                    {
                        "direction": c["direction"],
                        "score": c["score"],
                        "agreement": c["agreement"],
                        "sweep": c["sweep"].get(
                            "type"
                        ),
                    }
                    for c in candidates
                ],
                news_status=news_data.get(
                    "status"
                ),
            )

            save_state(state)

            return {
                "status": "NO_SIGNAL"
            }

        direction = signal[
            "direction"
        ]

        # --------------------------------------------
        # Cooldown
        # Same direction blocked for COOLDOWN_MIN.
        # Opposite direction is allowed.
        # --------------------------------------------
        last_direction = state.get(
            "last_direction"
        )

        last_signal_ts = safe_float(
            state.get(
                "last_signal_ts"
            )
        )

        cooldown_active = (
            last_direction == direction
            and (
                now_ts()
                - last_signal_ts
                < COOLDOWN_MIN * 60
            )
        )

        if cooldown_active:
            journal(
                "SIGNAL_COOLDOWN",
                direction=direction,
                score=signal["score"],
                remaining_seconds=(
                    COOLDOWN_MIN * 60
                    - (
                        now_ts()
                        - last_signal_ts
                    )
                ),
            )

            save_state(state)

            return {
                "status": "COOLDOWN"
            }

        # --------------------------------------------
        # Build entry / SL / target
        # --------------------------------------------
        prepared_15m = prepare(
            frameset["15m"]
        )

        price = ticker_price()

        atr_value = safe_float(
            prepared_15m.iloc[-1].atr
        )

        levels = calculate_levels(
            price,
            direction,
            atr_value
        )

        signal[
            "levels"
        ] = levels

        signal[
            "signal_id"
        ] = make_signal_id(
            direction
        )

        # --------------------------------------------
        # Persist continuation state
        # --------------------------------------------
        continuation_engine(
            frameset["15m"],
            direction,
            state,
            mutate=True
        )

        # --------------------------------------------
        # Telegram
        # --------------------------------------------
        text = signal_text(
            signal
        )

        message_id = telegram(
            text
        )

        # --------------------------------------------
        # Register active lifecycle
        # --------------------------------------------
        trade = register_trade(
            state,
            signal,
            message_id
        )

        # --------------------------------------------
        # State
        # --------------------------------------------
        state[
            "last_signal"
        ] = signal

        state[
            "last_signal_ts"
        ] = now_ts()

        state[
            "last_direction"
        ] = direction

        state[
            "last_sweep"
        ] = signal[
            "sweep"
        ]

        calibration_update(
            state,
            signal
        )

        # --------------------------------------------
        # Journal
        # --------------------------------------------
        journal(
            "SIGNAL_SENT",
            signal_id=signal["signal_id"],
            direction=direction,
            score=signal["score"],
            agreement=signal["agreement"],
            entry=levels["entry"],
            stop=levels["stop"],
            target=levels["target"],
            rr=levels["rr"],
            sweep=signal["sweep"],
            continuation=signal[
                "continuation"
            ],
            news=signal["news"],
            telegram_message_id=message_id,
        )

        # --------------------------------------------
        # CSV
        # --------------------------------------------
        csv_log({
            "event": "SIGNAL_SENT",
            "signal_id": signal["signal_id"],
            "direction": direction,
            "score": signal["score"],
            "agreement": signal["agreement"],
            "entry": levels["entry"],
            "sl": levels["stop"],
            "tp": levels["target"],
            "rr": levels["rr"],
            "news_status": signal[
                "news"
            ]["status"],
            "news_direction": (
                direction
                if signal["news"][
                    "status"
                ] == "SUPPORTIVE"
                else ""
            ),
            "news_confirmation": signal[
                "news"
            ]["confirmation"],
            "news_impact": signal[
                "news"
            ]["impact"],
            "news_confidence": signal[
                "news"
            ]["confidence"],
            "news_event_count": signal[
                "news"
            ]["event_count"],
            "continuation_state": signal[
                "continuation"
            ]["state"],
            "continuation_score": signal[
                "continuation"
            ]["score"],
            "pullback_depth": signal[
                "continuation"
            ].get(
                "pullback_depth"
            ),
            "breakout_level": signal[
                "continuation"
            ].get(
                "breakout_level"
            ),
            "reversal_risk": signal[
                "continuation"
            ].get(
                "reversal_risk"
            ),
            "lifecycle": trade[
                "status"
            ],
            "target_version": 0,
        })

        save_state(state)

        return {
            "status": "SIGNAL_SENT",
            "signal_id": signal[
                "signal_id"
            ],
            "direction": direction,
            "score": signal[
                "score"
            ],
        }

    except Exception as e:

        journal(
            "RUN_ERROR",
            error=repr(e),
            traceback=traceback.format_exc()
        )

        print(
            "RUN_ERROR",
            repr(e)
        )

        try:
            save_state(state)
        except Exception:
            pass

        return {
            "status": "ERROR",
            "error": repr(e)
        }


def status():
    state = load_state()

    print(
        json.dumps(
            state,
            indent=2,
            ensure_ascii=False,
            default=str
        )
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "BTC Adaptive Telegram Bot V7"
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan and exit"
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Show saved bot state"
    )

    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously"
    )

    args = parser.parse_args()

    if args.status:
        status()
        return

    if args.once:
        result = run_once()

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
                default=str
            )
        )

        return

    if args.loop:
        while True:
            try:
                result = run_once()

                print(
                    json.dumps(
                        result,
                        ensure_ascii=False,
                        default=str
                    )
                )

            except KeyboardInterrupt:
                print(
                    "Stopped."
                )
                break

            except Exception as e:
                print(
                    "LOOP_ERROR",
                    repr(e)
                )

            time.sleep(
                SCAN_SECONDS
            )

        return

    # Default behavior:
    # same as --once, which is convenient for GitHub Actions.
    result = run_once()

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
            default=str
        )
    )


if __name__ == "__main__":
    main()

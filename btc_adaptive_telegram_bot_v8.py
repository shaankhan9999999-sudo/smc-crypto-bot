import os
import sys
import time
import json
import math
import argparse
import traceback
import csv
import copy
import re
import hashlib

from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests
import pandas as pd
import numpy as np


# ============================================================
# BTC ADAPTIVE TELEGRAM BOT V8
# ============================================================
# Multi-Timeframe Market State
# Adaptive Statistical Learning
# Liquidity Sweep
# Order Flow
# News Confirmation
# Post-Impulse Continuation
# Trade Lifecycle
#
# IMPORTANT:
# No external AI / LLM decision tool is used in V8.
# Adaptive statistical learning is used instead.
# ============================================================


PRODUCT = "BTC-USD"
REST = "https://api.exchange.coinbase.com"

TOKEN = os.environ.get(
    "TELEGRAM_TOKEN",
    ""
)

CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    "7500472109"
)


# ============================================================
# V8 FILES
# ============================================================

STATE_FILE = "btc_v8_state.json"

JOURNAL_FILE = (
    "btc_v8_journal.jsonl"
)

CALIBRATION_FILE = (
    "btc_v8_calibration.json"
)

CSV_LOG_FILE = (
    "logs/btc_v8_signals.csv"
)


# ============================================================
# CORE SETTINGS
# ============================================================

SCAN_SECONDS = 60

COOLDOWN_MIN = 45

MIN_SCORE = 62

STRONG_SCORE = 76

MIN_AGREEMENT = 0.58

STRONG_AGREEMENT = 0.68

REQUEST_TIMEOUT = 20


# ============================================================
# MARKET HISTORY
# ============================================================

HISTORY_1H_BARS = 1200

HISTORY_5M_BARS = 500

FLOW_TRADE_LIMIT = 100

FLOW_LARGE_TRADE_BTC = 0.25

ORDERFLOW_STALE = 8


# ============================================================
# LIQUIDITY SWEEP
# ============================================================

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

SWEEP_REVERSAL_MIN_QUALITY = 0.45

SWEEP_REVERSAL_STRONG_CONFIRMATION = 0.60

SWEEP_REVERSAL_STRONG_TREND_SCORE = 76

SWEEP_REVERSAL_STRONG_TREND_EXTRA = 0.05

SWEEP_REVERSAL_MIN_15M_CONFIRMATION = 0.55

SWEEP_REVERSAL_STRONG_15M_CONFIRMATION = 0.68

NEW_CONTINUATION_ENTRY_SCORE = 0.70


# ============================================================
# SIGNAL WEIGHTS
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


# ============================================================
# NEWS
# ============================================================

NEWS_ENABLED = (
    os.environ.get(
        "NEWS_ENABLED",
        "1"
    ) != "0"
)

NEWS_API_KEY = os.environ.get(
    "CRYPTOPANIC_API_KEY",
    ""
)

NEWS_API_URL = os.environ.get(
    "NEWS_API_URL",
    "https://cryptopanic.com/api/developer/v2/posts/"
)

NEWS_LOOKBACK_MIN = int(
    os.environ.get(
        "NEWS_LOOKBACK_MIN",
        "30"
    )
)

NEWS_MAX_ARTICLES = int(
    os.environ.get(
        "NEWS_MAX_ARTICLES",
        "20"
    )
)

NEWS_MIN_RELEVANCE = float(
    os.environ.get(
        "NEWS_MIN_RELEVANCE",
        ".60"
    )
)

NEWS_MIN_CONFIDENCE = float(
    os.environ.get(
        "NEWS_MIN_CONFIDENCE",
        ".55"
    )
)

NEWS_CONFIRMATION_THRESHOLD = float(
    os.environ.get(
        "NEWS_CONFIRMATION_THRESHOLD",
        ".60"
    )
)


# ============================================================
# CONTINUATION
# ============================================================

CONT_LOOKBACK = 20

CONT_IMPULSE_BARS = 6

CONT_IMPULSE_ATR = 1.8

CONT_MIN_PULLBACK = 0.18

CONT_MAX_PULLBACK = 0.65

CONT_READY_SCORE = 0.62

MAX_TARGET_EXTENSIONS = 2

EXTENSION_COOLDOWN_MIN = 30

MAX_TRADE_AGE_HOURS = 36


# ============================================================
# ADAPTIVE STATISTICAL LEARNING
# ============================================================

STATE_HYSTERESIS_SCANS = 2

REGIME_MIN_CONFIDENCE = 0.55

ADAPTIVE_MIN_SAMPLES = 20

ADAPTIVE_STRONG_SAMPLES = 50

ADAPTIVE_DECAY_DAYS = 45.0

ADAPTIVE_MAX_BOOST = 8.0

ADAPTIVE_MAX_PENALTY = 10.0

ADAPTIVE_MIN_EXPECTANCY_EDGE = 0.10

ADAPTIVE_STRONG_NEGATIVE_EXPECTANCY = -0.20

EXECUTION_MIN_SCORE = 0.50

TRANSITION_BLOCK_CONFIDENCE = 0.68


# ============================================================
# MARKET REGIMES
# ============================================================

REGIME_NAMES = (
    "BULLISH_TREND",
    "BULLISH_PULLBACK",
    "BULLISH_EXHAUSTION",
    "BULLISH_REVERSAL",
    "BEARISH_REVERSAL",
    "BEARISH_TREND",
    "BEARISH_PULLBACK",
    "BEARISH_EXHAUSTION",
    "RANGE",
    "TRANSITION",
    "UNSTABLE",
)


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent":
        "BTC-Adaptive-Telegram-Bot-V8-GitHub/1.0"
    }
)


# ============================================================
# ORDER FLOW STATE
# ============================================================

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


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts():
    return time.time()


def utc_now():
    return datetime.now(
        timezone.utc
    )


def safe_float(
    value,
    default=0.0
):
    try:
        if value is None:
            return default

        value = float(value)

        if not math.isfinite(value):
            return default

        return value

    except Exception:
        return default


def clamp(
    value,
    low=0.0,
    high=1.0
):
    try:
        value = float(value)
    except Exception:
        return low

    return max(
        low,
        min(
            high,
            value
        )
    )


def ensure_parent_dir(
    path
):
    directory = os.path.dirname(
        path
    )

    if directory:
        os.makedirs(
            directory,
            exist_ok=True
        )


def journal(
    event,
    **payload
):
    try:
        ensure_parent_dir(
            JOURNAL_FILE
        )

        row = {
            "ts": utc_now().isoformat(),
            "event": event,
        }

        row.update(
            payload
        )

        with open(
            JOURNAL_FILE,
            "a",
            encoding="utf-8"
        ) as f:

            f.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    default=str
                )
                + "\n"
            )

    except Exception:
        pass


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "version": 8,

        "market_state":
            "UNSTABLE",

        "market_state_candidate":
            "UNSTABLE",

        "market_state_candidate_count":
            0,

        "market_state_changed_at":
            now_ts(),

        "last_signal":
            None,

        "last_signal_ts":
            0.0,

        "last_direction":
            None,

        "last_scan_ts":
            0.0,

        "active_trade":
            None,

        "completed_trades":
            0,

        "wins":
            0,

        "losses":
            0,

        "cooldown_until":
            0.0,

        "last_sweep":
            None,

        "last_news":
            None,

        "last_continuation":
            None,

        "target_extensions":
            0,

        "last_extension_ts":
            0.0,
    }


def load_state():

    default = default_state()

    try:

        if not os.path.exists(
            STATE_FILE
        ):
            return default

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(
            data,
            dict
        ):
            return default

        for key, value in default.items():

            if key not in data:
                data[key] = value

        return data

    except Exception as e:

        journal(
            "STATE_LOAD_ERROR",
            error=repr(e)
        )

        return default


def save_state(
    state
):

    try:

        ensure_parent_dir(
            STATE_FILE
        )

        tmp = (
            STATE_FILE +
            ".tmp"
        )

        with open(
            tmp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False,
                default=str
            )

        os.replace(
            tmp,
            STATE_FILE
        )

    except Exception as e:

        journal(
            "STATE_SAVE_ERROR",
            error=repr(e)
        )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text
):

    if not TOKEN:
        return False

    if not CHAT_ID:
        return False

    url = (
        "https://api.telegram.org/bot"
        + TOKEN
        + "/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": str(text),
        "disable_web_page_preview": True,
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return True

    except Exception as e:

        journal(
            "TELEGRAM_ERROR",
            error=repr(e)
        )

        return False


# ============================================================
# COINBASE REST
# ============================================================

def api_get(
    path,
    params=None
):

    url = (
        REST +
        path
    )

    response = session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# OHLCV
# ============================================================

def fetch_candles(
    granularity,
    limit
):

    data = api_get(
        "/products/"
        + PRODUCT
        + "/candles",
        {
            "granularity": granularity
        }
    )

    if not isinstance(
        data,
        list
    ):
        return pd.DataFrame()

    rows = []

    for item in data[:limit]:

        if not isinstance(
            item,
            list
        ):
            continue

        if len(item) < 6:
            continue

        rows.append(
            {
                "time":
                    safe_float(item[0]),

                "low":
                    safe_float(item[1]),

                "high":
                    safe_float(item[2]),

                "open":
                    safe_float(item[3]),

                "close":
                    safe_float(item[4]),

                "volume":
                    safe_float(item[5]),
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows
    )

    df = df.sort_values(
        "time"
    ).drop_duplicates(
        "time"
    ).reset_index(
        drop=True
    )

    return df


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def prepare(
    df
):

    if df is None or len(df) == 0:
        return pd.DataFrame()

    d = df.copy()

    for col in (
        "open",
        "high",
        "low",
        "close",
        "volume",
    ):
        d[col] = pd.to_numeric(
            d[col],
            errors="coerce"
        )

    d = d.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ).copy()

    if d.empty:
        return d

    d["ema21"] = (
        d["close"]
        .ewm(
            span=21,
            adjust=False
        )
        .mean()
    )

    d["ema50"] = (
        d["close"]
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    d["ema200"] = (
        d["close"]
        .ewm(
            span=200,
            adjust=False
        )
        .mean()
    )

    prev_close = (
        d["close"]
        .shift(1)
    )

    tr1 = (
        d["high"] -
        d["low"]
    )

    tr2 = (
        d["high"] -
        prev_close
    ).abs()

    tr3 = (
        d["low"] -
        prev_close
    ).abs()

    d["tr"] = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1
    ).max(
        axis=1
    )

    d["atr"] = (
        d["tr"]
        .rolling(
            14,
            min_periods=1
        )
        .mean()
    )

    delta = (
        d["close"]
        .diff()
    )

    gain = (
        delta.clip(
            lower=0
        )
        .rolling(
            14,
            min_periods=1
        )
        .mean()
    )

    loss = (
        (-delta.clip(
            upper=0
        ))
        .rolling(
            14,
            min_periods=1
        )
        .mean()
    )

    rs = (
        gain /
        loss.replace(
            0,
            np.nan
        )
    )

    d["rsi"] = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    d["rsi"] = (
        d["rsi"]
        .replace(
            [np.inf, -np.inf],
            np.nan
        )
        .fillna(50.0)
    )

    d["volume_ma"] = (
        d["volume"]
        .rolling(
            20,
            min_periods=1
        )
        .mean()
    )

    d["volume_ratio"] = (
        d["volume"] /
        d["volume_ma"]
        .replace(
            0,
            np.nan
        )
    )

    d["volume_ratio"] = (
        d["volume_ratio"]
        .replace(
            [np.inf, -np.inf],
            np.nan
        )
        .fillna(1.0)
    )

    d["ema21_prev"] = (
        d["ema21"]
        .shift(1)
        .fillna(
            d["ema21"]
        )
    )

    d["ret1"] = (
        d["close"]
        .pct_change(1)
        .fillna(0.0)
    )

    d["ret4"] = (
        d["close"]
        .pct_change(4)
        .fillna(0.0)
    )

    d["ret8"] = (
        d["close"]
        .pct_change(8)
        .fillna(0.0)
    )

    d["range"] = (
        d["high"] -
        d["low"]
    )

    d["body"] = (
        d["close"] -
        d["open"]
    )

    d["upper_wick"] = (
        d["high"] -
        d[["open", "close"]]
        .max(axis=1)
    )

    d["lower_wick"] = (
        d[["open", "close"]]
        .min(axis=1) -
        d["low"]
    )

    return d


# ============================================================
# TIMEFRAME DIRECTION
# ============================================================

def _tf_direction_score(
    df
):

    d = prepare(
        df
    )

    if d.empty:
        return {
            "bull": 0.0,
            "bear": 0.0,
            "impulse": 0.0,
            "rsi": 50.0,
            "close": 0.0,
            "atr": 0.0,
        }

    r = d.iloc[-1]

    av = max(
        safe_float(
            r.atr
        ),
        safe_float(
            r.close
        ) * .0001
    )

    bull = 0.0

    bear = 0.0

    bull += (
        1.0
        if r.close > r.ema21
        else 0.0
    )

    bull += (
        1.0
        if r.ema21 > r.ema50
        else 0.0
    )

    bull += (
        1.0
        if r.ema50 > r.ema200
        else 0.0
    )

    bull += (
        1.0
        if r.ema21 > r.ema21_prev
        else 0.0
    )

    bull += (
        1.0
        if r.ret8 > 0
        else 0.0
    )

    bear += (
        1.0
        if r.close < r.ema21
        else 0.0
    )

    bear += (
        1.0
        if r.ema21 < r.ema50
        else 0.0
    )

    bear += (
        1.0
        if r.ema50 < r.ema200
        else 0.0
    )

    bear += (
        1.0
        if r.ema21 < r.ema21_prev
        else 0.0
    )

    bear += (
        1.0
        if r.ret8 < 0
        else 0.0
    )

    impulse = 0.0

    if len(d) >= 5:

        impulse = (
            safe_float(
                r.close
            )
            -
            safe_float(
                d.close.iloc[-4]
            )
        ) / av

    return {
        "bull":
            bull / 5.0,

        "bear":
            bear / 5.0,

        "impulse":
            impulse,

        "rsi":
            safe_float(
                r.rsi,
                50.0
            ),

        "close":
            safe_float(
                r.close
            ),

        "atr":
            av,
    }


# ============================================================
# MARKET STATE ENGINE
# ============================================================

def market_state_engine(
    F,
    state
):

    tf = {
        key:
        _tf_direction_score(
            F[key]
        )

        for key in (
            "4h",
            "2h",
            "1h",
            "15m",
        )
    }

    macro = clamp(
        .60 * tf["4h"]["bull"]
        +
        .40 * tf["2h"]["bull"]
    )

    macro_bear = clamp(
        .60 * tf["4h"]["bear"]
        +
        .40 * tf["2h"]["bear"]
    )

    primary_bull = (
        tf["2h"]["bull"]
    )

    primary_bear = (
        tf["2h"]["bear"]
    )

    trans_bull = (
        tf["1h"]["bull"]
    )

    trans_bear = (
        tf["1h"]["bear"]
    )

    setup_bull = (
        tf["15m"]["bull"]
    )

    setup_bear = (
        tf["15m"]["bear"]
    )

    transition = clamp(
        .50 *
        max(
            trans_bull,
            trans_bear
        )
        +
        .30 *
        max(
            setup_bull,
            setup_bear
        )
        +
        .20 *
        abs(
            trans_bull -
            trans_bear
        )
    )

    bullish = clamp(
        .35 * macro
        +
        .25 * primary_bull
        +
        .25 * trans_bull
        +
        .15 * setup_bull
    )

    bearish = clamp(
        .35 * macro_bear
        +
        .25 * primary_bear
        +
        .25 * trans_bear
        +
        .15 * setup_bear
    )

    short_term_conflict = (
        abs(
            trans_bull -
            trans_bear
        ) < .20
        or
        abs(
            setup_bull -
            setup_bear
        ) < .20
    )

    macro_bull = (
        macro >= .62
        and
        macro >
        macro_bear + .10
    )

    macro_bearish = (
        macro_bear >= .62
        and
        macro_bear >
        macro + .10
    )

    ltf_bear = (
        trans_bear >= .62
        and
        setup_bear >= .55
    )

    ltf_bull = (
        trans_bull >= .62
        and
        setup_bull >= .55
    )

    exhaustion_bull = (
        tf["1h"]["rsi"] > 68
        and
        tf["1h"]["impulse"] < 0
    )

    exhaustion_bear = (
        tf["1h"]["rsi"] < 32
        and
        tf["1h"]["impulse"] > 0
    )

    if (
        short_term_conflict
        and
        max(
            bullish,
            bearish
        ) < .62
    ):

        regime = "UNSTABLE"

    elif (
        macro_bull
        and
        ltf_bear
        and
        transition >=
        TRANSITION_BLOCK_CONFIDENCE
    ):

        regime = "BEARISH_REVERSAL"

    elif (
        macro_bearish
        and
        ltf_bull
        and
        transition >=
        TRANSITION_BLOCK_CONFIDENCE
    ):

        regime = "BULLISH_REVERSAL"

    elif (
        macro_bull
        and
        ltf_bear
    ):

        regime = "BULLISH_PULLBACK"

    elif (
        macro_bearish
        and
        ltf_bull
    ):

        regime = "BEARISH_PULLBACK"

    elif (
        macro_bull
        and
        exhaustion_bull
    ):

        regime = "BULLISH_EXHAUSTION"

    elif (
        macro_bearish
        and
        exhaustion_bear
    ):

        regime = "BEARISH_EXHAUSTION"

    elif (
        bullish >= .65
        and
        bullish >
        bearish + .12
    ):

        regime = "BULLISH_TREND"

    elif (
        bearish >= .65
        and
        bearish >
        bullish + .12
    ):

        regime = "BEARISH_TREND"

    elif max(
        bullish,
        bearish
    ) < .58:

        regime = "RANGE"

    else:

        regime = "TRANSITION"

    confidence = clamp(
        max(
            bullish,
            bearish,
            transition
            if regime in (
                "TRANSITION",
                "BEARISH_REVERSAL",
                "BULLISH_REVERSAL",
            )
            else 0.0
        )
    )

    prev = state.get(
        "market_state",
        "UNSTABLE"
    )

    candidate = state.get(
        "market_state_candidate"
    )

    count = int(
        state.get(
            "market_state_candidate_count",
            0
        )
    )

    if candidate == regime:

        count += 1

    else:

        candidate = regime
        count = 1

    active = prev

    if prev not in REGIME_NAMES:

        prev = "UNSTABLE"
        active = prev

    if regime == prev:

        active = regime
        count = 0

    elif count >= STATE_HYSTERESIS_SCANS:

        active = regime
        count = 0

        state[
            "market_state_changed_at"
        ] = now_ts()

    state[
        "market_state"
    ] = active

    state[
        "market_state_candidate"
    ] = candidate

    state[
        "market_state_candidate_count"
    ] = count

    return {
        "regime":
            active,

        "candidate":
            candidate,

        "candidate_count":
            count,

        "confidence":
            confidence,

        "bull_score":
            bullish,

        "bear_score":
            bearish,

        "transition_score":
            transition,

        "timeframes":
            tf,

        "macro":
            (
                "BULLISH"
                if macro >= macro_bear
                else "BEARISH"
            ),

        "roles": {
            "4h":
                "macro_regime",

            "2h":
                "primary_trend",

            "1h":
                "transition_liquidity",

            "15m":
                "setup_confirmation",

            "5m":
                "entry_execution",
        },
    }


# ============================================================
# EXECUTION SCORE
# ============================================================

def execution_score(
    F,
    direction
):

    d5 = prepare(
        F["5m"]
    )

    if d5.empty:

        return {
            "score": 0.0,
            "agreement": 0.0,
            "parts": {},
            "price": 0.0,
            "atr": 0.0,
        }

    r5 = d5.iloc[-1]

    atr = max(
        safe_float(
            r5.atr
        ),
        safe_float(
            r5.close
        ) * .0001
    )

    price = safe_float(
        r5.close
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # Direction scores are calculated here from raw DataFrames.
    # This avoids the old F["1h"]["bull"] structure mismatch.
    # --------------------------------------------------------

    tf = {
        key:
        _tf_direction_score(
            F[key]
        )

        for key in (
            "4h",
            "2h",
            "1h",
            "15m",
        )
    }

    if direction == "LONG":

        trend = clamp(
            .35 * tf["1h"]["bull"]
            +
            .30 * tf["2h"]["bull"]
            +
            .20 * tf["4h"]["bull"]
            +
            .15 * tf["15m"]["bull"]
        )

    else:

        trend = clamp(
            .35 * tf["1h"]["bear"]
            +
            .30 * tf["2h"]["bear"]
            +
            .20 * tf["4h"]["bear"]
            +
            .15 * tf["15m"]["bear"]
        )

    structure = clamp(
        .50 *
        (
            1.0
            if (
                direction == "LONG"
                and
                price >
                safe_float(
                    r5.ema21
                )
            )
            or
            (
                direction == "SHORT"
                and
                price <
                safe_float(
                    r5.ema21
                )
            )
            else 0.0
        )
        +
        .50 *
        (
            1.0
            if (
                direction == "LONG"
                and
                safe_float(
                    r5.ema21
                )
                >
                safe_float(
                    r5.ema50
                )
            )
            or
            (
                direction == "SHORT"
                and
                safe_float(
                    r5.ema21
                )
                <
                safe_float(
                    r5.ema50
                )
            )
            else 0.0
        )
    )

    price_score = clamp(
        .50 *
        (
            1.0
            if (
                direction == "LONG"
                and
                price >
                safe_float(
                    r5.ema21
                )
            )
            or
            (
                direction == "SHORT"
                and
                price <
                safe_float(
                    r5.ema21
                )
            )
            else 0.0
        )
        +
        .50 *
        (
            1.0
            if (
                direction == "LONG"
                and
                price >
                safe_float(
                    r5.ema50
                )
            )
            or
            (
                direction == "SHORT"
                and
                price <
                safe_float(
                    r5.ema50
                )
            )
            else 0.0
        )
    )

    volume_score = clamp(
        safe_float(
            r5.volume_ratio,
            1.0
        ) / 1.5
    )

    momentum_score = clamp(
        .60 *
        (
            1.0
            if (
                direction == "LONG"
                and
                safe_float(
                    r5.ret8
                ) > 0
            )
            or
            (
                direction == "SHORT"
                and
                safe_float(
                    r5.ret8
                ) < 0
            )
            else 0.0
        )
        +
        .40 *
        (
            1.0
            if (
                direction == "LONG"
                and
                safe_float(
                    r5.rsi,
                    50.0
                ) >= 50
            )
            or
            (
                direction == "SHORT"
                and
                safe_float(
                    r5.rsi,
                    50.0
                ) <= 50
            )
            else 0.0
        )
    )

    retest_score = 0.50

    try:

        distance = abs(
            price -
            safe_float(
                r5.ema21
            )
        )

        retest_score = clamp(
            1.0 -
            (
                distance /
                max(
                    atr * 2.0,
                    price * .001
                )
            )
        )

    except Exception:

        retest_score = 0.50

    orderflow_score = 0.50

    try:

        buy = abs(
            safe_float(
                flow.get(
                    "buy"
                )
            )
        )

        sell = abs(
            safe_float(
                flow.get(
                    "sell"
                )
            )
        )

        total = buy + sell

        if total > 0:

            if direction == "LONG":

                orderflow_score = clamp(
                    buy / total
                )

            else:

                orderflow_score = clamp(
                    sell / total
                )

    except Exception:

        orderflow_score = 0.50

    reversal_score = 0.50

    try:

        rsi = safe_float(
            r5.rsi,
            50.0
        )

        if direction == "LONG":

            reversal_score = clamp(
                .50 +
                .50 *
                (
                    1.0
                    if rsi < 55
                    else 0.0
                )
            )

        else:

            reversal_score = clamp(
                .50 +
                .50 *
                (
                    1.0
                    if rsi > 45
                    else 0.0
                )
            )

    except Exception:

        reversal_score = 0.50

    parts = {
        "trend":
            trend,

        "structure":
            structure,

        "price":
            price_score,

        "volume":
            volume_score,

        "momentum":
            momentum_score,

        "retest":
            retest_score,

        "orderflow":
            orderflow_score,

        "reversal":
            reversal_score,
    }

    raw_score = 0.0

    for name, weight in WEIGHTS.items():

        raw_score += (
            safe_float(
                parts.get(
                    name,
                    0.50
                ),
                0.50
            )
            *
            float(weight)
        )

    raw_score = max(
        0.0,
        min(
            105.0,
            raw_score
        )
    )

    agreement = calculate_agreement(
        parts
    )

    return {
        "score":
            raw_score,

        "agreement":
            agreement,

        "parts":
            parts,

        "price":
            price,

        "atr":
            atr,
    }


# ============================================================
# NOTE:
# Part 2 continues directly after this function.
# ============================================================
# ============================================================
# PART 2 / 4
# ADAPTIVE STATISTICAL LEARNING
# ============================================================


# ============================================================
# CALIBRATION DEFAULTS
# ============================================================

def default_calibration():

    return {
        "version": 1,
        "updated_at": now_ts(),

        "total_closed": 0,
        "wins": 0,
        "losses": 0,

        "buckets": {},
        "regimes": {},
        "setups": {},
    }


# ============================================================
# LOAD CALIBRATION
# ============================================================

def load_calibration():

    default = default_calibration()

    try:

        if not os.path.exists(
            CALIBRATION_FILE
        ):
            return default

        with open(
            CALIBRATION_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(
            data,
            dict
        ):
            return default

        for key, value in default.items():

            if key not in data:
                data[key] = value

        return data

    except Exception as e:

        journal(
            "CALIBRATION_LOAD_ERROR",
            error=repr(e)
        )

        return default


# ============================================================
# SAVE CALIBRATION
# ============================================================

def save_calibration(
    calibration
):

    try:

        calibration[
            "updated_at"
        ] = now_ts()

        ensure_parent_dir(
            CALIBRATION_FILE
        )

        tmp = (
            CALIBRATION_FILE +
            ".tmp"
        )

        with open(
            tmp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                calibration,
                f,
                indent=2,
                ensure_ascii=False,
                default=str
            )

        os.replace(
            tmp,
            CALIBRATION_FILE
        )

    except Exception as e:

        journal(
            "CALIBRATION_SAVE_ERROR",
            error=repr(e)
        )


# ============================================================
# ADAPTIVE BUCKET
# ============================================================

def _adaptive_bucket(
    features
):

    regime = str(
        features.get(
            "regime",
            "UNSTABLE"
        )
    )

    direction = str(
        features.get(
            "direction",
            "UNKNOWN"
        )
    )

    setup = str(
        features.get(
            "setup",
            "UNKNOWN"
        )
    )

    score = safe_float(
        features.get(
            "score",
            0.0
        )
    )

    agreement = safe_float(
        features.get(
            "agreement",
            0.0
        )
    )

    score_band = int(
        min(
            100,
            max(
                0,
                int(
                    score /
                    10
                ) * 10
            )
        )
    )

    agreement_band = int(
        min(
            100,
            max(
                0,
                int(
                    agreement *
                    10
                ) * 10
            )
        )
    )

    return (
        regime
        + "|"
        + direction
        + "|"
        + setup
        + "|S"
        + str(score_band)
        + "|A"
        + str(agreement_band)
    )


# ============================================================
# DECAYED SAMPLE WEIGHT
# ============================================================

def decayed_weight(
    age_days
):

    age_days = max(
        0.0,
        safe_float(
            age_days
        )
    )

    return math.exp(
        -age_days /
        max(
            ADAPTIVE_DECAY_DAYS,
            1.0
        )
    )


# ============================================================
# EXPECTANCY
# ============================================================

def _bucket_expectancy(
    bucket
):

    if not isinstance(
        bucket,
        dict
    ):
        return 0.0

    weighted_r = safe_float(
        bucket.get(
            "weighted_r",
            0.0
        )
    )

    weight = safe_float(
        bucket.get(
            "weight",
            0.0
        )
    )

    if weight <= 0:
        return 0.0

    return (
        weighted_r /
        weight
    )


# ============================================================
# WIN RATE
# ============================================================

def _bucket_win_rate(
    bucket
):

    if not isinstance(
        bucket,
        dict
    ):
        return 0.50

    wins = safe_float(
        bucket.get(
            "wins",
            0
        )
    )

    losses = safe_float(
        bucket.get(
            "losses",
            0
        )
    )

    total = (
        wins +
        losses
    )

    if total <= 0:
        return 0.50

    return clamp(
        wins /
        total
    )


# ============================================================
# ENSURE BUCKET
# ============================================================

def _ensure_bucket(
    container,
    key
):

    if key not in container:

        container[key] = {
            "wins": 0,
            "losses": 0,
            "weight": 0.0,
            "weighted_r": 0.0,
            "last_update": now_ts(),
        }

    if not isinstance(
        container[key],
        dict
    ):

        container[key] = {
            "wins": 0,
            "losses": 0,
            "weight": 0.0,
            "weighted_r": 0.0,
            "last_update": now_ts(),
        }

    return container[key]


# ============================================================
# BUILD SIGNAL FEATURES
# ============================================================

def build_signal_features(
    state,
    market,
    score_data,
    direction,
    setup
):

    parts = (
        score_data.get(
            "parts",
            {}
        )
    )

    regime = str(
        market.get(
            "regime",
            state.get(
                "market_state",
                "UNSTABLE"
            )
        )
    )

    return {

        "direction":
            str(
                direction
            ),

        "setup":
            str(
                setup
            ),

        "regime":
            regime,

        "score":
            safe_float(
                score_data.get(
                    "score",
                    0.0
                )
            ),

        "agreement":
            safe_float(
                score_data.get(
                    "agreement",
                    0.0
                )
            ),

        "trend":
            safe_float(
                parts.get(
                    "trend",
                    0.0
                )
            ),

        "structure":
            safe_float(
                parts.get(
                    "structure",
                    0.0
                )
            ),

        "price":
            safe_float(
                parts.get(
                    "price",
                    0.0
                )
            ),

        "volume":
            safe_float(
                parts.get(
                    "volume",
                    0.0
                )
            ),

        "momentum":
            safe_float(
                parts.get(
                    "momentum",
                    0.0
                )
            ),

        "retest":
            safe_float(
                parts.get(
                    "retest",
                    0.0
                )
            ),

        "orderflow":
            safe_float(
                parts.get(
                    "orderflow",
                    0.0
                )
            ),

        "reversal":
            safe_float(
                parts.get(
                    "reversal",
                    0.0
                )
            ),

        "timestamp":
            now_ts(),
    }


# ============================================================
# ADAPTIVE ADJUSTMENT
# ============================================================

def adaptive_adjustment(
    calibration,
    features
):

    if not isinstance(
        calibration,
        dict
    ):
        return {
            "adjustment": 0.0,
            "expectancy": 0.0,
            "win_rate": 0.50,
            "samples": 0,
            "bucket": "",
        }

    bucket_key = (
        _adaptive_bucket(
            features
        )
    )

    bucket = (
        calibration
        .get(
            "buckets",
            {}
        )
        .get(
            bucket_key
        )
    )

    if not bucket:

        return {
            "adjustment": 0.0,
            "expectancy": 0.0,
            "win_rate": 0.50,
            "samples": 0,
            "bucket": bucket_key,
        }

    weight = safe_float(
        bucket.get(
            "weight",
            0.0
        )
    )

    wins = safe_float(
        bucket.get(
            "wins",
            0
        )
    )

    losses = safe_float(
        bucket.get(
            "losses",
            0
        )
    )

    samples = (
        wins +
        losses
    )

    expectancy = (
        _bucket_expectancy(
            bucket
        )
    )

    win_rate = (
        _bucket_win_rate(
            bucket
        )
    )

    if (
        samples <
        ADAPTIVE_MIN_SAMPLES
        or
        weight <= 0
    ):

        return {
            "adjustment": 0.0,
            "expectancy": expectancy,
            "win_rate": win_rate,
            "samples": int(samples),
            "bucket": bucket_key,
        }

    adjustment = 0.0

    if expectancy >= (
        ADAPTIVE_MIN_EXPECTANCY_EDGE
    ):

        strength = clamp(
            expectancy /
            0.50
        )

        adjustment = (
            ADAPTIVE_MAX_BOOST *
            strength
        )

    elif expectancy <= (
        ADAPTIVE_STRONG_NEGATIVE_EXPECTANCY
    ):

        strength = clamp(
            abs(expectancy) /
            0.50
        )

        adjustment = -(
            ADAPTIVE_MAX_PENALTY *
            strength
        )

    else:

        center = (
            win_rate -
            0.50
        )

        adjustment = (
            center *
            8.0
        )

    adjustment = max(
        -ADAPTIVE_MAX_PENALTY,
        min(
            ADAPTIVE_MAX_BOOST,
            adjustment
        )
    )

    return {

        "adjustment":
            adjustment,

        "expectancy":
            expectancy,

        "win_rate":
            win_rate,

        "samples":
            int(samples),

        "bucket":
            bucket_key,
    }


# ============================================================
# ADAPTIVE SIGNAL SCORE
# ============================================================

def adaptive_signal_score(
    base_score,
    calibration,
    features
):

    info = adaptive_adjustment(
        calibration,
        features
    )

    adjusted = (
        safe_float(
            base_score
        )
        +
        safe_float(
            info.get(
                "adjustment",
                0.0
            )
        )
    )

    adjusted = max(
        0.0,
        min(
            105.0,
            adjusted
        )
    )

    info[
        "base_score"
    ] = safe_float(
        base_score
    )

    info[
        "adjusted_score"
    ] = adjusted

    return info


# ============================================================
# RECORD CLOSED TRADE
# ============================================================

def learn_from_closed_trade(
    calibration,
    features,
    r_multiple,
    outcome=None
):

    if not isinstance(
        calibration,
        dict
    ):
        return

    if not isinstance(
        features,
        dict
    ):
        return

    r = safe_float(
        r_multiple,
        0.0
    )

    if outcome is None:

        outcome = (
            "WIN"
            if r > 0
            else "LOSS"
        )

    outcome = str(
        outcome
    ).upper()

    if outcome not in (
        "WIN",
        "LOSS"
    ):

        outcome = (
            "WIN"
            if r > 0
            else "LOSS"
        )

    calibration[
        "total_closed"
    ] = int(
        calibration.get(
            "total_closed",
            0
        )
    ) + 1

    if outcome == "WIN":

        calibration[
            "wins"
        ] = int(
            calibration.get(
                "wins",
                0
            )
        ) + 1

    else:

        calibration[
            "losses"
        ] = int(
            calibration.get(
                "losses",
                0
            )
        ) + 1

    bucket_key = (
        _adaptive_bucket(
            features
        )
    )

    buckets = calibration.setdefault(
        "buckets",
        {}
    )

    bucket = _ensure_bucket(
        buckets,
        bucket_key
    )

    timestamp = safe_float(
        features.get(
            "timestamp",
            now_ts()
        ),
        now_ts()
    )

    age_days = max(
        0.0,
        (
            now_ts() -
            timestamp
        ) /
        86400.0
    )

    weight = decayed_weight(
        age_days
    )

    bucket[
        "weight"
    ] = (
        safe_float(
            bucket.get(
                "weight",
                0.0
            )
        )
        +
        weight
    )

    bucket[
        "weighted_r"
    ] = (
        safe_float(
            bucket.get(
                "weighted_r",
                0.0
            )
        )
        +
        (
            r *
            weight
        )
    )

    if outcome == "WIN":

        bucket[
            "wins"
        ] = int(
            bucket.get(
                "wins",
                0
            )
        ) + 1

    else:

        bucket[
            "losses"
        ] = int(
            bucket.get(
                "losses",
                0
            )
        ) + 1

    bucket[
        "last_update"
    ] = now_ts()

    regime = str(
        features.get(
            "regime",
            "UNSTABLE"
        )
    )

    regime_bucket = (
        _ensure_bucket(
            calibration.setdefault(
                "regimes",
                {}
            ),
            regime
        )
    )

    regime_bucket[
        "weight"
    ] = (
        safe_float(
            regime_bucket.get(
                "weight",
                0.0
            )
        )
        +
        weight
    )

    regime_bucket[
        "weighted_r"
    ] = (
        safe_float(
            regime_bucket.get(
                "weighted_r",
                0.0
            )
        )
        +
        r *
        weight
    )

    if outcome == "WIN":

        regime_bucket[
            "wins"
        ] = int(
            regime_bucket.get(
                "wins",
                0
            )
        ) + 1

    else:

        regime_bucket[
            "losses"
        ] = int(
            regime_bucket.get(
                "losses",
                0
            )
        ) + 1

    setup = str(
        features.get(
            "setup",
            "UNKNOWN"
        )
    )

    setup_bucket = (
        _ensure_bucket(
            calibration.setdefault(
                "setups",
                {}
            ),
            setup
        )
    )

    setup_bucket[
        "weight"
    ] = (
        safe_float(
            setup_bucket.get(
                "weight",
                0.0
            )
        )
        +
        weight
    )

    setup_bucket[
        "weighted_r"
    ] = (
        safe_float(
            setup_bucket.get(
                "weighted_r",
                0.0
            )
        )
        +
        r *
        weight
    )

    if outcome == "WIN":

        setup_bucket[
            "wins"
        ] = int(
            setup_bucket.get(
                "wins",
                0
            )
        ) + 1

    else:

        setup_bucket[
            "losses"
        ] = int(
            setup_bucket.get(
                "losses",
                0
            )
        ) + 1

    journal(
        "ADAPTIVE_LEARNING_UPDATE",
        bucket=bucket_key,
        regime=regime,
        setup=setup,
        direction=features.get(
            "direction"
        ),
        r_multiple=r,
        outcome=outcome,
        weight=weight,
        expectancy=_bucket_expectancy(
            bucket
        ),
        win_rate=_bucket_win_rate(
            bucket
        ),
    )

    save_calibration(
        calibration
    )


# ============================================================
# SIGNAL FEATURE SERIALIZATION
# ============================================================

def feature_snapshot(
    features
):

    if not isinstance(
        features,
        dict
    ):
        return {}

    keys = (
        "direction",
        "setup",
        "regime",
        "score",
        "agreement",
        "trend",
        "structure",
        "price",
        "volume",
        "momentum",
        "retest",
        "orderflow",
        "reversal",
        "timestamp",
    )

    return {
        key:
        features.get(
            key
        )
        for key in keys
    }


# ============================================================
# CSV SIGNAL LOG
# ============================================================

def append_signal_csv(
    row
):

    try:

        ensure_parent_dir(
            CSV_LOG_FILE
        )

        fields = [
            "ts",
            "signal_id",
            "direction",
            "setup",
            "regime",
            "score",
            "agreement",
            "adaptive_adjustment",
            "adaptive_expectancy",
            "adaptive_win_rate",
            "adaptive_samples",
            "entry",
            "stop_loss",
            "target",
            "rr",
            "status",
        ]

        exists = os.path.exists(
            CSV_LOG_FILE
        )

        with open(
            CSV_LOG_FILE,
            "a",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=fields,
                extrasaction="ignore"
            )

            if not exists:
                writer.writeheader()

            writer.writerow(
                {
                    key:
                    row.get(
                        key,
                        ""
                    )
                    for key in fields
                }
            )

    except Exception as e:

        journal(
            "CSV_LOG_ERROR",
            error=repr(e)
        )


# ============================================================
# END OF PART 2
# ============================================================
# ============================================================
# PART 3 / 4
# SIGNAL AGREEMENT + NEWS + ORDER FLOW + SWEEP
# + CONTINUATION + TARGET / RISK HELPERS
# ============================================================


# ============================================================
# SIGNAL SCORING / AGREEMENT
# ============================================================

def calculate_agreement(
    parts
):

    vals = []

    if not isinstance(
        parts,
        dict
    ):
        return 0.0

    for value in parts.values():

        try:

            vals.append(
                clamp(
                    value
                )
            )

        except Exception:

            pass

    if not vals:
        return 0.0

    mean_value = float(
        np.mean(vals)
    )

    dispersion = float(
        np.mean(
            [
                abs(
                    x -
                    mean_value
                )
                for x in vals
            ]
        )
    )

    agreement = (
        1.0 -
        min(
            1.0,
            dispersion * 2.0
        )
    )

    return clamp(
        agreement
    )


# ============================================================
# NEWS SENTIMENT
# ============================================================

def news_sentiment_score(
    text
):

    text = str(
        text or ""
    ).lower()

    bullish_words = (
        "bullish",
        "surge",
        "rally",
        "breakout",
        "approval",
        "adoption",
        "inflow",
        "accumulate",
        "accumulation",
        "positive",
        "higher",
        "buying",
        "institutional",
        "etf inflow",
    )

    bearish_words = (
        "bearish",
        "crash",
        "dump",
        "selloff",
        "rejection",
        "outflow",
        "liquidation",
        "hack",
        "ban",
        "negative",
        "lower",
        "selling",
        "regulatory risk",
        "etf outflow",
    )

    bull = 0

    bear = 0

    for word in bullish_words:

        if word in text:
            bull += 1

    for word in bearish_words:

        if word in text:
            bear += 1

    total = (
        bull +
        bear
    )

    if total == 0:
        return 0.50

    return clamp(
        .50 +
        (
            (bull - bear) /
            max(
                total * 2.0,
                1.0
            )
        )
    )


# ============================================================
# FETCH NEWS
# ============================================================

def fetch_news():

    if not NEWS_ENABLED:

        return {
            "enabled": False,
            "articles": [],
            "score": .50,
            "confirmation": .50,
            "error": None,
        }

    try:

        headers = {}

        params = {
            "auth_token":
                NEWS_API_KEY,

            "currencies":
                "BTC",

            "kind":
                "news",
        }

        if NEWS_API_KEY:

            params[
                "auth_token"
            ] = NEWS_API_KEY

        response = session.get(
            NEWS_API_URL,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        results = []

        if isinstance(
            data,
            dict
        ):

            results = (
                data.get(
                    "results"
                )
                or
                data.get(
                    "data"
                )
                or
                []
            )

        if not isinstance(
            results,
            list
        ):

            results = []

        articles = []

        cutoff = (
            datetime.now(
                timezone.utc
            )
            -
            timedelta(
                minutes=
                NEWS_LOOKBACK_MIN
            )
        )

        for item in results:

            if not isinstance(
                item,
                dict
            ):
                continue

            title = str(
                item.get(
                    "title"
                )
                or
                item.get(
                    "headline"
                )
                or
                ""
            )

            published = (
                item.get(
                    "published_at"
                )
                or
                item.get(
                    "published"
                )
                or
                item.get(
                    "created_at"
                )
            )

            # ------------------------------------------------
            # Published-time filtering
            # ------------------------------------------------

            if published:

                try:

                    published_dt = (
                        datetime.fromisoformat(
                            str(
                                published
                            ).replace(
                                "Z",
                                "+00:00"
                            )
                        )
                    )

                    if (
                        published_dt <
                        cutoff
                    ):
                        continue

                except Exception:

                    pass

            text = (
                title
                +
                " "
                +
                str(
                    item.get(
                        "description"
                    )
                    or
                    ""
                )
            )

            score = (
                news_sentiment_score(
                    text
                )
            )

            articles.append(
                {
                    "title":
                        title[:300],

                    "published":
                        published,

                    "score":
                        score,
                }
            )

            if len(
                articles
            ) >= NEWS_MAX_ARTICLES:

                break

        if not articles:

            return {
                "enabled": True,
                "articles": [],
                "score": .50,
                "confirmation": .50,
                "error": None,
            }

        avg = float(
            np.mean(
                [
                    safe_float(
                        x.get(
                            "score",
                            .50
                        ),
                        .50
                    )
                    for x in articles
                ]
            )
        )

        confirmation = clamp(
            .50 +
            abs(
                avg -
                .50
            )
        )

        return {
            "enabled": True,
            "articles":
                articles[
                    :NEWS_MAX_ARTICLES
                ],

            "score":
                avg,

            "confirmation":
                confirmation,

            "error":
                None,
        }

    except Exception as e:

        journal(
            "NEWS_ERROR",
            error=repr(e)
        )

        return {
            "enabled": True,
            "articles": [],
            "score": .50,
            "confirmation": .0,
            "error": repr(e),
        }


# ============================================================
# ORDER FLOW
# ============================================================

def update_orderflow():

    global flow

    try:

        trades = api_get(
            "/products/"
            + PRODUCT
            + "/trades"
        )

        if not isinstance(
            trades,
            list
        ):
            return flow

        bids = {}

        asks = {}

        buy_volume = 0.0

        sell_volume = 0.0

        large_volume = 0.0

        delta = 0.0

        count = 0

        for trade in trades[
            :FLOW_TRADE_LIMIT
        ]:

            if not isinstance(
                trade,
                dict
            ):
                continue

            size = safe_float(
                trade.get(
                    "size"
                )
            )

            price = safe_float(
                trade.get(
                    "price"
                )
            )

            side = str(
                trade.get(
                    "side",
                    ""
                )
            ).lower()

            if size <= 0:
                continue

            count += 1

            if size >= (
                FLOW_LARGE_TRADE_BTC
            ):

                large_volume += size

            if side == "buy":

                buy_volume += size

                delta += size

                bids[
                    price
                ] = (
                    bids.get(
                        price,
                        0.0
                    )
                    +
                    size
                )

            elif side == "sell":

                sell_volume += size

                delta -= size

                asks[
                    price
                ] = (
                    asks.get(
                        price,
                        0.0
                    )
                    +
                    size
                )

        flow = {
            "bids":
                bids,

            "asks":
                asks,

            "delta":
                delta,

            "large":
                large_volume,

            "buy":
                buy_volume,

            "sell":
                sell_volume,

            "trade_count":
                count,

            "updated":
                now_ts(),
        }

        return flow

    except Exception as e:

        journal(
            "ORDERFLOW_ERROR",
            error=repr(e)
        )

        return flow


# ============================================================
# SWEEP DETECTION
# ============================================================

def detect_liquidity_sweep(
    df,
    direction
):

    d = prepare(
        df
    )

    if len(d) < (
        SWEEP_LOOKBACK + 2
    ):

        return {
            "confirmed": False,
            "direction":
                direction,
            "quality": 0.0,
            "confirmation": 0.0,
            "age": 999,
            "reclaim": 0.0,
            "penetration": 0.0,
            "reason":
                "insufficient_history",
        }

    r = d.iloc[-1]

    atr = max(
        safe_float(
            r.atr
        ),
        safe_float(
            r.close
        ) * .0001
    )

    recent = d.iloc[
        -(
            SWEEP_LOOKBACK + 1
        ):
        -1
    ]

    recent_low = safe_float(
        recent["low"].min()
    )

    recent_high = safe_float(
        recent["high"].max()
    )

    current_close = safe_float(
        r.close
    )

    current_low = safe_float(
        r.low
    )

    current_high = safe_float(
        r.high
    )

    volume_score = clamp(
        safe_float(
            r.volume_ratio,
            1.0
        ) /
        1.5
    )

    if direction == "LONG":

        penetration = (
            recent_low -
            current_low
        ) / atr

        reclaimed = (
            current_close -
            recent_low
        ) / atr

        wick = (
            current_close -
            current_low
        ) / max(
            safe_float(
                r.range
            ),
            atr * .1
        )

        sweep_exists = (
            current_low <
            recent_low
            and
            reclaimed >=
            SWEEP_MIN_RECLAIM
            and
            penetration >=
            SWEEP_MIN_PENETRATION_ATR
        )

    else:

        penetration = (
            current_high -
            recent_high
        ) / atr

        reclaimed = (
            recent_high -
            current_close
        ) / atr

        wick = (
            current_high -
            current_close
        ) / max(
            safe_float(
                r.range
            ),
            atr * .1
        )

        sweep_exists = (
            current_high >
            recent_high
            and
            reclaimed >=
            SWEEP_MIN_RECLAIM
            and
            penetration >=
            SWEEP_MIN_PENETRATION_ATR
        )

    quality = clamp(
        .40 *
        clamp(
            penetration /
            .50
        )
        +
        .30 *
        clamp(
            reclaimed /
            1.0
        )
        +
        .20 *
        clamp(
            wick
        )
        +
        .10 *
        volume_score
    )

    age = 0

    if sweep_exists:

        confirmation = clamp(
            .40 * quality
            +
            .20 * volume_score
            +
            .20 * clamp(
                abs(
                    safe_float(
                        flow.get(
                            "delta"
                        )
                    )
                ) /
                max(
                    safe_float(
                        flow.get(
                            "buy"
                        )
                    )
                    +
                    safe_float(
                        flow.get(
                            "sell"
                        )
                    ),
                    1e-9
                )
            )
            +
            .20 * clamp(
                reclaimed
            )
        )

        return {
            "confirmed":
                confirmation >=
                SWEEP_MIN_CONFIRMATION,

            "direction":
                direction,

            "quality":
                quality,

            "confirmation":
                confirmation,

            "age":
                age,

            "reclaim":
                clamp(
                    reclaimed
                ),

            "penetration":
                penetration,

            "reason":
                "confirmed"
                if
                confirmation >=
                SWEEP_MIN_CONFIRMATION
                else
                "sweep_not_confirmed",
        }

    return {
        "confirmed": False,

        "direction":
            direction,

        "quality":
            quality,

        "confirmation":
            0.0,

        "age":
            age,

        "reclaim":
            clamp(
                reclaimed
            ),

        "penetration":
            penetration,

        "reason":
            "no_sweep",
    }


# ============================================================
# SWEEP CONFIRMATION
# ============================================================

def sweep_confirmation_score(
    sweep,
    execution,
    market,
    direction
):

    if not isinstance(
        sweep,
        dict
    ):
        return 0.0

    parts = (
        execution.get(
            "parts",
            {}
        )
    )

    sweep_quality = clamp(
        safe_float(
            sweep.get(
                "quality",
                0.0
            )
        )
    )

    structure = clamp(
        safe_float(
            parts.get(
                "structure",
                0.50
            )
        )
    )

    momentum = clamp(
        safe_float(
            parts.get(
                "momentum",
                0.50
            )
        )
    )

    orderflow = clamp(
        safe_float(
            parts.get(
                "orderflow",
                0.50
            )
        )
    )

    reversal = clamp(
        safe_float(
            parts.get(
                "reversal",
                0.50
            )
        )
    )

    retest = clamp(
        safe_float(
            parts.get(
                "retest",
                0.50
            )
        )
    )

    volume = clamp(
        safe_float(
            parts.get(
                "volume",
                0.50
            )
        )
    )

    confirmation = (
        SWEEP_CONFIRM_WEIGHTS[
            "sweep_quality"
        ] * sweep_quality
        +
        SWEEP_CONFIRM_WEIGHTS[
            "structure"
        ] * structure
        +
        SWEEP_CONFIRM_WEIGHTS[
            "momentum"
        ] * momentum
        +
        SWEEP_CONFIRM_WEIGHTS[
            "orderflow"
        ] * orderflow
        +
        SWEEP_CONFIRM_WEIGHTS[
            "reversal"
        ] * reversal
        +
        SWEEP_CONFIRM_WEIGHTS[
            "retest"
        ] * retest
        +
        SWEEP_CONFIRM_WEIGHTS[
            "volume"
        ] * volume
    )

    return clamp(
        confirmation
    )


# ============================================================
# CONTINUATION DETECTION
# ============================================================

def detect_continuation(
    df,
    direction
):

    d = prepare(
        df
    )

    if len(d) < (
        CONT_LOOKBACK + 2
    ):

        return {
            "ready": False,
            "direction":
                direction,
            "score": 0.0,
            "impulse":
                0.0,
            "pullback":
                0.0,
            "reason":
                "insufficient_history",
        }

    r = d.iloc[-1]

    atr = max(
        safe_float(
            r.atr
        ),
        safe_float(
            r.close
        ) * .0001
    )

    start_index = max(
        0,
        len(d) -
        CONT_IMPULSE_BARS -
        1
    )

    start_price = safe_float(
        d.close.iloc[
            start_index
        ]
    )

    current_price = safe_float(
        r.close
    )

    if direction == "LONG":

        impulse = (
            current_price -
            start_price
        ) / atr

        recent_high = safe_float(
            d.high.iloc[
                -CONT_LOOKBACK:
            ].max()
        )

        pullback = (
            recent_high -
            current_price
        ) / max(
            abs(
                recent_high -
                start_price
            ),
            atr
        )

        trend_ok = (
            current_price >
            safe_float(
                r.ema21
            )
        )

        momentum_ok = (
            safe_float(
                r.ret4
            ) > 0
        )

    else:

        impulse = (
            start_price -
            current_price
        ) / atr

        recent_low = safe_float(
            d.low.iloc[
                -CONT_LOOKBACK:
            ].min()
        )

        pullback = (
            current_price -
            recent_low
        ) / max(
            abs(
                start_price -
                recent_low
            ),
            atr
        )

        trend_ok = (
            current_price <
            safe_float(
                r.ema21
            )
        )

        momentum_ok = (
            safe_float(
                r.ret4
            ) < 0
        )

    impulse_score = clamp(
        impulse /
        CONT_IMPULSE_ATR
    )

    pullback_score = (
        1.0
        if (
            CONT_MIN_PULLBACK
            <=
            pullback
            <=
            CONT_MAX_PULLBACK
        )
        else
        0.0
    )

    score = clamp(
        .45 *
        impulse_score
        +
        .30 *
        pullback_score
        +
        .15 *
        (
            1.0
            if trend_ok
            else 0.0
        )
        +
        .10 *
        (
            1.0
            if momentum_ok
            else 0.0
        )
    )

    ready = (
        score >=
        CONT_READY_SCORE
        and
        abs(impulse) >=
        CONT_IMPULSE_ATR
        and
 pullback >=
        CONT_MIN_PULLBACK
        and
        pullback <=
        CONT_MAX_PULLBACK
    )

    return {
        "ready":
            ready,

        "direction":
            direction,

        "score":
            score,

        "impulse":
            impulse,

        "pullback":
            pullback,

        "reason":
            "ready"
            if ready
            else
            "continuation_not_ready",
    }


# ============================================================
# ENTRY / TARGET HELPERS
# ============================================================

def calculate_trade_levels(
    price,
    atr,
    direction,
    score
):

    price = safe_float(
        price
    )

    atr = max(
        safe_float(
            atr
        ),
        price * .0005
    )

    score_factor = clamp(
        (
            safe_float(
                score,
                MIN_SCORE
            )
            -
            MIN_SCORE
        )
        /
        max(
            STRONG_SCORE -
            MIN_SCORE,
            1.0
        )
    )

    stop_distance = (
        atr *
        (
            1.20 -
            .20 *
            score_factor
        )
    )

    target_distance = (
        atr *
        (
            2.00 +
            .75 *
            score_factor
        )
    )

    if direction == "LONG":

        stop_loss = (
            price -
            stop_distance
        )

        target = (
            price +
            target_distance
        )

    else:

        stop_loss = (
            price +
            stop_distance
        )

        target = (
            price -
            target_distance
        )

    rr = (
        target_distance /
        max(
            stop_distance,
            1e-9
        )
    )

    return {
        "entry":
            price,

        "stop_loss":
            stop_loss,

        "target":
            target,

        "rr":
            rr,

        "stop_distance":
            stop_distance,

        "target_distance":
            target_distance,
    }


# ============================================================
# TARGET EXTENSION
# ============================================================

def calculate_extended_target(
    trade,
    current_price,
    atr
):

    if not isinstance(
        trade,
        dict
    ):
        return None

    direction = str(
        trade.get(
            "direction",
            ""
        )
    ).upper()

    current_price = safe_float(
        current_price
    )

    atr = max(
        safe_float(
            atr
        ),
        current_price * .0005
    )

    old_target = safe_float(
        trade.get(
            "target",
            current_price
        )
    )

    if direction == "LONG":

        new_target = (
            old_target +
            atr * 1.25
        )

    elif direction == "SHORT":

        new_target = (
            old_target -
            atr * 1.25
        )

    else:

        return None

    return new_target


# ============================================================
# SIGNAL ID
# ============================================================

def make_signal_id(
    direction,
    price,
    timestamp
):

    raw = (
        str(
            direction
        )
        + "|"
        +
        str(
            round(
                safe_float(
                    price
                ),
                2
            )
        )
        + "|"
        +
        str(
            int(
                timestamp
            )
        )
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()[:16]


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(
    state
):

    return (
        now_ts()
        <
        safe_float(
            state.get(
                "cooldown_until",
                0.0
            )
        )
    )


def set_cooldown(
    state,
    minutes=COOLDOWN_MIN
):

    state[
        "cooldown_until"
    ] = (
        now_ts()
        +
        float(minutes) *
        60.0
    )


# ============================================================
# ACTIVE TRADE HELPERS
# ============================================================

def active_trade_exists(
    state
):

    trade = state.get(
        "active_trade"
    )

    return (
        isinstance(
            trade,
            dict
        )
        and
        trade.get(
            "status"
        ) == "OPEN"
    )


def create_trade_record(
    signal,
    features,
    levels
):

    return {

        "signal_id":
            signal.get(
                "signal_id"
            ),

        "opened_at":
            now_ts(),

        "direction":
            signal.get(
                "direction"
            ),

        "setup":
            signal.get(
                "setup"
            ),

        "regime":
            signal.get(
                "regime"
            ),

        "entry":
            levels.get(
                "entry"
            ),

        "stop_loss":
            levels.get(
                "stop_loss"
            ),

        "target":
            levels.get(
                "target"
            ),

        "rr":
            levels.get(
                "rr"
            ),

        "score":
            signal.get(
                "score"
            ),

        "agreement":
            signal.get(
                "agreement"
            ),

        "features":
            feature_snapshot(
                features
            ),

        "status":
            "OPEN",

        "target_extensions":
            0,

        "last_extension_ts":
            0.0,
    }


# ============================================================
# END OF PART 3
# ============================================================
# ============================================================
# PART 4 / 4
# SIGNAL ENGINE + TRADE LIFECYCLE + MAIN LOOP
# ============================================================


# ============================================================
# REGIME HELPERS
# ============================================================

def regime_allows_direction(
    regime,
    direction
):

    regime = str(
        regime or ""
    ).upper()

    direction = str(
        direction or ""
    ).upper()

    if direction == "LONG":

        allowed = {
            "BULLISH_TREND",
            "BULLISH_PULLBACK",
            "BULLISH_EXHAUSTION",
            "BULLISH_REVERSAL",
            "RANGE",
            "TRANSITION",
        }

        return regime in allowed

    if direction == "SHORT":

        allowed = {
            "BEARISH_TREND",
            "BEARISH_PULLBACK",
            "BEARISH_EXHAUSTION",
            "BEARISH_REVERSAL",
            "RANGE",
            "TRANSITION",
        }

        return regime in allowed

    return False


def opposite_direction(
    direction
):

    return (
        "SHORT"
        if str(direction).upper() == "LONG"
        else "LONG"
    )


# ============================================================
# SIGNAL BUILDING
# ============================================================

def build_signal(
    F,
    state,
    calibration,
    market,
    news
):

    candidates = []

    regime = str(
        market.get(
            "regime",
            "UNSTABLE"
        )
    )

    regime_confidence = safe_float(
        market.get(
            "confidence",
            0.0
        )
    )

    price = safe_float(
        market.get(
            "price",
            0.0
        )
    )

    atr = safe_float(
        market.get(
            "atr",
            0.0
        )
    )

    if price <= 0:

        return None

    # --------------------------------------------------------
    # Evaluate LONG / SHORT independently
    # --------------------------------------------------------

    for direction in (
        "LONG",
        "SHORT",
    ):

        if not regime_allows_direction(
            regime,
            direction
        ):
            continue

        execution = execution_score(
            F,
            direction
        )

        base_score = safe_float(
            execution.get(
                "score",
                0.0
            )
        )

        agreement = safe_float(
            execution.get(
                "agreement",
                0.0
            )
        )

        parts = execution.get(
            "parts",
            {}
        )

        features = build_signal_features(
            F,
            direction,
            parts
        )

        adaptive = adaptive_signal_score(
            base_score,
            direction,
            regime,
            features,
            calibration
        )

        adaptive_score = safe_float(
            adaptive.get(
                "score",
                base_score
            )
        )

        adaptive_boost = safe_float(
            adaptive.get(
                "adjustment",
                0.0
            )
        )

        # ----------------------------------------------------
        # Liquidity sweep
        # ----------------------------------------------------

        sweep = detect_liquidity_sweep(
            F,
            direction
        )

        sweep_confirmation = (
            sweep_confirmation_score(
                F,
                direction,
                sweep
            )
        )

        sweep_confirmation = clamp(
            sweep_confirmation
        )

        sweep_quality = safe_float(
            sweep.get(
                "quality",
                0.0
            )
        )

        sweep_age = int(
            safe_float(
                sweep.get(
                    "age",
                    999
                ),
                999
            )
        )

        # ----------------------------------------------------
        # Continuation
        # ----------------------------------------------------

        continuation = detect_continuation(
            F,
            direction
        )

        continuation_ready = bool(
            continuation.get(
                "ready",
                False
            )
        )

        continuation_score = safe_float(
            continuation.get(
                "score",
                0.0
            )
        )

        # ----------------------------------------------------
        # Determine setup
        #
        # IMPORTANT:
        # Sweep is required for reversal setups only.
        # Normal trend / continuation signals do NOT get
        # blocked merely because a sweep is absent.
        # ----------------------------------------------------

        if (
            sweep.get(
                "detected",
                False
            )
            and
            sweep_confirmation >=
            SWEEP_MIN_CONFIRMATION
            and
            sweep_quality >=
            SWEEP_REVERSAL_MIN_QUALITY
        ):

            setup = (
                "SWEEP_REVERSAL"
            )

        elif continuation_ready:

            setup = (
                "CONTINUATION"
            )

        else:

            setup = (
                "TREND"
            )

        # ----------------------------------------------------
        # Setup-specific confirmation
        # ----------------------------------------------------

        if setup == "SWEEP_REVERSAL":

            if (
                sweep_confirmation <
                SWEEP_MIN_CONFIRMATION
            ):
                continue

        # ----------------------------------------------------
        # News confirmation
        # ----------------------------------------------------

        news_score = safe_float(
            news.get(
                "score",
                0.50
            ),
            0.50
        )

        news_confirmation = safe_float(
            news.get(
                "confirmation",
                0.50
            ),
            0.50
        )

        news_directional = (
            news_score
            if direction == "LONG"
            else
            1.0 - news_score
        )

        news_support = (
            .50 *
            news_directional
            +
            .50 *
            news_confirmation
        )

        # ----------------------------------------------------
        # Continuation contribution
        # ----------------------------------------------------

        continuation_support = (
            continuation_score
            if continuation_ready
            else
            0.50
        )

        # ----------------------------------------------------
        # Adaptive final score
        #
        # Score remains on the existing 0-105 scale.
        # Confirmation components add only a controlled
        # adjustment so they cannot dominate the engine.
        # ----------------------------------------------------

        confirmation_adjustment = (
            (
                sweep_confirmation -
                0.50
            )
            * 8.0
            if setup ==
            "SWEEP_REVERSAL"
            else
            0.0
        )

        confirmation_adjustment += (
            (
                continuation_support -
                0.50
            )
            * 5.0
            if setup ==
            "CONTINUATION"
            else
            0.0
        )

        confirmation_adjustment += (
            (
                news_support -
                0.50
            )
            * 3.0
        )

        final_score = clamp(
            adaptive_score +
            confirmation_adjustment,
            0.0,
            105.0
        )

        # ----------------------------------------------------
        # Agreement
        # ----------------------------------------------------

        confirmation_agreement = (
            .70 *
            agreement
            +
            .15 *
            (
                sweep_confirmation
                if setup ==
                "SWEEP_REVERSAL"
                else
                0.50
            )
            +
            .15 *
            news_support
        )

        final_agreement = clamp(
            confirmation_agreement
        )

        # ----------------------------------------------------
        # Strong signal rules
        # ----------------------------------------------------

        strong = (
            final_score >=
            STRONG_SCORE
            and
            final_agreement >=
            STRONG_AGREEMENT
        )

        ready = (
            final_score >=
            MIN_SCORE
            and
            final_agreement >=
            MIN_AGREEMENT
        )

        # ----------------------------------------------------
        # Transition protection
        # ----------------------------------------------------

        if (
            regime ==
            "TRANSITION"
            and
            regime_confidence >=
            TRANSITION_BLOCK_CONFIDENCE
        ):

            ready = (
                ready
                and
                final_score >=
                STRONG_SCORE
                and
                final_agreement >=
                STRONG_AGREEMENT
            )

        if not ready:
            continue

        candidates.append({

            "direction":
                direction,

            "setup":
                setup,

            "regime":
                regime,

            "regime_confidence":
                regime_confidence,

            "score":
                final_score,

            "base_score":
                base_score,

            "adaptive_score":
                adaptive_score,

            "adaptive_adjustment":
                adaptive_boost,

            "agreement":
                final_agreement,

            "base_agreement":
                agreement,

            "sweep":
                sweep,

            "sweep_confirmation":
                sweep_confirmation,

            "continuation":
                continuation,

            "news_score":
                news_score,

            "news_confirmation":
                news_confirmation,

            "parts":
                parts,

            "features":
                features,

            "price":
                price,

            "atr":
                atr,

            "strong":
                strong,
        })

    if not candidates:

        return None

    # --------------------------------------------------------
    # Select highest scoring direction.
    # No signal is generated when the two directions are
    # effectively tied.
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x:
        (
            safe_float(
                x.get(
                    "score",
                    0.0
                )
            ),
            safe_float(
                x.get(
                    "agreement",
                    0.0
                )
            )
        ),
        reverse=True
    )

    best = candidates[0]

    if len(candidates) > 1:

        second = candidates[1]

        score_gap = (
            safe_float(
                best.get(
                    "score"
                )
            )
            -
            safe_float(
                second.get(
                    "score"
                )
            )
        )

        if score_gap < 2.0:

            return None

    best["signal_id"] = make_signal_id(
        best["direction"],
        best["price"],
        now_ts()
    )

    return best


# ============================================================
# WAIT / SIGNAL MESSAGE
# ============================================================

def format_signal_message(
    signal
):

    direction = signal.get(
        "direction",
        "WAIT"
    )

    setup = signal.get(
        "setup",
        "UNKNOWN"
    )

    regime = signal.get(
        "regime",
        "UNKNOWN"
    )

    score = safe_float(
        signal.get(
            "score",
            0.0
        )
    )

    agreement = (
        safe_float(
            signal.get(
                "agreement",
                0.0
            )
        )
        * 100.0
    )

    price = safe_float(
        signal.get(
            "price",
            0.0
        )
    )

    sweep_confirmation = (
        safe_float(
            signal.get(
                "sweep_confirmation",
                0.0
            )
        )
        * 100.0
    )

    continuation = signal.get(
        "continuation",
        {}
    )

    continuation_score = (
        safe_float(
            continuation.get(
                "score",
                0.0
            )
        )
        * 100.0
    )

    news_confirmation = (
        safe_float(
            signal.get(
                "news_confirmation",
                0.50
            )
        )
        * 100.0
    )

    strength = (
        "STRONG"
        if signal.get(
            "strong",
            False
        )
        else
        "STANDARD"
    )

    return (
        "BTC V8 SIGNAL\n"
        "\n"
        f"Direction: {direction}\n"
        f"Setup: {setup}\n"
        f"Regime: {regime}\n"
        f"Strength: {strength}\n"
        f"Score: {score:.1f}/105\n"
        f"Agreement: {agreement:.1f}%\n"
        f"Price: ${price:,.2f}\n"
        f"Sweep confirmation: "
        f"{sweep_confirmation:.1f}%\n"
        f"Continuation: "
        f"{continuation_score:.1f}%\n"
        f"News confirmation: "
        f"{news_confirmation:.1f}%"
    )


# ============================================================
# TRADE OPEN
# ============================================================

def open_trade_from_signal(
    state,
    signal
):

    price = safe_float(
        signal.get(
            "price"
        )
    )

    atr = safe_float(
        signal.get(
            "atr"
        )
    )

    direction = signal.get(
        "direction"
    )

    score = safe_float(
        signal.get(
            "score"
        )
    )

    levels = calculate_trade_levels(
        price,
        atr,
        direction,
        score
    )

    trade = create_trade_record(
        signal,
        signal.get(
            "features",
            {}
        ),
        levels
    )

    state[
        "active_trade"
    ] = trade

    set_cooldown(
        state
    )

    return trade


# ============================================================
# CLOSED TRADE LEARNING
# ============================================================

def close_trade(
    state,
    calibration,
    current_price,
    reason
):

    trade = state.get(
        "active_trade"
    )

    if not isinstance(
        trade,
        dict
    ):
        return None

    if trade.get(
        "status"
    ) != "OPEN":
        return None

    entry = safe_float(
        trade.get(
            "entry"
        )
    )

    stop_loss = safe_float(
        trade.get(
            "stop_loss"
        )
    )

    direction = str(
        trade.get(
            "direction",
            ""
        )
    ).upper()

    risk = abs(
        entry -
        stop_loss
    )

    current_price = safe_float(
        current_price
    )

    if risk <= 0:

        risk = max(
            entry * .001,
            1e-9
        )

    if direction == "LONG":

        pnl_r = (
            current_price -
            entry
        ) / risk

    else:

        pnl_r = (
            entry -
            current_price
        ) / risk

    outcome = (
        "WIN"
        if pnl_r > 0
        else
        "LOSS"
        if pnl_r < 0
        else
        "FLAT"
    )

    trade["status"] = "CLOSED"

    trade["closed_at"] = now_ts()

    trade["exit"] = (
        current_price
    )

    trade["close_reason"] = (
        reason
    )

    trade["pnl_r"] = (
        pnl_r
    )

    trade["outcome"] = (
        outcome
    )

    try:

        learn_from_closed_trade(
            calibration,
            trade
        )

    except Exception as e:

        journal(
            "LEARNING_ERROR",
            error=repr(e)
        )

    state[
        "active_trade"
    ] = None

    state[
        "last_closed_trade"
    ] = trade

    return trade


# ============================================================
# ACTIVE TRADE MANAGEMENT
# ============================================================

def manage_active_trade(
    F,
    state,
    calibration
):

    trade = state.get(
        "active_trade"
    )

    if not isinstance(
        trade,
        dict
    ):
        return None

    if trade.get(
        "status"
    ) != "OPEN":
        return None

    price = safe_float(
        F["5m"]["close"].iloc[-1]
    )

    atr = safe_float(
        F["5m"]["atr"].iloc[-1]
    )

    direction = str(
        trade.get(
            "direction",
            ""
        )
    ).upper()

    stop_loss = safe_float(
        trade.get(
            "stop_loss"
        )
    )

    target = safe_float(
        trade.get(
            "target"
        )
    )

    # --------------------------------------------------------
    # STOP / TARGET
    # --------------------------------------------------------

    if direction == "LONG":

        if price <= stop_loss:

            return close_trade(
                state,
                calibration,
                price,
                "STOP_LOSS"
            )

        if price >= target:

            extensions = int(
                safe_float(
                    trade.get(
                        "target_extensions",
                        0
                    )
                )
            )

            last_extension = safe_float(
                trade.get(
                    "last_extension_ts",
                    0.0
                )
            )

            can_extend = (
                extensions <
                MAX_TARGET_EXTENSIONS
                and
                (
                    now_ts()
                    -
                    last_extension
                )
                >=
                EXTENSION_COOLDOWN_MIN *
                60.0
            )

            if can_extend:

                new_target = (
                    calculate_extended_target(
                        trade,
                        price,
                        atr
                    )
                )

                if new_target is not None:

                    trade[
                        "target"
                    ] = new_target

                    trade[
                        "target_extensions"
                    ] = (
                        extensions +
                        1
                    )

                    trade[
                        "last_extension_ts"
                    ] = now_ts()

                    journal(
                        "TARGET_EXTENDED",
                        direction=direction,
                        price=price,
                        new_target=new_target,
                        extensions=
                        extensions + 1
                    )

                    return trade

            return close_trade(
                state,
                calibration,
                price,
                "TARGET_HIT"
            )

    else:

        if price >= stop_loss:

            return close_trade(
                state,
                calibration,
                price,
                "STOP_LOSS"
            )

        if price <= target:

            extensions = int(
                safe_float(
                    trade.get(
                        "target_extensions",
                        0
                    )
                )
            )

            last_extension = safe_float(
                trade.get(
                    "last_extension_ts",
                    0.0
                )
            )

            can_extend = (
                extensions <
                MAX_TARGET_EXTENSIONS
                and
                (
                    now_ts()
                    -
                    last_extension
                )
                >=
                EXTENSION_COOLDOWN_MIN *
                60.0
            )

            if can_extend:

                new_target = (
                    calculate_extended_target(
                        trade,
                        price,
                        atr
                    )
                )

                if new_target is not None:

                    trade[
                        "target"
                    ] = new_target

                    trade[
                        "target_extensions"
                    ] = (
                        extensions +
                        1
                    )

                    trade[
                        "last_extension_ts"
                    ] = now_ts()

                    journal(
                        "TARGET_EXTENDED",
                        direction=direction,
                        price=price,
                        new_target=new_target,
                        extensions=
                        extensions + 1
                    )

                    return trade

            return close_trade(
                state,
                calibration,
                price,
                "TARGET_HIT"
            )

    # --------------------------------------------------------
    # TIME EXPIRY
    # --------------------------------------------------------

    opened_at = safe_float(
        trade.get(
            "opened_at",
            now_ts()
        )
    )

    age_hours = (
        now_ts() -
        opened_at
    ) / 3600.0

    if age_hours >= MAX_TRADE_AGE_HOURS:

        return close_trade(
            state,
            calibration,
            price,
            "MAX_TRADE_AGE"
        )

    return trade


# ============================================================
# WAIT JOURNAL
# ============================================================

def journal_wait(
    market,
    reason,
    details=None
):

    payload = {

        "regime":
            market.get(
                "regime"
            ),

        "price":
            market.get(
                "price"
            ),

        "reason":
            reason,
    }

    if isinstance(
        details,
        dict
    ):

        payload.update(
            details
        )

    journal(
        "WAIT",
        **payload
    )


# ============================================================
# SIGNAL CSV RECORD
# ============================================================

def record_signal(
    signal,
    action
):

    if not isinstance(
        signal,
        dict
    ):
        return

    row = {

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "signal_id":
            signal.get(
                "signal_id"
            ),

        "direction":
            signal.get(
                "direction"
            ),

        "setup":
            signal.get(
                "setup"
            ),

        "regime":
            signal.get(
                "regime"
            ),

        "score":
            signal.get(
                "score"
            ),

        "agreement":
            signal.get(
                "agreement"
            ),

        "price":
            signal.get(
                "price"
            ),

        "sweep_confirmation":
            signal.get(
                "sweep_confirmation"
            ),

        "news_confirmation":
            signal.get(
                "news_confirmation"
            ),

        "action":
            action,
    }

    append_signal_csv(
        row
    )


# ============================================================
# SINGLE SCAN
# ============================================================

def run_scan(
    state,
    calibration
):

    try:

        F = {

            "5m":
                fetch_candles(
                    300,
                    HISTORY_5M_BARS
                ),

            "15m":
                fetch_candles(
                    900,
                    300
                ),

            "1h":
                fetch_candles(
                    3600,
                    HISTORY_1H_BARS
                ),

            "2h":
                fetch_candles(
                    7200,
                    300
                ),

            "4h":
                fetch_candles(
                    21600,
                    300
                ),
        }

        # ----------------------------------------------------
        # Validate market data
        # ----------------------------------------------------

        for key, df in F.items():

            if (
                df is None
                or
                len(df) < 50
            ):

                raise RuntimeError(
                    "Insufficient "
                    f"{key} market data"
                )

        # ----------------------------------------------------
        # Order flow
        # ----------------------------------------------------

        update_orderflow()

        # ----------------------------------------------------
        # Market state
        # ----------------------------------------------------

        market = market_state_engine(
            F
        )

        # ----------------------------------------------------
        # News
        # ----------------------------------------------------

        news = fetch_news()

        # ----------------------------------------------------
        # Active trade first
        # ----------------------------------------------------

        if active_trade_exists(
            state
        ):

            managed = (
                manage_active_trade(
                    F,
                    state,
                    calibration
                )
            )

            if (
                managed is not None
                and
                managed.get(
                    "status"
                ) == "CLOSED"
            ):

                telegram_send(
                    (
                        "BTC V8 TRADE CLOSED\n"
                        "\n"
                        f"Direction: "
                        f"{managed.get('direction')}\n"
                        f"Reason: "
                        f"{managed.get('close_reason')}\n"
                        f"Entry: "
                        f"${safe_float(managed.get('entry')):,.2f}\n"
                        f"Exit: "
                        f"${safe_float(managed.get('exit')):,.2f}\n"
                        f"PnL: "
                        f"{safe_float(managed.get('pnl_r')):.2f}R\n"
                        f"Outcome: "
                        f"{managed.get('outcome')}"
                    )
                )

                save_state(
                    state
                )

                save_calibration(
                    calibration
                )

                return managed

            # ------------------------------------------------
            # Do not open another trade while one is active.
            # ------------------------------------------------

            save_state(
                state
            )

            return managed

        # ----------------------------------------------------
        # Cooldown
        # ----------------------------------------------------

        if cooldown_active(
            state
        ):

            journal_wait(
                market,
                "cooldown_active"
            )

            return None

        # ----------------------------------------------------
        # Build signal
        # ----------------------------------------------------

        signal = build_signal(
            F,
            state,
            calibration,
            market,
            news
        )

        if signal is None:

            journal_wait(
                market,
                "no_valid_signal"
            )

            return None

        # ----------------------------------------------------
        # Open trade
        # ----------------------------------------------------

        trade = open_trade_from_signal(
            state,
            signal
        )

        record_signal(
            signal,
            "OPEN"
        )

        telegram_send(
            format_signal_message(
                signal
            )
            +
            "\n\n"
            "TRADE OPENED"
            "\n"
            f"Entry: "
            f"${safe_float(trade.get('entry')):,.2f}\n"
            f"Stop: "
            f"${safe_float(trade.get('stop_loss')):,.2f}\n"
            f"Target: "
            f"${safe_float(trade.get('target')):,.2f}\n"
            f"RR: "
            f"{safe_float(trade.get('rr')):.2f}"
        )

        journal(
            "TRADE_OPENED",
            signal=signal,
            trade=trade
        )

        save_state(
            state
        )

        save_calibration(
            calibration
        )

        return trade

    except Exception as e:

        journal(
            "SCAN_ERROR",
            error=repr(e),
            traceback=traceback.format_exc()
        )

        return None


# ============================================================
# INITIALIZATION
# ============================================================

def initialize():

    ensure_parent_dir(
        STATE_FILE
    )

    ensure_parent_dir(
        JOURNAL_FILE
    )

    ensure_parent_dir(
        CALIBRATION_FILE
    )

    ensure_parent_dir(
        CSV_LOG_FILE
    )

    state = load_state()

    calibration = load_calibration()

    if not isinstance(
        state,
        dict
    ):

        state = default_state()

    if not isinstance(
        calibration,
        dict
    ):

        calibration = default_calibration()

    return (
        state,
        calibration
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=
        "BTC Adaptive Telegram Bot V8"
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help=
        "Run exactly one market scan"
    )

    args = parser.parse_args()

    state, calibration = (
        initialize()
    )

    journal(
        "BOT_START",
        version="V8",
        once=args.once
    )

    if args.once:

        run_scan(
            state,
            calibration
        )

        save_state(
            state
        )

        save_calibration(
            calibration
        )

        journal(
            "BOT_STOP",
            version="V8",
            once=True
        )

        return

    while True:

        started = now_ts()

        run_scan(
            state,
            calibration
        )

        save_state(
            state
        )

        save_calibration(
            calibration
        )

        elapsed = (
            now_ts() -
            started
        )

        sleep_for = max(
            1.0,
            SCAN_SECONDS -
            elapsed
        )

        time.sleep(
            sleep_for
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        journal(
            "BOT_STOP",
            reason="KeyboardInterrupt"
        )

    except Exception as e:

        journal(
            "FATAL_ERROR",
            error=repr(e),
            traceback=traceback.format_exc()
        )

        raise


# ============================================================
# END OF PART 4
# END OF BTC ADAPTIVE TELEGRAM BOT V8
# ============================================================

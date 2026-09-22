import os, sys, time, json, math, argparse, traceback, csv, copy, re, hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests
import pandas as pd
import numpy as np


# ============================================================
# BTC ADAPTIVE TELEGRAM BOT V8
# Market State + Timeframe Coordination + Adaptive Statistical Learning
# Liquidity Sweep + News + Post-Impulse Continuation + Lifecycle
# Coinbase REST only. Designed for GitHub Actions --once.
# ============================================================

PRODUCT = "BTC-USD"
REST = "https://api.exchange.coinbase.com"

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7500472109")

STATE_FILE = "btc_v8_state.json"
JOURNAL_FILE = "btc_v8_journal.jsonl"
CALIBRATION_FILE = "btc_v8_calibration.json"
CSV_LOG_FILE = "logs/btc_v8_signals.csv"

SCAN_SECONDS = 60
COOLDOWN_MIN = 45

MIN_SCORE = 62
STRONG_SCORE = 76

MIN_AGREEMENT = 0.58
STRONG_AGREEMENT = 0.68

REQUEST_TIMEOUT = 20

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
    "sweep_quality": .20,
    "structure": .20,
    "momentum": .15,
    "orderflow": .15,
    "reversal": .10,
    "retest": .10,
    "volume": .10,
}

# Sweep reversal is a separate candidate path.
# A sweep alone never creates an entry.

SWEEP_REVERSAL_MIN_QUALITY = 0.45
SWEEP_REVERSAL_STRONG_CONFIRMATION = 0.60
SWEEP_REVERSAL_STRONG_TREND_SCORE = 76
SWEEP_REVERSAL_STRONG_TREND_EXTRA = 0.05

SWEEP_REVERSAL_MIN_15M_CONFIRMATION = 0.55
SWEEP_REVERSAL_STRONG_15M_CONFIRMATION = 0.68


# ============================================================
# POST-IMPULSE CONTINUATION
# ============================================================

NEW_CONTINUATION_ENTRY_SCORE = 0.70

CONT_LOOKBACK = 20
CONT_IMPULSE_BARS = 6
CONT_IMPULSE_ATR = 1.8
CONT_MIN_PULLBACK = .18
CONT_MAX_PULLBACK = .65
CONT_READY_SCORE = .62


# ============================================================
# TARGET EXTENSION / TRADE LIFECYCLE
# ============================================================

MAX_TARGET_EXTENSIONS = 2
EXTENSION_COOLDOWN_MIN = 30
MAX_TRADE_AGE_HOURS = 36


# ============================================================
# BASE SCORE WEIGHTS
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
# NEWS CONFIGURATION
# ============================================================

# CryptoPanic endpoint is configurable.
# API key is optional; if the provider requires auth,
# set CRYPTOPANIC_API_KEY in GitHub Secrets.

NEWS_ENABLED = os.environ.get("NEWS_ENABLED", "1") != "0"

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
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent":
        "BTC-Adaptive-Telegram-Bot-V8-GitHub/1.0"
})


# ============================================================
# GLOBAL ORDER FLOW STATE
# ============================================================

flow = {
    "bids": {},
    "asks": {},
    "delta": 0.0,
    "large": 0.0,
    "buy": 0.0,
    "sell": 0.0,
    "trade_count": 0,
    "updated": 0,
}


# ============================================================
# V8 MARKET STATE / ADAPTIVE STATISTICAL LEARNING
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
# MARKET REGIME NAMES
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
# SAFE DATAFRAME VALUE
# ============================================================

def _safe_last(df, col, default=0.0):
    try:
        return safe_float(
            df.iloc[-1][col],
            default
        )
    except Exception:
        return default


# ============================================================
# TIMEFRAME DIRECTION SCORE
# ============================================================

def _tf_direction_score(df):

    d = prepare(df)

    r = d.iloc[-1]

    av = max(
        safe_float(r.atr),
        safe_float(r.close) * .0001
    )

    bull = 0.0
    bear = 0.0


    # ----------------------------
    # BULLISH CONDITIONS
    # ----------------------------

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


    # ----------------------------
    # BEARISH CONDITIONS
    # ----------------------------

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


    # ----------------------------
    # IMPULSE
    # ----------------------------

    if len(d) >= 5:

        impulse = (
            r.close -
            d.close.iloc[-4]
        ) / av

    else:

        impulse = 0.0


    return {
        "bull": bull / 5.0,
        "bear": bear / 5.0,
        "impulse": impulse,
        "rsi": safe_float(r.rsi),
        "close": safe_float(r.close),
        "atr": av,
    }


# ============================================================
# TRANSITION SCORE
# ============================================================

def _transition_score(tf):

    # 1H transition is deliberately more important
    # than 4H for direction change.

    t1 = tf["1h"]
    t15 = tf["15m"]
    t2 = tf["2h"]

    return clamp(
        .45 * abs(
            t1["bull"] -
            t1["bear"]
        )
        +
        .35 * abs(
            t15["bull"] -
            t15["bear"]
        )
        +
        .20 * abs(
            t2["bull"] -
            t2["bear"]
        )
    )


# ============================================================
# MARKET STATE ENGINE
# ============================================================

def market_state_engine(F, state):

    tf = {
        k: _tf_direction_score(F[k])
        for k in (
            "4h",
            "2h",
            "1h",
            "15m",
        )
    }


    # ----------------------------
    # MACRO
    # ----------------------------

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


    # ----------------------------
    # PRIMARY TREND
    # ----------------------------

    primary_bull = tf["2h"]["bull"]
    primary_bear = tf["2h"]["bear"]


    # ----------------------------
    # TRANSITION
    # ----------------------------

    trans_bull = tf["1h"]["bull"]
    trans_bear = tf["1h"]["bear"]


    # ----------------------------
    # SETUP
    # ----------------------------

    setup_bull = tf["15m"]["bull"]
    setup_bear = tf["15m"]["bear"]


    # ----------------------------
    # TRANSITION CONFIDENCE
    # ----------------------------

    transition = clamp(
        .50 * max(
            trans_bull,
            trans_bear
        )
        +
        .30 * max(
            setup_bull,
            setup_bear
        )
        +
        .20 * abs(
            trans_bull -
            trans_bear
        )
    )


    # ----------------------------
    # COMPOSITE BULL / BEAR
    # ----------------------------

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


    # ----------------------------
    # SHORT TERM CONFLICT
    # ----------------------------

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


    # ----------------------------
    # MACRO DIRECTION
    # ----------------------------

    macro_bull = (
        macro >= .62
        and
        macro > macro_bear + .10
    )

    macro_bearish = (
        macro_bear >= .62
        and
        macro_bear > macro + .10
    )


    # ----------------------------
    # LOWER TIMEFRAME REVERSAL
    # ----------------------------

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


    # ----------------------------
    # EXHAUSTION
    # ----------------------------

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


    # ========================================================
    # REGIME CLASSIFICATION
    # ========================================================

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
        transition >= TRANSITION_BLOCK_CONFIDENCE
    ):

        regime = "BEARISH_REVERSAL"


    elif (
        macro_bearish
        and
        ltf_bull
        and
        transition >= TRANSITION_BLOCK_CONFIDENCE
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
        bullish > bearish + .12
    ):

        regime = "BULLISH_TREND"


    elif (
        bearish >= .65
        and
        bearish > bullish + .12
    ):

        regime = "BEARISH_TREND"


    elif max(
        bullish,
        bearish
    ) < .58:

        regime = "RANGE"


    else:

        regime = "TRANSITION"


    # ========================================================
    # REGIME CONFIDENCE
    # ========================================================

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


    # ========================================================
    # STATE HYSTERESIS
    # ========================================================

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


    # ========================================================
    # OUTPUT
    # ========================================================

    out = {

        "regime": active,

        "candidate": candidate,

        "candidate_count": count,

        "confidence": confidence,

        "bull_score": bullish,

        "bear_score": bearish,

        "transition_score": transition,

        "timeframes": tf,

        "macro": (
            "BULLISH"
            if macro >= macro_bear
            else
            "BEARISH"
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


    return out


# ============================================================
# EXECUTION SCORE
# ============================================================

def execution_score(F, direction):

    d = prepare(F["5m"])

    r = d.iloc[-1]

    atr = max(
        safe_float(r.atr),
        safe_float(r.close) * .0001
    )


    if direction == "LONG":

        trend = clamp(
            .35 * safe_float(
                F["1h"]["bull"]
            )
            +
            .30 * safe_float(
                F["2h"]["bull"]
            )
            +
            .20 * safe_float(
                F["4h"]["bull"]
            )
            +
            .15 * safe_float(
                F["15m"]["bull"]
            )
        )

    else:

        trend = clamp(
            .35 * safe_float(
                F["1h"]["bear"]
            )
            +
            .30 * safe_float(
                F["2h"]["bear"]
            )
            +
            .20 * safe_float(
                F["4h"]["bear"]
            )
            +
            .15 * safe_float(
                F["15m"]["bear"]
            )
        )
def execution_score(F,direction):
    d=prepare(F["5m"])
    r=d.iloc[-1]
    atr=max(safe_float(r.atr),safe_float(r.close)*.0001)

    if direction=="LONG":
        trend=clamp(
            .35*safe_float(F["1h"]["bull"]) +
            .30*safe_float(F["2h"]["bull"]) +
            .20*safe_float(F["4h"]["bull"]) +
            .15*safe_float(F["15m"]["bull"])
        )
    else:
        trend=clamp(
            .35*safe_float(F["1h"]["bear"]) +
            .30*safe_float(F["2h"]["bear"]) +
            .20*safe_float(F["4h"]["bear"]) +
            .15*safe_float(F["15m"]["bear"])
        )

    price_score=0.0
    momentum_score=0.0
    volume_score=0.0
    structure_score=0.0
    orderflow_score=0.0

    try:
        if direction=="LONG":
            price_score=clamp(
                .50*(1.0 if r.close>r.ema21 else 0.0)+
                .30*(1.0 if r.close>r.ema50 else 0.0)+
                .20*(1.0 if r.ret3>0 else 0.0)
            )
            momentum_score=clamp(
                .55*(1.0 if r.ret3>0 else 0.0)+
                .45*(1.0 if r.rsi>=50 else 0.0)
            )
        else:
            price_score=clamp(
                .50*(1.0 if r.close<r.ema21 else 0.0)+
                .30*(1.0 if r.close<r.ema50 else 0.0)+
                .20*(1.0 if r.ret3<0 else 0.0)
            )
            momentum_score=clamp(
                .55*(1.0 if r.ret3<0 else 0.0)+
                .45*(1.0 if r.rsi<=50 else 0.0)
            )

        volume_score=clamp(
            safe_float(r.volume_ratio)/1.50
        )

        structure_score=clamp(
            .50*(1.0 if (
                (direction=="LONG" and r.close>r.high.shift(1)) or
                (direction=="SHORT" and r.close<r.low.shift(1))
            ) else 0.0)+
            .50*(1.0 if (
                (direction=="LONG" and r.close>r.ema21) or
                (direction=="SHORT" and r.close<r.ema21)
            ) else 0.0)
        )
    except Exception:
        pass

    flow_age=now_ts()-safe_float(flow.get("updated"),0)
    if flow_age<=ORDERFLOW_STALE:
        delta=safe_float(flow.get("delta"))
        total=max(
            abs(safe_float(flow.get("buy"))) +
            abs(safe_float(flow.get("sell"))),
            1e-9
        )
        if direction=="LONG":
            orderflow_score=clamp(.50+.50*(delta/total))
        else:
            orderflow_score=clamp(.50-.50*(delta/total))
    else:
        orderflow_score=.50

    return {
        "trend":clamp(trend),
        "price":clamp(price_score),
        "momentum":clamp(momentum_score),
        "volume":clamp(volume_score),
        "structure":clamp(structure_score),
        "orderflow":clamp(orderflow_score),
        "atr":atr,
    }


def classify_market_regime(state):
    regime=state.get("market_state","UNSTABLE")
    confidence=safe_float(state.get("market_state_confidence"),0.0)

    if regime not in REGIME_NAMES:
        return "UNSTABLE",0.0

    return regime,confidence


def regime_direction_bias(regime):
    if regime in (
        "BULLISH_TREND",
        "BULLISH_PULLBACK",
        "BULLISH_EXHAUSTION",
        "BULLISH_REVERSAL",
    ):
        return "LONG"

    if regime in (
        "BEARISH_TREND",
        "BEARISH_PULLBACK",
        "BEARISH_EXHAUSTION",
        "BEARISH_REVERSAL",
    ):
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# ADAPTIVE STATISTICAL LEARNING
# ============================================================

def adaptive_default():
    return {
        "samples":0,
        "wins":0,
        "losses":0,
        "timeouts":0,
        "sum_r":0.0,
        "sum_r2":0.0,
        "expectancy":0.0,
        "win_rate":0.0,
        "avg_win":0.0,
        "avg_loss":0.0,
        "last_update":None,
    }


def load_calibration():
    try:
        if not os.path.exists(CALIBRATION_FILE):
            return {
                "version":8,
                "updated_at":None,
                "buckets":{},
            }

        with open(CALIBRATION_FILE,"r",encoding="utf-8") as f:
            obj=json.load(f)

        if not isinstance(obj,dict):
            raise ValueError("invalid calibration")

        obj.setdefault("version",8)
        obj.setdefault("updated_at",None)
        obj.setdefault("buckets",{})
        return obj

    except Exception:
        return {
            "version":8,
            "updated_at":None,
            "buckets":{},
        }


def save_calibration(calibration):
    calibration["updated_at"]=utc_iso()

    tmp=CALIBRATION_FILE+".tmp"

    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(
            calibration,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True
        )

    os.replace(tmp,CALIBRATION_FILE)


def adaptive_bucket_key(
    direction,
    regime,
    setup,
    sweep,
    score_band,
    agreement_band
):
    return "|".join([
        str(direction or "UNKNOWN"),
        str(regime or "UNSTABLE"),
        str(setup or "UNKNOWN"),
        str(sweep or "NONE"),
        str(score_band or "MID"),
        str(agreement_band or "MID"),
    ])


def score_band(score):
    score=safe_float(score)

    if score>=STRONG_SCORE:
        return "STRONG"

    if score>=MIN_SCORE:
        return "VALID"

    if score>=50:
        return "MID"

    return "LOW"


def agreement_band(value):
    value=safe_float(value)

    if value>=STRONG_AGREEMENT:
        return "STRONG"

    if value>=MIN_AGREEMENT:
        return "VALID"

    return "LOW"


def get_adaptive_bucket(
    calibration,
    direction,
    regime,
    setup,
    sweep,
    score,
    agreement
):
    key=adaptive_bucket_key(
        direction,
        regime,
        setup,
        sweep,
        score_band(score),
        agreement_band(agreement)
    )

    buckets=calibration.setdefault("buckets",{})

    if key not in buckets:
        buckets[key]=adaptive_default()

    return key,buckets[key]


def decayed_weight(timestamp):
    try:
        if not timestamp:
            return 1.0

        if isinstance(timestamp,str):
            dt=datetime.fromisoformat(
                timestamp.replace("Z","+00:00")
            )
        else:
            dt=datetime.fromtimestamp(
                float(timestamp),
                tz=timezone.utc
            )

        age=max(
            0.0,
            (datetime.now(timezone.utc)-dt).total_seconds()/86400.0
        )

        return math.exp(-age/max(ADAPTIVE_DECAY_DAYS,1.0))

    except Exception:
        return 1.0


def adaptive_expectancy(bucket):
    samples=int(bucket.get("samples",0))
    if samples<=0:
        return 0.0

    return safe_float(
        bucket.get("expectancy"),
        safe_float(bucket.get("sum_r"))/max(samples,1)
    )


def adaptive_win_rate(bucket):
    samples=int(bucket.get("samples",0))
    if samples<=0:
        return 0.0

    return clamp(
        safe_float(bucket.get("wins"))/max(samples,1)
    )


def adaptive_adjustment(bucket):
    samples=int(bucket.get("samples",0))

    if samples<ADAPTIVE_MIN_SAMPLES:
        return 0.0

    expectancy=adaptive_expectancy(bucket)
    win_rate=adaptive_win_rate(bucket)

    if expectancy>=ADAPTIVE_MIN_EXPECTANCY_EDGE:
        strength=min(
            1.0,
            samples/max(ADAPTIVE_STRONG_SAMPLES,1)
        )

        boost=(
            min(expectancy,.50)/.50
        )*ADAPTIVE_MAX_BOOST*strength

        return clamp(
            boost,
            0.0,
            ADAPTIVE_MAX_BOOST
        )

    if expectancy<=ADAPTIVE_STRONG_NEGATIVE_EXPECTANCY:
        strength=min(
            1.0,
            samples/max(ADAPTIVE_STRONG_SAMPLES,1)
        )

        penalty=(
            min(abs(expectancy),.75)/.75
        )*ADAPTIVE_MAX_PENALTY*strength

        return -clamp(
            penalty,
            0.0,
            ADAPTIVE_MAX_PENALTY
        )

    # Mild statistical adjustment around neutral expectancy.
    centered=(expectancy-ADAPTIVE_MIN_EXPECTANCY_EDGE)

    return clamp(
        centered*8.0,
        -ADAPTIVE_MAX_PENALTY,
        ADAPTIVE_MAX_BOOST
    )


def adaptive_context(
    calibration,
    direction,
    regime,
    setup,
    sweep,
    score,
    agreement
):
    key,bucket=get_adaptive_bucket(
        calibration,
        direction,
        regime,
        setup,
        sweep,
        score,
        agreement
    )

    samples=int(bucket.get("samples",0))
    expectancy=adaptive_expectancy(bucket)
    win_rate=adaptive_win_rate(bucket)
    adjustment=adaptive_adjustment(bucket)

    return {
        "key":key,
        "samples":samples,
        "wins":int(bucket.get("wins",0)),
        "losses":int(bucket.get("losses",0)),
        "expectancy":expectancy,
        "win_rate":win_rate,
        "adjustment":adjustment,
        "strong_negative":(
            samples>=ADAPTIVE_MIN_SAMPLES and
            expectancy<=ADAPTIVE_STRONG_NEGATIVE_EXPECTANCY
        ),
        "reliable":samples>=ADAPTIVE_MIN_SAMPLES,
        "strong":samples>=ADAPTIVE_STRONG_SAMPLES,
    }


def update_adaptive_bucket(
    calibration,
    direction,
    regime,
    setup,
    sweep,
    score,
    agreement,
    result_r,
    result_status
):
    key,bucket=get_adaptive_bucket(
        calibration,
        direction,
        regime,
        setup,
        sweep,
        score,
        agreement
    )

    r=safe_float(result_r,0.0)

    bucket["samples"]=int(bucket.get("samples",0))+1

    if r>0:
        bucket["wins"]=int(bucket.get("wins",0))+1
    elif r<0:
        bucket["losses"]=int(bucket.get("losses",0))+1
    else:
        bucket["timeouts"]=int(bucket.get("timeouts",0))+1

    bucket["sum_r"]=safe_float(bucket.get("sum_r"))+r
    bucket["sum_r2"]=safe_float(bucket.get("sum_r2"))+(r*r)

    samples=max(int(bucket["samples"]),1)

    bucket["expectancy"]=(
        safe_float(bucket["sum_r"])/samples
    )

    bucket["win_rate"]=(
        int(bucket.get("wins",0))/samples
    )

    bucket["last_update"]=utc_iso()
    bucket["last_status"]=str(result_status or "")

    calibration["buckets"][key]=bucket
    return bucket


def adaptive_should_block(ctx):
    if not ctx:
        return False

    return bool(
        ctx.get("reliable") and
        ctx.get("strong_negative")
    )


def adaptive_score_adjustment(ctx):
    if not ctx:
        return 0.0

    return clamp(
        safe_float(ctx.get("adjustment")),
        -ADAPTIVE_MAX_PENALTY,
        ADAPTIVE_MAX_BOOST
    )


def adaptive_probability(ctx):
    if not ctx or int(ctx.get("samples",0))<ADAPTIVE_MIN_SAMPLES:
        return None

    return clamp(
        safe_float(ctx.get("win_rate")),
        0.0,
        1.0
    )


# ============================================================
# MARKET-STATE / ADAPTIVE INTEGRATION
# ============================================================

def apply_market_state_adjustment(score,direction,market_state):
    regime=market_state.get("regime","UNSTABLE")
    confidence=safe_float(market_state.get("confidence"),0.0)

    adjustment=0.0

    if direction=="LONG":
        if regime=="BULLISH_TREND":
            adjustment+=3.0*confidence
        elif regime=="BULLISH_PULLBACK":
            adjustment+=2.0*confidence
        elif regime=="BULLISH_REVERSAL":
            adjustment+=1.0*confidence
        elif regime=="BEARISH_TREND":
            adjustment-=4.0*confidence
        elif regime=="BEARISH_PULLBACK":
            adjustment-=3.0*confidence
        elif regime=="BEARISH_REVERSAL":
            adjustment-=2.0*confidence
        elif regime in ("RANGE","UNSTABLE"):
            adjustment-=1.0

    elif direction=="SHORT":
        if regime=="BEARISH_TREND":
            adjustment+=3.0*confidence
        elif regime=="BEARISH_PULLBACK":
            adjustment+=2.0*confidence
        elif regime=="BEARISH_REVERSAL":
            adjustment+=1.0*confidence
        elif regime=="BULLISH_TREND":
            adjustment-=4.0*confidence
        elif regime=="BULLISH_PULLBACK":
            adjustment-=3.0*confidence
        elif regime=="BULLISH_REVERSAL":
            adjustment-=2.0*confidence
        elif regime in ("RANGE","UNSTABLE"):
            adjustment-=1.0

    return clamp(
        safe_float(score)+adjustment,
        0.0,
        105.0
    )


def market_state_gate(
    market_state,
    direction,
    score,
    agreement
):
    regime=market_state.get("regime","UNSTABLE")
    confidence=safe_float(
        market_state.get("confidence"),
        0.0
    )

    score=safe_float(score)
    agreement=safe_float(agreement)

    if regime=="UNSTABLE":
        return {
            "allowed":False,
            "reason":"market_state_unstable",
            "adjustment":0.0,
        }

    if regime=="TRANSITION":
        if confidence<TRANSITION_BLOCK_CONFIDENCE:
            return {
                "allowed":False,
                "reason":"transition_low_confidence",
                "adjustment":0.0,
            }

    bias=regime_direction_bias(regime)

    if bias in ("LONG","SHORT") and bias!=direction:
        if confidence>=REGIME_MIN_CONFIDENCE:
            return {
                "allowed":False,
                "reason":"market_state_opposes_direction",
                "adjustment":0.0,
            }

    if agreement<MIN_AGREEMENT:
        return {
            "allowed":False,
            "reason":"agreement_below_market_state_gate",
            "adjustment":0.0,
        }

    return {
        "allowed":True,
        "reason":"market_state_aligned",
        "adjustment":0.0,
    }


# ============================================================
# FEATURE SNAPSHOT FOR ADAPTIVE LEARNING
# ============================================================

def feature_snapshot(
    direction,
    score,
    agreement,
    market_state,
    setup="UNKNOWN",
    sweep="NONE"
):
    return {
        "direction":direction,
        "score":round(safe_float(score),4),
        "agreement":round(safe_float(agreement),4),
        "score_band":score_band(score),
        "agreement_band":agreement_band(agreement),
        "regime":market_state.get("regime","UNSTABLE"),
        "regime_confidence":round(
            safe_float(market_state.get("confidence")),
            4
        ),
        "setup":setup,
        "sweep":sweep,
        "ts":utc_iso(),
    }


def normalize_trade_result(result):
    if not isinstance(result,dict):
        return {
            "status":"UNKNOWN",
            "r":0.0,
        }

    status=str(
        result.get("status") or
        result.get("result") or
        "UNKNOWN"
    ).upper()

    r=safe_float(
        result.get("r"),
        result.get("rr",0.0)
    )

    return {
        "status":status,
        "r":r,
    }


# ============================================================
# ADAPTIVE LIFECYCLE HOOK
# ============================================================

def learn_from_closed_trade(
    calibration,
    trade,
    result
):
    try:
        if not isinstance(trade,dict):
            return None

        normalized=normalize_trade_result(result)

        direction=trade.get(
            "direction",
            trade.get("side","UNKNOWN")
        )

        regime=trade.get(
            "market_regime",
            trade.get("regime","UNSTABLE")
        )

        setup=trade.get(
            "setup",
            trade.get("setup_type","UNKNOWN")
        )

        sweep=trade.get(
            "sweep",
            "NONE"
        )

        score=safe_float(
            trade.get("score"),
            0.0
        )

        agreement=safe_float(
            trade.get("agreement"),
            0.0
        )

        bucket=update_adaptive_bucket(
            calibration,
            direction,
            regime,
            setup,
            sweep,
            score,
            agreement,
            normalized["r"],
            normalized["status"]
        )

        save_calibration(calibration)

        return {
            "status":normalized["status"],
            "r":normalized["r"],
            "bucket_samples":int(
                bucket.get("samples",0)
            ),
            "expectancy":safe_float(
                bucket.get("expectancy")
            ),
            "win_rate":safe_float(
                bucket.get("win_rate")
            ),
        }

    except Exception:
        return None
# ============================================================
# DATA / INDICATORS / FEATURE ENGINEERING
# ============================================================

def utc_iso():
    return datetime.now(timezone.utc).isoformat()


def now_ts():
    return time.time()


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        x=float(value)
        if not math.isfinite(x):
            return default
        return x
    except Exception:
        return default


def clamp(value, low=0.0, high=1.0):
    try:
        return max(low, min(high, safe_float(value, low)))
    except Exception:
        return low


def atomic_json_save(path, obj):
    tmp=path+".tmp"

    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(
            obj,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True
        )

    os.replace(tmp,path)


def load_json(path, default):
    try:
        if not os.path.exists(path):
            return copy.deepcopy(default)

        with open(path,"r",encoding="utf-8") as f:
            obj=json.load(f)

        return obj
    except Exception:
        return copy.deepcopy(default)


def load_state():
    default={
        "version":8,
        "market_state":"UNSTABLE",
        "market_state_candidate":"UNSTABLE",
        "market_state_candidate_count":0,
        "market_state_changed_at":None,
        "last_signal":None,
        "last_signal_ts":0,
        "last_direction":None,
        "last_scan_ts":0,
        "active_trade":None,
        "trade_history":[],
        "extension_count":0,
        "last_extension_ts":0,
        "last_news":{},
        "adaptive_last_update":None,
    }

    state=load_json(STATE_FILE,default)

    if not isinstance(state,dict):
        state=default

    state.setdefault("version",8)
    state.setdefault("market_state","UNSTABLE")
    state.setdefault("market_state_candidate","UNSTABLE")
    state.setdefault("market_state_candidate_count",0)
    state.setdefault("active_trade",None)
    state.setdefault("trade_history",[])
    state.setdefault("extension_count",0)
    state.setdefault("last_extension_ts",0)

    return state


def save_state(state):
    atomic_json_save(STATE_FILE,state)


def ensure_csv():
    folder=os.path.dirname(CSV_LOG_FILE)

    if folder:
        os.makedirs(folder,exist_ok=True)

    if not os.path.exists(CSV_LOG_FILE):
        with open(
            CSV_LOG_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:
            writer=csv.writer(f)

            writer.writerow([
                "ts",
                "signal_id",
                "direction",
                "setup",
                "regime",
                "score",
                "agreement",
                "entry",
                "stop",
                "target",
                "rr",
                "adaptive_adjustment",
                "adaptive_samples",
                "adaptive_expectancy",
                "status",
            ])


def csv_log(row):
    try:
        ensure_csv()

        with open(
            CSV_LOG_FILE,
            "a",
            newline="",
            encoding="utf-8"
        ) as f:
            csv.writer(f).writerow(row)

    except Exception:
        pass


def journal(event, **payload):
    obj={
        "ts":utc_iso(),
        "event":event,
    }

    obj.update(payload)

    try:
        with open(
            JOURNAL_FILE,
            "a",
            encoding="utf-8"
        ) as f:
            f.write(
                json.dumps(
                    obj,
                    ensure_ascii=False,
                    default=str
                )+"\n"
            )
    except Exception:
        pass


# ============================================================
# COINBASE REST
# ============================================================

def coinbase_get(path,params=None):
    url=REST+path

    response=session.get(
        url,
        params=params or {},
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()
    return response.json()


def fetch_candles(granularity,limit):
    end=datetime.now(timezone.utc)
    seconds=int(granularity)

    start=end-timedelta(
        seconds=seconds*limit
    )

    params={
        "start":start.isoformat(),
        "end":end.isoformat(),
        "granularity":seconds,
    }

    raw=coinbase_get(
        f"/products/{PRODUCT}/candles",
        params
    )

    if not raw:
        return pd.DataFrame(
            columns=[
                "time",
                "low",
                "high",
                "open",
                "close",
                "volume"
            ]
        )

    rows=[]

    for x in raw:
        try:
            rows.append([
                pd.to_datetime(
                    safe_float(x[0]),
                    unit="s",
                    utc=True
                ),
                safe_float(x[1]),
                safe_float(x[2]),
                safe_float(x[3]),
                safe_float(x[4]),
                safe_float(x[5]),
            ])
        except Exception:
            continue

    df=pd.DataFrame(
        rows,
        columns=[
            "time",
            "low",
            "high",
            "open",
            "close",
            "volume"
        ]
    )

    if df.empty:
        return df

    df=df.drop_duplicates("time")
    df=df.sort_values("time")
    df=df.reset_index(drop=True)

    return df


def fetch_market_frames():
    return {
        "5m":fetch_candles(300,HISTORY_5M_BARS),
        "15m":fetch_candles(900,500),
        "1h":fetch_candles(3600,HISTORY_1H_BARS),
        "2h":fetch_candles(7200,700),
        "4h":fetch_candles(14400,500),
    }


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def ema(series,period):
    return series.ewm(
        span=period,
        adjust=False
    ).mean()


def rsi(series,period=14):
    delta=series.diff()

    gain=delta.clip(lower=0)
    loss=-delta.clip(upper=0)

    avg_gain=gain.ewm(
        alpha=1/period,
        adjust=False
    ).mean()

    avg_loss=loss.ewm(
        alpha=1/period,
        adjust=False
    ).mean()

    rs=avg_gain/avg_loss.replace(0,np.nan)

    out=100-(100/(1+rs))

    return out.fillna(50.0)


def atr(df,period=14):
    prev_close=df["close"].shift(1)

    tr=pd.concat([
        df["high"]-df["low"],
        (df["high"]-prev_close).abs(),
        (df["low"]-prev_close).abs(),
    ],axis=1).max(axis=1)

    return tr.ewm(
        alpha=1/period,
        adjust=False
    ).mean()


def prepare(df):
    if df is None or df.empty:
        return pd.DataFrame()

    d=df.copy()

    d["ema9"]=ema(d["close"],9)
    d["ema21"]=ema(d["close"],21)
    d["ema50"]=ema(d["close"],50)
    d["ema200"]=ema(d["close"],200)

    d["rsi"]=rsi(d["close"],14)
    d["atr"]=atr(d,14)

    d["ema21_prev"]=d["ema21"].shift(1)

    d["ret1"]=d["close"].pct_change(1)
    d["ret3"]=d["close"].pct_change(3)
    d["ret8"]=d["close"].pct_change(8)

    volume_mean=d["volume"].rolling(
        20,
        min_periods=1
    ).mean()

    d["volume_ratio"]=(
        d["volume"]/
        volume_mean.replace(0,np.nan)
    ).fillna(1.0)

    d["range"]=(d["high"]-d["low"]).abs()

    d["body"]=(d["close"]-d["open"]).abs()

    d["body_ratio"]=(
        d["body"]/
        d["range"].replace(0,np.nan)
    ).fillna(0.0)

    d["high20"]=d["high"].rolling(
        SWEEP_LOOKBACK
    ).max().shift(1)

    d["low20"]=d["low"].rolling(
        SWEEP_LOOKBACK
    ).min().shift(1)

    d["high5"]=d["high"].rolling(5).max().shift(1)
    d["low5"]=d["low"].rolling(5).min().shift(1)

    return d.replace(
        [np.inf,-np.inf],
        np.nan
    ).ffill().bfill()


# ============================================================
# ORDER FLOW
# ============================================================

def reset_flow():
    global flow

    flow={
        "bids":{},
        "asks":{},
        "delta":0.0,
        "large":0.0,
        "buy":0.0,
        "sell":0.0,
        "trade_count":0,
        "updated":0,
    }


def fetch_recent_trades():
    try:
        raw=coinbase_get(
            f"/products/{PRODUCT}/trades"
        )

        if not isinstance(raw,list):
            return []

        return raw[:FLOW_TRADE_LIMIT]

    except Exception as e:
        journal(
            "FLOW_ERROR",
            error=repr(e)
        )
        return []


def update_order_flow():
    global flow

    trades=fetch_recent_trades()

    if not trades:
        return flow

    buy=0.0
    sell=0.0
    delta=0.0
    large=0.0
    count=0

    for trade in trades:
        try:
            size=safe_float(
                trade.get("size"),
                0.0
            )

            side=str(
                trade.get("side","")
            ).lower()

            if size<=0:
                continue

            count+=1

            if size>=FLOW_LARGE_TRADE_BTC:
                large+=size

            if side=="buy":
                buy+=size
                delta+=size
            elif side=="sell":
                sell+=size
                delta-=size

        except Exception:
            continue

    flow["buy"]=buy
    flow["sell"]=sell
    flow["delta"]=delta
    flow["large"]=large
    flow["trade_count"]=count
    flow["updated"]=now_ts()

    return flow


def orderflow_direction(direction):
    age=now_ts()-safe_float(
        flow.get("updated"),
        0
    )

    if age>ORDERFLOW_STALE:
        return .50

    buy=safe_float(flow.get("buy"))
    sell=safe_float(flow.get("sell"))

    total=max(buy+sell,1e-9)

    if direction=="LONG":
        return clamp(
            .50+.50*((buy-sell)/total)
        )

    return clamp(
        .50+.50*((sell-buy)/total)
    )


# ============================================================
# LIQUIDITY SWEEP DETECTION
# ============================================================

def detect_sweep(df):
    d=prepare(df)

    if d.empty or len(d)<SWEEP_LOOKBACK+3:
        return {
            "type":"NONE",
            "age":999,
            "penetration":0.0,
            "reclaim":0.0,
            "quality":0.0,
            "confirmation":0.0,
            "direction":None,
            "reason":"insufficient_data",
        }

    i=len(d)-1

    row=d.iloc[i]

    atr_value=max(
        safe_float(row.atr),
        safe_float(row.close)*.0001
    )

    prior_high=safe_float(
        d.iloc[i-1].high20,
        row.high
    )

    prior_low=safe_float(
        d.iloc[i-1].low20,
        row.low
    )

    buy_sweep=False
    sell_sweep=False

    buy_pen=0.0
    sell_pen=0.0

    if row.low<prior_low:
        buy_sweep=True
        buy_pen=(prior_low-row.low)/atr_value

    if row.high>prior_high:
        sell_sweep=True
        sell_pen=(row.high-prior_high)/atr_value

    direction=None
    penetration=0.0
    reclaim=0.0

    if buy_sweep and not sell_sweep:
        direction="LONG"
        penetration=buy_pen

        reclaim=clamp(
            (row.close-row.low)/
            max(row.high-row.low,atr_value)
        )

    elif sell_sweep and not buy_sweep:
        direction="SHORT"
        penetration=sell_pen

        reclaim=clamp(
            (row.high-row.close)/
            max(row.high-row.low,atr_value)
        )

    elif buy_sweep and sell_sweep:
        # Both sides swept in the same candle = ambiguous liquidity.
        return {
            "type":"OPPOSITE_SWEEP",
            "age":0,
            "penetration":max(
                buy_pen,
                sell_pen
            ),
            "reclaim":0.0,
            "quality":0.0,
            "confirmation":0.0,
            "direction":None,
            "reason":"both_sides_swept",
        }

    if direction is None:
        return {
            "type":"NONE",
            "age":999,
            "penetration":0.0,
            "reclaim":0.0,
            "quality":0.0,
            "confirmation":0.0,
            "direction":None,
            "reason":"no_sweep",
        }

    penetration_quality=clamp(
        penetration/
        max(SWEEP_MIN_PENETRATION_ATR*4.0,1e-9)
    )

    reclaim_quality=clamp(
        reclaim/
        max(SWEEP_MIN_RECLAIM,1e-9)
    )

    body_quality=clamp(
        safe_float(row.body_ratio)
    )

    volume_quality=clamp(
        safe_float(row.volume_ratio)/1.5
    )

    quality=clamp(
        .35*penetration_quality+
        .35*reclaim_quality+
        .15*body_quality+
        .15*volume_quality
    )

    confirmation=clamp(
        .35*quality+
        .20*reclaim_quality+
        .15*volume_quality+
        .15*orderflow_direction(direction)+
        .15*(1.0 if (
            (direction=="LONG" and row.close>row.open) or
            (direction=="SHORT" and row.close<row.open)
        ) else 0.0)
    )

    return {
        "type":(
            "BUY_SIDE_SWEEP"
            if direction=="LONG"
            else "SELL_SIDE_SWEEP"
        ),
        "age":0,
        "penetration":penetration,
        "reclaim":reclaim,
        "quality":quality,
        "confirmation":confirmation,
        "direction":direction,
        "reason":"liquidity_sweep_detected",
    }


# ============================================================
# MULTI-TIMEFRAME SWEEP CONFIRMATION
# ============================================================

def sweep_confirmation(F,sweep):
    if not sweep or sweep.get("direction") is None:
        return 0.0

    direction=sweep["direction"]

    d15=prepare(F["15m"])
    d5=prepare(F["5m"])

    if d15.empty or d5.empty:
        return 0.0

    r15=d15.iloc[-1]
    r5=d5.iloc[-1]

    if direction=="LONG":
        structure=clamp(
            .50*(1 if r15.close>r15.ema21 else 0)+
            .50*(1 if r5.close>r5.ema21 else 0)
        )

        momentum=clamp(
            .50*(1 if r15.ret3>0 else 0)+
            .50*(1 if r5.ret3>0 else 0)
        )

    else:
        structure=clamp(
            .50*(1 if r15.close<r15.ema21 else 0)+
            .50*(1 if r5.close<r5.ema21 else 0)
        )

        momentum=clamp(
            .50*(1 if r15.ret3<0 else 0)+
            .50*(1 if r5.ret3<0 else 0)
        )

    of=orderflow_direction(direction)

    reversal=clamp(
        safe_float(sweep.get("reclaim"))
    )

    retest=clamp(
        .50*structure+
        .50*reversal
    )

    volume=clamp(
        (
            safe_float(r15.volume_ratio)+
            safe_float(r5.volume_ratio)
        )/3.0
    )

    confirmation=(
        SWEEP_CONFIRM_WEIGHTS["sweep_quality"]*
        safe_float(sweep.get("quality"))+
        SWEEP_CONFIRM_WEIGHTS["structure"]*
        structure+
        SWEEP_CONFIRM_WEIGHTS["momentum"]*
        momentum+
        SWEEP_CONFIRM_WEIGHTS["orderflow"]*
        of+
        SWEEP_CONFIRM_WEIGHTS["reversal"]*
        reversal+
        SWEEP_CONFIRM_WEIGHTS["retest"]*
        retest+
        SWEEP_CONFIRM_WEIGHTS["volume"]*
        volume
    )

    return clamp(confirmation)


# ============================================================
# IMPULSE / CONTINUATION DETECTION
# ============================================================

def detect_impulse_continuation(F,direction):
    d=prepare(F["1h"])

    if d.empty or len(d)<CONT_LOOKBACK+3:
        return {
            "ready":False,
            "direction":direction,
            "impulse":0.0,
            "pullback":0.0,
            "score":0.0,
            "reason":"insufficient_data",
        }

    recent=d.iloc[-CONT_IMPULSE_BARS:]
    atr_value=max(
        safe_float(d.iloc[-1].atr),
        safe_float(d.iloc[-1].close)*.0001
    )

    start=safe_float(recent.iloc[0].open)
    end=safe_float(recent.iloc[-1].close)

    impulse_abs=abs(end-start)/atr_value

    if direction=="LONG":
        directional=clamp(
            (end-start)/
            max(CONT_IMPULSE_ATR*atr_value,1e-9)
        )
    else:
        directional=clamp(
            (start-end)/
            max(CONT_IMPULSE_ATR*atr_value,1e-9)
        )

    lookback_high=safe_float(
        d["high"].iloc[-CONT_LOOKBACK:].max()
    )

    lookback_low=safe_float(
        d["low"].iloc[-CONT_LOOKBACK:].min()
    )

    if direction=="LONG":
        pullback=clamp(
            (lookback_high-d.iloc[-1].close)/
            max(
                lookback_high-lookback_low,
                atr_value
            )
        )
    else:
        pullback=clamp(
            (d.iloc[-1].close-lookback_low)/
            max(
                lookback_high-lookback_low,
                atr_value
            )
        )

    structure=(
        1.0
        if (
            direction=="LONG" and
            d.iloc[-1].close>d.iloc[-1].ema21
        )
        or (
            direction=="SHORT" and
            d.iloc[-1].close<d.iloc[-1].ema21
        )
        else 0.0
    )

    ready=(
        impulse_abs>=CONT_IMPULSE_ATR and
        directional>=.75 and
        CONT_MIN_PULLBACK<=pullback<=CONT_MAX_PULLBACK and
        structure>=1.0
    )

    score=clamp(
        .45*directional+
        .25*clamp(impulse_abs/3.0)+
        .20*(
            1.0-
            abs(
                pullback-
                ((CONT_MIN_PULLBACK+CONT_MAX_PULLBACK)/2)
            )/
            .50
        )+
        .10*structure
    )

    return {
        "ready":bool(ready),
        "direction":direction,
        "impulse":impulse_abs,
        "directional":directional,
        "pullback":pullback,
        "structure":structure,
        "score":score,
        "reason":"continuation_candidate" if ready else "not_ready",
    }


# ============================================================
# NEWS LAYER
# ============================================================

def news_sentiment_score(text):
    text=str(text or "").lower()

    bullish_words=[
        "surge","bullish","rally","adoption",
        "approval","approved","inflow",
        "breakout","accumulation","positive",
        "institutional","buy"
    ]

    bearish_words=[
        "crash","bearish","hack","lawsuit",
        "outflow","ban","ban","liquidation",
        "selloff","negative","exploit",
        "fraud","regulation","decline"
    ]

    bull=sum(
        1 for x in bullish_words
        if x in text
    )

    bear=sum(
        1 for x in bearish_words
        if x in text
    )

    total=max(
        bull+bear,
        1
    )

    return clamp(
        .50+
        .50*((bull-bear)/total)
    )


def fetch_news():
    if not NEWS_ENABLED:
        return {
            "enabled":False,
            "articles":[],
            "score":.50,
            "confirmation":.50,
            "error":None,
        }

    params={
        "currencies":"BTC",
        "kind":"news",
        "limit":NEWS_MAX_ARTICLES,
    }

    if NEWS_API_KEY:
        params["auth_token"]=NEWS_API_KEY
        params["api_key"]=NEWS_API_KEY

    try:
        response=session.get(
            NEWS_API_URL,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data=response.json()

        results=[]

        if isinstance(data,dict):
            results=(
                data.get("results") or
                data.get("data") or
                []
            )

        if not isinstance(results,list):
            results=[]

        articles=[]

        cutoff=(
            datetime.now(timezone.utc)-
            timedelta(minutes=NEWS_LOOKBACK_MIN)
        )

        for item in results:
            if not isinstance(item,dict):
                continue

            title=str(
                item.get("title") or
                item.get("headline") or
                ""
            )

            published=(
                item.get("published_at") or
                item.get("published") or
                item.get("created_at")
            )
            # ============================================================
# SIGNAL SCORING / AGREEMENT
# ============================================================

def calculate_agreement(parts):
    vals=[]

    for value in parts.values():
        try:
            vals.append(clamp(value))
        except Exception:
            pass

    if not vals:
        return 0.0

    mean=float(np.mean(vals))

    dispersion=float(
        np.std(vals)
    )

    return clamp(
        mean*(1.0-dispersion)
    )


def calculate_base_score(features,direction):
    weights={
        "trend":15,
        "structure":20,
        "price":15,
        "volume":10,
        "momentum":10,
        "retest":12,
        "orderflow":13,
        "reversal":10,
    }

    score=0.0
    maximum=sum(weights.values())

    for key,weight in weights.items():
        score+=clamp(
            features.get(key,.0)
        )*weight

    return clamp(
        score,
        0.0,
        float(maximum)
    )


def build_signal_features(F,direction,sweep,continuation):
    execution=execution_score(
        F,
        direction
    )

    sweep_confirmation_value=sweep_confirmation(
        F,
        sweep
    )

    if direction=="LONG":
        reversal=clamp(
            .50*safe_float(
                sweep.get("reclaim")
            )+
            .50*(
                1.0
                if safe_float(
                    F["15m"]["bull"]
                    if isinstance(F["15m"],dict)
                    else 0
                )>.50
                else 0.0
            )
        )
    else:
        reversal=clamp(
            .50*safe_float(
                sweep.get("reclaim")
            )+
            .50*(
                1.0
                if safe_float(
                    F["15m"]["bear"]
                    if isinstance(F["15m"],dict)
                    else 0
                )>.50
                else 0.0
            )
        )

    return {
        "trend":execution["trend"],
        "structure":execution["structure"],
        "price":execution["price"],
        "volume":execution["volume"],
        "momentum":execution["momentum"],
        "retest":clamp(
            .50*execution["price"]+
            .50*safe_float(
                continuation.get("score")
            )
        ),
        "orderflow":execution["orderflow"],
        "reversal":clamp(
            .50*reversal+
            .50*sweep_confirmation_value
        ),
        "sweep_confirmation":sweep_confirmation_value,
        "continuation_score":safe_float(
            continuation.get("score")
        ),
    }


def direction_timeframe_snapshot(F,direction):
    result={}

    for tf in ("4h","2h","1h","15m"):
        item=F[tf]

        result[tf]=(
            safe_float(item.get("bull"))
            if direction=="LONG"
            else safe_float(item.get("bear"))
        )

    return result


# ============================================================
# RISK / TARGET ENGINE
# ============================================================

def calculate_trade_levels(
    df,
    direction,
    entry,
    regime,
    setup
):
    d=prepare(df)

    if d.empty:
        atr_value=entry*.005
        recent_high=entry+atr_value
        recent_low=entry-atr_value
    else:
        r=d.iloc[-1]

        atr_value=max(
            safe_float(r.atr),
            entry*.0005
        )

        recent_high=safe_float(
            d["high"].tail(20).max(),
            entry+atr_value
        )

        recent_low=safe_float(
            d["low"].tail(20).min(),
            entry-atr_value
        )

    if setup=="SWEEP_REVERSAL":
        stop_distance=atr_value*1.35
        target_multiple=2.0

    elif setup=="POST_IMPULSE_CONTINUATION":
        stop_distance=atr_value*1.25
        target_multiple=2.05

    elif setup=="TREND_CONTINUATION":
        stop_distance=atr_value*1.45
        target_multiple=1.75

    else:
        stop_distance=atr_value*1.50
        target_multiple=1.60

    if direction=="LONG":
        structural_stop=min(
            recent_low-atr_value*.10,
            entry-stop_distance
        )

        stop=max(
            entry*.0005,
            entry-structural_stop
        )

        target=entry+stop*target_multiple
        stop_price=entry-stop

    else:
        structural_stop=max(
            recent_high+atr_value*.10,
            entry+stop_distance
        )

        stop=max(
            entry*.0005,
            structural_stop-entry
        )

        target=entry-stop*target_multiple
        stop_price=entry+stop

    rr=abs(
        target-entry
    )/max(abs(entry-stop_price),1e-9)

    return {
        "entry":entry,
        "stop":stop_price,
        "target":target,
        "rr":rr,
        "atr":atr_value,
    }


# ============================================================
# SETUP SELECTION
# ============================================================

def select_setup(
    direction,
    market_state,
    sweep,
    continuation
):
    regime=market_state.get(
        "regime",
        "UNSTABLE"
    )

    sweep_direction=sweep.get(
        "direction"
    )

    sweep_quality=safe_float(
        sweep.get("quality")
    )

    sweep_confirmation_value=safe_float(
        sweep.get("confirmation")
    )

    if (
        sweep_direction==direction and
        sweep_quality>=SWEEP_REVERSAL_MIN_QUALITY and
        sweep_confirmation_value>=SWEEP_REVERSAL_STRONG_CONFIRMATION
    ):
        return "SWEEP_REVERSAL"

    if continuation.get("ready"):
        return "POST_IMPULSE_CONTINUATION"

    if regime in (
        "BULLISH_TREND",
        "BEARISH_TREND",
        "BULLISH_PULLBACK",
        "BEARISH_PULLBACK",
    ):
        return "TREND_CONTINUATION"

    return "STRUCTURE_CONTINUATION"


# ============================================================
# SIGNAL GENERATOR
# ============================================================

def generate_signal(
    F,
    state,
    calibration,
    news
):
    market_state=market_state_engine(
        F,
        state
    )

    state["market_state_confidence"]=safe_float(
        market_state.get("confidence")
    )

    candidates=[]

    for direction in ("LONG","SHORT"):
        regime_gate=market_state_gate(
            market_state,
            direction,
            MIN_SCORE,
            MIN_AGREEMENT
        )

        if not regime_gate["allowed"]:
            continue

        sweep=detect_sweep(
            F["5m"]
        )

        continuation=detect_impulse_continuation(
            F,
            direction
        )

        setup=select_setup(
            direction,
            market_state,
            sweep,
            continuation
        )

        features=build_signal_features(
            F,
            direction,
            sweep,
            continuation
        )

        base_score=calculate_base_score(
            features,
            direction
        )

        state_adjusted_score=apply_market_state_adjustment(
            base_score,
            direction,
            market_state
        )

        tf_snapshot=direction_timeframe_snapshot(
            F,
            direction
        )

        agreement=calculate_agreement(
            {
                "trend":features["trend"],
                "structure":features["structure"],
                "price":features["price"],
                "momentum":features["momentum"],
                "orderflow":features["orderflow"],
                "timeframe_4h":tf_snapshot["4h"],
                "timeframe_2h":tf_snapshot["2h"],
                "timeframe_1h":tf_snapshot["1h"],
                "timeframe_15m":tf_snapshot["15m"],
            }
        )

        adaptive=adaptive_context(
            calibration,
            direction,
            market_state.get(
                "regime",
                "UNSTABLE"
            ),
            setup,
            sweep.get("type","NONE"),
            state_adjusted_score,
            agreement
        )

        adaptive_adjustment=adaptive_score_adjustment(
            adaptive
        )

        final_score=clamp(
            state_adjusted_score+
            adaptive_adjustment,
            0.0,
            105.0
        )

        # News is confirmation, never the sole entry trigger.
        news_score=safe_float(
            news.get("score"),
            .50
        )

        if direction=="LONG":
            news_alignment=news_score
        else:
            news_alignment=1.0-news_score

        if (
            news.get("enabled") and
            safe_float(news.get("confirmation"))>=
            NEWS_CONFIRMATION_THRESHOLD
        ):
            if news_alignment<NEWS_MIN_RELEVANCE:
                final_score-=2.0

        final_score=clamp(
            final_score,
            0.0,
            105.0
        )

        market_bias=regime_direction_bias(
            market_state.get(
                "regime",
                "UNSTABLE"
            )
        )

        if market_bias in ("LONG","SHORT"):
            if market_bias!=direction:
                continue

        if adaptive_should_block(adaptive):
            continue

        if final_score<MIN_SCORE:
            continue

        if agreement<MIN_AGREEMENT:
            continue

        if safe_float(
            features.get("sweep_confirmation")
        )<SWEEP_MIN_CONFIRMATION:

            # A sweep is optional for normal continuation.
            if setup=="SWEEP_REVERSAL":
                continue

        entry=safe_float(
            F["5m"]["close"],
            0.0
        )

        if entry<=0:
            continue

        levels=calculate_trade_levels(
            F["5m"],
            direction,
            entry,
            market_state.get(
                "regime"
            ),
            setup
        )

        signal_id=(
            f"{direction}_{setup}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        )

        candidates.append({
            "signal_id":signal_id,
            "direction":direction,
            "setup":setup,
            "market_regime":market_state.get(
                "regime",
                "UNSTABLE"
            ),
            "regime_confidence":safe_float(
                market_state.get("confidence")
            ),
            "score":final_score,
            "base_score":base_score,
            "agreement":agreement,
            "adaptive_adjustment":adaptive_adjustment,
            "adaptive_samples":adaptive.get("samples",0),
            "adaptive_expectancy":adaptive.get(
                "expectancy",
                0.0
            ),
            "adaptive_win_rate":adaptive.get(
                "win_rate",
                0.0
            ),
            "sweep":sweep.get(
                "type",
                "NONE"
            ),
            "sweep_quality":sweep.get(
                "quality",
                0.0
            ),
            "sweep_confirmation":features.get(
                "sweep_confirmation",
                0.0
            ),
            "continuation":continuation,
            "features":features,
            "entry":levels["entry"],
            "stop":levels["stop"],
            "target":levels["target"],
            "rr":levels["rr"],
            "atr":levels["atr"],
            "created_at":utc_iso(),
            "news_score":news_score,
            "news_alignment":news_alignment,
        })

    if not candidates:
        return None,market_state

    candidates.sort(
        key=lambda x:(
            safe_float(x["score"]),
            safe_float(x["agreement"])
        ),
        reverse=True
    )

    return candidates[0],market_state


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TOKEN or not CHAT_ID:
        return False

    url=(
        "https://api.telegram.org/bot"+
        TOKEN+
        "/sendMessage"
    )

    payload={
        "chat_id":CHAT_ID,
        "text":text,
    }

    try:
        response=session.post(
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


def format_signal(signal):
    direction=signal["direction"]

    icon="🟢" if direction=="LONG" else "🔴"

    adaptive_line=(
        f"Adaptive: "
        f"{signal['adaptive_adjustment']:+.2f} | "
        f"N={signal['adaptive_samples']} | "
        f"E={signal['adaptive_expectancy']:.3f}"
    )

    return (
        f"{icon} {direction} ENTRY\n\n"
        f"Entry: {signal['entry']:.2f}\n"
        f"Stop Loss: {signal['stop']:.2f}\n"
        f"Target: {signal['target']:.2f}\n"
        f"RR: {signal['rr']:.2f}\n\n"
        f"Setup: {signal['setup']}\n"
        f"Regime: {signal['market_regime']} "
        f"({signal['regime_confidence']:.0%})\n"
        f"Sweep: {signal['sweep']}\n\n"
        f"Score: {signal['score']:.1f}/105\n"
        f"Agreement: {signal['agreement']:.0%}\n"
        f"{adaptive_line}\n\n"
        f"Trend: {signal['features']['trend']:.2f} | "
        f"Structure: {signal['features']['structure']:.2f}\n"
        f"Price: {signal['features']['price']:.2f} | "
        f"Volume: {signal['features']['volume']:.2f}\n"
        f"Momentum: {signal['features']['momentum']:.2f} | "
        f"Orderflow: {signal['features']['orderflow']:.2f}\n"
        f"Reversal: {signal['features']['reversal']:.2f}"
    )


def format_wait(reason,market_state=None):
    regime="UNSTABLE"

    if market_state:
        regime=market_state.get(
            "regime",
            "UNSTABLE"
        )

    return (
        "⏸ WAIT\n\n"
        f"Reason: {reason}\n"
        f"Market State: {regime}"
    )


# ============================================================
# TRADE LIFECYCLE
# ============================================================

def create_active_trade(signal):
    return {
        "signal_id":signal["signal_id"],
        "direction":signal["direction"],
        "setup":signal["setup"],
        "market_regime":signal["market_regime"],
        "entry":signal["entry"],
        "stop":signal["stop"],
        "target":signal["target"],
        "rr":signal["rr"],
        "score":signal["score"],
        "agreement":signal["agreement"],
        "sweep":signal["sweep"],
        "adaptive_adjustment":signal[
            "adaptive_adjustment"
        ],
        "adaptive_samples":signal[
            "adaptive_samples"
        ],
        "adaptive_expectancy":signal[
            "adaptive_expectancy"
        ],
        "opened_at":utc_iso(),
        "extension_count":0,
        "status":"OPEN",
    }


def check_active_trade(
    active,
    price,
    calibration,
    state
):
    if not active:
        return None

    direction=active["direction"]

    entry=safe_float(
        active["entry"]
    )

    stop=safe_float(
        active["stop"]
    )

    target=safe_float(
        active["target"]
    )

    result=None

    if direction=="LONG":
        if price<=stop:
            result={
                "status":"SL_HIT",
                "r":-1.0,
            }

        elif price>=target:
            result={
                "status":"TARGET_HIT",
                "r":safe_float(
                    active.get("rr"),
                    1.0
                ),
            }

    else:
        if price>=stop:
            result={
                "status":"SL_HIT",
                "r":-1.0,
            }

        elif price<=target:
            result={
                "status":"TARGET_HIT",
                "r":safe_float(
                    active.get("rr"),
                    1.0
                ),
            }

    if result is None:
        return None

    learning=learn_from_closed_trade(
        calibration,
        active,
        result
    )

    active["status"]=result["status"]
    active["closed_at"]=utc_iso()
    active["result_r"]=result["r"]
    active["learning"]=learning

    state.setdefault(
        "trade_history",
        []
    ).append(
        copy.deepcopy(active)
    )

    state["trade_history"]=state[
        "trade_history"
    ][-200:]

    state["active_trade"]=None

    journal(
        "TRADE_CLOSED",
        signal_id=active.get("signal_id"),
        direction=direction,
        status=result["status"],
        r=result["r"],
        learning=learning,
    )

    return result


# ============================================================
# DYNAMIC TARGET EXTENSION
# ============================================================

def maybe_extend_target(
    active,
    F,
    state
):
    if not active:
        return False

    if active.get("setup") not in (
        "TREND_CONTINUATION",
        "POST_IMPULSE_CONTINUATION",
    ):
        return False

    count=int(
        active.get("extension_count",0)
    )

    if count>=MAX_TARGET_EXTENSIONS:
        return False

    elapsed=(
        now_ts()-
        safe_float(
            state.get("last_extension_ts"),
            0
        )
    )

    if elapsed<EXTENSION_COOLDOWN_MIN*60:
        return False

    d=prepare(F["5m"])

    if d.empty:
        return False

    r=d.iloc[-1]

    atr_value=max(
        safe_float(r.atr),
        safe_float(r.close)*.0005
    )

    direction=active["direction"]

    current_target=safe_float(
        active["target"]
    )

    current_price=safe_float(
        r.close
    )

    if direction=="LONG":
        favorable=current_price>current_target-atr_value*.50

        if not favorable:
            return False

        active["target"]=(
            current_target+
            atr_value*1.25
        )

    else:
        favorable=current_price<current_target+atr_value*.50

        if not favorable:
            return False

        active["target"]=(
            current_target-
            atr_value*1.25
        )

    active["extension_count"]=count+1

    state["extension_count"]=count+1
    state["last_extension_ts"]=now_ts()

    journal(
        "TARGET_EXTENDED",
        signal_id=active.get("signal_id"),
        direction=direction,
        new_target=active["target"],
        extension_count=active["extension_count"],
    )

    return True


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(state):
    last_ts=safe_float(
        state.get("last_signal_ts"),
        0
    )

    if last_ts<=0:
        return False

    return (
        now_ts()-last_ts
    ) < COOLDOWN_MIN*60


# ============================================================
# MAIN SCAN
# ============================================================

def run_once():
    state=load_state()
    calibration=load_calibration()

    journal(
        "SCAN_START",
        version=8
    )

    try:
        F=fetch_market_frames()

        for tf in (
            "5m",
            "15m",
            "1h",
            "2h",
            "4h",
        ):
            if F[tf] is None or F[tf].empty:
                raise RuntimeError(
                    f"missing timeframe data: {tf}"
                )

        update_order_flow()

        news=fetch_news()

        state["last_news"]={
            "score":safe_float(
                news.get("score"),
                .50
            ),
            "confirmation":safe_float(
                news.get("confirmation"),
                .50
            ),
            "updated_at":utc_iso(),
        }

        # ----------------------------------------------------
        # Existing trade gets lifecycle priority.
        # ----------------------------------------------------
        if state.get("active_trade"):

            price=safe_float(
                F["5m"].iloc[-1]["close"]
            )

            result=check_active_trade(
                state["active_trade"],
                price,
                calibration,
                state
            )

            if result:
                msg=(
                    f"📘 TRADE CLOSED\n\n"
                    f"Status: {result['status']}\n"
                    f"R: {result['r']:+.2f}"
                )

                telegram_send(msg)

                save_state(state)

                journal(
                    "SCAN_END",
                    action="trade_closed",
                    status=result["status"]
                )

                return

            extended=maybe_extend_target(
                state["active_trade"],
                F,
                state
            )

            if extended:
                telegram_send(
                    "🎯 TARGET EXTENDED\n\n"
                    f"New Target: "
                    f"{state['active_trade']['target']:.2f}"
                )

            state["last_scan_ts"]=now_ts()

            save_state(state)

            journal(
                "SCAN_END",
                action="active_trade_monitor"
            )

            return

        # ----------------------------------------------------
        # New signal generation.
        # ----------------------------------------------------
        if cooldown_active(state):

            state["last_scan_ts"]=now_ts()
            save_state(state)

            msg=format_wait(
                "cooldown_active",
                {
                    "regime":state.get(
                        "market_state",
                        "UNSTABLE"
                    )
                }
            )

            telegram_send(msg)

            journal(
                "WAIT",
                reason="cooldown_active"
            )

            return

        signal,market_state=generate_signal(
            F,
            state,
            calibration,
            news
        )

        state["last_scan_ts"]=now_ts()

        if signal is None:

            save_state(state)

            msg=format_wait(
                "no_valid_setup",
                market_state
            )

            telegram_send(msg)

            journal(
                "WAIT",
                reason="no_valid_setup",
                market_state=market_state
            )

            return

        # ----------------------------------------------------
        # Final execution gate.
        # ----------------------------------------------------
        execution_confidence=clamp(
            .55*safe_float(
                signal["agreement"]
            )+
            .45*(
                safe_float(
                    signal["score"]
                )/100.0
            )
        )

        if execution_confidence<EXECUTION_MIN_SCORE:

            save_state(state)

            telegram_send(
                format_wait(
                    "execution_confidence_below_gate",
                    market_state
                )
            )

            journal(
                "WAIT",
                reason="execution_confidence_below_gate",
                confidence=execution_confidence
            )

            return

        if signal["score"]<MIN_SCORE:
            save_state(state)

            telegram_send(
                format_wait(
                    "score_below_threshold",
                    market_state
                )
            )

            return

        if signal["agreement"]<MIN_AGREEMENT:
            save_state(state)

            telegram_send(
                format_wait(
                    "agreement_below_threshold",
                    market_state
                )
            )

            return

        # ----------------------------------------------------
        # Create active trade.
        # ----------------------------------------------------
        active=create_active_trade(
            signal
        )

        state["active_trade"]=active
        state["last_signal"]=signal["signal_id"]
        state["last_signal_ts"]=now_ts()
        state["last_direction"]=signal["direction"]

        journal(
            "SIGNAL_CREATED",
            signal_id=signal["signal_id"],
            direction=signal["direction"],
            setup=signal["setup"],
            regime=signal["market_regime"],
            score=signal["score"],
            agreement=signal["agreement"],
            adaptive_adjustment=signal[
                "adaptive_adjustment"
            ],
            adaptive_samples=signal[
                "adaptive_samples"
            ],
            adaptive_expectancy=signal[
                "adaptive_expectancy"
            ],
            entry=signal["entry"],
            stop=signal["stop"],
            target=signal["target"],
            rr=signal["rr"],
        )

        csv_log([
            utc_iso(),
            signal["signal_id"],
            signal["direction"],
            signal["setup"],
            signal["market_regime"],
            round(signal["score"],4),
            round(signal["agreement"],4),
            round(signal["entry"],4),
            round(signal["stop"],4),
            round(signal["target"],4),
            round(signal["rr"],4),
            round(
                signal["adaptive_adjustment"],
                4
            ),
            signal["adaptive_samples"],
            round(
                signal["adaptive_expectancy"],
                6
            ),
            "OPEN",
        ])

        telegram_send(
            format_signal(signal)
        )

        save_state(state)

        journal(
            "SCAN_END",
            action="signal_created"
        )

    except Exception as e:

        journal(
            "FATAL_ERROR",
            error=repr(e),
            traceback=traceback.format_exc()
        )

        try:
            telegram_send(
                "⚠️ BTC V8 BOT ERROR\n\n"+
                repr(e)
            )
        except Exception:
            pass

        raise


# ============================================================
# CLI / GITHUB ACTIONS
# ============================================================

def main():
    parser=argparse.ArgumentParser(
        description=(
            "BTC Adaptive Telegram Bot V8"
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="run one scan and exit"
    )

    args=parser.parse_args()

    if args.once:
        run_once()
        return

    while True:
        started=now_ts()

        try:
            run_once()
        except Exception:
            pass

        elapsed=now_ts()-started

        sleep_for=max(
            1,
            SCAN_SECONDS-elapsed
        )

        time.sleep(sleep_for)


if __name__=="__main__":
    main()

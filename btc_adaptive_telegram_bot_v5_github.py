import os, sys, time, json, math, threading, argparse, traceback, csv, copy
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np

PRODUCT = "BTC-USD"
REST = "https://api.exchange.coinbase.com"
WS_URL = "wss://ws-feed.exchange.coinbase.com"

TOKEN = TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = "7500472109"

STATE_FILE = "btc_v5_state.json"
JOURNAL_FILE = "btc_v5_journal.jsonl"
CALIBRATION_FILE = "btc_v5_calibration.json"
CSV_LOG_FILE = "logs/btc_signals.csv"

SCAN_SECONDS = 60
COOLDOWN_MIN = 45

MIN_SCORE = 62
STRONG_SCORE = 76
MIN_AGREEMENT = 0.58
STRONG_AGREEMENT = 0.68

ORDERFLOW_STALE = 8
WS_WARMUP_SECONDS = 5
REQUEST_TIMEOUT = 20
HISTORY_1H_BARS = 1200
FLOW_TRADE_LIMIT = 100
FLOW_LARGE_TRADE_BTC = 0.25

WEIGHTS = {
    "trend": 15, "structure": 20, "price": 15, "volume": 10,
    "momentum": 10, "retest": 12, "orderflow": 13, "reversal": 10,
}

session = requests.Session()
session.headers.update({"User-Agent": "BTC-Adaptive-Telegram-Bot-V5-GitHub/1.0"})

flow = {
    "bids": {}, "asks": {}, "delta": 0.0, "large": 0.0,
    "buy": 0.0, "sell": 0.0, "updated": 0.0,
}
flow_lock = threading.Lock()
ws_thread = None


def now_ts():
    return time.time()


def iso(ts=None):
    return datetime.fromtimestamp(ts or now_ts(), tz=timezone.utc).isoformat()


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_state():
    s = load_json(STATE_FILE, {})
    if not isinstance(s, dict):
        s = {}
    s.setdefault("last_signal", None)
    s.setdefault("last_signal_ts", 0)
    s.setdefault("last_direction", None)
    s.setdefault("impulse", None)
    s.setdefault("last_scan_ts", 0)
    s.setdefault("flow_snapshot", {})
    return s


def save_state(s):
    save_json(STATE_FILE, s)


def journal(event, **kwargs):
    rec = {"ts": iso(), "event": event, **kwargs}
    try:
        with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("JOURNAL_ERROR", repr(e))


def log_signal_csv(
    timestamp,
    direction,
    score,
    agreement,
    reason,
    plan,
    flow_data,
):
    try:
        os.makedirs("logs", exist_ok=True)

        file_exists = os.path.exists(CSV_LOG_FILE)
        file_empty = file_exists and os.path.getsize(CSV_LOG_FILE) == 0

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
                ])

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
                flow_data.get("trade_count", 0),
            ])

    except Exception as e:
        print("CSV_LOG_ERROR", repr(e))
        journal("CSV_LOG_ERROR", error=repr(e))


def telegram(text):
    if not TOKEN or not CHAT_ID:
        print("TELEGRAM_NOT_CONFIGURED")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        r = session.post(
            url,
            json={"chat_id": CHAT_ID, "text": text},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print("TELEGRAM_ERROR", repr(e))
        journal("TELEGRAM_ERROR", error=repr(e))
        return False


def coinbase_get(path, params=None):
    r = session.get(
        REST + path,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def candles(granularity, limit=300):
    data = coinbase_get(
        f"/products/{PRODUCT}/candles",
        params={"granularity": int(granularity)}
    )
    if not isinstance(data, list):
        raise RuntimeError("Unexpected candle response")

    rows = []

    for x in data[:limit]:
        if len(x) < 6:
            continue

        rows.append({
            "ts": pd.to_datetime(int(x[0]), unit="s", utc=True),
            "low": safe_float(x[1]),
            "high": safe_float(x[2]),
            "open": safe_float(x[3]),
            "close": safe_float(x[4]),
            "volume": safe_float(x[5]),
        })

    df = pd.DataFrame(rows)

    if df.empty:
        raise RuntimeError(
            f"No candles returned for granularity={granularity}"
        )

    return (
        df.sort_values("ts")
        .drop_duplicates("ts")
        .reset_index(drop=True)
    )


def candles_1h_history(total=HISTORY_1H_BARS):
    granularity = 3600
    per = 300
    need = int(math.ceil(total / per))
    end = int(time.time() // granularity * granularity)
    chunks = []

    for _ in range(need):
        start = end - per * granularity

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

        if not isinstance(data, list):
            raise RuntimeError("Unexpected 1H candle response")

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

    df = pd.DataFrame(chunks)

    if df.empty:
        raise RuntimeError("No 1H history returned")

    df = (
        df.sort_values("ts")
        .drop_duplicates("ts")
        .reset_index(drop=True)
    )

    cutoff = pd.Timestamp(
        int(time.time() // 3600 * 3600),
        unit="s",
        tz="UTC",
    )

    df = df[df["ts"] < cutoff].copy()

    return df.tail(total).reset_index(drop=True)


def resample_ohlcv(df, hours):
    if df.empty:
        return df.copy()

    x = df.copy().set_index("ts").sort_index()
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

    counts = x["close"].resample(
        rule,
        origin="epoch",
        label="left",
        closed="left",
    ).count()

    out = out[counts >= hours].dropna().reset_index()

    return out


def frames():
    h1 = candles_1h_history(HISTORY_1H_BARS)

    m15_raw = candles(900, 300)
    m15_closed, m15_live = closed_live(m15_raw)

    h2 = resample_ohlcv(h1, 2)
    h4 = resample_ohlcv(h1, 4)

    return {
        "15m": m15_closed,
        "15m_live": m15_live,
        "1h": h1,
        "2h": h2,
        "4h": h4,
    }


def closed_live(df):
    if len(df) < 5:
        raise RuntimeError("Insufficient candles")

    d = df.copy()

    if not pd.api.types.is_datetime64_any_dtype(d["ts"]):
        d["ts"] = pd.to_datetime(d["ts"], utc=True)

    step = int(
        (d["ts"].iloc[-1] - d["ts"].iloc[-2]).total_seconds()
    )

    if step > 0:
        end = d["ts"].iloc[-1].timestamp() + step

        if time.time() < end:
            return d.iloc[:-1].copy(), d.iloc[-1].copy()

    return d.copy(), d.iloc[-1].copy()


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)

    tr = pd.concat([
        (h - l),
        (h - prev).abs(),
        (l - prev).abs(),
    ], axis=1).max(axis=1)

    return tr.rolling(n).mean()


def indicators(df):
    x = df.copy()

    x["atr"] = atr(x)
    x["rsi"] = rsi(x["close"], 14)

    for n in [9, 21, 50, 200]:
        x[f"ema{n}"] = x["close"].ewm(
            span=n,
            adjust=False,
        ).mean()

    x["vol_ma20"] = x["volume"].rolling(20).mean()
    x["vol_ratio"] = (
        x["volume"] /
        x["vol_ma20"].replace(0, np.nan)
    )

    x["ret1"] = x["close"].pct_change()
    x["ret8"] = x["close"].pct_change(8)

    x["vol_z"] = (
        (x["volume"] - x["volume"].rolling(30).mean())
        / x["volume"].rolling(30).std()
    )

    x["atr_pct"] = x["atr"].rank(pct=True)

    return x


def rsi(s, n=14):
    d = s.diff()

    up = d.clip(lower=0)
    dn = -d.clip(upper=0)

    au = up.ewm(alpha=1/n, adjust=False).mean()
    ad = dn.ewm(alpha=1/n, adjust=False).mean()

    rs = au / ad.replace(0, np.nan)

    out = 100 - (100 / (1 + rs))

    return out.fillna(50)


def context(row):
    e21, e50, e200 = (
        row["ema21"],
        row["ema50"],
        row["ema200"],
    )

    close = row["close"]

    slope = (
        row["ema21"] - row["ema21_prev"]
        if "ema21_prev" in row
        else 0
    )

    if close > e21 > e50 > e200 and slope > 0:
        label = "BULLISH"
    elif close < e21 < e50 < e200 and slope < 0:
        label = "BEARISH"
    else:
        label = "NEUTRAL"

    return {
        "label": label,
        "slope": slope,
        "above21": close > e21,
    }


def prepare(df):
    x = indicators(df)

    x["ema21_prev"] = x["ema21"].shift(3)

    x["hh20"] = x["high"].rolling(20).max()
    x["ll20"] = x["low"].rolling(20).min()

    x["hh8"] = x["high"].rolling(8).max()
    x["ll8"] = x["low"].rolling(8).min()

    return x.dropna().reset_index(drop=True)


def book_depth():
    data = coinbase_get(
        f"/products/{PRODUCT}/book",
        {"level": 2},
    )

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    with flow_lock:
        flow["bids"] = {
            safe_float(p): safe_float(s)
            for p, s, *_ in bids[:100]
        }

        flow["asks"] = {
            safe_float(p): safe_float(s)
            for p, s, *_ in asks[:100]
        }

        flow["updated"] = now_ts()


def recent_trades():
    data = coinbase_get(
        f"/products/{PRODUCT}/trades",
        {"limit": FLOW_TRADE_LIMIT},
    )

    if not isinstance(data, list):
        raise RuntimeError("Unexpected trades response")

    buy = sell = large = 0.0
    delta = 0.0
    trade_count = 0

    for t in data[:FLOW_TRADE_LIMIT]:
        if not isinstance(t, dict):
            continue

        size = safe_float(t.get("size"))
        side = str(t.get("side", "")).lower()

        trade_count += 1

        if side == "sell":
            buy += size
            delta += size

            if size >= FLOW_LARGE_TRADE_BTC:
                large += size

        elif side == "buy":
            sell += size
            delta -= size

            if size >= FLOW_LARGE_TRADE_BTC:
                large -= size

    with flow_lock:
        flow["buy"] = buy
        flow["sell"] = sell
        flow["delta"] = delta
        flow["large"] = large
        flow["trade_count"] = trade_count
        flow["updated"] = now_ts()


def flow_metrics():
    with flow_lock:
        bids = dict(flow["bids"])
        asks = dict(flow["asks"])
        delta = flow["delta"]
        large = flow["large"]
        buy = flow["buy"]
        sell = flow["sell"]
        trade_count = flow.get("trade_count", 0)
        updated = flow["updated"]

    bid = sum(
        sorted(bids.values(), reverse=True)[:20]
    )

    ask = sum(
        sorted(asks.values(), reverse=True)[:20]
    )

    imb = (
        (bid - ask)
        / max(bid + ask, 1e-9)
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


def rest_flow_snapshot(state=None):
    try:
        book_depth()
        recent_trades()

        current = flow_metrics()

        previous = (
            (state or {}).get("flow_snapshot")
            or {}
        )

        current["delta_change"] = (
            safe_float(current.get("delta"))
            - safe_float(previous.get("delta"))
        )

        current["imb_change"] = (
            safe_float(current.get("imb"))
            - safe_float(previous.get("imb"))
        )

        if state is not None:
            state["flow_snapshot"] = {
                "ts": now_ts(),
                "imb": current["imb"],
                "delta": current["delta"],
                "large": current["large"],
                "buy": current["buy"],
                "sell": current["sell"],
                "trade_count": current["trade_count"],
            }

        return True, current

    except Exception as e:
        journal(
            "FLOW_REST_ERROR",
            error=repr(e),
        )

        return False, {
            "error": repr(e)
        }


def flow_ev(price):
    m = flow_metrics()

    stale = (
        now_ts() - m["updated"]
    ) > ORDERFLOW_STALE

    imb = m["imb"]
    delta = m["delta"]
    large = m["large"]
    buy = m["buy"]
    sell = m["sell"]

    if stale:
        return {
            "score": 0.0,
            "text": "order flow stale",
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
        "score": max(-1, min(1, score)),
        "text": (
            f"imb={imb:.2f} "
            f"delta={delta:.3f} "
            f"large={large:.3f} "
            f"buy={buy:.3f} "
            f"sell={sell:.3f} "
            f"trades={m.get('trade_count', 0)}"
        ),
        "stale": False,
        "imb": imb,
        "buy": buy,
        "sell": sell,
        "delta": delta,
        "large": large,
        "trade_count": m.get("trade_count", 0),
    }


def trend_ev(df, direction):
    r = df.iloc[-1]

    bullish = (
        r.close > r.ema21 > r.ema50 > r.ema200
    )

    bearish = (
        r.close < r.ema21 < r.ema50 < r.ema200
    )

    slope = r.ema21 - r.ema21_prev

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


def price_ev(df, direction):
    r = df.iloc[-1]

    dist = (
        (r.close - r.ema21)
        / max(r.atr, 1e-9)
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


def momentum_ev(df, direction):
    r = df.iloc[-1]

    if direction == "LONG":
        return (
            1.0
            if 52 <= r.rsi <= 72 and r.ret8 > 0
            else 0.5
            if r.ret8 > 0
            else 0
        )

    return (
        1.0
        if 28 <= r.rsi <= 48 and r.ret8 < 0
        else 0.5
        if r.ret8 < 0
        else 0
    )


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


def structure_ev(df, direction):
    r = df.iloc[-1]

    if direction == "LONG":
        return (
            1.0
            if r.close > r.hh8 * 0.998
            else 0.5
            if r.close > r.ema21
            else 0.25
        )

    return (
        1.0
        if r.close < r.ll8 * 1.002
        else 0.5
        if r.close < r.ema21
        else 0.25
    )


def reversal_ev(df, direction):
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

        return 1.0 if pin else 0.0

    pin = (
        c.high > b.high
        and c.close < c.open
        and c.close < b.close
    )

    return 1.0 if pin else 0.0


def impulse_retest_ev(df, direction, state):
    r = df.iloc[-1]

    imp = state.get("impulse")

    if imp and imp.get("direction") == direction:
        age = int(imp.get("bars", 0))
        level = safe_float(imp.get("level"))

        state["impulse"]["bars"] = age + 1

        if (
            direction == "LONG"
            and r.low <= level * 1.002
            and r.close > level
        ):
            return 1.0

        if (
            direction == "SHORT"
            and r.high >= level * 0.998
            and r.close < level
        ):
            return 1.0

        if age > 8:
            state["impulse"] = None

    move = safe_float(r.ret8)

    if direction == "LONG" and move > 0.025:
        state["impulse"] = {
            "direction": "LONG",
            "level": float(r.close),
            "bars": 0,
        }

    elif direction == "SHORT" and move < -0.025:
        state["impulse"] = {
            "direction": "SHORT",
            "level": float(r.close),
            "bars": 0,
        }

    return 0.0


def score_direction(F, direction, state):
    d15 = prepare(F["15m"])
    d1 = prepare(F["1h"])
    d2 = prepare(F["2h"])
    d4 = prepare(F["4h"])

    r1 = d1.iloc[-1]
    r2 = d2.iloc[-1]
    r4 = d4.iloc[-1]

    vals = {
        "trend": (
            trend_ev(d4, direction)
            + trend_ev(d2, direction)
            + trend_ev(d1, direction)
        ) / 3,

        "price": price_ev(d1, direction),

        "momentum": momentum_ev(d1, direction),

        "volume": volume_ev(d1),

        "structure": (
            structure_ev(d1, direction)
            + structure_ev(d15, direction)
        ) / 2,

        "reversal": reversal_ev(
            d15,
            direction,
        ),

        "retest": impulse_retest_ev(
            d1,
            direction,
            state,
        ),
    }

    fe = flow_ev(
        float(r1.close)
    )

    vals["orderflow"] = (
        fe["score"]
        if direction == "LONG"
        else -fe["score"]
    )

    total = sum(
        WEIGHTS[k] * vals[k]
        for k in WEIGHTS
    )

    agreement = (
        sum(v >= 0.5 for v in vals.values())
        / len(vals)
    )

    return total, agreement, vals, fe


def risk_plan(df, direction):
    r = df.iloc[-1]

    entry = float(r.close)

    a = max(
        float(r.atr),
        entry * 0.002,
    )

    if direction == "LONG":
        stop = entry - 1.25 * a
        target = entry + 2.0 * a
    else:
        stop = entry + 1.25 * a
        target = entry - 2.0 * a

    risk = abs(
        entry - stop
    )

    reward = abs(
        target - entry
    )

    rr = (
        reward
        / max(risk, 1e-9)
    )

    return {
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "atr": a,
    }


def build_signal(F, state):
    d1 = prepare(F["1h"])

    if len(d1) < 210:
        raise RuntimeError(
            "Not enough 1H candles for EMA200"
        )

    # LONG and SHORT are scored using independent copies
    # so impulse/retest state from one direction cannot
    # affect the other direction.
    long_state = copy.deepcopy(state)
    short_state = copy.deepcopy(state)

    long_score, long_ag, long_vals, long_flow = score_direction(
        F,
        "LONG",
        long_state,
    )

    short_score, short_ag, short_vals, short_flow = score_direction(
        F,
        "SHORT",
        short_state,
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

    # Keep only the selected direction's impulse/retest state.
    state["impulse"] = selected_state.get("impulse")

    plan = risk_plan(
        d1,
        direction,
    )

    reasons = []

    if score < MIN_SCORE:
        reasons.append("score_low")

    if agreement < MIN_AGREEMENT:
        reasons.append("agreement_low")

    if plan["rr"] < 1.20:
        reasons.append("rr_low")

    if reasons:
        return None, {
            "direction": direction,
            "score": score,
            "agreement": agreement,
            "vals": vals,
            "flow": fe,
            "plan": plan,
            "reason": ",".join(reasons),
        }

    strength = (
        "STRONG"
        if score >= STRONG_SCORE
        and agreement >= STRONG_AGREEMENT
        else "VALID"
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
        "ts": iso(),
    }, {}


def format_signal(sig):
    p = sig["plan"]
    v = sig["vals"]

    return (
        f"₿ BTC V5 — "
        f"{sig['strength']} "
        f"{sig['direction']}\n"

        f"Entry: {p['entry']:.2f}\n"
        f"SL: {p['stop']:.2f}\n"
        f"TP: {p['target']:.2f}\n"
        f"RR: {p['rr']:.2f}\n"

        f"Score: "
        f"{sig['score']:.1f}/"
        f"{sum(WEIGHTS.values())}\n"

        f"Agreement: "
        f"{sig['agreement'] * 100:.0f}%\n"

        f"Trend {v['trend']:.2f} | "
        f"Structure {v['structure']:.2f}\n"

        f"Price {v['price']:.2f} | "
        f"Vol {v['volume']:.2f}\n"

        f"Momentum {v['momentum']:.2f} | "
        f"Retest {v['retest']:.2f}\n"

        f"Reversal {v['reversal']:.2f} | "
        f"OF {v['orderflow']:.2f}\n"

        f"UTC: {sig['ts']}"
    )


def run_once():
    state = load_state()

    state["last_scan_ts"] = now_ts()

    print(
        "BTC Adaptive Telegram Bot V5 — "
        "GitHub one-shot scan"
    )

    F = frames()

    flow_ok, flow_snapshot = rest_flow_snapshot(
        state
    )

    sig, det = build_signal(
        F,
        state,
    )

    if sig is None:
        msg = (
            f"₿ BTC V5 — WAIT\n"
            f"Best: {det['direction']}\n"
            f"Score: {det['score']:.1f}\n"
            f"Agreement: "
            f"{det['agreement'] * 100:.0f}%\n"
            f"Reason: {det['reason']}\n"
            f"OrderFlow: "
            f"{det['flow'].get('text', 'n/a')}"
        )

        telegram(msg)

        journal(
            "WAIT",
            flow_ok=flow_ok,
            flow_snapshot=flow_snapshot,
            **det,
        )

        log_signal_csv(
            timestamp=iso(),
            direction=det["direction"],
            score=det["score"],
            agreement=det["agreement"],
            reason=det["reason"],
            plan=det["plan"],
            flow_data=det["flow"],
        )

        save_state(state)
        return

    # 45-minute cooldown is DIRECTION-SPECIFIC.
    # A previous SHORT can suppress another SHORT,
    # but it will NOT suppress a new LONG signal.
    last_ts = safe_float(
        state.get("last_signal_ts")
    )

    last_direction = state.get(
        "last_direction"
    )

    same = (
        state.get("last_signal")
        == sig["id"]
    )

    cooldown = (
        last_direction == sig["direction"]
        and (
            now_ts() - last_ts
        ) < COOLDOWN_MIN * 60
    )

    if same or cooldown:
        reason = (
            "same_signal"
            if same
            else "same_direction_cooldown"
        )

        journal(
            "SUPPRESSED",
            signal=sig,
            same=same,
            cooldown=cooldown,
            last_direction=last_direction,
        )

        telegram(
            f"₿ BTC V5 — SIGNAL SUPPRESSED\n"
            f"{sig['direction']} "
            f"{sig['score']:.1f}\n"
            f"Reason: {reason}"
        )

        log_signal_csv(
            timestamp=sig["ts"],
            direction=sig["direction"],
            score=sig["score"],
            agreement=sig["agreement"],
            reason=reason,
            plan=sig["plan"],
            flow_data=sig["flow"],
        )

        save_state(state)
        return

    telegram(
        format_signal(sig)
    )

    journal(
        "SIGNAL",
        signal=sig,
        flow_ok=flow_ok,
        flow_snapshot=flow_snapshot,
    )

    log_signal_csv(
        timestamp=sig["ts"],
        direction=sig["direction"],
        score=sig["score"],
        agreement=sig["agreement"],
        reason=f"signal_{sig['strength'].lower()}",
        plan=sig["plan"],
        flow_data=sig["flow"],
    )

    state["last_signal"] = sig["id"]
    state["last_signal_ts"] = now_ts()
    state["last_direction"] = sig["direction"]

    save_state(state)


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
                traceback=traceback.format_exc(),
            )

        time.sleep(
            max(
                2,
                SCAN_SECONDS
                - (time.time() - started),
            )
        )


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
                traceback=traceback.format_exc(),
            )

            raise

    else:
        run()


if __name__ == "__main__":
    main()

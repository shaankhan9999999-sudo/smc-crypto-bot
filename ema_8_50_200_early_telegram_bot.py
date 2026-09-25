#!/usr/bin/env python3
""" EMA 8 / 50 / 200 Early Detection Telegram Bot ------------------------------------------------ Standalone bot for: - BTC - XAUUSD (Gold) Timeframes: - M5 : Entry - M15 : Upgrade M5 scalp -> swing hold - H1 : Upgrade M5 trade -> longer trend hold Alerts are intentionally limited and state-based: 1) EMA ZONE 2) EMA EARLY CROSS 3) EMA DUAL CROSS / ENTRY 4) M15 CONFIRMATION 5) H1 CONFIRMATION 6) M5 WEAKENING 7) M5 INVALIDATED / EXIT No automatic order execution is performed. The bot only calculates and sends Telegram alerts. Environment variables required: TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID Optional: POLL_SECONDS=60 """

import os
import time
import math
import json
import requests
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
STATE_FILE = os.getenv("STATE_FILE", "ema_bot_state.json")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise SystemExit(
        "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variable."
    )

# Yahoo Finance public chart endpoint.
# BTC and Gold are both obtained from the same simple source.
SYMBOLS = {
    "BTC": "BTC-USD",
    "XAUUSD": "XAUUSD=X",
}

TIMEFRAMES = {
    "M5": ("5m", 300),
    "M15": ("15m", 600),
    "H1": ("1h", 800),
}

EMA_FAST = 8
EMA_MID = 50
EMA_SLOW = 200

# Simple thresholds; intentionally kept small and understandable.
ZONE_ATR = 0.20          # all three EMA lines are close to one another
EARLY_ATR = 0.30         # EMA50 is close to EMA200 after EMA8 crosses
DUAL_CANDLES = 3         # 8 and 50 can cross 200 within this many candles
MAX_SAVED_DUAL_EVENTS = 20  # remember recent crossover events to prevent duplicate alerts

ATR_PERIOD = 14
SWING_LOOKBACK = 5
ATR_SL_BUFFER = 0.20

# Initial targets agreed in the design:
# TP1 = 1.5R, TP2 = 2R.
TP1_R = 1.5
TP2_R = 2.0

# Higher-timeframe upgrade targets.
# They do not create another entry; they extend the existing M5 trade.
EXTEND_ATR = 2.0

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "EMA-200-Early-Bot/1.0"})

states = {}


def load_state():
    global states
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            states = data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        states = {}


def save_state():
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(states, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }
    r = HTTP.post(url, json=payload, timeout=15)
    r.raise_for_status()


def fetch_candles(symbol, interval, period_seconds):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "period1": int(time.time()) - period_seconds * 100,
        "period2": int(time.time()),
        "interval": interval,
        "events": "history",
        "includeAdjustedClose": "true",
    }
    r = HTTP.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    result = data["chart"]["result"]
    if not result:
        raise ValueError(f"No data returned for {symbol} {interval}")

    result = result[0]
    timestamps = result.get("timestamp", [])
    q = result["indicators"]["quote"][0]

    rows = []
    for i, ts in enumerate(timestamps):
        try:
            o = float(q["open"][i])
            h = float(q["high"][i])
            l = float(q["low"][i])
            c = float(q["close"][i])
        except (TypeError, ValueError, IndexError):
            continue

        if any(math.isnan(x) for x in (o, h, l, c)):
            continue

        rows.append({
            "ts": int(ts),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        })

    if len(rows) < EMA_SLOW + ATR_PERIOD + 10:
        raise ValueError(f"Not enough candles for {symbol} {interval}")

    return rows


def ema(values, period):
    if len(values) < period:
        return [None] * len(values)

    out = [None] * len(values)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1.0)

    prev = seed
    for i in range(period, len(values)):
        prev = (values[i] - prev) * alpha + prev
        out[i] = prev

    return out


def atr(candles, period=ATR_PERIOD):
    if len(candles) < period + 1:
        return [None] * len(candles)

    tr = [None] * len(candles)
    tr[0] = candles[0]["high"] - candles[0]["low"]

    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    out = [None] * len(candles)
    seed = sum(tr[1:period + 1]) / period
    out[period] = seed
    alpha = 1.0 / period
    prev = seed

    for i in range(period + 1, len(candles)):
        prev = ((period - 1) * prev + tr[i]) / period
        out[i] = prev

    return out


def prepare(candles):
    closes = [x["close"] for x in candles]
    e8 = ema(closes, EMA_FAST)
    e50 = ema(closes, EMA_MID)
    e200 = ema(closes, EMA_SLOW)
    a = atr(candles)

    for i, row in enumerate(candles):
        row["ema8"] = e8[i]
        row["ema50"] = e50[i]
        row["ema200"] = e200[i]
        row["atr"] = a[i]

    return candles


def valid(row):
    return all(row.get(k) is not None for k in ("ema8", "ema50", "ema200", "atr")) and row["atr"] > 0


def crossed_up(prev, cur, fast_key):
    return prev[fast_key] <= prev["ema200"] and cur[fast_key] > cur["ema200"]


def crossed_down(prev, cur, fast_key):
    return prev[fast_key] >= prev["ema200"] and cur[fast_key] < cur["ema200"]


def direction_alignment(row):
    if row["ema8"] > row["ema50"] > row["ema200"]:
        return "BULLISH"
    if row["ema8"] < row["ema50"] < row["ema200"]:
        return "BEARISH"
    return "MIXED"


def close_to_200(row, key, multiplier):
    return abs(row[key] - row["ema200"]) <= row["atr"] * multiplier


def compressed(row):
    if not valid(row):
        return False
    spread = max(row["ema8"], row["ema50"], row["ema200"]) - min(
        row["ema8"], row["ema50"], row["ema200"]
    )
    return spread <= row["atr"] * ZONE_ATR


def recent_dual_cross(candles, direction):
    """ True when EMA8 and EMA50 have crossed EMA200 in the same direction within DUAL_CANDLES candles. """
    n = len(candles)
    if n < DUAL_CANDLES + 2:
        return False

    recent = candles[max(1, n - DUAL_CANDLES - 1):]

    up8 = any(crossed_up(recent[i - 1], recent[i], "ema8")
              for i in range(1, len(recent)))
    up50 = any(crossed_up(recent[i - 1], recent[i], "ema50")
               for i in range(1, len(recent)))

    dn8 = any(crossed_down(recent[i - 1], recent[i], "ema8")
              for i in range(1, len(recent)))
    dn50 = any(crossed_down(recent[i - 1], recent[i], "ema50")
               for i in range(1, len(recent)))

    if direction == "BULLISH":
        return up8 and up50
    if direction == "BEARISH":
        return dn8 and dn50
    return False


def find_recent_crosses(candles, key, direction):
    """ Return recent completed-candle cross events as (age, timestamp). age=0 means the cross happened on the latest completed candle. """
    events = []
    max_age = min(DUAL_CANDLES, len(candles) - 2)

    for age in range(0, max_age + 1):
        i = len(candles) - 1 - age
        if i <= 0:
            continue

        prev = candles[i - 1]
        cur = candles[i]
        crossed = (
            direction == "BULLISH" and crossed_up(prev, cur, key)
        ) or (
            direction == "BEARISH" and crossed_down(prev, cur, key)
        )

        if crossed:
            events.append((age, cur["ts"]))

    return events


def dual_cross_event(candles, direction):
    """ Find one FRESH dual-cross event and return a stable event id. The event is defined by the actual completed candle timestamps on which EMA8 and EMA50 crossed EMA200. This is deliberately NOT keyed to the current candle timestamp, so the same crossover cannot generate a new ENTRY message on every 5-minute scan. A crossover is accepted only while the current completed candle still has the expected directional EMA structure. This prevents an old cross from becoming a late/stale entry after the market has already reversed. """
    if len(candles) < EMA_SLOW + DUAL_CANDLES + 2:
        return None

    row = candles[-1]
    if not valid(row):
        return None

    alignment = direction_alignment(row)
    if alignment != direction:
        return None

    crosses8 = find_recent_crosses(candles, "ema8", direction)
    crosses50 = find_recent_crosses(candles, "ema50", direction)

    if not crosses8 or not crosses50:
        return None

    # Use the newest pair that occurred within DUAL_CANDLES of each other.
    candidates = []
    for age8, ts8 in crosses8:
        for age50, ts50 in crosses50:
            if abs(age8 - age50) <= DUAL_CANDLES:
                candidates.append((max(age8, age50), ts8, ts50))

    if not candidates:
        return None

    age, ts8, ts50 = min(candidates, key=lambda x: (x[0], max(x[1], x[2])))
    event_id = f"{direction}:{min(ts8, ts50)}:{max(ts8, ts50)}"

    return {
        "id": event_id,
        "age": age,
        "ts8": ts8,
        "ts50": ts50,
    }


def remember_dual_event(state, event_id):
    events = state.setdefault("seen_dual_events", [])
    if event_id not in events:
        events.append(event_id)
    if len(events) > MAX_SAVED_DUAL_EVENTS:
        del events[:-MAX_SAVED_DUAL_EVENTS]


def dual_event_already_seen(state, event_id):
    return event_id in state.setdefault("seen_dual_events", [])

def structure_failure(candles, direction):
    row = candles[-1]
    if not valid(row):
        return False

    if direction == "BULLISH":
        return row["ema8"] < row["ema50"] or row["close"] < row["ema200"]

    if direction == "BEARISH":
        return row["ema8"] > row["ema50"] or row["close"] > row["ema200"]

    return False


def swing_level(candles, direction):
    recent = candles[-SWING_LOOKBACK - 1:-1]
    if not recent:
        recent = candles[-SWING_LOOKBACK:]

    if direction == "BULLISH":
        return min(x["low"] for x in recent)
    return max(x["high"] for x in recent)


def calculate_trade(candles, direction):
    row = candles[-1]
    entry = row["close"]
    a = row["atr"]
    swing = swing_level(candles, direction)

    if direction == "BULLISH":
        sl = min(swing, entry - a) - (a * ATR_SL_BUFFER)
        risk = entry - sl
        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
    else:
        sl = max(swing, entry + a) + (a * ATR_SL_BUFFER)
        risk = sl - entry
        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R

    return {
        "entry": entry,
        "sl": sl,
        "risk": risk,
        "tp1": tp1,
        "tp2": tp2,
    }


def extended_target(candles, trade_direction):
    row = candles[-1]
    if not valid(row):
        return None

    if trade_direction == "BULLISH":
        return row["close"] + row["atr"] * EXTEND_ATR
    return row["close"] - row["atr"] * EXTEND_ATR


def fmt(x):
    if x >= 1000:
        return f"{x:,.2f}"
    return f"{x:.2f}"


def base_message(symbol, title, direction, row, extra=""):
    return (
        f"{title}\n"
        f"{symbol} | {direction}\n"
        f"EMA8: {fmt(row['ema8'])}\n"
        f"EMA50: {fmt(row['ema50'])}\n"
        f"EMA200: {fmt(row['ema200'])}\n"
        f"Price: {fmt(row['close'])}\n"
        f"{extra}\n"
        f"{now_utc()}"
    )


def scan_symbol(symbol_name):
    s = states.setdefault(symbol_name, {
        "m5_state": "NORMAL",
        "trade": None,
        "last_zone_key": None,
        "last_early_key": None,
        "last_dual_key": None,
        "seen_dual_events": [],
        "m15_confirmed": False,
        "h1_confirmed": False,
        "last_weak_key": None,
        "last_invalid_key": None,
    })

    # Backward-compatible state migration for older state files.
    if not isinstance(s.get("seen_dual_events"), list):
        s["seen_dual_events"] = []

    data = {}
    for tf, (interval, seconds) in TIMEFRAMES.items():
        candles = fetch_candles(SYMBOLS[symbol_name], interval, seconds)
        data[tf] = prepare(candles)

    m5 = data["M5"]
    m15 = data["M15"]
    h1 = data["H1"]

    # Use completed candles only. The latest returned candle can still be forming.
    m5 = m5[:-1]
    m15 = m15[:-1]
    h1 = h1[:-1]

    if len(m5) < EMA_SLOW + 5:
        return

    row = m5[-1]
    prev = m5[-2]

    # ---------------- M5 ZONE ----------------
    if compressed(row):
        key = row["ts"]
        if s["m5_state"] == "NORMAL" and s["last_zone_key"] != key:
            send_telegram(
                base_message(
                    symbol_name,
                    "ðŸŸ¡ M5 EMA ZONE",
                    "WATCH",
                    row,
                    "EMA 8 / 50 / 200 are compressed near each other.\n"
                    "EMA-200 decision zone detected."
                )
            )
            s["m5_state"] = "ZONE"
            s["last_zone_key"] = key

    # ---------------- M5 EARLY CROSS ----------------
    bull_early = (
        crossed_up(prev, row, "ema8")
        and close_to_200(row, "ema50", EARLY_ATR)
        and row["ema50"] > prev["ema50"]
    )
    bear_early = (
        crossed_down(prev, row, "ema8")
        and close_to_200(row, "ema50", EARLY_ATR)
        and row["ema50"] < prev["ema50"]
    )

    if bull_early or bear_early:
        direction = "BULLISH" if bull_early else "BEARISH"
        key = (row["ts"], direction)

        if s["last_early_key"] != key:
            send_telegram(
                base_message(
                    symbol_name,
                    "ðŸŸ  M5 EMA EARLY CROSS",
                    direction,
                    row,
                    "EMA8 has crossed EMA200.\n"
                    "EMA50 is close to EMA200 and moving toward it.\n"
                    "Potential dual-cross developing."
                )
            )
            s["last_early_key"] = key
            s["m5_state"] = "EARLY"

    # ---------------- M5 DUAL CROSS / ENTRY ----------------
    #
    # Important anti-spam rule:
    # - A dual cross gets a stable event id based on the actual crossover
    # candles, NOT the current scan time.
    # - The same event can therefore be seen on multiple 5-minute scans but
    # can generate only one ENTRY alert.
    # - If an M5 trade is already active, another scan of the same event (or
    # another nearby duplicate event) cannot create a second entry.
    # - A fresh opposite trade is considered only after the existing trade has
    # been invalidated/cleared by the normal management logic.
    for direction in ("BULLISH", "BEARISH"):
        event = dual_cross_event(m5, direction)
        if not event:
            continue

        event_id = event["id"]
        already_seen = dual_event_already_seen(s, event_id)

        # Always remember the event, even if a trade is already active. This
        # prevents a later scan/restart from turning the same event into a
        # second entry.
        if not already_seen:
            remember_dual_event(s, event_id)

        # Existing trade owns the alert stream until it is invalidated.
        if s.get("trade"):
            continue

        if already_seen:
            continue

        trade = calculate_trade(m5, direction)

        send_telegram(
            f"ðŸš¨ M5 EMA DUAL CROSS â€” ENTRY\n"
            f"{symbol_name} | {direction}\n"
            f"EMA8 + EMA50 crossed EMA200 within {DUAL_CANDLES} candles.\n"
            f"Fresh crossover event; current M5 structure still confirms it.\n\n"
            f"Entry: {fmt(trade['entry'])}\n"
            f"SL: {fmt(trade['sl'])}\n"
            f"TP1: {fmt(trade['tp1'])} (1.5R)\n"
            f"TP2: {fmt(trade['tp2'])} (2R)\n\n"
            f"Initial mode: M5 SCALP\n"
            f"Watch M15 confirmation to extend the trade.\n"
            f"{now_utc()}"
        )

        s["trade"] = {
            "direction": direction,
            "entry": trade["entry"],
            "sl": trade["sl"],
            "tp1": trade["tp1"],
            "tp2": trade["tp2"],
            "m15_confirmed": False,
            "h1_confirmed": False,
            "opened_ts": row["ts"],
            "dual_event_id": event_id,
        }
        s["m15_confirmed"] = False
        s["h1_confirmed"] = False
        s["last_dual_key"] = event_id
        s["m5_state"] = "DUAL"

    # ---------------- Existing trade management ----------------
    trade = s.get("trade")
    if not trade:
        return

    direction = trade["direction"]

    # M15 confirmation: same directional dual-cross.
    if not trade["m15_confirmed"] and dual_cross_event(m15, direction):
        target = extended_target(m15, direction)

        send_telegram(
            f"ðŸŸ¢ M15 CONFIRMATION â€” EXTEND TRADE\n"
            f"{symbol_name} | Existing M5 {direction} trade\n"
            f"M15 EMA8 + EMA50 have crossed EMA200 in the same direction.\n"
            f"Do not treat this as a new entry.\n"
            f"Extend/hold the existing M5 position.\n"
            f"Extended target reference: {fmt(target)}\n"
            f"M5 SL remains: {fmt(trade['sl'])}\n"
            f"{now_utc()}"
        )

        trade["m15_confirmed"] = True
        s["m15_confirmed"] = True

    # H1 confirmation: same directional dual-cross.
    if not trade["h1_confirmed"] and dual_cross_event(h1, direction):
        target = extended_target(h1, direction)

        send_telegram(
            f"ðŸ”µ H1 CONFIRMATION â€” LONG HOLD MODE\n"
            f"{symbol_name} | Existing M5 {direction} trade\n"
            f"H1 EMA8 + EMA50 have crossed EMA200 in the same direction.\n"
            f"Do not treat this as a new entry.\n"
            f"Longer-trend confirmation detected.\n"
            f"Extended target reference: {fmt(target)}\n"
            f"M5 SL remains: {fmt(trade['sl'])}\n"
            f"{now_utc()}"
        )

        trade["h1_confirmed"] = True
        s["h1_confirmed"] = True

    # M5 weakening / invalidation.
    if structure_failure(m5, direction):
        key = (row["ts"], direction)

        if s["last_weak_key"] != key:
            send_telegram(
                f"âš ï¸ M5 TRADE WEAKENING\n"
                f"{symbol_name} | Existing {direction} trade\n"
                f"M5 EMA structure is weakening.\n"
                f"Higher-timeframe confirmation: "
                f"{'M15 YES' if trade['m15_confirmed'] else 'M15 NO'} / "
                f"{'H1 YES' if trade['h1_confirmed'] else 'H1 NO'}\n"
                f"Protect the existing position and watch for invalidation.\n"
                f"{now_utc()}"
            )
            s["last_weak_key"] = key

        # Clear the trade only after a clear opposite structure.
        opposite = (
            direction == "BULLISH"
            and row["ema8"] < row["ema50"] < row["ema200"]
        ) or (
            direction == "BEARISH"
            and row["ema8"] > row["ema50"] > row["ema200"]
        )

        if opposite:
            if s["last_invalid_key"] != key:
                send_telegram(
                    f"ðŸ”´ M5 TRADE INVALIDATED â€” EXIT\n"
                    f"{symbol_name} | Existing {direction} trade\n"
                    f"M5 has established the opposite EMA structure.\n"
                    f"M15 confirmation: {'YES' if trade['m15_confirmed'] else 'NO'}\n"
                    f"H1 confirmation: {'YES' if trade['h1_confirmed'] else 'NO'}\n"
                    f"Exit/protect the remaining position.\n"
                    f"{now_utc()}"
                )
                s["last_invalid_key"] = key

            s["trade"] = None
            s["m15_confirmed"] = False
            s["h1_confirmed"] = False
            s["m5_state"] = "NORMAL"


def main():
    # GitHub Actions starts this script every 5 minutes.
    # The state file keeps alert/trade state between runs.
    load_state()

    for symbol_name in SYMBOLS:
        try:
            scan_symbol(symbol_name)
        except Exception as e:
            print(f"[{now_utc()}] {symbol_name}: {e}")

    save_state()


if __name__ == "__main__":
    main()

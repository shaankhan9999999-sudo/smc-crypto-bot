#!/usr/bin/env python3
"""Standalone EMA 8/50/200 Early Telegram Bot. BTC : Coinbase candles XAU : TradingView -> Pine alert -> xau_tradingview_webhook.py -> HTTP state Signal model: - M5 primary trigger; M15 confirmation; H1 hold confirmation - Closed candles only; no forming-candle decisions - EMA 8 / 50 / 200 - Zone -> Early Cross -> Dual Cross - EMA8 and EMA50 must cross EMA200 within 0-3 closed M5 candles - ATR/swing SL; TP1 1.5R; TP2 2R - Catch-up window <= 8 minutes; old signals are not revived - Persistent duplicate protection This bot sends informational Telegram alerts only; it does not place orders. """

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

EMA8, EMA50, EMA200 = 8, 50, 200
ATR_N = 14
ZONE_ATR = 0.20
EARLY_ATR = 0.30
DUAL_CANDLES = 3
SWING_LOOKBACK = 5
ATR_SL_BUFFER = 0.20
TP1_R, TP2_R = 1.5, 2.0
G = {"M5": 300, "M15": 900, "H1": 3600}

COINBASE_PRODUCT = os.getenv("COINBASE_PRODUCT", "BTC-USD")
XAU_STATE_URL = os.getenv("XAU_STATE_URL", "").strip().rstrip("/")
XAU_STATE_TOKEN = os.getenv("XAU_STATE_TOKEN", "").strip()
STATE_FILE = Path(os.getenv("STATE_FILE", "ema_bot_state.json"))

S = requests.Session()
S.headers.update({"User-Agent": "EMA-8-50-200-Early-Telegram-Bot/3.0"})


def now():
    return datetime.now(timezone.utc)


def log(msg):
    print(f"[{now():%Y-%m-%d %H:%M:%S UTC}] {msg}", flush=True)


def tg(msg):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    r = S.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat, "text": msg},
        timeout=20,
    )
    r.raise_for_status()


def load_state():
    try:
        raw = json.loads(STATE_FILE.read_text())
        if isinstance(raw, dict):
            return raw
    except Exception as exc:
        log(f"State load skipped: {exc}")
    return {"version": 3, "symbols": {}}


def save_state(state):
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)


def sym_state(state, symbol):
    state.setdefault("version", 3)
    state.setdefault("symbols", {})
    state["symbols"].setdefault(
        symbol,
        {
            "active_trade": None,
            "last_zone_event": None,
            "last_early_event": None,
            "last_entry_event": None,
            "last_m15_event": None,
            "last_h1_event": None,
            "last_weak_event": None,
            "last_invalid_event": None,
            "last_tp1_event": None,
            "last_tp2_event": None,
        },
    )
    return state["symbols"][symbol]


# ---------------- BTC: Coinbase native candles ----------------
def coinbase(tf, needed=430):
    sec = G[tf]
    end = int(now().timestamp())
    rows = []
    remaining = needed
    url = f"https://api.exchange.coinbase.com/products/{COINBASE_PRODUCT}/candles"

    while remaining > 0:
        count = min(280, remaining)
        start = end - count * sec
        params = {
            "granularity": sec,
            "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        }
        r = S.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        rows.extend(data)
        oldest = min(int(row[0]) for row in data)
        if oldest >= end:
            break
        end = oldest - sec
        remaining -= len(data)
        if len(data) < count:
            break

    if not rows:
        raise RuntimeError(f"Coinbase returned no {tf} candles")

    d = pd.DataFrame(rows, columns=["ts", "low", "high", "open", "close", "volume"])
    d = d.drop_duplicates("ts").sort_values("ts")
    d["ts"] = pd.to_datetime(d["ts"], unit="s", utc=True)
    d = d.set_index("ts")
    for col in ["open", "high", "low", "close", "volume"]:
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna()
    if len(d) > 1:
        d = d.iloc[:-1]  # forming candle excluded
    if len(d) < EMA200 + 30:
        raise RuntimeError(f"Not enough Coinbase {tf} candles: {len(d)}")
    return d


# ---------------- XAUUSD: TradingView webhook state ----------------
def _json_to_frame(rows, tf):
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"XAU webhook returned no {tf} rows")
    d = pd.DataFrame(rows)
    required = {"ts", "open", "high", "low", "close"}
    if not required.issubset(d.columns):
        raise RuntimeError(f"XAU {tf} payload missing columns: {required - set(d.columns)}")
    d["ts"] = pd.to_datetime(d["ts"], utc=True)
    for col in ["open", "high", "low", "close"]:
        d[col] = pd.to_numeric(d[col], errors="coerce")
    if "volume" not in d:
        d["volume"] = 0.0
    d["volume"] = pd.to_numeric(d["volume"], errors="coerce").fillna(0.0)
    d = d.dropna(subset=["open", "high", "low", "close"])
    d = d.drop_duplicates("ts").sort_values("ts").set_index("ts")
    if len(d) < EMA200 + 30:
        raise RuntimeError(f"Not enough TradingView XAU {tf} candles: {len(d)}")
    return d[["open", "high", "low", "close", "volume"]]


def xau_frames():
    if not XAU_STATE_URL:
        raise RuntimeError("XAU_STATE_URL secret/variable is required")
    params = {"token": XAU_STATE_TOKEN} if XAU_STATE_TOKEN else {}
    r = S.get(f"{XAU_STATE_URL}/state", params=params, timeout=20)
    r.raise_for_status()
    payload = r.json()
    if payload.get("ok") is not True:
        raise RuntimeError(f"XAU webhook state error: {payload}")
    return {tf: _json_to_frame(payload["frames"][tf], tf) for tf in G}


# ---------------- Indicators / closed-candle signals ----------------
def indicators(d):
    x = d.copy()
    x["ema8"] = x.close.ewm(span=EMA8, adjust=False).mean()
    x["ema50"] = x.close.ewm(span=EMA50, adjust=False).mean()
    x["ema200"] = x.close.ewm(span=EMA200, adjust=False).mean()
    prev_close = x.close.shift(1)
    tr = pd.concat(
        [x.high - x.low, (x.high - prev_close).abs(), (x.low - prev_close).abs()], axis=1
    ).max(axis=1)
    x["atr"] = tr.rolling(ATR_N).mean()
    return x.dropna()


def cross_up(pfast, cfast, pslow, cslow):
    return pfast <= pslow and cfast > cslow


def cross_down(pfast, cfast, pslow, cslow):
    return pfast >= pslow and cfast < cslow


def dual(d, direction):
    if len(d) < 3:
        return None
    cross8, cross50 = [], []
    for i in range(1, len(d)):
        p, c = d.iloc[i - 1], d.iloc[i]
        if direction == "LONG":
            if cross_up(p.ema8, c.ema8, p.ema200, c.ema200): cross8.append(i)
            if cross_up(p.ema50, c.ema50, p.ema200, c.ema200): cross50.append(i)
        else:
            if cross_down(p.ema8, c.ema8, p.ema200, c.ema200): cross8.append(i)
            if cross_down(p.ema50, c.ema50, p.ema200, c.ema200): cross50.append(i)
    pairs = [(max(i, j), i, j) for i in cross8 for j in cross50 if abs(i - j) <= DUAL_CANDLES]
    if not pairs:
        return None
    event_i, i8, i50 = max(pairs)
    return {"event_ts": d.index[event_i].isoformat(), "i": event_i, "i8": i8, "i50": i50}


def zone(d):
    c = d.iloc[-1]
    spread = max(c.ema8, c.ema50, c.ema200) - min(c.ema8, c.ema50, c.ema200)
    return c if spread <= ZONE_ATR * c.atr else None


def early(d, direction):
    if len(d) < 2:
        return None
    p, c = d.iloc[-2], d.iloc[-1]
    if direction == "LONG":
        ok = cross_up(p.ema8, c.ema8, p.ema200, c.ema200) and abs(c.ema50-c.ema200) <= EARLY_ATR*c.atr and c.ema50 > p.ema50 and c.ema50 >= c.ema200
    else:
        ok = cross_down(p.ema8, c.ema8, p.ema200, c.ema200) and abs(c.ema50-c.ema200) <= EARLY_ATR*c.atr and c.ema50 < p.ema50 and c.ema50 <= c.ema200
    return c if ok else None


def build_trade(d, direction):
    c = d.iloc[-1]
    look = d.iloc[-(SWING_LOOKBACK + 1):-1]
    if len(look) < SWING_LOOKBACK or float(c.atr) <= 0:
        return None
    entry = float(c.close); atr = float(c.atr)
    if direction == "LONG":
        sl = float(look.low.min()) - ATR_SL_BUFFER*atr; risk = entry-sl; tp1=entry+TP1_R*risk; tp2=entry+TP2_R*risk
    else:
        sl = float(look.high.max()) + ATR_SL_BUFFER*atr; risk = sl-entry; tp1=entry-TP1_R*risk; tp2=entry-TP2_R*risk
    if risk <= 0: return None
    return {"direction":direction,"entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,"entry_ts":c.name.isoformat(),"m15_confirmed":False,"h1_confirmed":False}


def fp(_symbol, value):
    return f"{float(value):.2f}"


def process(symbol, frames, state):
    s = sym_state(state, symbol)
    m5, m15, h1 = (indicators(frames[x]) for x in ("M5", "M15", "H1"))
    trade = s.get("active_trade")

    if trade:
        direction = trade["direction"]; c = m5.iloc[-1]; current=float(c.close)
        if direction == "LONG":
            tp1_hit=current>=float(trade["tp1"]); tp2_hit=current>=float(trade["tp2"]); weak=c.ema8<c.ema50 or current<c.ema200; invalid=c.ema8<c.ema50 and c.ema50<c.ema200
        else:
            tp1_hit=current<=float(trade["tp1"]); tp2_hit=current<=float(trade["tp2"]); weak=c.ema8>c.ema50 or current>c.ema200; invalid=c.ema8>c.ema50 and c.ema50>c.ema200
        if tp1_hit and s.get("last_tp1_event") != c.name.isoformat():
            s["last_tp1_event"]=c.name.isoformat(); tg(f"🎯 TP1 REACHED — {symbol} {direction}\n\nEntry: {fp(symbol,trade['entry'])}\nTP1 (1.5R): {fp(symbol,trade['tp1'])}\nCurrent close: {fp(symbol,current)}\n\nInformational alert only — no order is placed by this bot.")
        if tp2_hit and s.get("last_tp2_event") != c.name.isoformat():
            s["last_tp2_event"]=c.name.isoformat(); tg(f"🎯 TP2 REACHED — {symbol} {direction}\n\nEntry: {fp(symbol,trade['entry'])}\nTP2 (2R): {fp(symbol,trade['tp2'])}\nCurrent close: {fp(symbol,current)}\n\nInformational alert only — no order is placed by this bot.")
        me=dual(m15,direction)
        if me and me["event_ts"] != s.get("last_m15_event"):
            s["last_m15_event"]=me["event_ts"]; trade["m15_confirmed"]=True; tg(f"🟢 M15 CONFIRMATION — EXTEND TRADE\n\n{symbol} {direction}\nM15 EMA 8 + EMA 50 confirmed the same direction through EMA 200.\nExisting M5 trade: HOLD / EXTEND.")
        he=dual(h1,direction)
        if he and he["event_ts"] != s.get("last_h1_event"):
            s["last_h1_event"]=he["event_ts"]; trade["h1_confirmed"]=True; tg(f"🔵 H1 CONFIRMATION — LONG HOLD MODE\n\n{symbol} {direction}\nH1 EMA 8 + EMA 50 confirmed the same direction through EMA 200.\nExisting M5 trade: LONG HOLD MODE.")
        if invalid:
            ev=c.name.isoformat()
            if ev != s.get("last_invalid_event"):
                s["last_invalid_event"]=ev; tg(f"🔴 M5 TRADE INVALIDATED — EXIT\n\n{symbol} {direction}\nM5 EMA structure flipped against the active trade.\n\nInformational alert only — no order is placed by this bot.")
            s["active_trade"]=None; return
        if weak:
            ev=c.name.isoformat()
            if ev != s.get("last_weak_event"):
                s["last_weak_event"]=ev; tg(f"⚠️ M5 TRADE WEAKENING\n\n{symbol} {direction}\nM5 structure is weakening. Monitor/protect the active trade.")
        return

    z=zone(m5)
    if z is not None and z.name.isoformat()!=s.get("last_zone_event"):
        s["last_zone_event"]=z.name.isoformat(); tg(f"🟡 M5 EMA ZONE\n\n{symbol}\nEMA 8 / EMA 50 / EMA 200 are compressed near a decision zone.")
    for direction in ("LONG","SHORT"):
        e=early(m5,direction)
        if e is not None and e.name.isoformat()!=s.get("last_early_event"):
            s["last_early_event"]=e.name.isoformat(); tg(f"🟠 M5 EMA EARLY CROSS\n\n{symbol} {direction}\nEMA 8 crossed EMA 200; EMA 50 is close and moving toward confirmation.")
        ev=dual(m5,direction)
        if not ev or ev["event_ts"]==s.get("last_entry_event"): continue
        age=now()-pd.Timestamp(ev["event_ts"]).to_pydatetime()
        if age.total_seconds()<0 or age>timedelta(minutes=8): continue
        t=build_trade(m5,direction)
        if not t: continue
        s["last_entry_event"]=ev["event_ts"]; s["active_trade"]=t
        tg(f"🚨 M5 EMA DUAL CROSS — ENTRY\n\n{'🟢' if direction=='LONG' else '🔴'} {symbol} {direction}\nEntry: {fp(symbol,t['entry'])}\nSL: {fp(symbol,t['sl'])}\nTP1 (1.5R): {fp(symbol,t['tp1'])}\nTP2 (2R): {fp(symbol,t['tp2'])}\n\nEMA 8 + EMA 50 crossed EMA 200 within {DUAL_CANDLES} M5 candles.\nInformational alert only — no order is placed by this bot.")
        return


def main():
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise RuntimeError("Telegram secrets are required")
    state=load_state()
    try: process("BTC", {tf:coinbase(tf) for tf in G}, state)
    except Exception as exc: log(f"BTC error: {exc}")
    try: process("XAUUSD", xau_frames(), state)
    except Exception as exc: log(f"XAUUSD error: {exc}")
    save_state(state)
    log("Run complete.")


if __name__ == "__main__":
    try: main()
    except Exception as exc: log(f"FATAL: {exc}"); sys.exit(1)

#!/usr/bin/env python3
# BTC ADAPTIVE TELEGRAM BOT V5
# 4H/2H = context, never hard signal veto
# 1H = primary setup, 15M = timing/reversal
# Long/Short scored independently
# Evidence > rigid gates; only risk/data problems hard-veto
#
# Install:
#   pip install requests pandas numpy websocket-client
#
# Credentials:
#   export TELEGRAM_BOT_TOKEN="YOUR_NEW_TOKEN"
#   export TELEGRAM_CHAT_ID="YOUR_CHAT_ID"
#
# Run:
#   python btc_adaptive_bot_v5.py
#   python btc_adaptive_bot_v5.py --backtest 60
#   python btc_adaptive_bot_v5.py --status

from __future__ import annotations
import argparse, hashlib, json, math, os, threading, time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

# ---------------- CONFIG ----------------

PRODUCT = "BTC-USD"
REST = "https://api.exchange.coinbase.com"
WS = "wss://ws-feed.exchange.coinbase.com"

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8662975391:AAET5mjz3-A9Jorpmn5-tM-qRZOuFoEtGzM")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7500472109")

STATE_FILE = Path("btc_v5_state.json")
JOURNAL_FILE = Path("btc_v5_journal.jsonl")
CALIBRATION_FILE = Path("btc_v5_calibration.json")

SCAN_SECONDS = 60
COOLDOWN_MIN = 45

# These are soft scoring levels, not indicator gates.
MIN_SCORE = 62
STRONG_SCORE = 76
MIN_AGREEMENT = .58
STRONG_AGREEMENT = .68

# Risk is deliberately stricter than signal generation.
MIN_RR = 1.20
MIN_STOP_ATR = .75
MAX_STOP_ATR = 3.20
RISK_PER_TRADE = .005

ORDERFLOW_STALE = 8
ORDERFLOW_WINDOW = 90
BOOK_LEVELS = 20
LARGE_TRADE_Z = 2.0

# ---------------- HELPERS ----------------

def sf(x, default=0.0):
    try:
        x = float(x)
        return x if math.isfinite(x) else default
    except Exception:
        return default

def clamp(x, lo, hi):
    return float(max(lo, min(hi, x)))

def norm(x, scale=1.0):
    return float(np.tanh(x / max(scale, 1e-9)))

def now():
    return time.time()

def iso():
    return pd.Timestamp.now(tz="UTC").isoformat()

def journal(event):
    e = dict(event)
    e.setdefault("timestamp", iso())
    with JOURNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e, default=str) + "\n")

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {
        "last_signal_id": None,
        "last_signal_time": 0,
        "last_direction": None,
        "impulse": None,
        "missed_watch": None,
        "stats": {"signals": 0, "long": 0, "short": 0,
                  "early_watch": 0, "missed": 0},
    }

def save_state(s):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2, default=str))
    tmp.replace(STATE_FILE)

# ---------------- TELEGRAM ----------------

def telegram_enabled():
    return TOKEN and CHAT_ID and "PASTE_" not in TOKEN and "PASTE_" not in CHAT_ID

def telegram(text):
    if not telegram_enabled():
        print("\n[TELEGRAM DISABLED]\n" + text)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text,
                  "disable_web_page_preview": True},
            timeout=15,
        )
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception as e:
        print("Telegram error:", e)
        return False

# ---------------- COINBASE DATA ----------------

def candles(granularity, limit=300):
    r = requests.get(
        f"{REST}/products/{PRODUCT}/candles",
        params={"granularity": granularity},
        headers={"User-Agent": "BTC-ADAPTIVE-V5"},
        timeout=15,
    )
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        raise ValueError("No Coinbase candle data")

    d = pd.DataFrame(rows, columns=["time","low","high","open","close","volume"])
    for c in d.columns:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna().drop_duplicates("time").sort_values("time").tail(limit)
    d["time"] = pd.to_datetime(d["time"], unit="s", utc=True)
    return d.reset_index(drop=True)

def frames():
    return {
        "15m": candles(900),
        "1h": candles(3600),
        "2h": candles(7200),
        "4h": candles(14400),
    }

def closed_live(d, seconds):
    if len(d) < 50:
        raise ValueError("Not enough candles")
    t = d.iloc[-1]["time"].timestamp()
    if time.time() < t + seconds:
        return d.iloc[:-1].copy(), d.iloc[-1].copy()
    return d.copy(), d.iloc[-1].copy()

# ---------------- INDICATORS ----------------

def tr(d):
    pc = d.close.shift(1)
    return pd.concat([
        d.high-d.low,
        (d.high-pc).abs(),
        (d.low-pc).abs()
    ], axis=1).max(axis=1)

def atr(d, n=14):
    return tr(d).ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def rsi(d, n=14):
    z = d.close.diff()
    up = z.clip(lower=0)
    dn = -z.clip(upper=0)
    au = up.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = au/ad.replace(0, np.nan)
    out = 100 - 100/(1+rs)
    return out.fillna(np.where(au>0, 100, np.where(ad>0, 0, 50)))

def indicators(d):
    x = d.copy()
    x["atr"] = atr(x)
    x["rsi"] = rsi(x)
    for n in (9,21,50,200):
        x[f"ema{n}"] = x.close.ewm(span=n, adjust=False).mean()
    x["range"] = x.high-x.low
    x["body"] = (x.close-x.open).abs()
    x["body_pct"] = x.body/x["range"].replace(0,np.nan)
    x["vma"] = x.volume.rolling(20, min_periods=5).mean()
    x["rv"] = x.volume/x.vma.replace(0,np.nan)
    x["ret3"] = x.close.pct_change(3)
    x["ret8"] = x.close.pct_change(8)
    x["volz"] = (x.volume-x.volume.rolling(80,min_periods=20).mean()) / \
                x.volume.rolling(80,min_periods=20).std().replace(0,np.nan)
    x["atrpct"] = x.atr.rolling(120,min_periods=30).rank(pct=True)
    return x.dropna(subset=["atr","rsi"]).reset_index(drop=True)

def context(d):
    x = indicators(d)
    a = x.iloc[-1]
    parts = [
        np.sign(a.ema21-a.ema50),
        np.sign(a.ema50-a.ema200),
        np.sign(a.close-a.ema21),
        np.sign(a.ema21-x.ema21.iloc[-6]),
        clamp(sf(a.ret8)*20,-1,1),
    ]
    v = clamp(float(np.mean(parts)), -1, 1)
    if v > .25: label = "BULLISH"
    elif v < -.25: label = "BEARISH"
    else: label = "NEUTRAL"
    return {"direction":v,"trend":label,"rsi":sf(a.rsi,50),
            "atr":sf(a.atr),"rv":sf(a.rv,1),"close":sf(a.close)}

def structure(d):
    x = indicators(d)
    r = x.tail(min(24,len(x)))
    hi = float(r.high.iloc[:-1].max())
    lo = float(r.low.iloc[:-1].min())
    a = x.iloc[-1]
    return {
        "support":lo, "resistance":hi, "close":sf(a.close),
        "bull_break":bool(a.close>hi), "bear_break":bool(a.close<lo),
        "sweep_high":bool(a.high>hi and a.close<hi),
        "sweep_low":bool(a.low<lo and a.close>lo),
    }

# ---------------- EVIDENCE ENGINES ----------------
# Each returns independent long/short evidence in [0,1].
# They support signals; they do not individually veto them.

def trend_ev(ctx):
    v = .20*ctx["4h"]["direction"] + .25*ctx["2h"]["direction"] + \
        .35*ctx["1h"]["direction"] + .20*ctx["15m"]["direction"]
    return clamp(.5+.5*v,0,1), clamp(.5-.5*v,0,1)

def price_ev(d):
    x=indicators(d); a=x.iloc[-1]; p=x.iloc[-2]
    L=S=0; r=[]
    rng=max(sf(a.range),1e-9); loc=(a.close-a.low)/rng
    if a.close>a.open: L += min(.45,.20+sf(a.body_pct)*.35)
    else: S += min(.45,.20+sf(a.body_pct)*.35)
    if loc>.75: L+=.25; r.append("strong bullish close")
    if loc<.25: S+=.25; r.append("strong bearish close")
    uw=a.high-max(a.open,a.close); lw=min(a.open,a.close)-a.low
    if uw>sf(a.body)*1.4: S+=.30; r.append("upper-wick rejection")
    if lw>sf(a.body)*1.4: L+=.30; r.append("lower-wick rejection")
    if a.close>a.open and p.close<p.open and a.close>p.open and a.open<p.close:
        L+=.30; r.append("bullish engulfing")
    if a.close<a.open and p.close>p.open and a.close<p.open and a.open>p.close:
        S+=.30; r.append("bearish engulfing")
    return clamp(L,0,1),clamp(S,0,1),r

def momentum_ev(d):
    x=indicators(d); a=x.iloc[-1]
    L=S=0; r=[]; q=sf(a.rsi,50)
    if q>=55: L+=.30
    if q<=45: S+=.30
    if q>=70: S+=.10; r.append("RSI stretched")
    if q<=30: L+=.10; r.append("RSI stretched")
    if sf(a.ret3)>0: L+=.25
    elif sf(a.ret3)<0: S+=.25
    if sf(a.ret8)>0: L+=.25
    elif sf(a.ret8)<0: S+=.25
    return clamp(L,0,1),clamp(S,0,1),r

def volume_ev(d):
    x=indicators(d); a=x.iloc[-1]; rv=sf(a.rv,1); vz=sf(a.volz)
    strength=clamp((rv-1)/1.5,-.5,.8)
    L=S=0; r=[]
    if a.close>a.open: L+=max(0,strength)*.65
    elif a.close<a.open: S+=max(0,strength)*.65
    if vz>1.5: r.append("volume expansion")
    elif vz<-1: r.append("low-volume movement")
    return clamp(L,0,1),clamp(S,0,1),r

def structure_ev(d15,d1,d2):
    a=indicators(d15).iloc[-1]; s1=structure(d1); s2=structure(d2)
    L=S=0; r=[]
    if s2["bull_break"]: L+=.40; r.append("2H bullish break")
    if s2["bear_break"]: S+=.40; r.append("2H bearish break")
    if s1["bull_break"]: L+=.30; r.append("1H breakout")
    if s1["bear_break"]: S+=.30; r.append("1H breakdown")
    if s2["sweep_low"]: L+=.45; r.append("2H downside sweep")
    if s2["sweep_high"]: S+=.45; r.append("2H upside sweep")
    hi=indicators(d15).high.iloc[-6:-1].max()
    lo=indicators(d15).low.iloc[-6:-1].min()
    if a.close>hi: L+=.25; r.append("15M local breakout")
    if a.close<lo: S+=.25; r.append("15M local breakdown")
    return clamp(L,0,1),clamp(S,0,1),r

def reversal_ev(d15,d1,d2):
    x=indicators(d15); a=x.iloc[-1]; s1=structure(d1); s2=structure(d2)
    L=S=0; r=[]
    if s2["sweep_low"] or s1["sweep_low"]:
        L+=.45; r.append("major downside sweep")
    if s2["sweep_high"] or s1["sweep_high"]:
        S+=.45; r.append("major upside sweep")
    if a.rsi<38 and a.close>a.open:
        L+=.25; r.append("bearish exhaustion -> buyers")
    if a.rsi>62 and a.close<a.open:
        S+=.25; r.append("bullish exhaustion -> sellers")
    if a.high>s1["resistance"] and a.close<s1["resistance"]:
        S+=.40; r.append("failed upside breakout")
    if a.low<s1["support"] and a.close>s1["support"]:
        L+=.40; r.append("failed downside breakout")
    return clamp(L,0,1),clamp(S,0,1),r

def impulse_retest(state,d1):
    x=indicators(d1); a=x.iloc[-1]; p=x.iloc[-2]
    L=S=0; r=[]
    atrv=max(sf(a.atr),1e-9); body=abs(a.close-a.open)
    if a.close>a.open and body>.75*atrv and sf(a.ret3)>0:
        state["impulse"]={"dir":"LONG","level":float(max(p.high,a.open)),
                          "vol":sf(a.rv,1),"bars":0}
        r.append("new bullish impulse")
    elif a.close<a.open and body>.75*atrv and sf(a.ret3)<0:
        state["impulse"]={"dir":"SHORT","level":float(min(p.low,a.open)),
                          "vol":sf(a.rv,1),"bars":0}
        r.append("new bearish impulse")
    imp=state.get("impulse")
    if not imp: return state,L,S,r
    imp["bars"]=int(imp.get("bars",0))+1
    if imp["bars"]>20:
        state["impulse"]=None
        return state,L,S,r
    level=sf(imp["level"]); rv=sf(a.rv,1)
    if imp["dir"]=="LONG":
        if a.low<=level*1.0015 and a.close>level and rv<=max(1.15,imp["vol"]*.8):
            L+=.55; r.append("bullish impulse retest held")
        if a.close<level-.35*atrv:
            state["impulse"]=None
    else:
        if a.high>=level*.9985 and a.close<level and rv<=max(1.15,imp["vol"]*.8):
            S+=.55; r.append("bearish impulse retest held")
        if a.close>level+.35*atrv:
            state["impulse"]=None
    return state,clamp(L,0,1),clamp(S,0,1),r

# ---------------- ORDER FLOW ----------------

bids, asks = {}, {}
trades = deque(maxlen=4000)
flow_lock = threading.Lock()
flow = {"delta":0,"cvd_slope":0,"book":0,"large":0,"sweep":0,
        "spread":0,"updated":0}
prev_depth = {"bid":0,"ask":0}

def book_depth(book):
    return sum(sorted(book.values(),reverse=True)[:20])

def handle_l2(m):
    global prev_depth
    with flow_lock:
        if m.get("type")=="snapshot":
            bids.clear(); asks.clear()
            for p,s in m.get("bids",[]): bids[sf(p)]=sf(s)
            for p,s in m.get("asks",[]): asks[sf(p)]=sf(s)
        elif m.get("type")=="l2update":
            for side,p,s in m.get("changes",[]):
                book=bids if side=="buy" else asks
                p=sf(p); s=sf(s)
                if s<=0: book.pop(p,None)
                else: book[p]=s
        if bids and asks:
            bd,ad=book_depth(bids),book_depth(asks)
            tot=bd+ad
            flow["book"]=(bd-ad)/tot if tot else 0
            bb=max(bids); ba=min(asks)
            flow["spread"]=(ba-bb)/((ba+bb)/2)*10000 if ba>bb else 0
            old=prev_depth["bid"]+prev_depth["ask"]
            new=bd+ad
            # liquidity pull/add is only a modifier, never a directional gate
            flow["liq"]=(new-old)/old if old else 0
            prev_depth={"bid":bd,"ask":ad}
            flow["updated"]=now()

def handle_match(m):
    if m.get("type")!="match": return
    p,s=sf(m.get("price")),sf(m.get("size"))
    side=m.get("side")
    if p<=0 or s<=0 or side not in ("buy","sell"): return
    # Coinbase maker side: maker sell => aggressive buyer; maker buy => seller
    aggr="BUY" if side=="sell" else "SELL"
    with flow_lock:
        trades.append({"t":now(),"s":s,"a":aggr,"p":p})
        cut=now()-90
        while trades and trades[0]["t"]<cut: trades.popleft()
        buy=sum(t["s"] for t in trades if t["a"]=="BUY")
        sell=sum(t["s"] for t in trades if t["a"]=="SELL")
        total=buy+sell
        flow["delta"]=(buy-sell)/total if total else 0
        if len(trades)>=20:
            arr=np.array([t["s"] for t in trades])
            mu,sd=arr.mean(),arr.std()
            lb=sum(t["s"] for t in trades if t["a"]=="BUY" and t["s"]>mu+2*sd)
            ls=sum(t["s"] for t in trades if t["a"]=="SELL" and t["s"]>mu+2*sd)
            flow["large"]=(lb-ls)/(lb+ls) if lb+ls else 0
        flow["sweep"]=flow["delta"]
        flow["updated"]=now()

def flow_ev():
    with flow_lock: f=dict(flow)
    if now()-f.get("updated",0)>ORDERFLOW_STALE:
        return 0,0,["order flow stale"]
    raw=.40*f["delta"]+.20*f["cvd_slope"]+.20*f["book"]+.10*f["large"]+.10*f["sweep"]
    L=clamp(.5+.5*raw,0,1); S=clamp(.5-.5*raw,0,1)
    r=[]
    if f["delta"]>.25:r.append("aggressive buyers")
    if f["delta"]<-.25:r.append("aggressive sellers")
    if f["book"]>.25:r.append("bid depth stronger")
    if f["book"]<-.25:r.append("ask depth stronger")
    if abs(f["large"])>.3:r.append("large-trade bias")
    return L,S,r

def start_ws():
    try:
        import websocket
    except ImportError:
        print("Install websocket-client for live order flow.")
        return
    def loop():
        while True:
            try:
                def opened(ws):
                    ws.send(json.dumps({"type":"subscribe","product_ids":[PRODUCT],
                                        "channels":["level2","matches"]}))
                def msg(ws,raw):
                    try:
                        m=json.loads(raw)
                        if m.get("type") in ("snapshot","l2update"): handle_l2(m)
                        elif m.get("type")=="match": handle_match(m)
                    except Exception as e: print("WS msg:",e)
                app=websocket.WebSocketApp(WS,on_open=opened,on_message=msg)
                app.run_forever(ping_interval=20,ping_timeout=10)
            except Exception as e: print("WS:",e)
            time.sleep(3)
    threading.Thread(target=loop,daemon=True).start()

# ---------------- RISK / SCORE ----------------

def risk(direction,entry,d1):
    x=indicators(d1); a=x.iloc[-1]; atrv=sf(a.atr)
    rank=sf(a.atrpct,.5)
    stop_atr=MIN_STOP_ATR+rank*(MAX_STOP_ATR-MIN_STOP_ATR)
    dist=atrv*stop_atr
    s=structure(d1)
    if direction=="LONG":
        sl=entry-dist
        tp=max(entry+2*atrv,s["resistance"])
    else:
        sl=entry+dist
        tp=min(entry-2*atrv,s["support"])
    rr=abs(tp-entry)/max(abs(entry-sl),1e-9)
    return sl,tp,rr

def score(e):
    # Anti-double-counting: categories have capped influence.
    w={"trend":15,"structure":20,"price":15,"volume":10,
       "momentum":10,"retest":12,"orderflow":13,"reversal":10}
    total=sum(w.values())
    L=sum(clamp(v[0],0,1)*w[k] for k,v in e.items())/total*100
    S=sum(clamp(v[1],0,1)*w[k] for k,v in e.items())/total*100
    return clamp(L,0,100),clamp(S,0,100)

def build_signal(F,state):
    d15,live15=closed_live(F["15m"],900)
    d1,live1=closed_live(F["1h"],3600)
    d2,_=closed_live(F["2h"],7200)
    d4,_=closed_live(F["4h"],14400)

    C={"15m":context(d15),"1h":context(d1),"2h":context(d2),"4h":context(d4)}
    E={}
    E["trend"]=trend_ev(C)
    l,s,r=structure_ev(d15,d1,d2);E["structure"]=(l,s)
    reasons=r[:]
    l,s,r=price_ev(d15);E["price"]=(l,s);reasons+=r
    l,s,r=volume_ev(d1);E["volume"]=(l,s);reasons+=r
    l,s,r=momentum_ev(d1);E["momentum"]=(l,s);reasons+=r
    state,l,s,r=impulse_retest(state,d1);E["retest"]=(l,s);reasons+=r
    l,s,r=reversal_ev(d15,d1,d2);E["reversal"]=(l,s);reasons+=r
    l,s,r=flow_ev();E["orderflow"]=(l,s);reasons+=r

    # Live current-candle evidence is soft: it can raise urgency but is not
    # allowed to replace closed-candle confirmation.
    a=indicators(d1).iloc[-1]
    move=sf(live1["close"])-sf(live1["open"])
    velocity=abs(move)/max(sf(a.atr),1e-9)
    liveL=.0; liveS=.0
    if velocity>.35:
        if move>0: liveL=min(.65,velocity/2)
        elif move<0: liveS=min(.65,velocity/2)
    if liveL or liveS: reasons.append("live current-candle momentum")

    L,S=score(E)
    # Tiny regime adjustment. Opposite-side reversals remain possible.
    reg=.20*C["4h"]["direction"]+.25*C["2h"]["direction"]+\
        .30*C["1h"]["direction"]+.25*C["15m"]["direction"]
    L=clamp(L+4*reg,0,100); S=clamp(S-4*reg,0,100)

    direction="LONG" if L>S else "SHORT"
    best=max(L,S); other=min(L,S)
    agreement=best/max(L+S,1e-9)

    entry=sf(live1["close"],sf(d1.close.iloc[-1]))
    sl,tp,rr=risk(direction,entry,d1)

    # Only genuine risk/data conditions veto.
    veto=None
    if rr<MIN_RR: veto=f"R:R too low ({rr:.2f})"
    if not all(math.isfinite(v) for v in (entry,sl,tp,rr)): veto="invalid risk data"
    if sf(a.atr)<=0: veto="ATR unavailable"

    details={"L":L,"S":S,"agreement":agreement,"contexts":C,
             "evidence":E,"reasons":list(dict.fromkeys(reasons)),
             "live":(liveL,liveS),"state":state,"veto":veto}

    if veto or best<MIN_SCORE or agreement<MIN_AGREEMENT or abs(L-S)<8:
        return None,details

    if max(E["reversal"])>.55: setup="REVERSAL"
    elif max(E["retest"])>.45: setup="CONTINUATION / RETEST"
    elif E["structure"][0 if direction=="LONG" else 1]>.55:
        setup="BREAKOUT / STRUCTURE"
    else: setup="MOMENTUM"

    strength="STRONG" if best>=STRONG_SCORE and agreement>=STRONG_AGREEMENT else "MODERATE"
    if direction=="SHORT" and C["4h"]["trend"]=="BULLISH":
        details["reasons"].append("4H bullish context, but lower-timeframe short reversal")
    if direction=="LONG" and C["4h"]["trend"]=="BEARISH":
        details["reasons"].append("4H bearish context, but lower-timeframe long reversal")

    return {
        "direction":direction,"score":best,"agreement":agreement,
        "entry":entry,"sl":sl,"tp":tp,"rr":rr,
        "setup":setup,"strength":strength,
        "reasons":details["reasons"][:8],
    },details

# ---------------- MESSAGES / STATE ----------------

def sid(sig):
    raw=f'{sig["direction"]}|{round(sig["entry"],-1)}|{sig["setup"]}|{time.strftime("%Y%m%d%H")}'
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def message(sig,det):
    e="🟢" if sig["direction"]=="LONG" else "🔴"
    side="BUY" if sig["direction"]=="LONG" else "SELL"
    c=det["contexts"]
    return (
        f"{e} BTC {side}\n"
        f"Strength: {sig['strength']}\n"
        f"Score: {sig['score']:.0f}/100\n"
        f"Agreement: {sig['agreement']*100:.0f}%\n"
        f"Setup: {sig['setup']}\n\n"
        f"Entry: {sig['entry']:.2f}\nSL: {sig['sl']:.2f}\n"
        f"TP: {sig['tp']:.2f}\nR:R: {sig['rr']:.2f}\n\n"
        f"4H: {c['4h']['trend']}\n2H: {c['2h']['trend']}\n"
        f"1H: {c['1h']['trend']}\n15M: {c['15m']['trend']}\n\n"
        "Reasons:\n"+ "\n".join("• "+x for x in sig["reasons"][:6])+
        "\n\n⚠️ Dynamic risk model; no profit guarantee."
    )

def early_watch(state,F,det):
    l,s=det["live"]
    if max(l,s)<.35 or time.time()-state["last_signal_time"]<900:return
    d="LONG" if l>s else "SHORT"
    p=sf(F["1h"].iloc[-1].close)
    telegram(f"🟡 EARLY {d} WATCH\nLive move detected\nPrice: {p:.2f}\n"
             "This is not a confirmed entry.")
    state["stats"]["early_watch"]+=1
    state["last_signal_time"]=time.time()
    journal({"event":"EARLY_WATCH","direction":d,"price":p})

def missed(state,F,det):
    L,S=det["L"],det["S"]
    if max(L,S)<45:return
    d="LONG" if L>S else "SHORT"
    p=sf(F["1h"].iloc[-1].close)
    m=state.get("missed_watch")
    if not m or m["direction"]!=d:
        state["missed_watch"]={"direction":d,"start":p}
        return
    move=((p-m["start"])/m["start"]) if d=="LONG" else ((m["start"]-p)/m["start"])
    if move>=.006:
        state["stats"]["missed"]+=1
        journal({"event":"MISSED_OPPORTUNITY","direction":d,
                 "start":m["start"],"end":p,"move_pct":move*100,
                 "L":L,"S":S,"reason":det.get("veto")})
        state["missed_watch"]=None

# ---------------- BACKTEST ----------------

def download(days=60):
    end=int(time.time()); start=end-days*86400
    rows=[]
    cur=end
    while cur>start:
        a=max(start,cur-250*3600)
        r=requests.get(f"{REST}/products/{PRODUCT}/candles",
                       params={"granularity":3600,
                               "start":pd.Timestamp(a,unit="s",tz="UTC").isoformat(),
                               "end":pd.Timestamp(cur,unit="s",tz="UTC").isoformat()},
                       timeout=15)
        r.raise_for_status()
        rows+=r.json()
        cur=a-3600
        time.sleep(.15)
    d=pd.DataFrame(rows,columns=["time","low","high","open","close","volume"])
    for c in d.columns:d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.dropna().drop_duplicates("time").sort_values("time").reset_index(drop=True)
    d["time"]=pd.to_datetime(d.time,unit="s",utc=True)
    return d

def backtest(days):
    d=indicators(download(days))
    Rs=[]; equity=1; peak=1; dd=0; wins=losses=0; streak=mxstreak=0
    for i in range(220,len(d)-13):
        a=d.iloc[i]
        direction=np.sign(
            .35*np.sign(a.ema21-a.ema50)+
            .25*np.sign(a.ema50-a.ema200)+
            .20*np.sign(a.ret3)+.20*np.sign(a.ret8)
        )
        if direction==0:continue
        entry=sf(a.close); av=max(sf(a.atr),1e-9)
        stop_atr=.75+sf(a.atrpct,.5)*(3.2-.75)
        risk=av*stop_atr
        tp=entry+direction*risk*1.6
        sl=entry-direction*risk
        out=None
        for _,f in d.iloc[i+1:i+13].iterrows():
            hit_sl=(f.low<=sl) if direction>0 else (f.high>=sl)
            hit_tp=(f.high>=tp) if direction>0 else (f.low<=tp)
            if hit_sl and hit_tp:out=-1;break
            if hit_sl:out=-1;break
            if hit_tp:out=1.6;break
        if out is None:
            out=clamp(((d.iloc[min(i+12,len(d)-1)].close-entry)/risk)*direction,-1,1.6)
        Rs.append(out)
        if out>0:wins+=1;streak=0
        else:losses+=1;streak+=1;mxstreak=max(mxstreak,streak)
        equity*=max(.01,1+out*RISK_PER_TRADE)
        peak=max(peak,equity);dd=max(dd,(peak-equity)/peak)
    n=len(Rs); avg=float(np.mean(Rs)) if Rs else 0
    gp=sum(x for x in Rs if x>0); gl=abs(sum(x for x in Rs if x<0))
    result={"days":days,"trades":n,"win_rate":wins/n if n else 0,
            "avg_R":avg,"expectancy_R":avg,
            "profit_factor":gp/gl if gl else None,
            "max_drawdown":dd,"max_losing_streak":mxstreak}
    CALIBRATION_FILE.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

# ---------------- MAIN ----------------

def run():
    state=load_state()
    start_ws()
    print("BTC V5 running: 4H/2H context, 1H setup, 15M timing.")
    while True:
        t=time.time()
        try:
            F=frames()
            sig,det=build_signal(F,state)
            state=det["state"]
            if sig:
                age=time.time()-state["last_signal_time"]
                opposite=state["last_direction"] and state["last_direction"]!=sig["direction"]
                strong_opposite=opposite and sig["score"]>=STRONG_SCORE+5
                if age>=COOLDOWN_MIN*60 or strong_opposite:
                    telegram(message(sig,det))
                    state["last_signal_id"]=sid(sig)
                    state["last_signal_time"]=time.time()
                    state["last_direction"]=sig["direction"]
                    state["stats"]["signals"]+=1
                    state["stats"]["long" if sig["direction"]=="LONG" else "short"]+=1
                    journal({"event":"SIGNAL","signal":sig,"L":det["L"],"S":det["S"],
                             "contexts":det["contexts"],"evidence":det["evidence"]})
                    print("SIGNAL",sig)
                else: print("COOLDOWN",sig["direction"],sig["score"])
            else:
                early_watch(state,F,det)
                missed(state,F,det)
                print("WAIT",round(det["L"],1),round(det["S"],1),
                      "A",round(det["agreement"],2),det.get("veto"))
            save_state(state)
        except KeyboardInterrupt: break
        except Exception as e:
            print("ERROR",repr(e));journal({"event":"ERROR","error":repr(e)})
        time.sleep(max(2,SCAN_SECONDS-(time.time()-t)))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--backtest",type=int)
    p.add_argument("--status",action="store_true")
    a=p.parse_args()
    if a.status:
        print(json.dumps(load_state(),indent=2));return
    if a.backtest:
        backtest(a.backtest);return
    run()

if __name__=="__main__":
    main()

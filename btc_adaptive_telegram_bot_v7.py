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
SWEEP_CONFIRM_WEIGHTS = {"sweep_quality": .20, "structure": .20, "momentum": .15, "orderflow": .15, "reversal": .10, "retest": .10, "volume": .10}
# Sweep reversal is a separate candidate path. A sweep alone never creates an entry.
SWEEP_REVERSAL_MIN_QUALITY = 0.45
SWEEP_REVERSAL_STRONG_CONFIRMATION = 0.60
SWEEP_REVERSAL_STRONG_TREND_SCORE = 76
SWEEP_REVERSAL_STRONG_TREND_EXTRA = 0.05
SWEEP_REVERSAL_MIN_15M_CONFIRMATION = 0.55
SWEEP_REVERSAL_STRONG_15M_CONFIRMATION = 0.68
NEW_CONTINUATION_ENTRY_SCORE = 0.70
WEIGHTS = {"trend":15,"structure":20,"price":15,"volume":10,"momentum":10,"retest":12,"orderflow":13,"reversal":10}

# News: CryptoPanic endpoint is configurable. API key is optional; if the
# provider requires auth, set CRYPTOPANIC_API_KEY in GitHub Secrets.
NEWS_ENABLED = os.environ.get("NEWS_ENABLED", "1") != "0"
NEWS_API_KEY = os.environ.get("CRYPTOPANIC_API_KEY", "")
NEWS_API_URL = os.environ.get("NEWS_API_URL", "https://cryptopanic.com/api/developer/v2/posts/")
NEWS_LOOKBACK_MIN = int(os.environ.get("NEWS_LOOKBACK_MIN", "30"))
NEWS_MAX_ARTICLES = int(os.environ.get("NEWS_MAX_ARTICLES", "20"))
NEWS_MIN_RELEVANCE = float(os.environ.get("NEWS_MIN_RELEVANCE", ".60"))
NEWS_MIN_CONFIDENCE = float(os.environ.get("NEWS_MIN_CONFIDENCE", ".55"))
NEWS_CONFIRMATION_THRESHOLD = float(os.environ.get("NEWS_CONFIRMATION_THRESHOLD", ".60"))

CONT_LOOKBACK = 20
CONT_IMPULSE_BARS = 6
CONT_IMPULSE_ATR = 1.8
CONT_MIN_PULLBACK = .18
CONT_MAX_PULLBACK = .65
CONT_READY_SCORE = .62
MAX_TARGET_EXTENSIONS = 2
EXTENSION_COOLDOWN_MIN = 30
MAX_TRADE_AGE_HOURS = 36

session = requests.Session()
session.headers.update({"User-Agent":"BTC-Adaptive-Telegram-Bot-V7-GitHub/1.0"})

flow = {"bids":{},"asks":{},"delta":0.,"large":0.,"buy":0.,"sell":0.,"trade_count":0,"updated":0.}

def now_ts(): return time.time()
def iso(ts=None): return datetime.fromtimestamp(ts or now_ts(), tz=timezone.utc).isoformat()
def safe_float(x, default=0.):
    try: return float(x)
    except Exception: return default

def clamp(x,a=0.,b=1.): return max(a,min(b,safe_float(x)))
def coinbase_get(path, params=None):
    r=session.get(REST+path,params=params,timeout=REQUEST_TIMEOUT); r.raise_for_status(); return r.json()

def load_json(path, default):
    try:
        with open(path,encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def save_json(path,obj):
    tmp=path+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(obj,f,indent=2,ensure_ascii=False,default=str)
    os.replace(tmp,path)

def load_state():
    s=load_json(STATE_FILE,{})
    if not isinstance(s,dict): s={}
    defaults={"last_signal":None,"last_signal_ts":0,"last_direction":None,"last_scan_ts":0,"flow_snapshot":{},"last_sweep":None,"continuation":None,"news_events":[],"active_trades":{}}
    for k,v in defaults.items(): s.setdefault(k,v)
    return s

def save_state(s): save_json(STATE_FILE,s)

def journal(event,**kwargs):
    rec={"ts":iso(),"event":event,**kwargs}
    try:
        with open(JOURNAL_FILE,"a",encoding="utf-8") as f: f.write(json.dumps(rec,ensure_ascii=False,default=str)+"\n")
    except Exception as e: print("JOURNAL_ERROR",repr(e))

def csv_log(rec):
    try:
        os.makedirs("logs",exist_ok=True); exists=os.path.exists(CSV_LOG_FILE)
        fields=["timestamp","event","signal_id","direction","score","agreement","entry","sl","tp","rr","news_status","news_direction","news_confirmation","news_impact","news_confidence","news_relevance","news_event_count","continuation_state","continuation_score","pullback_depth","breakout_level","reversal_risk","lifecycle","target_version","previous_target","new_target","reason"]
        with open(CSV_LOG_FILE,"a",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields); 
            if not exists or os.path.getsize(CSV_LOG_FILE)==0: w.writeheader()
            w.writerow({k:rec.get(k,"") for k in fields})
    except Exception as e: journal("CSV_LOG_ERROR",error=repr(e))

def telegram(text, reply_to_message_id=None):
    if not TOKEN or not CHAT_ID:
        print("TELEGRAM_NOT_CONFIGURED\n"+text); return None
    url=f"https://api.telegram.org/bot{TOKEN}/sendMessage"; payload={"chat_id":CHAT_ID,"text":text}
    if reply_to_message_id:
        payload["reply_parameters"]={"message_id":int(reply_to_message_id)}
    try:
        r=session.post(url,json=payload,timeout=REQUEST_TIMEOUT); r.raise_for_status(); data=r.json()
        return data.get("result",{}).get("message_id")
    except Exception as e:
        if reply_to_message_id:
            try:
                r=session.post(url,json={"chat_id":CHAT_ID,"text":text},timeout=REQUEST_TIMEOUT); r.raise_for_status(); return r.json().get("result",{}).get("message_id")
            except Exception: pass
        journal("TELEGRAM_ERROR",error=repr(e)); print("TELEGRAM_ERROR",repr(e)); return None

def candles(granularity,limit=300):
    data=coinbase_get(f"/products/{PRODUCT}/candles",{"granularity":int(granularity)})
    rows=[{"ts":pd.to_datetime(int(x[0]),unit="s",utc=True),"low":safe_float(x[1]),"high":safe_float(x[2]),"open":safe_float(x[3]),"close":safe_float(x[4]),"volume":safe_float(x[5])} for x in data[:limit] if len(x)>=6]
    df=pd.DataFrame(rows)
    if df.empty: raise RuntimeError("No candles returned")
    return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)

def candles_1h_history(total=HISTORY_1H_BARS):
    g=3600; per=300; end=int(time.time()//g*g); chunks=[]
    for _ in range(math.ceil(total/per)):
        start=end-per*g
        data=coinbase_get(f"/products/{PRODUCT}/candles",{"granularity":g,"start":iso(start),"end":iso(end)})
        for x in data:
            if len(x)>=6: chunks.append({"ts":pd.to_datetime(int(x[0]),unit="s",utc=True),"low":safe_float(x[1]),"high":safe_float(x[2]),"open":safe_float(x[3]),"close":safe_float(x[4]),"volume":safe_float(x[5])})
        end=start-1
    df=pd.DataFrame(chunks)
    if df.empty: raise RuntimeError("No 1H history")
    cutoff=pd.Timestamp(int(time.time()//3600*3600),unit="s",tz="UTC")
    return df.sort_values("ts").drop_duplicates("ts").query("ts < @cutoff").tail(total).reset_index(drop=True)

def resample_ohlcv(df,hours):
    x=df.copy().set_index("ts").sort_index(); rule=f"{hours}h"
    out=x.resample(rule,origin="epoch",label="left",closed="left").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"})
    cnt=x["close"].resample(rule,origin="epoch",label="left",closed="left").count()
    return out[cnt>=hours].dropna().reset_index()

def closed_live(df):
    if len(df)<5: raise RuntimeError("Insufficient candles")
    d=df.copy(); d["ts"]=pd.to_datetime(d["ts"],utc=True); step=int((d.ts.iloc[-1]-d.ts.iloc[-2]).total_seconds())
    if step>0 and time.time()<d.ts.iloc[-1].timestamp()+step: return d.iloc[:-1].copy(),d.iloc[-1].copy()
    return d.copy(),d.iloc[-1].copy()

def frames():
    h1=candles_1h_history(); m15,m15_live=closed_live(candles(900,300)); return {"15m":m15,"15m_live":m15_live,"1h":h1,"2h":resample_ohlcv(h1,2),"4h":resample_ohlcv(h1,4)}

def atr(df,n=14):
    p=df.close.shift(1); tr=pd.concat([df.high-df.low,(df.high-p).abs(),(df.low-p).abs()],axis=1).max(axis=1); return tr.rolling(n).mean()

def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0); au=up.ewm(alpha=1/n,adjust=False).mean(); ad=dn.ewm(alpha=1/n,adjust=False).mean(); rs=au/ad.replace(0,np.nan); return (100-100/(1+rs)).fillna(50)

def prepare(df):
    x=df.copy(); x["atr"]=atr(x); x["rsi"]=rsi(x.close)
    for n in [9,21,50,200]: x[f"ema{n}"]=x.close.ewm(span=n,adjust=False).mean()
    x["vol_ma20"]=x.volume.rolling(20).mean(); x["vol_ratio"]=x.volume/x.vol_ma20.replace(0,np.nan); x["ret1"]=x.close.pct_change(); x["ret8"]=x.close.pct_change(8); x["vol_z"]=(x.volume-x.volume.rolling(30).mean())/x.volume.rolling(30).std(); x["ema21_prev"]=x.ema21.shift(3); x["hh20"]=x.high.rolling(20).max(); x["ll20"]=x.low.rolling(20).min(); x["hh8"]=x.high.rolling(8).max(); x["ll8"]=x.low.rolling(8).min()
    return x.dropna().reset_index(drop=True)

def trend_ev(df,direction):
    r=df.iloc[-1]; bull=r.close>r.ema21>r.ema50>r.ema200 and r.ema21>r.ema21_prev; bear=r.close<r.ema21<r.ema50<r.ema200 and r.ema21<r.ema21_prev
    if direction=="LONG": return 1.0 if bull else .5 if r.close>r.ema21 else 0.
    return 1.0 if bear else .5 if r.close<r.ema21 else 0.

def price_ev(df,direction):
    r=df.iloc[-1]; dist=(r.close-r.ema21)/max(r.atr,1e-9)
    return (1.0 if -.5<=dist<=1.5 else .5 if dist>0 else .25) if direction=="LONG" else (1.0 if -1.5<=dist<=.5 else .5 if dist<0 else .25)

def momentum_ev(df,direction):
    r=df.iloc[-1]
    if direction=="LONG": return 1.0 if 52<=r.rsi<=72 and r.ret8>0 else .5 if r.ret8>0 else 0.
    return 1.0 if 28<=r.rsi<=48 and r.ret8<0 else .5 if r.ret8<0 else 0.

def volume_ev(df):
    vr=safe_float(df.iloc[-1].vol_ratio,1); return 1.0 if vr>=1.25 else .5 if vr>=.9 else .25

def structure_ev(df,direction):
    r=df.iloc[-1]; return (1.0 if r.close>r.hh8*.998 else .5 if r.close>r.ema21 else .25) if direction=="LONG" else (1.0 if r.close<r.ll8*1.002 else .5 if r.close<r.ema21 else .25)

def reversal_ev(df,direction):
    if len(df)<3:return 0.
    a,b,c=df.iloc[-3],df.iloc[-2],df.iloc[-1]
    if direction=="LONG": return 1.0 if c.low<b.low and c.close>c.open and c.close>b.close else 0.
    return 1.0 if c.high>b.high and c.close<c.open and c.close<b.close else 0.
    for n in [9,21,50,200]: x[f"ema{n}"]=x.close.ewm(span=n,adjust=False).mean()
    x["vol_ma20"]=x.volume.rolling(20).mean(); x["vol_ratio"]=x.volume/x.vol_ma20.replace(0,np.nan); x["ret1"]=x.close.pct_change(); x["ret8"]=x.close.pct_change(8); x["vol_z"]=(x.volume-x.volume.rolling(30).mean())/x.volume.rolling(30).std(); x["ema21_prev"]=x.ema21.shift(3); x["hh20"]=x.high.rolling(20).max(); x["ll20"]=x.low.rolling(20).min(); x["hh8"]=x.high.rolling(8).max(); x["ll8"]=x.low.rolling(8).min()
    return x.dropna().reset_index(drop=True)

def trend_ev(df,direction):
    r=df.iloc[-1]; bull=r.close>r.ema21>r.ema50>r.ema200 and r.ema21>r.ema21_prev; bear=r.close<r.ema21<r.ema50<r.ema200 and r.ema21<r.ema21_prev
    if direction=="LONG": return 1.0 if bull else .5 if r.close>r.ema21 else 0.
    return 1.0 if bear else .5 if r.close<r.ema21 else 0.

def price_ev(df,direction):
    r=df.iloc[-1]; dist=(r.close-r.ema21)/max(r.atr,1e-9)
    return (1.0 if -.5<=dist<=1.5 else .5 if dist>0 else .25) if direction=="LONG" else (1.0 if -1.5<=dist<=.5 else .5 if dist<0 else .25)

def momentum_ev(df,direction):
    r=df.iloc[-1]
    if direction=="LONG": return 1.0 if 52<=r.rsi<=72 and r.ret8>0 else .5 if r.ret8>0 else 0.
    return 1.0 if 28<=r.rsi<=48 and r.ret8<0 else .5 if r.ret8<0 else 0.

def volume_ev(df):
    vr=safe_float(df.iloc[-1].vol_ratio,1); return 1.0 if vr>=1.25 else .5 if vr>=.9 else .25

def structure_ev(df,direction):
    r=df.iloc[-1]; return (1.0 if r.close>r.hh8*.998 else .5 if r.close>r.ema21 else .25) if direction=="LONG" else (1.0 if r.close<r.ll8*1.002 else .5 if r.close<r.ema21 else .25)

def reversal_ev(df,direction):
    if len(df)<3:return 0.
    a,b,c=df.iloc[-3],df.iloc[-2],df.iloc[-1]
    if direction=="LONG": return 1.0 if c.low<b.low and c.close>c.open and c.close>b.close else 0.
    return 1.0 if c.high>b.high and c.close<c.open and c.close<b.close else 0.

def book_depth():
    d=coinbase_get(f"/products/{PRODUCT}/book",{"level":2}); flow["bids"]={safe_float(p):safe_float(s) for p,s,*_ in d.get("bids",[])[:100]}; flow["asks"]={safe_float(p):safe_float(s) for p,s,*_ in d.get("asks",[])[:100]}; flow["updated"]=now_ts()

def recent_trades():
    d=coinbase_get(f"/products/{PRODUCT}/trades",{"limit":FLOW_TRADE_LIMIT}); buy=sell=large=delta=0.; n=0
    for t in d[:FLOW_TRADE_LIMIT]:
        if not isinstance(t,dict): continue
        size=safe_float(t.get("size")); side=str(t.get("side","")).lower(); n+=1
        if side=="sell": buy+=size; delta+=size; large+=size if size>=FLOW_LARGE_TRADE_BTC else 0
        elif side=="buy": sell+=size; delta-=size; large-=size if size>=FLOW_LARGE_TRADE_BTC else 0
    flow.update({"buy":buy,"sell":sell,"delta":delta,"large":large,"trade_count":n,"updated":now_ts()})

def flow_metrics():
    bid=sum(sorted(flow["bids"].values(),reverse=True)[:20]); ask=sum(sorted(flow["asks"].values(),reverse=True)[:20]); imb=(bid-ask)/max(bid+ask,1e-9)
    return {"bid20":bid,"ask20":ask,"imb":imb,"delta":flow["delta"],"large":flow["large"],"buy":flow["buy"],"sell":flow["sell"],"trade_count":flow["trade_count"],"updated":flow["updated"]}

def rest_flow_snapshot(state):
    try:
        book_depth(); recent_trades(); cur=flow_metrics(); prev=state.get("flow_snapshot",{}); cur["delta_change"]=cur["delta"]-safe_float(prev.get("delta")); cur["imb_change"]=cur["imb"]-safe_float(prev.get("imb")); state["flow_snapshot"]={k:cur.get(k,0) for k in ["imb","delta","large","buy","sell","trade_count"]}|{"ts":now_ts()}; return True,cur
    except Exception as e: journal("FLOW_REST_ERROR",error=repr(e)); return False,{"error":repr(e),"imb":0,"delta":0,"large":0,"buy":0,"sell":0,"trade_count":0,"updated":0}

def flow_ev():
    m=flow_metrics(); stale=now_ts()-m["updated"]>ORDERFLOW_STALE; score=0.
    if not stale:
        score += .5 if m["imb"]>.08 else -.5 if m["imb"]<-.08 else 0
        score += .35 if m["delta"]>0 else -.35 if m["delta"]<0 else 0
        score += .15 if m["large"]>0 else -.15 if m["large"]<0 else 0
    return {**m,"score":clamp((score+1)/2)*2-1 if not stale else 0.,"stale":stale,"text":f"imb={m['imb']:.2f} delta={m['delta']:.3f} large={m['large']:.3f} buy={m['buy']:.3f} sell={m['sell']:.3f} trades={m['trade_count']}"}

def orderflow_confirmation(fd,direction):
    if not fd or fd.get("stale"): return .5
    s=safe_float(fd.get("score")); return clamp((s+1)/2) if direction=="LONG" else clamp((-s+1)/2)

# -------------------- Sweep: current closed candle + prior 3 --------------------
def _sweep_at(d,i):
    if i<SWEEP_LOOKBACK: return None
    r=d.iloc[i]; h=d.iloc[i-SWEEP_LOOKBACK:i]; ph=float(h.high.max()); pl=float(h.low.min()); atrv=max(safe_float(r.atr),float(r.close)*1e-4); rng=max(float(r.high-r.low),1e-9)
    bs=float(r.high)>ph and float(r.close)<ph; ss=float(r.low)<pl and float(r.close)>pl
    if not(bs or ss): return None
    if bs and ss:
        hp=(r.high-ph)/atrv; lp=(pl-r.low)/atrv; rec=max((ph-r.close)/rng,(r.close-pl)/rng); return {"type":"DOUBLE_SWEEP","level":ph if hp>=lp else pl,"distance_atr":max(hp,lp),"reclaim_strength":clamp(rec),"quality":clamp(.5*clamp(max(hp,lp)/.5)+.5*clamp(rec)),"age":0,"direction":None,"candle_ts":r.ts.isoformat(),"extreme":float(r.high) if hp>=lp else float(r.low)}
    if ss:
        pen=(pl-r.low)/atrv; rec=clamp((r.close-pl)/rng); rej=clamp((min(r.open,r.close)-r.low)/rng); return {"type":"SELL_SIDE_SWEEP","level":pl,"distance_atr":pen,"reclaim_strength":rec,"quality":clamp(.4*clamp(pen/.5)+.4*rec+.2*rej),"age":0,"direction":"LONG","candle_ts":r.ts.isoformat(),"extreme":float(r.low)}
    pen=(r.high-ph)/atrv; rec=clamp((ph-r.close)/rng); rej=clamp((r.high-max(r.open,r.close))/rng); return {"type":"BUY_SIDE_SWEEP","level":ph,"distance_atr":pen,"reclaim_strength":rec,"quality":clamp(.4*clamp(pen/.5)+.4*rec+.2*rej),"age":0,"direction":"SHORT","candle_ts":r.ts.isoformat(),"extreme":float(r.high)}

def detect_liquidity_sweep(df):
    d=prepare(df)
    for age in range(SWEEP_MAX_AGE):
        i=len(d)-1-age
        s=_sweep_at(d,i)
        if s: s["age"]=age; return s
    return {"type":"NONE","age":None,"direction":None,"level":0.,"distance_atr":0.,"reclaim_strength":0.,"quality":0.}

def sweep_confirmation(direction,sweep,vals,fd):
    if not sweep or sweep.get("type")=="NONE": return {"active":False,"confirmation":0.,"reason":"no_sweep"}
    if sweep.get("type")=="DOUBLE_SWEEP": return {"active":True,"confirmation":0.,"reason":"double_sweep_direction_unclear"}
    if sweep.get("direction")!=direction: return {"active":True,"confirmation":0.,"reason":"opposite_sweep"}
    if safe_float(sweep.get("distance_atr"))<SWEEP_MIN_PENETRATION_ATR: return {"active":True,"confirmation":0.,"reason":"sweep_too_shallow"}
    if safe_float(sweep.get("reclaim_strength"))<SWEEP_MIN_RECLAIM: return {"active":True,"confirmation":0.,"reason":"reclaim_too_weak"}
    c={"sweep_quality":clamp(sweep.get("quality")),"structure":clamp(vals["structure"]),"momentum":clamp(vals["momentum"]),"orderflow":orderflow_confirmation(fd,direction),"reversal":clamp(vals["reversal"]),"retest":clamp(vals["retest"]),"volume":clamp(vals["volume"])}; conf=sum(SWEEP_CONFIRM_WEIGHTS[k]*c[k] for k in c)
    return {"active":True,"confirmation":conf,"reason":"confirmed" if conf>=SWEEP_MIN_CONFIRMATION else "confirmation_low",**c}

def sweep_text(s,c):
    if not s or s.get("type")=="NONE": return "Sweep: NONE"
    return f"Sweep: {s.get('type')} | age={s.get('age')} | dist={safe_float(s.get('distance_atr')):.2f} ATR | reclaim={safe_float(s.get('reclaim_strength'))*100:.0f}% | quality={safe_float(s.get('quality'))*100:.0f}% | confirmation={safe_float(c.get('confirmation'))*100:.0f}% ({c.get('reason')})"

# -------------------- Post impulse continuation --------------------
def continuation_engine(df,direction,state,fd,mutate=False):
    d=prepare(df); r=d.iloc[-1]; out={"state":"NONE","score":0.,"pullback_depth":0.,"breakout_level":0.,"impulse_range":0.,"reasons":[],"reversal_risk":0.}
    prev=d.iloc[:-1]; look=prev.tail(CONT_LOOKBACK)
    if len(look)<10: return out
    atrv=max(safe_float(r.atr),r.close*.0001)
    move=(r.close-d.close.iloc[-CONT_IMPULSE_BARS-1])/atrv if len(d)>CONT_IMPULSE_BARS+1 else 0
    direction_move=move if direction=="LONG" else -move
    prior_high=float(look.high.max()); prior_low=float(look.low.min())
    breakout=prior_high if direction=="LONG" else prior_low
    key="continuation"
    st=copy.deepcopy(state.get(key)) or {}
    if st.get("direction")!=direction or st.get("status") in ("INVALIDATED","CLOSED"):
        st={}
    if not st and direction_move>=CONT_IMPULSE_ATR:
        imp_low=float(d.low.iloc[-CONT_IMPULSE_BARS-1:-1].min()); imp_high=float(d.high.iloc[-CONT_IMPULSE_BARS-1:-1].max()); st={"direction":direction,"status":"IMPULSE","breakout_level":breakout,"impulse_low":imp_low,"impulse_high":imp_high,"impulse_close":float(r.close),"created_ts":now_ts(),"age":0}
    if not st: return out
    st["age"]=int(st.get("age",0))+1; bl=safe_float(st.get("breakout_level")); ih=safe_float(st.get("impulse_high")); il=safe_float(st.get("impulse_low")); ir=max(ih-il,atrv)
    if direction=="LONG":
        depth=clamp((ih-float(r.low))/ir); hold=float(r.close)>=bl-.25*atrv; reac=float(r.close)>float(d.close.iloc[-2]); structure=float(r.low)>=float(d.low.tail(5).min())*.999
        risk=(0 if hold else .35)+(.25 if r.rsi<45 else 0)+(.25 if safe_float(fd.get("score"))<-.45 else 0)+(.20 if r.close<r.ema21-.25*atrv else 0)
        accel=(r.close-d.close.iloc[-2])/atrv
    else:
        depth=clamp((float(r.high)-il)/ir); hold=float(r.close)<=bl+.25*atrv; reac=float(r.close)<float(d.close.iloc[-2]); structure=float(r.high)<=float(d.high.tail(5).max())*1.001
        risk=(0 if hold else .35)+(.25 if r.rsi>55 else 0)+(.25 if safe_float(fd.get("score"))>.45 else 0)+(.20 if r.close>r.ema21+.25*atrv else 0)
        accel=(d.close.iloc[-2]-r.close)/atrv
    risk=clamp(risk); score=.25*clamp(direction_move/3)+.20*clamp(1-abs(depth-.40)/.40)+.20*clamp(1-risk)+.15*(1 if hold else 0)+.10*(1 if reac else 0)+.10*orderflow_confirmation(fd,direction)
    if risk>.65 or (direction=="LONG" and r.close<bl-.35*atrv) or (direction=="SHORT" and r.close>bl+.35*atrv): status="INVALIDATED"
    elif depth>=CONT_MIN_PULLBACK and depth<=CONT_MAX_PULLBACK and hold: status="CONTINUATION_READY" if score>=CONT_READY_SCORE and accel>0 else "PULLBACK"
    elif direction_move>=CONT_IMPULSE_ATR: status="IMPULSE"
    else: status="NONE"
    out={"state":status,"score":score,"pullback_depth":depth,"breakout_level":bl,"impulse_range":ir,"reasons":["breakout_hold" if hold else "breakout_lost","healthy_depth" if CONT_MIN_PULLBACK<=depth<=CONT_MAX_PULLBACK else "depth_outside_range","reacceleration" if accel>0 else "no_reacceleration"],"reversal_risk":risk}
    st.update({"status":status,"breakout_level":bl,"age":st["age"],"pullback_depth":depth,"score":score,"reversal_risk":risk})
    if mutate:
        state[key]=st if status not in ("INVALIDATED","NONE") else None
    return out

def reversal_risk(df,direction,fd,cont): return clamp(cont.get("reversal_risk",0.))

# -------------------- News engine --------------------
BULL=["etf inflow","etf approval","institutional buy","institutional buying","reserve","adoption","accumulate","accumulation","dovish","rate cut","cuts rates","easing","positive regulation","clarity","approval","inflow","buyback","treasury buys"]
BEAR=["etf outflow","institutional sell","institutional selling","hack","exploit","insolvency","ban","crackdown","hawkish","rate hike","higher rates","liquidation","seized","lawsuit","outflow","miner selling","selloff"]
CATS={"etf":["etf","spot bitcoin","fund flow"],"regulation":["sec","regulation","regulator","law","ban","court"],"macro":["fed","federal reserve","inflation","cpi","jobs","rates","yield","dollar"],"institutional":["institutional","blackrock","fidelity","microstrategy","strategy","treasury"],"exchange":["coinbase","binance","kraken","listing","delist"],"security":["hack","exploit","breach","stolen","attack"],"stablecoin":["stablecoin","tether","usdt","usdc"],"mining":["miner","mining","hashrate","difficulty"],"network":["bitcoin network","halving","upgrade","fee"],"legal":["lawsuit","court","settlement","sec"]}

def news_source_quality(src):
    s=(src or "").lower(); return .9 if any(x in s for x in ["reuters","bloomberg","wsj","cnbc"]) else .82 if any(x in s for x in ["coindesk","the block","cointelegraph","decrypt"]) else .65

def classify_news(title,body,source,published):
    text=(title+" "+body).lower(); bull=sum(1 for k in BULL if k in text); bear=sum(1 for k in BEAR if k in text); direction="LONG" if bull>bear and bull>0 else "SHORT" if bear>bull and bear>0 else "NEUTRAL"; margin=abs(bull-bear); cat="other"
    for k,words in CATS.items():
        if any(w in text for w in words): cat=k; break
    relevance=clamp(.45+.18*text.count("bitcoin")+.10*text.count("btc")+.08*(cat!="other"))
    impact=clamp(.35+.12*min(margin,4)+(.15 if cat in ["etf","macro","regulation","security","institutional"] else 0))
    try: age=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(published.replace("Z","+00:00"))).total_seconds()/60); freshness=math.exp(-age/NEWS_LOOKBACK_MIN)
    except Exception: freshness=.5
    sq=news_source_quality(source); confidence=clamp(.35+.25*min(margin,2)+.20*sq+.20*relevance)
    return {"direction":direction,"impact":impact,"confidence":confidence,"freshness":freshness,"relevance":relevance,"source_quality":sq,"category":cat}

def news_fingerprint(title):
    words=re.findall(r"[a-z0-9]+",title.lower()); words=[w for w in words if len(w)>2 and w not in {"the","and","for","with","bitcoin","btc"}]; return hashlib.sha1(" ".join(words[:16]).encode()).hexdigest()[:16]

def fetch_news(state):
    if not NEWS_ENABLED: return {"status":"disabled","direction":"NEUTRAL","confirmation":0.,"events":[],"count":0}
    params={"currencies":"BTC","kind":"news","limit":NEWS_MAX_ARTICLES}
    if NEWS_API_KEY: params["auth_token"]=NEWS_API_KEY
    try:
        r=session.get(NEWS_API_URL,params=params,timeout=REQUEST_TIMEOUT); r.raise_for_status(); data=r.json(); items=data.get("results",data.get("data",data if isinstance(data,list) else []))
    except Exception as e:
        journal("NEWS_ERROR",error=repr(e)); return {"status":"unavailable","direction":"NEUTRAL","confirmation":0.,"events":[],"count":0,"error":repr(e)}
    events=[]; seen=state.get("news_events",[]); cutoff=now_ts()-NEWS_LOOKBACK_MIN*60
    for a in items[:NEWS_MAX_ARTICLES]:
        if not isinstance(a,dict): continue
        title=str(a.get("title") or a.get("headline") or "").strip(); url=str(a.get("url") or a.get("link") or ""); body=str(a.get("description") or a.get("body") or ""); src=a.get("source") or a.get("source_name") or "unknown"; src=src.get("title") if isinstance(src,dict) else str(src)
        pub=str(a.get("published_at") or a.get("published") or a.get("created_at") or "")
        if not title: continue
        c=classify_news(title,body,src,pub); fp=news_fingerprint(title); c.update({"event_id":fp,"headline":title,"source":src,"published_at":pub,"url":url})
        if c["relevance"]<NEWS_MIN_RELEVANCE or c["confidence"]<NEWS_MIN_CONFIDENCE or c["freshness"]<.10: continue
        if any(e.get("event_id")==fp and safe_float(e.get("seen_ts"))>cutoff for e in seen): continue
        events.append(c); seen.append({"event_id":fp,"seen_ts":now_ts(),"headline":title})
    # group by event fingerprint; each event counts once
    seen=seen[-150:]; state["news_events"]=seen
    if not events: return {"status":"neutral","direction":"NEUTRAL","confirmation":0.,"events":[],"count":0}
    long_score=sum(e["impact"]*e["confidence"]*e["freshness"]*e["source_quality"] for e in events if e["direction"]=="LONG"); short_score=sum(e["impact"]*e["confidence"]*e["freshness"]*e["source_quality"] for e in events if e["direction"]=="SHORT"); total=long_score+short_score; direction="LONG" if long_score>short_score else "SHORT" if short_score>long_score else "NEUTRAL"; confirmation=clamp(abs(long_score-short_score)/max(total,.01)); return {"status":"supportive" if confirmation>=NEWS_CONFIRMATION_THRESHOLD else "neutral","direction":direction,"confirmation":confirmation,"events":events,"count":len(events),"impact":max([e["impact"] for e in events],default=0),"confidence":max([e["confidence"] for e in events],default=0),"relevance":max([e["relevance"] for e in events],default=0),"freshness":max([e["freshness"] for e in events],default=0),"source_quality":max([e["source_quality"] for e in events],default=0)}
def news_alignment(news,direction):
    if news.get("status") in ("unavailable","disabled") or news.get("count",0)==0: return "UNAVAILABLE" if news.get("status")=="unavailable" else "NEUTRAL"
    if news.get("confirmation",0)<NEWS_CONFIRMATION_THRESHOLD: return "NEUTRAL"
    return "SUPPORTIVE" if news.get("direction")==direction else "CONFLICT"

# -------------------- Scoring/risk --------------------
def score_direction(F,direction,state,fd=None):
    m15,h2,h4=map(lambda k:prepare(F[k]),["15m","2h","4h"]); fd=fd or flow_ev(); vals={"trend":np.mean([trend_ev(h4,direction),trend_ev(h2,direction),trend_ev(m15,direction)]),"structure":structure_ev(m15,direction),"price":price_ev(m15,direction),"volume":volume_ev(m15),"momentum":momentum_ev(m15,direction),"retest":0.,"orderflow":orderflow_confirmation(fd,direction),"reversal":reversal_ev(m15,direction)}
    cont=continuation_engine(m15,direction,state,fd,False); vals["retest"]=max(vals["retest"],cont["score"] if cont["state"] in ("PULLBACK","CONTINUATION_READY") else 0.)
    score=sum(WEIGHTS[k]*clamp(vals[k]) for k in WEIGHTS); agreement=sum(v>=.5 for v in vals.values())/len(vals)
    return {"direction":direction,"score":score,"agreement":agreement,"vals":vals,"flow":fd,"prepared":{"15m":m15,"2h":h2,"4h":h4},"continuation":cont}

def risk_plan(df,direction,entry=None):
    r=df.iloc[-1]; entry=safe_float(entry or r.close); av=max(safe_float(r.atr),entry*.005); sd=1.25*av; td=2*av; stop=entry-sd if direction=="LONG" else entry+sd; target=entry+td if direction=="LONG" else entry-td; return {"entry":entry,"stop":stop,"target":target,"rr":td/sd,"atr":av,"stop_atr":1.25,"target_atr":2.}

def sweep_reversal_plan(df,direction,sweep):
    """Risk plan for a confirmed sweep reversal.
    Stop is protected beyond the actual sweep extreme; target prefers the
    nearest opposing 15m structure and falls back to 2 ATR when needed.
    """
    r=df.iloc[-1]; entry=safe_float(r.close); av=max(safe_float(r.atr),entry*.005)
    extreme=safe_float(sweep.get("extreme"))
    if extreme<=0:
        return risk_plan(df,direction,entry)

    pad=.35*av
    if direction=="LONG":
        stop=extreme-pad
        candidates=[safe_float(x) for x in df.iloc[:-1].high.tail(30) if safe_float(x)>entry]
        structure_target=min(candidates) if candidates else entry+2.0*av
        fallback=entry+2.0*av
        target=max(structure_target,entry+1.20*abs(entry-stop))
        if target<=entry: target=fallback
    else:
        stop=extreme+pad
        candidates=[safe_float(x) for x in df.iloc[:-1].low.tail(30) if safe_float(x)<entry]
        structure_target=max(candidates) if candidates else entry-2.0*av
        fallback=entry-2.0*av
        target=min(structure_target,entry-1.20*abs(stop-entry))
        if target>=entry: target=fallback

    sd=abs(entry-stop); reward=abs(target-entry); rr=reward/max(sd,1e-9)
    if sd<=0 or rr<1.20:
        stop=entry-(1.25*av) if direction=="LONG" else entry+(1.25*av)
        target=entry+(2.0*av) if direction=="LONG" else entry-(2.0*av)
        rr=2.0/1.25
        return {"entry":entry,"stop":stop,"target":target,"rr":rr,"atr":av,
                "stop_atr":1.25,"target_atr":2.0,"risk_source":"atr_fallback"}
    return {"entry":entry,"stop":stop,"target":target,"rr":rr,"atr":av,
            "stop_atr":sd/av,"target_atr":reward/av,"risk_source":"sweep_extreme_structure"}

def sweep_reversal_candidate(F,state,sweep,fd):
    """Evaluate a liquidity sweep as an independent reversal setup.
    The 1H sweep is only the setup; a separate 15m confirmation is required.
    """
    base={"active":False,"eligible":False,"direction":None,"score":0.,"agreement":0.,
          "confirmation":0.,"tf_confirmation":0.,"reason":"no_sweep"}
    if not sweep or sweep.get("type") in (None,"NONE"):
        return base
    if sweep.get("type")=="DOUBLE_SWEEP" or not sweep.get("direction"):
        return {**base,"active":True,"reason":"double_sweep_direction_unclear"}

    direction=sweep["direction"]
    if safe_float(sweep.get("distance_atr")) < SWEEP_MIN_PENETRATION_ATR:
        return {**base,"active":True,"direction":direction,"reason":"sweep_too_shallow"}
    if safe_float(sweep.get("reclaim_strength")) < SWEEP_MIN_RECLAIM:
        return {**base,"active":True,"direction":direction,"reason":"reclaim_too_weak"}
    if safe_float(sweep.get("quality")) < SWEEP_REVERSAL_MIN_QUALITY:
        return {**base,"active":True,"direction":direction,"reason":"sweep_quality_low"}

    cand=score_direction(F,direction,state,fd)
    vals=cand["vals"]
    sc=sweep_confirmation(direction,sweep,vals,fd)

    # Dedicated 15m reversal confirmation. This prevents a 1H sweep alone
    # from becoming an entry and avoids using the normal trend score as proof
    # of reversal.
    tf_parts={
        "structure":clamp(vals["structure"]),
        "momentum":clamp(vals["momentum"]),
        "reversal":clamp(vals["reversal"]),
        "orderflow":clamp(vals["orderflow"]),
        "volume":clamp(vals["volume"]),
        "price":clamp(vals["price"]),
    }
    tf_weights={"structure":.30,"momentum":.20,"reversal":.20,"orderflow":.15,"volume":.10,"price":.05}
    tf_conf=sum(tf_weights[k]*tf_parts[k] for k in tf_parts)
    sweep_conf=safe_float(sc.get("confirmation"))
    combined=.55*sweep_conf+.45*tf_conf

    # If the normal trend is very strong against the sweep, demand stronger
    # reversal evidence instead of immediately flipping direction.
    normal_opposite=score_direction(F,"LONG" if direction=="SHORT" else "SHORT",state,fd)
    required=SWEEP_MIN_CONFIRMATION
    if normal_opposite["score"]>=SWEEP_REVERSAL_STRONG_TREND_SCORE and normal_opposite["agreement"]>=STRONG_AGREEMENT:
        required=max(required,SWEEP_REVERSAL_STRONG_15M_CONFIRMATION)

    eligible=(cand["score"]>=MIN_SCORE and cand["agreement"]>=MIN_AGREEMENT
              and sweep_conf>=SWEEP_MIN_CONFIRMATION
              and tf_conf>=SWEEP_REVERSAL_MIN_15M_CONFIRMATION
              and combined>=required)
    reason="confirmed" if eligible else ("15m_reversal_weak" if tf_conf<SWEEP_REVERSAL_MIN_15M_CONFIRMATION else "confirmation_low")
    return {**base,"active":True,"eligible":eligible,"direction":direction,
            "score":cand["score"],"agreement":cand["agreement"],"vals":vals,
            "flow":fd,"prepared":cand["prepared"],"continuation":cand["continuation"],
            "confirmation":combined,"tf_confirmation":tf_conf,"sweep_confirmation":sc,
            "reason":reason,"required_confirmation":required}

def build_signal(F,state,news,fd):
    # Two independent paths:
    # A) normal trend/continuation
    # B) liquidity-sweep reversal
    le=score_direction(F,"LONG",state,fd)
    se=score_direction(F,"SHORT",state,fd)
    normal=se if se["score"]>le["score"] else le
    sweep=detect_liquidity_sweep(F["1h"])
    reversal=sweep_reversal_candidate(F,state,sweep,fd)

    if reversal.get("eligible"):
        best={"direction":reversal["direction"],"score":reversal["score"],
              "agreement":reversal["agreement"],"vals":reversal["vals"],
              "flow":fd,"prepared":reversal["prepared"],
              "continuation":reversal["continuation"]}
        setup_type="LIQUIDITY SWEEP REVERSAL"
        sc=reversal["sweep_confirmation"]
        plan=sweep_reversal_plan(best["prepared"]["15m"],best["direction"],sweep)
    else:
        best=normal
        setup_type="TREND CONTINUATION"
        sc=sweep_confirmation(best["direction"],sweep,best["vals"],fd)
        plan=risk_plan(best["prepared"]["15m"],best["direction"])

    direction=best["direction"]
    vals=best["vals"]
    cont=best["continuation"]
    reasons=[]
    if best["score"]<MIN_SCORE: reasons.append("score_low")
    if best["agreement"]<MIN_AGREEMENT: reasons.append("agreement_low")
    if plan["rr"]<1.2: reasons.append("rr_low")

    # A confirmed opposite sweep reversal blocks a normal trend entry.
    # A weak/unconfirmed sweep does NOT block a valid normal trend setup.
    if setup_type=="LIQUIDITY SWEEP REVERSAL":
        if not reversal.get("eligible"): reasons.append("reversal_not_confirmed")
    elif reversal.get("active") and reversal.get("direction") and reversal.get("direction")!=direction:
        if (safe_float(reversal.get("confirmation"))>=SWEEP_REVERSAL_STRONG_CONFIRMATION
            and safe_float(reversal.get("score"))>=MIN_SCORE
            and safe_float(reversal.get("agreement"))>=MIN_AGREEMENT):
            reasons.append("opposite_reversal_unresolved")

    news_status=news_alignment(news,direction)
    det={"direction":direction,"score":best["score"],"agreement":best["agreement"],
         "vals":vals,"flow":fd,"plan":plan,"sweep":sweep,
         "sweep_confirmation":sc,"reversal_candidate":reversal,
         "normal_long":le,"normal_short":se,"normal_direction":normal["direction"],
         "normal_score":normal["score"],"normal_agreement":normal["agreement"],
         "continuation":cont,"news":news,"news_alignment":news_status,
         "setup_type":setup_type}
    if reasons:
        det["reason"]=",".join(reasons)
        return None,det

    strength="STRONG" if best["score"]>=STRONG_SCORE and best["agreement"]>=STRONG_AGREEMENT else "SIGNAL"
    if setup_type=="LIQUIDITY SWEEP REVERSAL":
        strength="SWEEP_REVERSAL_"+strength
    elif sc.get("reason")=="confirmed":
        strength="SWEEP_CONFIRMED_"+strength
    if cont.get("state")=="CONTINUATION_READY" and setup_type!="LIQUIDITY SWEEP REVERSAL":
        strength="CONTINUATION_"+strength
    if news_status=="SUPPORTIVE" and news.get("confirmation",0)>=.75:
        strength="NEWS_CONFIRMED_"+strength

    ts=best["prepared"]["15m"].iloc[-1].ts.isoformat()
    sid=f"{direction}_{setup_type.replace(' ','_')}_{ts}"
    sig={"id":sid,"ts":iso(),"entry_candle_ts":ts,"direction":direction,
         "strength":strength,"setup_type":setup_type,"score":best["score"],
         "agreement":best["agreement"],"vals":vals,"flow":fd,"plan":plan,
         "sweep":sweep,"sweep_confirmation":sc,"reversal_candidate":reversal,
         "normal_long":le,"normal_short":se,"continuation":cont,"news":news,
         "news_alignment":news_status}
    det["reason"]="all_gates_pass"
    return sig,det

def format_signal(sig):
    p=sig["plan"]; v=sig["vals"]; c=sig["continuation"]; n=sig["news"]
    setup=sig.get("setup_type","TREND CONTINUATION")
    news_event=n.get("events",[{}])[0] if n.get("events") else {}
    headline=news_event.get("headline","")

    if setup=="LIQUIDITY SWEEP REVERSAL":
        title="🔴 SHORT ENTRY — LIQUIDITY SWEEP REVERSAL" if sig["direction"]=="SHORT" else "🟢 LONG ENTRY — LIQUIDITY SWEEP REVERSAL"
        sweep_block=(f"Setup: LIQUIDITY SWEEP REVERSAL\n"
                     f"Sweep: {sig['sweep'].get('type','NONE')}\n"
                     f"Sweep Direction: {sig['sweep'].get('direction','NONE')}\n"
                     f"1H Sweep Confirmation: {sig['sweep_confirmation'].get('confirmation',0)*100:.0f}% ✓\n"
                     f"15m Reversal Confirmation: {sig['reversal_candidate'].get('tf_confirmation',0)*100:.0f}% ✓\n"
                     f"Final Reversal Confirmation: {sig['reversal_candidate'].get('confirmation',0)*100:.0f}% ✓")
        why="1H liquidity sweep + 15m reversal confirmation aligned."
    else:
        title=f"🟢 {sig['direction']} ENTRY"
        sweep_block=f"Setup: TREND CONTINUATION\n{sweep_text(sig['sweep'],sig['sweep_confirmation'])}"
        why="Trend, structure, momentum and risk gates aligned."

    return (f"{title}\n\n"
            f"Entry: {p['entry']:.2f}\nStop Loss: {p['stop']:.2f}\nTarget: {p['target']:.2f}\nRR: {p['rr']:.2f}\n\n"
            f"Why: {why}\n\n{sweep_block}\n\n"
            f"Score: {sig['score']:.1f}/{sum(WEIGHTS.values())}\n"
            f"Agreement: {sig['agreement']*100:.0f}%\n\n"
            f"Trend: {v['trend']:.2f} | Structure: {v['structure']:.2f}\n"
            f"Price: {v['price']:.2f} | Volume: {v['volume']:.2f}\n"
            f"Momentum: {v['momentum']:.2f} | Retest: {v['retest']:.2f}\n"
            f"Reversal: {v['reversal']:.2f} | Order Flow: {v['orderflow']:.2f}\n\n"
            f"Post-Impulse: {c.get('state')} | Score {c.get('score',0)*100:.0f}%\n"
            f"Pullback: {c.get('pullback_depth',0)*100:.0f}% | Reversal Risk: {c.get('reversal_risk',0)*100:.0f}%\n\n"
            f"News: {sig['news_alignment']} | Confirmation {n.get('confirmation',0)*100:.0f}% | Events {n.get('count',0)}"
            + (f"\nHeadline: {headline[:180]}" if headline else "")
            + f"\n\nSignal ID: {sig['id']}\nUTC: {sig['ts']}")

def ticker_price(fallback):
    try: return safe_float(coinbase_get(f"/products/{PRODUCT}/ticker").get("price"),fallback)
    except Exception: return fallback

def register_trade(state,sig,msg_id):
    state.setdefault("active_trades",{})[sig["id"]]={"signal_id":sig["id"],"direction":sig["direction"],"entry_candle_ts":sig.get("entry_candle_ts"),"entry":sig["plan"]["entry"],"stop":sig["plan"]["stop"],"target":sig["plan"]["target"],"initial_target":sig["plan"]["target"],"target_version":0,"status":"ACTIVE","created_at":now_ts(),"last_update_at":now_ts(),"last_update_price":sig["plan"]["entry"],"telegram_message_id":msg_id,"score":sig["score"],"agreement":sig["agreement"],"news_alignment":sig["news_alignment"],"setup_type":sig.get("setup_type","TREND CONTINUATION"),"continuation":sig["continuation"],"directional_snapshot":sig["vals"]}

def extension_candidate(trade,prepared,cont,fd,news):
    if trade.get("target_version",0)>=MAX_TARGET_EXTENSIONS or cont.get("state")!="CONTINUATION_READY" or cont.get("reversal_risk",1)>.4: return None
    align=news_alignment(news,trade["direction"])
    if align=="CONFLICT" and news.get("confirmation",0)>=.75: return None
    price=ticker_price(float(prepared.iloc[-1].close)); atrv=max(safe_float(prepared.iloc[-1].atr),price*.005); target=safe_float(trade["target"]); dist=abs(target-float(trade["entry"])); moved=abs(price-float(trade["entry"]))
    if moved/max(dist,1e-9)<.70: return None
    if trade["direction"]=="LONG":
        structure=float(prepared.tail(20).high.max()); new=max(target+.75*atrv,structure+.25*atrv,price+1.0*atrv)
    else:
        structure=float(prepared.tail(20).low.min()); new=min(target-.75*atrv,structure-.25*atrv,price-1.0*atrv)
    if (trade["direction"]=="LONG" and new<=target) or (trade["direction"]=="SHORT" and new>=target): return None
    return {"price":price,"new_target":new,"atr":atrv,"reason":"healthy continuation + structure expansion + supportive flow"}

def calibration_update(trade,status,exit_price=None):
    c=load_json(CALIBRATION_FILE,{"samples":0,"outcomes":[]}); c.setdefault("outcomes",[]); entry=safe_float(trade.get("entry")); stop=safe_float(trade.get("stop")); ex=safe_float(exit_price); r=((ex-entry)/(entry-stop)) if trade.get("direction")=="LONG" else ((entry-ex)/(stop-entry)) if stop!=entry else 0.; rec={"ts":iso(),"signal_id":trade.get("signal_id"),"status":status,"r_multiple":r,"score":trade.get("score"),"agreement":trade.get("agreement"),"news_alignment":trade.get("news_alignment"),"continuation":(trade.get("continuation") or {}).get("state")}; c["outcomes"].append(rec); c["outcomes"]=c["outcomes"][-500:]; c["samples"]=len(c["outcomes"]); save_json(CALIBRATION_FILE,c)

def close_trade(state,sid,trade,status,price,reason):
    trade["status"]=status; trade["closed_at"]=now_ts(); trade["exit_price"]=price; trade["close_reason"]=reason; msg=(f"🔄 EXISTING TRADE UPDATE\n\nSignal ID: {sid}\nThis is NOT a new signal. It is an update to the existing {trade['direction']} setup.\n\nStatus: {status}\nEntry: {trade['entry']:.2f}\nExit: {price:.2f}\nOriginal TP: {trade['initial_target']:.2f}\nReason: {reason}"); telegram(msg,trade.get("telegram_message_id")); journal("TRADE_CLOSED",trade=trade); calibration_update(trade,status,price); state["active_trades"].pop(sid,None)

def manage_active_trades(state,F,news,fd):
    if not state.get("active_trades"): return
    m15=prepare(F["15m"]); price=ticker_price(float(F["15m_live"]["close"]));
    for sid,trade in list(state["active_trades"].items()):
        if now_ts()-safe_float(trade.get("created_at"))>MAX_TRADE_AGE_HOURS*3600: close_trade(state,sid,trade,"EXPIRED",price,"maximum trade age reached"); continue
        d=trade["direction"]; cont=continuation_engine(m15,d,state,fd,False); trade["continuation"]=cont
        stop=safe_float(trade["stop"]); target=safe_float(trade["target"])
        if (d=="LONG" and price<=stop) or (d=="SHORT" and price>=stop): close_trade(state,sid,trade,"SL_HIT",price,"stop level reached"); continue
        hit=(d=="LONG" and price>=target) or (d=="SHORT" and price<=target)
        can=extension_candidate(trade,m15,cont,fd,news)
        if hit and can:
            old=target; trade["target"]=can["new_target"]; trade["target_version"]=int(trade.get("target_version",0))+1; trade["status"]="TARGET_EXTENDED"; trade["last_update_at"]=now_ts(); trade["last_update_price"]=price; text=(f"🔄 EXISTING TRADE UPDATE\n\nSignal ID: {sid}\nThis is NOT a new signal. It is an update to the existing {d} setup.\n\nTARGET EXTENSION #{trade['target_version']}\nPrevious TP: {old:.2f}\nNew TP: {trade['target']:.2f}\nCurrent price: {price:.2f}\nReason: {can['reason']}\nContinuation: {cont['state']} ({cont['score']*100:.0f}%)\nReversal risk: {cont['reversal_risk']*100:.0f}%\nNews: {news_alignment(news,d)}"); telegram(text,trade.get("telegram_message_id")); journal("TARGET_EXTENDED",signal_id=sid,previous_target=old,new_target=trade["target"],trade=trade,news=news,continuation=cont); csv_log({"timestamp":iso(),"event":"TARGET_EXTENDED","signal_id":sid,"direction":d,"entry":trade["entry"],"sl":trade["stop"],"tp":trade["target"],"lifecycle":"TARGET_EXTENDED","target_version":trade["target_version"],"previous_target":old,"new_target":trade["target"],"reason":can["reason"]}); continue
        if hit: close_trade(state,sid,trade,"TP_HIT",price,"target reached without valid extension"); continue
        if can and now_ts()-safe_float(trade.get("last_update_at",0))>EXTENSION_COOLDOWN_MIN*60:
            old=target; trade["target"]=can["new_target"]; trade["target_version"]=int(trade.get("target_version",0))+1; trade["status"]="TARGET_EXTENDED"; trade["last_update_at"]=now_ts(); trade["last_update_price"]=price; telegram(f"🔄 EXISTING TRADE UPDATE\n\nSignal ID: {sid}\nThis is NOT a new signal. It is an update to the existing {d} setup.\n\nTARGET EXTENSION #{trade['target_version']}\nPrevious TP: {old:.2f}\nNew TP: {trade['target']:.2f}\nReason: {can['reason']}\nContinuation: {cont['state']}",trade.get("telegram_message_id")); journal("TARGET_EXTENDED",signal_id=sid,previous_target=old,new_target=trade["target"],trade=trade)

def wait_message(det):
    n=det.get("news",{}); c=det.get("continuation",{}); sweep=det.get("sweep",{}); rev=det.get("reversal_candidate",{})
    normal_dir=det.get("normal_direction",det.get("direction"))
    sweep_dir=rev.get("direction") or sweep.get("direction") or "NONE"
    normal_score=det.get("normal_score",det.get("score",0))
    normal_agreement=det.get("normal_agreement",det.get("agreement",0))

    if sweep.get("type") in (None,"NONE"):
        sweep_status="NONE — no liquidity sweep."
    elif sweep.get("type")=="DOUBLE_SWEEP":
        sweep_status="UNCLEAR — both sides swept."
    elif rev.get("eligible"):
        sweep_status=f"CONFIRMED → {sweep_dir}"
    else:
        sweep_status=f"NOT CONFIRMED → {sweep_dir} ({rev.get('reason','confirmation_low')})"

    return (f"🟡 WAIT — NO TRADE\n\n"
            f"Normal Direction: {normal_dir}\n"
            f"Normal Score: {normal_score:.1f}/{sum(WEIGHTS.values())}\n"
            f"Normal Agreement: {normal_agreement*100:.0f}%\n\n"
            f"Liquidity Sweep: {sweep.get('type','NONE')}\n"
            f"Sweep Direction: {sweep_dir}\n"
            f"Reversal Status: {sweep_status}\n"
            f"Reversal Confirmation: {rev.get('confirmation',0)*100:.0f}%\n"
            f"15m Confirmation: {rev.get('tf_confirmation',0)*100:.0f}%\n\n"
            f"Why: {det['reason']}\n\n"
            f"Post-Impulse: {c.get('state','NONE')} | {c.get('score',0)*100:.0f}%\n"
            f"News: {det.get('news_alignment','NEUTRAL')} | {n.get('confirmation',0)*100:.0f}%\n"
            f"Order Flow: {det['flow'].get('text','n/a')}\n\n"
            f"👉 Action: कोई trade नहीं। अगली valid confirmation का इंतजार।")

def run_once():
    state=load_state(); state["last_scan_ts"]=now_ts(); F=frames(); flow_ok,fd=rest_flow_snapshot(state); fd=flow_ev(); news=fetch_news(state); manage_active_trades(state,F,news,fd); sig,det=build_signal(F,state,news,fd); state["last_sweep"]=det.get("sweep")
    if sig is None:
        det["news_alignment"]=news_alignment(news,det["direction"]); telegram(wait_message(det)); journal("WAIT",flow_ok=flow_ok,**det); csv_log({"timestamp":iso(),"event":"WAIT","signal_id":"","direction":det["direction"],"score":det["score"],"agreement":det["agreement"],"entry":det["plan"]["entry"],"sl":det["plan"]["stop"],"tp":det["plan"]["target"],"rr":det["plan"]["rr"],"news_status":news.get("status"),"news_direction":news.get("direction"),"news_confirmation":news.get("confirmation"),"news_event_count":news.get("count"),"continuation_state":det["continuation"].get("state"),"continuation_score":det["continuation"].get("score"),"pullback_depth":det["continuation"].get("pullback_depth"),"breakout_level":det["continuation"].get("breakout_level"),"reversal_risk":det["continuation"].get("reversal_risk"),"reason":det["reason"]}); save_state(state); return
    last=safe_float(state.get("last_signal_ts"))
    same=state.get("last_signal")==sig["id"]
    same_direction_cooldown=state.get("last_direction")==sig["direction"] and now_ts()-last<COOLDOWN_MIN*60

    # A genuinely new, strong continuation can create another entry even while
    # an older trade is active. The candle must be new and continuation must be
    # clearly ready; ordinary repeated scans remain suppressed.
    cont=sig.get("continuation",{})
    fresh_continuation=(sig.get("setup_type")=="TREND CONTINUATION"
                        and cont.get("state")=="CONTINUATION_READY"
                        and safe_float(cont.get("score"))>=NEW_CONTINUATION_ENTRY_SCORE
                        and sig.get("entry_candle_ts")!=state.get("last_entry_candle_ts"))
    fresh_reversal=(sig.get("setup_type")=="LIQUIDITY SWEEP REVERSAL"
                    and sig.get("entry_candle_ts")!=state.get("last_entry_candle_ts"))
    cooldown_bypass=fresh_continuation or fresh_reversal

    if same or (same_direction_cooldown and not cooldown_bypass):
        reason="same_signal" if same else "same_direction_cooldown"
        telegram(f"🟡 BTC V7 — ENTRY SUPPRESSED\nDirection: {sig['direction']}\nReason: {reason}\nSignal ID: {sig['id']}\n\nNo new entry was opened.")
        journal("SUPPRESSED",signal=sig,reason=reason)
        save_state(state)
        return
    msgid=telegram(format_signal(sig)); journal("SIGNAL",signal=sig,telegram_message_id=msgid,flow_ok=flow_ok); register_trade(state,sig,msgid); state["last_signal"]=sig["id"]; state["last_signal_ts"]=now_ts(); state["last_direction"]=sig["direction"]; state["last_entry_candle_ts"]=sig.get("entry_candle_ts"); csv_log({"timestamp":sig["ts"],"event":"SIGNAL","signal_id":sig["id"],"direction":sig["direction"],"score":sig["score"],"agreement":sig["agreement"],"entry":sig["plan"]["entry"],"sl":sig["plan"]["stop"],"tp":sig["plan"]["target"],"rr":sig["plan"]["rr"],"news_status":news.get("status"),"news_direction":news.get("direction"),"news_confirmation":news.get("confirmation"),"news_impact":news.get("impact"),"news_confidence":news.get("confidence"),"news_relevance":news.get("relevance"),"news_event_count":news.get("count"),"continuation_state":sig["continuation"].get("state"),"continuation_score":sig["continuation"].get("score"),"pullback_depth":sig["continuation"].get("pullback_depth"),"breakout_level":sig["continuation"].get("breakout_level"),"reversal_risk":sig["continuation"].get("reversal_risk"),"lifecycle":"NEW","reason":sig["strength"]}); save_state(state)

def run():
    while True:
        started=time.time()
        try: run_once()
        except KeyboardInterrupt: break
        except Exception as e: print("ERROR",repr(e)); journal("ERROR",error=repr(e),traceback=traceback.format_exc())
        time.sleep(max(2,SCAN_SECONDS-(time.time()-started)))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--once",action="store_true"); ap.add_argument("--status",action="store_true"); a=ap.parse_args()
    if a.status: print(json.dumps(load_state(),indent=2,ensure_ascii=False,default=str)); return
    if a.once: run_once()
    else: run()
if __name__=="__main__": main()

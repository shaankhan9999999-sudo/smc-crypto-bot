import os,sys,time,json,math,argparse,traceback,csv,copy,re,hashlib
from datetime import datetime,timezone,timedelta
import requests,pandas as pd,numpy as np

PRODUCT="BTC-USD"; REST="https://api.exchange.coinbase.com"
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN","")
CHAT_ID=os.getenv("TELEGRAM_CHAT_ID","7500472109")
JOURNAL_FILE="logs/journal/btc_v8_journal.jsonl"; STATE_FILE="logs/state/btc_v8_state.json"
CALIBRATION_FILE="logs/calibration/btc_v8_calibration.json"; CSV_LOG_FILE="logs/csv/btc_v8_signals.csv"
SCAN_CSV_FILE="logs/csv/btc_v8_scans.csv"
# V8 SHADOW MODE: isolated diagnostic/confirmation intelligence only. It never enters
# decision_engine, live scoring, execution, or live Telegram trade decisions during validation.
SHADOW_SR_ENABLED=os.getenv("SHADOW_SR_ENABLED","1").lower() not in {"0","false","no"}
SHADOW_SR_FILE="logs/csv/btc_v8_shadow_sr.csv"
SHADOW_SR_LOOKBACK=80
SHADOW_SR_PIVOT_WINDOW=2
SHADOW_SR_CLUSTER_ATR=.35
SHADOW_SR_RETEST_ATR=.35
SHADOW_SR_RECLAIM_ATR=.15
SHADOW_SR_REJECTION_ATR=.20
SHADOW_CONFIRM_MAX_SCANS=3
SHADOW_LEVEL_MATCH_ATR=.45
SHADOW_STATE_MAX_LEVELS=200
# Unified Shadow Opportunity Intelligence: diagnostic/evidence only.
# It never changes decision_engine(), thresholds, execution, or Telegram trade decisions.
SHADOW_INTEL_ENABLED=os.getenv("SHADOW_INTEL_ENABLED","1").lower() not in {"0","false","no"}
SHADOW_INTEL_TIMEFRAMES=("1h","15m","5m")
SHADOW_INTEL_LOOKBACK=120
SHADOW_INTEL_NEAR_ATR=0.75
SHADOW_INTEL_OB_CONFIRM_ATR=0.75
SHADOW_INTEL_OB_MAX_AGE_BARS=80
SHADOW_INTEL_SWEEP_LOOKBACK=20
SHADOW_INTEL_SWEEP_PENETRATION_ATR=0.05
SHADOW_INTEL_SWEEP_RECLAIM_ATR=0.15
SHADOW_INTEL_DISPLACEMENT_ATR=0.75
SHADOW_INTEL_VOLUME_RATIO=1.15
SHADOW_INTEL_CONFIRM_BARS=3
SHADOW_INTEL_STATE_TTL_BARS=6
SHADOW_INTEL_CSV="logs/csv/btc_v8_shadow_opportunity_intelligence.csv"
SHADOW_ORDERBOOK_ENABLED=os.getenv("SHADOW_ORDERBOOK_ENABLED","1").lower() not in {"0","false","no"}
SHADOW_ORDERBOOK_LEVEL=2
SHADOW_ORDERBOOK_TOP_N=25
SHADOW_ORDERBOOK_BAND_PCT=0.005
SHADOW_ORDERBOOK_CSV="logs/csv/btc_v8_shadow_orderbook.csv"
# Adaptive Opportunity Intelligence: interpretation/monitoring layer only.
# It does NOT change V8 Core entry thresholds, decision_engine(), or execution permission.
ADAPTIVE_OPPORTUNITY_ENABLED=os.getenv("ADAPTIVE_OPPORTUNITY_ENABLED","1").lower() not in {"0","false","no"}
ADAPTIVE_SMALL_STRENGTH=.45
ADAPTIVE_MEDIUM_STRENGTH=.62
ADAPTIVE_LARGE_STRENGTH=.78
ADAPTIVE_SMALL_ROOM_ATR=.8
ADAPTIVE_MEDIUM_ROOM_ATR=1.8
ADAPTIVE_LARGE_ROOM_ATR=3.0
ADAPTIVE_EXTENSION_MIN_STRENGTH=.68
ADAPTIVE_EXTENSION_MIN_ROOM_ATR=1.8
ADAPTIVE_EXTENSION_MIN_MFE_ATR=.75
ADAPTIVE_OPPORTUNITY_CSV="logs/csv/btc_v8_adaptive_opportunity_intelligence.csv"
# Adaptive Signal Bridge: the two advanced engines can feed V8 Core as structured
# evidence. It never executes by itself. V8 still performs final score/risk checks.
ADAPTIVE_BRIDGE_ENABLED=os.getenv("ADAPTIVE_BRIDGE_ENABLED","1").lower() not in {"0","false","no"}
ADAPTIVE_BRIDGE_MIN_STRENGTH=.78
ADAPTIVE_BRIDGE_MIN_STRUCTURE=.68
ADAPTIVE_BRIDGE_MIN_FLOW=.68
ADAPTIVE_BRIDGE_MIN_MTF=.52
ADAPTIVE_BRIDGE_MIN_BASE_AGREEMENT=.50
ADAPTIVE_BRIDGE_MIN_SCORE=54.0
ADAPTIVE_BRIDGE_MAX_SCORE_BONUS=8.0
ADAPTIVE_BRIDGE_MAX_AGREEMENT_BOOST=.08
ADAPTIVE_BRIDGE_MIN_ROOM_ATR=.80
ADAPTIVE_BRIDGE_MAX_CONFLICT=.20
SCAN_SECONDS=60; COOLDOWN_MIN=45; MIN_SCORE=62; STRONG_SCORE=76
MIN_AGREEMENT=.58; STRONG_AGREEMENT=.68; REQUEST_TIMEOUT=20
CANDLE_API_MAX=300
HISTORY_1H_BARS=1200; HISTORY_5M_BARS=500; FLOW_TRADE_LIMIT=100
FLOW_LARGE_TRADE_BTC=.25; ORDERFLOW_STALE=8
SWEEP_LOOKBACK=20; SWEEP_MAX_AGE=3; SWEEP_MIN_PENETRATION_ATR=.05
SWEEP_MIN_RECLAIM=.35; SWEEP_MIN_CONFIRMATION=.55
SWEEP_REVERSAL_MIN_QUALITY=.45; STRONG_CONFIRMATION=.60
STRONG_TREND_SCORE=76; STRONG_TREND_EXTRA=.05; MIN_15M_CONFIRMATION=.55
STRONG_15M_CONFIRMATION=.68; NEW_CONTINUATION_ENTRY_SCORE=.70
WEIGHTS={"trend15":15,"structure":20,"price":15,"volume":10,"momentum":10,"retest":12,"orderflow":13,"reversal":10}
NEWS_ENABLED=os.getenv("NEWS_ENABLED","1").lower() not in {"0","false","no"}
NEWS_API_KEY=os.getenv("CRYPTOPANIC_API_KEY") or os.getenv("NEWS_API_KEY","")
NEWS_API_URL=os.getenv("NEWS_API_URL","https://cryptopanic.com/api/developer/v2/posts/")
NEWS_LOOKBACK_MIN=30; MAX_ARTICLES=20; MIN_RELEVANCE=.60; MIN_CONFIDENCE=.55; CONFIRMATION_THRESHOLD=.60
CONT_LOOKBACK=20; CONT_IMPULSE_BARS=6; CONT_IMPULSE_ATR=1.8; CONT_MIN_PULLBACK=.18; CONT_MAX_PULLBACK=.65
CONT_READY_SCORE=.62; MAX_TARGET_EXTENSIONS=2; EXTENSION_COOLDOWN_MIN=30; MAX_TRADE_AGE_HOURS=36
STATE_HYSTERESIS_SCANS=2; REGIME_MIN_CONFIDENCE=.55; ADAPTIVE_MIN_SAMPLES=20; ADAPTIVE_STRONG_SAMPLES=50
ADAPTIVE_DECAY_DAYS=45; ADAPTIVE_MAX_BOOST=8; ADAPTIVE_MAX_PENALTY=10
ADAPTIVE_MIN_EXPECTANCY_EDGE=.10; ADAPTIVE_STRONG_NEGATIVE_EXPECTANCY=-.20
EXECUTION_MIN_SCORE=.50; TRANSITION_BLOCK_CONFIDENCE=.68
# Market-flow intelligence / decision layer
FLOW_WEIGHTS={"structure":.24,"momentum":.16,"orderflow":.14,"liquidity":.12,"volume":.10,"mtf":.14,"displacement":.10}
FLOW_WARNING_THRESHOLD=.60; FLOW_CONFIRM_THRESHOLD=.72; FLOW_DIRECTION_GAP=.08
FLOW_MEMORY_SCANS=6; FLOW_REVERSAL_CONFIRM_SCANS=2
USE_MICRO_CONFIRMATION=os.getenv("USE_MICRO_CONFIRMATION","1").lower() not in {"0","false","no"}
NEWS_SUPPORT_MAX_POINTS=3.0
# V8 final Decision Engine: regime/flow authority is resolved here, not by additive score stacking.
DECISION_MIN_FLOW_FOR_TREND=.54
DECISION_MIN_FLOW_FOR_REVERSAL=.72
DECISION_MIN_SWEEP_FOR_REVERSAL=.60
DECISION_MIN_STRUCTURE_FOR_REVERSAL=.55
DECISION_MAX_NEUTRAL_SCORE_GAP=.06
DECISION_SETUP_BONUS_MAX=4.0
DECISION_VERSION="V8-FINAL-2"
REGIMES=("BULLISH_TREND","BULLISH_PULLBACK","BULLISH_EXHAUSTION","BULLISH_REVERSAL",
"BEARISH_REVERSAL","BEARISH_TREND","BEARISH_PULLBACK","BEARISH_EXHAUSTION","RANGE","TRANSITION","UNSTABLE")
S=requests.Session()
FLOW={"ts":0.0,"buy":0.0,"sell":0.0,"large_buy":0.0,"large_sell":0.0,"delta":0.0,"bids":{},"asks":{}}
SHADOW_ORDERBOOK={"ts":0.0,"price":0.0,"spread":0.0,"spread_bps":0.0,"bid_depth":0.0,"ask_depth":0.0,"imbalance":0.0,"bid_levels":0,"ask_levels":0,"nearest_bid":0.0,"nearest_ask":0.0,"nearest_bid_distance_bps":0.0,"nearest_ask_distance_bps":0.0,"top_bid_clusters":[],"top_ask_clusters":[],"buy_volume":0.0,"sell_volume":0.0,"trade_delta":0.0,"error":None}

def now_ts(): return time.time()
def utc_now(): return datetime.now(timezone.utc)
def safe_float(x,default=0.0):
    try:
        v=float(x); return default if not math.isfinite(v) else v
    except: return default
def clamp(x,lo=0.0,hi=1.0): return max(lo,min(hi,safe_float(x,0.0)))
def ensure_parent_dir(p):
    d=os.path.dirname(p)
    if d: os.makedirs(d,exist_ok=True)
def journal(event,**kw):
    try:
        ensure_parent_dir(JOURNAL_FILE)
        with open(JOURNAL_FILE,"a",encoding="utf-8") as f:
            f.write(json.dumps({"ts":utc_now().isoformat(),"event":event,**kw},default=str)+"\n")
    except: pass

def default_state():
    return {"active_trade":None,"last_signal":None,"last_signal_ts":0.0,"last_direction":None,
            "last_scan_ts":0.0,"cooldown_until":0.0,"regime":None,"regime_candidate":None,
            "regime_candidate_count":0,"last_closed_trade":None,"flow_state":{},
            "shadow_state":{"levels":{}},"shadow_opportunity":{},"shadow_opportunity_episodes":{},"adaptive_opportunity":{}}
def load_json(path,default):
    try:
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except: return copy.deepcopy(default)
def save_json(path,obj):
    try:
        ensure_parent_dir(path); tmp=path+".tmp"
        with open(tmp,"w",encoding="utf-8") as f: json.dump(obj,f,indent=2,default=str)
        os.replace(tmp,path)
    except Exception as e: journal("SAVE_ERROR",file=path,error=repr(e))
def load_state():
    x=load_json(STATE_FILE,default_state()); z=default_state(); z.update(x if isinstance(x,dict) else {}); return z
def save_state(state): save_json(STATE_FILE,state)

def telegram_send(text):
    """Send Telegram text and journal the exact delivery result. Returning a boolean is retained for compatibility, but every attempt now records success/failure so a missed entry notification can be retried from persisted state. """
    if not TOKEN or not CHAT_ID:
        journal("TELEGRAM_ERROR",stage="CONFIG",error="missing_token_or_chat_id")
        return False
    try:
        r=S.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text},timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data=r.json() if r.content else {}
        message_id=(data.get("result") or {}).get("message_id") if isinstance(data,dict) else None
        journal("TELEGRAM_SENT",message_id=message_id,text_hash=hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16])
        return True
    except Exception as e:
        journal("TELEGRAM_ERROR",stage="SEND",error=repr(e),text_hash=hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16])
        return False

def format_trade_entry_notification(trade,signal=None):
    """Build the exact entry notification from the persisted trade record."""
    d="ðŸŸ¢ LONG ENTRY" if trade.get("direction")=="LONG" else "ðŸ”´ SHORT ENTRY"
    return (f"{d}\n\nEntry: {safe_float(trade.get('entry')):.2f}\n"
            f"Stop Loss: {safe_float(trade.get('stop_loss')):.2f}\n"
            f"Target: {safe_float(trade.get('target')):.2f}\n"
            f"RR: {safe_float(trade.get('rr')):.2f}\n\n"
            f"Setup: {trade.get('setup','UNKNOWN')}\nRegime: {trade.get('regime','UNKNOWN')}\n"
            f"Opportunity: {trade.get('opportunity_size','SMALL')} | Bridge: {trade.get('bridge_state','NONE')}\n"
            f"Score: {safe_float(trade.get('score')):.1f}/105\n"
            f"Agreement: {safe_float(trade.get('agreement'))*100:.0f}%\n"
            f"Signal ID: {trade.get('signal_id','UNKNOWN')}")

def ensure_entry_notification(state):
    """Retry a missing entry Telegram notification until it is acknowledged as sent. This is deliberately separate from trade execution: it cannot create/close a trade. """
    trade=state.get("active_trade")
    if not isinstance(trade,dict) or trade.get("status")!="OPEN": return False
    if trade.get("entry_notification_sent") is True: return True
    text=format_trade_entry_notification(trade)
    trade["entry_notification_attempts"]=safe_int(trade.get("entry_notification_attempts",0))+1
    ok=telegram_send(text)
    trade["entry_notification_last_attempt"]=utc_now().isoformat()
    trade["entry_notification_sent"]=bool(ok)
    if ok:
        journal("ENTRY_NOTIFICATION_CONFIRMED",signal_id=trade.get("signal_id"),attempt=trade.get("entry_notification_attempts"))
    else:
        journal("ENTRY_NOTIFICATION_PENDING",signal_id=trade.get("signal_id"),attempt=trade.get("entry_notification_attempts"))
    save_state(state)
    return ok

def format_target_extension_notification(trade,adaptive,old_target,new_target):
    direction="LONG" if trade.get("direction")=="LONG" else "SHORT"
    arrow="â†‘" if direction=="LONG" else "â†“"
    return (f"ðŸ“ˆ TRADE UPDATE â€” TARGET EXTENSION\n\n"
            f"Direction: {direction}\nEntry: {safe_float(trade.get('entry')):.2f}\n"
            f"Current: {safe_float(adaptive.get('price')):.2f}\n"
            f"Old Target: {safe_float(old_target):.2f}\nNew Target: {safe_float(new_target):.2f} {arrow}\n"
            f"Opportunity: {adaptive.get('opportunity_size','SMALL')}\n"
            f"State: {adaptive.get('state','NONE')}\n"
            f"Structure: {safe_float(adaptive.get('structure_score')):.2f} | Flow: {safe_float(adaptive.get('flow_score')):.2f}\n"
            f"Strength: {safe_float(adaptive.get('combined_strength')):.2f} | Room: {safe_float(adaptive.get('room_atr')):.2f} ATR\n"
            f"Reason: stronger continuation evidence; target extended without widening SL.")

def format_trade_close_notification(trade):
    return (f"TRADE CLOSED\n{trade.get('direction','UNKNOWN')} {trade.get('outcome','UNKNOWN')}\n"
            f"Exit: {safe_float(trade.get('exit')):.2f}\n"
            f"P/L: {safe_float(trade.get('pnl_r')):+.2f}R\n"
            f"Reason: {trade.get('close_reason','UNKNOWN')}\n"
            f"Signal ID: {trade.get('signal_id','UNKNOWN')}")

def ensure_closed_trade_notifications(state):
    """Deliver a closed trade's entry notification before its exit notification. This preserves message order even when the opening Telegram call failed. """
    trade=state.get("last_closed_trade")
    if not isinstance(trade,dict): return False
    if not trade.get("entry_notification_sent"):
        trade["entry_notification_attempts"]=safe_int(trade.get("entry_notification_attempts",0))+1
        ok_entry=telegram_send(format_trade_entry_notification(trade))
        trade["entry_notification_last_attempt"]=utc_now().isoformat()
        trade["entry_notification_sent"]=bool(ok_entry)
        journal("ENTRY_NOTIFICATION_CONFIRMED" if ok_entry else "ENTRY_NOTIFICATION_PENDING",signal_id=trade.get("signal_id"),attempt=trade.get("entry_notification_attempts"),closed_trade=True)
    if not trade.get("entry_notification_sent"):
        journal("CLOSE_NOTIFICATION_HELD",signal_id=trade.get("signal_id"),reason="entry_notification_pending")
        save_state(state)
        return False
    if trade.get("close_notification_sent") is True:
        return True
    ok=telegram_send(format_trade_close_notification(trade))
    trade["close_notification_sent"]=bool(ok)
    trade["close_notification_last_attempt"]=utc_now().isoformat()
    journal("EXIT_NOTIFICATION_CONFIRMED" if ok else "EXIT_NOTIFICATION_PENDING",signal_id=trade.get("signal_id"))
    save_state(state)
    return ok

def api_get(path,params=None):
    r=S.get(REST+path,params=params,timeout=REQUEST_TIMEOUT); r.raise_for_status(); return r.json()

def fetch_candles(product,granularity,limit=300):
    """Fetch up to *limit* candles while respecting Coinbase's per-request candle cap."""
    g=int(granularity); target=max(1,int(limit)); now=int(time.time()); rows=[]; end=now
    # Paginate backwards. A small overlap prevents boundary gaps/duplicates between pages.
    while len(rows)<target:
        batch=min(CANDLE_API_MAX,target-len(rows)+2)
        start=end-g*batch
        data=api_get(f"/products/{product}/candles",{"granularity":g,"start":datetime.fromtimestamp(start,timezone.utc).isoformat(),"end":datetime.fromtimestamp(end,timezone.utc).isoformat()})
        parsed=[[int(x[0]),safe_float(x[1]),safe_float(x[2]),safe_float(x[3]),safe_float(x[4]),safe_float(x[5])] for x in (data or []) if len(x)>=6]
        if not parsed: break
        rows.extend(parsed)
        oldest=min(x[0] for x in parsed)
        if oldest>=end-g: break
        end=oldest-g
        if len(parsed)<2: break
    if not rows:
        return pd.DataFrame(columns=["ts","low","high","open","close","volume"])
    rows=sorted({x[0]:x for x in rows}.values(),key=lambda x:x[0])
    return pd.DataFrame(rows,columns=["ts","low","high","open","close","volume"]).tail(target).reset_index(drop=True)

def aggregate_candles(df,seconds):
    d=df.copy()
    if d.empty:return d
    d["dt"]=pd.to_datetime(d.ts,unit="s",utc=True)
    rule=f"{int(seconds//3600)}h" if seconds%3600==0 else f"{int(seconds//60)}min"
    out=d.set_index("dt").resample(rule,origin="epoch",label="left",closed="left").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("volume","sum")).dropna().reset_index()
    out["ts"]=(out["dt"].astype("int64")//10**9).astype(int)
    return out[["ts","low","high","open","close","volume"]].reset_index(drop=True)

def prepare(df):
    d=df.copy()
    for c in ["open","high","low","close","volume"]:d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.dropna(subset=["close"]).reset_index(drop=True)
    d["ema9"]=d.close.ewm(span=9,adjust=False).mean(); d["ema21"]=d.close.ewm(span=21,adjust=False).mean(); d["ema50"]=d.close.ewm(span=50,adjust=False).mean(); d["ema200"]=d.close.ewm(span=200,adjust=False).mean()
    tr=pd.concat([(d.high-d.low),(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1); d["atr"]=tr.rolling(14,min_periods=1).mean()
    d["ret1"]=d.close.pct_change().fillna(0); d["ret4"]=d.close.pct_change(4).fillna(0); d["vol_ma20"]=d.volume.rolling(20,min_periods=1).mean()
    up=d.close.diff().clip(lower=0).rolling(14,min_periods=1).mean(); dn=d.close.diff().clip(upper=0).abs().rolling(14,min_periods=1).mean()
    d["rsi"]=(100-100/(1+(up/dn.replace(0,np.nan)))).fillna(50); d["range"]=d.high-d.low; return d

def _tf_direction_score(df):
    d=prepare(df)
    if d.empty:return {"close":0,"atr":0,"trend":0,"structure":0,"momentum":0,"volume":0,"retest":0}
    r=d.iloc[-1]; close=safe_float(r.close); atr=max(safe_float(r.atr),close*.0001)
    return {"close":close,"atr":atr,"trend":clamp((r.ema9-r.ema50)/(atr*2)+.5),"structure":clamp((close-r.ema21)/(atr*1.5)+.5),
            "momentum":clamp(.5+r.ret4/.02),"volume":clamp(safe_float(r.volume)/(safe_float(r.vol_ma20) or 1)),
            "retest":clamp(.5-abs(close-r.ema21)/(atr*2)),"rsi":safe_float(r.rsi,50)}

def _structure_transition(df,direction):
    d=prepare(df)
    if len(d)<12:return {"score":.5,"bos":False,"choch":False,"displacement":0.0}
    recent=d.iloc[-8:]; prev=d.iloc[-16:-8] if len(d)>=16 else d.iloc[:-8]
    last=safe_float(d.iloc[-1].close); atr=max(safe_float(d.iloc[-1].atr),last*.0001)
    ph=safe_float(prev.high.max()); pl=safe_float(prev.low.min()); rh=safe_float(recent.high.max()); rl=safe_float(recent.low.min())
    ret=safe_float(d.iloc[-1].ret4); disp=clamp(abs(ret)/(atr/max(last,1e-9)))
    if direction=="LONG":
        bos=last>ph; choch=last<pl; structure=clamp(.5+(last-ph)/(atr*2)) if not choch else .25
        displacement=clamp(disp)
    else:
        bos=last<pl; choch=last>ph; structure=clamp(.5+(pl-last)/(atr*2)) if not choch else .25
        displacement=clamp(disp)
    return {"score":structure,"bos":bool(bos),"choch":bool(choch),"displacement":displacement,"recent_high":rh,"recent_low":rl}

def _flow_direction(F,direction):
    sign=1 if direction=="LONG" else -1
    x1=_tf_direction_score(F.get("1h",F["15m"])); x15=_tf_direction_score(F["15m"]); x5=_tf_direction_score(F["5m"]); x1m=_tf_direction_score(F["1m"]) if USE_MICRO_CONFIRMATION and "1m" in F else x5
    st=_structure_transition(F.get("15m",F["5m"]),direction)
    of=FLOW
    total=safe_float(of.get("buy"))+safe_float(of.get("sell"))
    ofscore=clamp(.5+sign*safe_float(of.get("delta"))/(total or 1)/2)
    mtf=clamp((x1["trend"] if direction=="LONG" else 1-x1["trend"])*.45+(x15["trend"] if direction=="LONG" else 1-x15["trend"])*.30+(x5["momentum"] if direction=="LONG" else 1-x5["momentum"])*.15+(x1m["momentum"] if direction=="LONG" else 1-x1m["momentum"])*.10)
    vol=x5["volume"]
    mom=x5["momentum"] if direction=="LONG" else 1-x5["momentum"]
    liq=clamp(.5 + (x5["retest"]-.5)*1.2)
    score=(FLOW_WEIGHTS["structure"]*st["score"]+FLOW_WEIGHTS["momentum"]*mom+FLOW_WEIGHTS["orderflow"]*ofscore+FLOW_WEIGHTS["liquidity"]*liq+FLOW_WEIGHTS["volume"]*vol+FLOW_WEIGHTS["mtf"]*mtf+FLOW_WEIGHTS["displacement"]*st["displacement"])
    return {"score":clamp(score),"structure":st,"orderflow":ofscore,"mtf":mtf,"momentum":mom,"volume":vol,"liquidity":liq}

def market_flow_engine(F,state,base_market):
    long=_flow_direction(F,"LONG"); short=_flow_direction(F,"SHORT")
    prev=state.get("flow_state") or {}; prev_reg=prev.get("regime",base_market.get("regime","UNSTABLE"))
    lb,sb=long["score"],short["score"]; gap=lb-sb
    if gap>=FLOW_DIRECTION_GAP: flow_dir="LONG"
    elif gap<=-FLOW_DIRECTION_GAP: flow_dir="SHORT"
    else: flow_dir="NEUTRAL"
    prior_dir=prev.get("direction","NEUTRAL")
    weakening=(prior_dir=="LONG" and flow_dir=="SHORT") or (prior_dir=="SHORT" and flow_dir=="LONG")
    transition="NORMAL"
    if flow_dir=="NEUTRAL" or abs(gap)<FLOW_DIRECTION_GAP: transition="WEAKENING"
    elif weakening: transition="TRANSITION"
    elif max(lb,sb)>=FLOW_CONFIRM_THRESHOLD: transition="CONFIRMED"
    # CHOCH is an early structural warning; require repeated confirmation before declaring reversal.
    choch_long=bool(long["structure"].get("choch")); choch_short=bool(short["structure"].get("choch"))
    if (prior_dir=="LONG" and choch_short) or (prior_dir=="SHORT" and choch_long): transition="TRANSITION"
    confirmed_dir=flow_dir
    if transition=="TRANSITION" and prev.get("transition_count",0)+1< FLOW_REVERSAL_CONFIRM_SCANS:
        confirmed_dir=prior_dir
    elif transition=="CONFIRMED":
        confirmed_dir=flow_dir
    tcount=(safe_int(prev.get("transition_count",0))+1) if transition=="TRANSITION" else 0
    out={"direction":flow_dir,"confirmed_direction":confirmed_dir,"transition":transition,"long_score":lb,"short_score":sb,
         "gap":gap,"warning":max(lb,sb)>=FLOW_WARNING_THRESHOLD,"confirmed":transition=="CONFIRMED","transition_count":tcount,
         "long":long,"short":short,"previous_direction":prior_dir,"previous_regime":prev_reg}
    state["flow_state"]=out
    return out

def safe_int(x,default=0):
    try:return int(x)
    except:return default

def market_state_engine(F,state=None):
    tf={k:_tf_direction_score(v) for k,v in F.items() if isinstance(v,pd.DataFrame)}
    if not tf:return {"regime":"UNSTABLE","confidence":0.0,"bull":0.0,"bear":0.0,"transition":1.0,"timeframes":{},"flow":{}}
    bull=[]
    for k in ("2h","1h","15m","5m"):
        if k in tf:
            x=tf[k]; bull.append(.55*x["trend"]+.25*x["structure"]+.20*x["momentum"])
    b=float(np.mean(bull)) if bull else .5; br=1-b; t1=tf.get("1h",{}); t2=tf.get("2h",t1); t4=tf.get("4h",t2); t15=tf.get("15m",t1)
    # 4h is a macro context check, not an additional AND-gate; 2h remains the primary macro timeframe.
    macro_bull=b>.60 and t2.get("trend",.5)>.55 and t4.get("trend",.5)>=.50
    macro_bear=br>.60 and t2.get("trend",.5)<.45 and t4.get("trend",.5)<=.50
    exhaustion_bull=t1.get("rsi",50)>72; exhaustion_bear=t1.get("rsi",50)<28; transition=clamp(1-abs(b-.5)*2)
    if macro_bull and exhaustion_bull:regime="BULLISH_EXHAUSTION"
    elif macro_bear and exhaustion_bear:regime="BEARISH_EXHAUSTION"
    elif macro_bull and t15.get("structure",.5)>.50:regime="BULLISH_TREND"
    elif macro_bear and t15.get("structure",.5)<.50:regime="BEARISH_TREND"
    elif b>.56:regime="BULLISH_PULLBACK"
    elif br>.56:regime="BEARISH_PULLBACK"
    elif abs(b-.5)<.08:regime="RANGE"
    else:regime="TRANSITION"
    conf=clamp(abs(b-.5)*2)
    flow=market_flow_engine(F,state or {"flow_state":{}}, {"regime":regime})
    if state is not None:
        prev=state.get("regime")
        # Early flow reversal blocks stale regime from driving new entries, but does not instantly flip the regime label.
        if flow["confirmed_direction"]=="SHORT" and prev and prev.startswith("BULLISH"): regime="TRANSITION"
        elif flow["confirmed_direction"]=="LONG" and prev and prev.startswith("BEARISH"): regime="TRANSITION"
        elif prev and prev!=regime and conf<REGIME_MIN_CONFIDENCE:regime=prev
        state["regime"]=regime
    return {"regime":regime,"confidence":conf,"bull":b,"bear":br,"transition":transition,"timeframes":tf,
            "macro_bull":macro_bull,"macro_bear":macro_bear,"roles":{"4h":"macro_context","2h":"macro","1h":"structure_liquidity","15m":"transition_setup","5m":"execution"},"flow":flow}

def execution_score(F,direction):
    sign=1 if direction=="LONG" else -1; x5=_tf_direction_score(F["5m"]); x15=_tf_direction_score(F["15m"])
    def d(x,k):return x[k] if sign==1 else 1-x[k]
    parts={"trend15":d(x15,"trend"),"structure":d(x15,"structure"),"price":clamp(.5+sign*(x5["close"]-x15["close"])/(x5["atr"]*2)),
           "volume":x5["volume"],"momentum":d(x5,"momentum"),"retest":d(x5,"retest"),"orderflow":.5,"reversal":.5}
    of=update_orderflow()
    if of["buy"]+of["sell"]>0:parts["orderflow"]=clamp(.5+sign*of["delta"]/(of["buy"]+of["sell"])/2)
    return {"score":sum(WEIGHTS[k]*clamp(v) for k,v in parts.items()),"agreement":calculate_agreement(parts),"parts":parts,"orderflow":of}

def default_calibration():return {"version":1,"buckets":{},"updated_at":utc_now().isoformat()}
def load_calibration():
    x=load_json(CALIBRATION_FILE,default_calibration()); z=default_calibration(); z.update(x if isinstance(x,dict) else {}); z.setdefault("buckets",{}); return z
def save_calibration(c):c["updated_at"]=utc_now().isoformat(); save_json(CALIBRATION_FILE,c)
def _adaptive_bucket(f):return "|".join([str(f.get("direction","NA")),str(f.get("setup","UNKNOWN")),str(f.get("regime","UNKNOWN"))])
def _ensure_bucket(c,k):
    c.setdefault("buckets",{}); c["buckets"].setdefault(k,{"samples":0,"wins":0,"losses":0,"sum_r":0.0,"weighted_r":0.0,"weight":0.0}); return c["buckets"][k]
def decayed_weight(ts):
    return math.exp(-max(0,now_ts()-safe_float(ts,now_ts()))/(ADAPTIVE_DECAY_DAYS*86400))
def _bucket_expectancy(b):return safe_float(b.get("weighted_r",0))/max(safe_float(b.get("weight",0)),1e-9)
def _bucket_win_rate(b):return safe_float(b.get("wins",0))/max(safe_float(b.get("samples",0)),1)
def _bucket_samples(b):return int(b.get("samples",0))
def adaptive_adjustment(c,f):
    k=_adaptive_bucket(f); b=c.get("buckets",{}).get(k)
    if not b or _bucket_samples(b)<ADAPTIVE_MIN_SAMPLES:return {"adjustment":0.0,"expectancy":0.0,"win_rate":.5,"samples":_bucket_samples(b) if b else 0,"bucket":k}
    e=_bucket_expectancy(b); w=_bucket_win_rate(b)
    a=ADAPTIVE_MAX_BOOST*clamp(e/.5) if e>=ADAPTIVE_MIN_EXPECTANCY_EDGE else -(ADAPTIVE_MAX_PENALTY*clamp(abs(e)/.5)) if e<=ADAPTIVE_STRONG_NEGATIVE_EXPECTANCY else (w-.5)*8
    a=max(-ADAPTIVE_MAX_PENALTY,min(ADAPTIVE_MAX_BOOST,a)); return {"adjustment":a,"expectancy":e,"win_rate":w,"samples":_bucket_samples(b),"bucket":k}
def adaptive_signal_score(base,c,f):
    i=adaptive_adjustment(c,f); i["base_score"]=base; i["adjusted_score"]=max(0,min(105,base+i["adjustment"])); i["score"]=i["adjusted_score"]; return i
def learn_from_closed_trade(c,f,r,outcome=None):
    b=_ensure_bucket(c,_adaptive_bucket(f)); w=decayed_weight(f.get("timestamp",now_ts())); b["samples"]+=1; b["wins"]+=outcome=="WIN"; b["losses"]+=outcome=="LOSS"; b["sum_r"]=safe_float(b.get("sum_r"))+r; b["weighted_r"]=safe_float(b.get("weighted_r"))+r*w; b["weight"]=safe_float(b.get("weight"))+w
def build_signal_features(F,direction,parts,regime="UNSTABLE",setup="UNKNOWN",score=0,agreement=0):
    return {"direction":direction,"setup":setup,"regime":regime,"score":safe_float(score),"agreement":safe_float(agreement),
            "trend":safe_float(parts.get("trend15")),"structure":safe_float(parts.get("structure")),"momentum":safe_float(parts.get("momentum")),
            "orderflow":safe_float(parts.get("orderflow")),"timestamp":now_ts()}
CSV_FIELDS=["ts","scan_id","event","reason","signal_id","direction","setup","regime","confidence","score","agreement","price","adaptive_adjustment","sweep_confirmation","news_confirmation","active_trade","action","error"]

def append_scan_csv(row):
    try:
        ensure_parent_dir(SCAN_CSV_FILE); new=not os.path.exists(SCAN_CSV_FILE)
        with open(SCAN_CSV_FILE,"a",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=CSV_FIELDS,extrasaction="ignore")
            if new:w.writeheader()
            w.writerow({k:row.get(k,"") for k in CSV_FIELDS})
    except Exception as e:journal("CSV_SCAN_ERROR",error=repr(e))

SIGNAL_CSV_FIELDS=["ts","scan_id","event","signal_id","direction","setup","regime","confidence","score","agreement","price","adaptive_adjustment","sweep_confirmation","news_confirmation","flow_direction","flow_transition","flow_score","action","opportunity_size","bridge_state","bridge_strength"]

def append_signal_csv(row):
    try:
        ensure_parent_dir(CSV_LOG_FILE); new=not os.path.exists(CSV_LOG_FILE)
        with open(CSV_LOG_FILE,"a",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=SIGNAL_CSV_FIELDS,extrasaction="ignore")
            if new:w.writeheader()
            w.writerow({k:row.get(k,"") for k in SIGNAL_CSV_FIELDS})
    except Exception as e: journal("CSV_SIGNAL_ERROR",error=repr(e))

def calculate_agreement(parts):
    """Agreement is measured across evidence groups, not every correlated feature."""
    if not isinstance(parts,dict): return 0.0
    groups={
        "trend_structure": float(np.mean([clamp(parts.get("trend15",.5)),clamp(parts.get("structure",.5))])),
        "price_momentum": float(np.mean([clamp(parts.get("price",.5)),clamp(parts.get("momentum",.5))])),
        "participation": float(np.mean([clamp(parts.get("volume",.5)),clamp(parts.get("orderflow",.5))])),
        "location": float(np.mean([clamp(parts.get("retest",.5)),clamp(parts.get("reversal",.5))])),
    }
    vals=list(groups.values()); m=float(np.mean(vals))
    dispersion=float(np.mean([abs(x-m) for x in vals]))
    return clamp(1-dispersion*2)

def news_sentiment_score(text):
    s=str(text or "").lower(); bull=("bullish","surge","rally","breakout","buy","positive","uptrend","approval","inflow"); bear=("bearish","drop","crash","sell","negative","downtrend","outflow","liquidation")
    b=sum(s.count(w) for w in bull); r=sum(s.count(w) for w in bear); return .5 if b+r==0 else clamp(.5+.5*(b-r)/(b+r))
def fetch_news():
    if not NEWS_ENABLED:return {"score":.5,"confirmation":0.0,"articles":0,"error":None}
    if not NEWS_API_KEY:return {"score":.5,"confirmation":0.0,"articles":0,"error":"missing_api_key"}
    try:
        r=S.get(NEWS_API_URL,params={"auth_token":NEWS_API_KEY,"currencies":"BTC","kind":"news","limit":MAX_ARTICLES},timeout=REQUEST_TIMEOUT); r.raise_for_status()
        data=r.json(); items=data.get("results") or data.get("data") or []; cutoff=utc_now()-timedelta(minutes=NEWS_LOOKBACK_MIN); vals=[]
        for a in items[:MAX_ARTICLES]:
            ts=a.get("published_at") or a.get("published") or a.get("created_at")
            if ts:
                try:
                    if datetime.fromisoformat(str(ts).replace("Z","+00:00"))<cutoff:continue
                except:pass
            vals.append(news_sentiment_score(str(a.get("title",""))+" "+str(a.get("description","") or a.get("body",""))))
        score=float(np.mean(vals)) if vals else .5; return {"score":score,"confirmation":clamp(.50+abs(score-.50)),"articles":len(vals),"error":None}
    except Exception as e:journal("NEWS_ERROR",error=repr(e)); return {"score":.5,"confirmation":0.0,"articles":0,"error":repr(e)}

def update_orderflow():
    try:
        trades=api_get(f"/products/{PRODUCT}/trades",{"limit":FLOW_TRADE_LIMIT}); buy=sell=lb=ls=0.; bids={}; asks={}
        for t in trades or []:
            size=safe_float(t.get("size")); price=safe_float(t.get("price")); side=str(t.get("side","")).lower()
            if size<=0:continue
            if side=="buy":buy+=size; lb+=size if size>=FLOW_LARGE_TRADE_BTC else 0; asks[price]=asks.get(price,0)+size
            elif side=="sell":sell+=size; ls+=size if size>=FLOW_LARGE_TRADE_BTC else 0; bids[price]=bids.get(price,0)+size
        FLOW.update({"ts":now_ts(),"buy":buy,"sell":sell,"large_buy":lb,"large_sell":ls,"delta":buy-sell,"bids":bids,"asks":asks})
    except Exception as e:journal("ORDERFLOW_ERROR",error=repr(e))
    return FLOW.copy()

def fetch_shadow_orderbook(price_hint=0.0):
    """Fetch one Coinbase Level-2 order-book snapshot for Shadow diagnostics only. It never mutates FLOW and never enters decision_engine(), scoring, execution, or Telegram decisions. """
    if not SHADOW_ORDERBOOK_ENABLED:
        return SHADOW_ORDERBOOK.copy()
    try:
        data=api_get(f"/products/{PRODUCT}/book",{"level":SHADOW_ORDERBOOK_LEVEL}) or {}
        bids=[]; asks=[]
        for row in data.get("bids",[]) or []:
            if len(row)>=2:
                px=safe_float(row[0]); sz=safe_float(row[1]); n=safe_int(row[2],0) if len(row)>=3 else 0
                if px>0 and sz>0:bids.append((px,sz,n))
        for row in data.get("asks",[]) or []:
            if len(row)>=2:
                px=safe_float(row[0]); sz=safe_float(row[1]); n=safe_int(row[2],0) if len(row)>=3 else 0
                if px>0 and sz>0:asks.append((px,sz,n))
        bids=sorted(bids,key=lambda x:x[0],reverse=True)[:SHADOW_ORDERBOOK_TOP_N]
        asks=sorted(asks,key=lambda x:x[0])[:SHADOW_ORDERBOOK_TOP_N]
        if not bids or not asks: raise ValueError("empty_orderbook")
        ref=safe_float(price_hint) or (bids[0][0]+asks[0][0])/2
        best_bid=bids[0][0]; best_ask=asks[0][0]; spread=max(0.0,best_ask-best_bid)
        band=ref*SHADOW_ORDERBOOK_BAND_PCT
        bid_band=[x for x in bids if x[0]>=ref-band]
        ask_band=[x for x in asks if x[0]<=ref+band]
        bid_depth=sum(x[1] for x in bid_band); ask_depth=sum(x[1] for x in ask_band)
        total=bid_depth+ask_depth
        imbalance=(bid_depth-ask_depth)/total if total>0 else 0.0
        top_bids=sorted(bids,key=lambda x:x[1],reverse=True)[:5]
        top_asks=sorted(asks,key=lambda x:x[1],reverse=True)[:5]
        out={"ts":now_ts(),"price":ref,"spread":spread,"spread_bps":spread/max(ref,1e-9)*10000,
             "bid_depth":bid_depth,"ask_depth":ask_depth,"imbalance":imbalance,
             "bid_levels":len(bid_band),"ask_levels":len(ask_band),"nearest_bid":best_bid,"nearest_ask":best_ask,
             "nearest_bid_distance_bps":max(0.0,(ref-best_bid)/max(ref,1e-9)*10000),
             "nearest_ask_distance_bps":max(0.0,(best_ask-ref)/max(ref,1e-9)*10000),
             "top_bid_clusters":[{"price":x[0],"size":x[1],"orders":x[2]} for x in top_bids],
             "top_ask_clusters":[{"price":x[0],"size":x[1],"orders":x[2]} for x in top_asks],
             "buy_volume":safe_float(FLOW.get("buy")),"sell_volume":safe_float(FLOW.get("sell")),"trade_delta":safe_float(FLOW.get("delta")),"error":None}
        SHADOW_ORDERBOOK.update(out)
        return SHADOW_ORDERBOOK.copy()
    except Exception as e:
        SHADOW_ORDERBOOK["ts"]=now_ts(); SHADOW_ORDERBOOK["error"]=repr(e)
        journal("SHADOW_ORDERBOOK_ERROR",error=repr(e),diagnostic_only=True)
        return SHADOW_ORDERBOOK.copy()

def log_shadow_orderbook_snapshot(ob,scan_identifier,price):
    if not isinstance(ob,dict): return
    row={"ts":utc_now().isoformat(),"scan_id":scan_identifier,"price":safe_float(price),"spread":safe_float(ob.get("spread")),
         "spread_bps":safe_float(ob.get("spread_bps")),"bid_depth":safe_float(ob.get("bid_depth")),"ask_depth":safe_float(ob.get("ask_depth")),
         "imbalance":safe_float(ob.get("imbalance")),"bid_levels":safe_int(ob.get("bid_levels")),"ask_levels":safe_int(ob.get("ask_levels")),
         "nearest_bid":safe_float(ob.get("nearest_bid")),"nearest_ask":safe_float(ob.get("nearest_ask")),
         "nearest_bid_distance_bps":safe_float(ob.get("nearest_bid_distance_bps")),"nearest_ask_distance_bps":safe_float(ob.get("nearest_ask_distance_bps")),
         "buy_volume":safe_float(ob.get("buy_volume")),"sell_volume":safe_float(ob.get("sell_volume")),"trade_delta":safe_float(ob.get("trade_delta")),
         "top_bid_clusters":ob.get("top_bid_clusters",[]),"top_ask_clusters":ob.get("top_ask_clusters",[]),"error":ob.get("error"),"diagnostic_only":True}
    ensure_parent_dir(SHADOW_ORDERBOOK_CSV); new=not os.path.exists(SHADOW_ORDERBOOK_CSV)
    fields=list(row.keys())
    with open(SHADOW_ORDERBOOK_CSV,"a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        if new:w.writeheader()
        w.writerow(row)
    journal("SHADOW_ORDERBOOK_SNAPSHOT",**row)

def detect_order_block(df,direction):
    """Lightweight, explainable OB detector: displacement candle + preceding opposite candle + freshness/retest."""
    d=prepare(df)
    if len(d)<12:return {"detected":False,"quality":0.0,"fresh":False,"reaction":0.0}
    r=d.iloc[-1]; atr=max(safe_float(r.atr),safe_float(r.close)*.0001)
    look=d.iloc[-10:-1].copy(); best=None
    for i in range(len(look)-2,-1,-1):
        c=look.iloc[i]; nxt=look.iloc[i+1]
        body=abs(safe_float(c.close)-safe_float(c.open)); disp=abs(safe_float(nxt.close)-safe_float(nxt.open))/atr
        if direction=="LONG" and c.close<c.open and nxt.close>nxt.open and disp>=.8:
            best=(safe_float(c.low),safe_float(c.high),disp); break
        if direction=="SHORT" and c.close>c.open and nxt.close<nxt.open and disp>=.8:
            best=(safe_float(c.low),safe_float(c.high),disp); break
    if not best:return {"detected":False,"quality":0.0,"fresh":False,"reaction":0.0}
    lo,hi,disp=best; price=safe_float(r.close); inside=lo<=price<=hi
    distance=min(abs(price-lo),abs(price-hi))/atr; reaction=clamp(1-distance/2) if not inside else 1.0
    # Fresh means price has not repeatedly closed through the block after its creation.
    closes=d.close.iloc[-5:]
    invalid=(bool((closes<lo).any()) if direction=="LONG" else bool((closes>hi).any()))
    fresh=not invalid
    quality=clamp(.45*clamp(disp/2)+.35*reaction+.20*bool(fresh))
    return {"detected":True,"quality":quality,"fresh":fresh,"reaction":reaction,"low":lo,"high":hi,"displacement":disp,"inside":inside}

def detect_liquidity_sweep(df,direction):
    d=prepare(df)
    if len(d)<SWEEP_LOOKBACK+2:return {"detected":False,"confirmed":False,"quality":0.0,"confirmation":0.0,"reason":"insufficient_data","age":99}
    r=d.iloc[-1]; atr=max(safe_float(r.atr),safe_float(r.close)*.0001); prev=d.iloc[-SWEEP_LOOKBACK-1:-1]
    # Keep the 20-bar external reference, but add a recent 5-bar internal swing reference.
    internal=d.iloc[-6:-1]
    hi=max(safe_float(prev.high.max()),safe_float(internal.high.max())); lo=min(safe_float(prev.low.min()),safe_float(internal.low.min()))
    rng=max(safe_float(r.high-r.low),atr*.1)
    if direction=="LONG":pen=max(0,(lo-safe_float(r.low))/atr); rec=max(0,(safe_float(r.close)-lo)/atr); wick=clamp((safe_float(r.close)-r.low)/rng); detected=bool(r.low<lo and pen>=SWEEP_MIN_PENETRATION_ATR and rec>=SWEEP_MIN_RECLAIM)
    else:pen=max(0,(safe_float(r.high)-hi)/atr); rec=max(0,(hi-safe_float(r.close))/atr); wick=clamp((r.high-safe_float(r.close))/rng); detected=bool(r.high>hi and pen>=SWEEP_MIN_PENETRATION_ATR and rec>=SWEEP_MIN_RECLAIM)
    vol=clamp(safe_float(r.volume)/(safe_float(r.vol_ma20) or 1)); of=FLOW; flow=clamp(abs(safe_float(of.get("delta")))/(safe_float(of.get("buy"))+safe_float(of.get("sell")) or 1))
    ob=detect_order_block(df,direction)
    q=.32*clamp(pen/.50)+.28*clamp(rec)+.18*wick+.10*vol+.12*safe_float(ob.get("quality"))
    conf=.35*q+.20*vol+.15*flow+.20*clamp(rec)+.10*safe_float(ob.get("quality")) if detected else 0
    ok=detected and conf>=SWEEP_MIN_CONFIRMATION
    return {"detected":detected,"confirmed":ok,"quality":q,"confirmation":conf,"age":0,"penetration":pen,"reclaim":rec,
            "order_block":ob,"reason":"confirmed" if ok else ("sweep_not_confirmed" if detected else "no_sweep")}

def sweep_confirmation_score(sweep,execution,market,direction):
    if not isinstance(sweep,dict):return 0.0
    return clamp(.60*safe_float(sweep.get("confirmation"))+.25*safe_float(sweep.get("quality"))+.15*safe_float(execution.get("agreement") if isinstance(execution,dict) else 0))

def detect_continuation(df,direction):
    d=prepare(df)
    if len(d)<CONT_LOOKBACK+2:return {"ready":False,"score":0.0,"impulse":0.0,"pullback":0.0}
    r=d.iloc[-1]; atr=max(safe_float(r.atr),safe_float(r.close)*.0001); start=d.iloc[max(0,len(d)-CONT_IMPULSE_BARS-1)]; current=safe_float(r.close); sp=safe_float(start.close)
    if direction=="LONG":imp=(current-sp)/atr; ext=safe_float(d.high.iloc[-CONT_LOOKBACK:].max()); pb=(ext-current)/max(abs(ext-sp),atr); trend=current>r.ema21; mom=r.ret4>0
    else:imp=(sp-current)/atr; ext=safe_float(d.low.iloc[-CONT_LOOKBACK:].min()); pb=(current-ext)/max(abs(sp-ext),atr); trend=current<r.ema21; mom=r.ret4<0
    ok=CONT_MIN_PULLBACK<=pb<=CONT_MAX_PULLBACK; score=.45*clamp(imp/CONT_IMPULSE_ATR)+.30*ok+.15*bool(trend)+.10*bool(mom)
    return {"ready":score>=CONT_READY_SCORE and imp>=CONT_IMPULSE_ATR and ok,"score":clamp(score),"impulse":imp,"pullback":pb}
def _shadow_pivot_levels(df,kind):
    """Extract recent swing highs/lows for diagnostics only. Never used by strategy logic."""
    d=prepare(df)
    if len(d)<(SHADOW_SR_PIVOT_WINDOW*2+3): return []
    n=SHADOW_SR_PIVOT_WINDOW; start=max(n,len(d)-SHADOW_SR_LOOKBACK); end=len(d)-n; levels=[]
    for i in range(start,end):
        row=d.iloc[i]
        if kind=="RESISTANCE":
            v=safe_float(row.high)
            left=d.high.iloc[i-n:i]; right=d.high.iloc[i+1:i+n+1]
            if v>=safe_float(left.max(),v) and v>=safe_float(right.max(),v): levels.append({"price":v,"ts":int(row.ts),"kind":kind})
        else:
            v=safe_float(row.low)
            left=d.low.iloc[i-n:i]; right=d.low.iloc[i+1:i+n+1]
            if v<=safe_float(left.min(),v) and v<=safe_float(right.min(),v): levels.append({"price":v,"ts":int(row.ts),"kind":kind})
    return levels

def _shadow_cluster_levels(levels,atr):
    """Cluster nearby pivots so diagnostics show key zones instead of every tiny swing."""
    if not levels:return []
    tol=max(atr*SHADOW_SR_CLUSTER_ATR,1e-9); clusters=[]
    for lv in sorted(levels,key=lambda x:x["price"]):
        if not clusters or abs(lv["price"]-clusters[-1]["price"])>tol:
            clusters.append({"price":lv["price"],"touches":1,"kind":lv["kind"],"first_ts":lv["ts"],"last_ts":lv["ts"]})
        else:
            c=clusters[-1]; c["price"]=(c["price"]*c["touches"]+lv["price"])/(c["touches"]+1); c["touches"]+=1; c["first_ts"]=min(c["first_ts"],lv["ts"]); c["last_ts"]=max(c["last_ts"],lv["ts"])
    return sorted(clusters,key=lambda x:(x["touches"],x["price"]),reverse=True)

def _shadow_level_event(prev_close,cur_open,cur_high,cur_low,cur_close,level,atr):
    """Classify a nearby S/R interaction without changing any trading decision."""
    p=safe_float(level["price"]); tol=max(atr*SHADOW_SR_RETEST_ATR,1e-9); reclaim=max(atr*SHADOW_SR_RECLAIM_ATR,1e-9); reject=max(atr*SHADOW_SR_REJECTION_ATR,1e-9)
    if level["kind"]=="SUPPORT":
        retest=cur_low<=p+tol and cur_close>=p-tol
        reclaim_event=prev_close<p-reclaim and cur_close>=p+reclaim
        rejection=cur_low<p-reject and cur_close>p+reclaim
        event="RECLAIM" if reclaim_event else "REJECTION" if rejection else "RETEST" if retest else "NONE"
    else:
        retest=cur_high>=p-tol and cur_close<=p+tol
        reclaim_event=prev_close>p+reclaim and cur_close<=p-reclaim
        rejection=cur_high>p+reject and cur_close<p-reclaim
        event="RECLAIM" if reclaim_event else "REJECTION" if rejection else "RETEST" if retest else "NONE"
    return event

def _shadow_update_structure_state(state, timeframe, level, event, prev_close, cur_close, atr):
    """Stateful S/R confirmation model; diagnostic only. RETEST is setup evidence. A cross creates a candidate. A later hold/retest within up to SHADOW_CONFIRM_MAX_SCANS confirms; a failed hold invalidates the candidate. No trade decision is returned or changed here. """
    root=state.setdefault("shadow_state",{}).setdefault("levels",{})
    kind=str(level.get("kind","")); price=safe_float(level.get("price")); atr=max(safe_float(atr),1e-9)
    match_tol=atr*SHADOW_LEVEL_MATCH_ATR
    key=None; best_dist=None
    for k,v in root.items():
        if v.get("timeframe")==timeframe and v.get("kind")==kind:
            dist=abs(safe_float(v.get("level"))-price)
            if dist<=match_tol and (best_dist is None or dist<best_dist): key,best_dist=k,dist
    if key is None:
        key=f"{timeframe}:{kind}:{int(price/max(atr,1e-9))}"
        root[key]={"timeframe":timeframe,"kind":kind,"level":price,"state":"NONE","direction":None,"age_scans":0,"last_event":"NONE"}
    st=root[key]; st.update({"level":price,"last_event":event,"last_price":cur_close,"last_ts":utc_now().isoformat()})
    old_state=st.get("state","NONE"); new_state=old_state; direction=st.get("direction")
    reclaim=max(atr*SHADOW_SR_RECLAIM_ATR,1e-9); reject=max(atr*SHADOW_SR_REJECTION_ATR,1e-9)
    if old_state in {"BREAKOUT_CANDIDATE","BREAKDOWN_CANDIDATE"}:
        st["age_scans"]=safe_int(st.get("age_scans",0))+1
        if st["age_scans"]<=SHADOW_CONFIRM_MAX_SCANS:
            if old_state=="BREAKOUT_CANDIDATE" and cur_close>price+reclaim:
                new_state="BREAKOUT_CONFIRMED"; direction="LONG"
            elif old_state=="BREAKDOWN_CANDIDATE" and cur_close<price-reclaim:
                new_state="BREAKDOWN_CONFIRMED"; direction="SHORT"
            elif old_state=="BREAKOUT_CANDIDATE" and cur_close<price-reject:
                new_state="FAILURE_REJECTION"; direction=None
            elif old_state=="BREAKDOWN_CANDIDATE" and cur_close>price+reject:
                new_state="FAILURE_RECLAIM"; direction=None
        else:
            new_state="FAILURE_TIMEOUT"; direction=None
    else:
        st["age_scans"]=0
        if kind=="RESISTANCE" and cur_close>price+reclaim and prev_close<=price+reclaim:
            new_state="BREAKOUT_CANDIDATE"; direction="LONG"; st["age_scans"]=0
        elif kind=="SUPPORT" and cur_close<price-reclaim and prev_close>=price-reclaim:
            new_state="BREAKDOWN_CANDIDATE"; direction="SHORT"; st["age_scans"]=0
        elif event=="RETEST":
            new_state="TEST_RETEST"; direction=None
        elif event=="RECLAIM":
            # Reclaim is evidence, not final confirmation. Keep it as a test/retest state
            # until a subsequent hold confirms the structure.
            new_state="TEST_RETEST"; direction=None
        elif event=="REJECTION":
            new_state="TEST_RETEST"; direction=None
        elif old_state in {"BREAKOUT_CONFIRMED","BREAKDOWN_CONFIRMED","FAILURE_REJECTION","FAILURE_RECLAIM","FAILURE_TIMEOUT"}:
            new_state="NONE"; direction=None
    st["state"]=new_state; st["direction"]=direction
    st["state_changed"]=new_state!=old_state
    st["confirmation_age_scans"]=st.get("age_scans",0)
    # Bound persisted diagnostic state.
    if len(root)>SHADOW_STATE_MAX_LEVELS:
        oldest=sorted(root.items(),key=lambda kv:kv[1].get("last_ts","") or "")[0][0]
        root.pop(oldest,None)
    return {"state":new_state,"previous_state":old_state,"direction":direction,"age_scans":st.get("age_scans",0),"changed":new_state!=old_state}

def _shadow_intel_candle_features(d, idx):
    r=d.iloc[idx]; close=safe_float(r.close); op=safe_float(r.open); hi=safe_float(r.high); lo=safe_float(r.low)
    atr=max(safe_float(r.atr),close*.0001); rng=max(hi-lo,1e-9); body=abs(close-op)
    return {"open":op,"high":hi,"low":lo,"close":close,"atr":atr,"range":rng,
            "body":body,"body_atr":body/atr,"bullish":close>op,"bearish":close<op,
            "lower_wick":max(0.0,min(op,close)-lo),"upper_wick":max(0.0,hi-max(op,close)),
            "lower_wick_ratio":max(0.0,min(op,close)-lo)/rng,
            "upper_wick_ratio":max(0.0,hi-max(op,close))/rng,
            "volume":safe_float(r.volume),"volume_ma":max(safe_float(r.vol_ma20),1e-9),
            "volume_ratio":safe_float(r.volume)/max(safe_float(r.vol_ma20),1e-9),
            "ts":int(safe_float(r.ts)) if "ts" in d.columns else 0}

def _shadow_intel_near_levels(df, direction):
    if not isinstance(df,pd.DataFrame) or len(df)<10:return {"support":None,"resistance":None,"levels":[]}
    d=prepare(df); last=d.iloc[-1]; price=safe_float(last.close); atr=max(safe_float(last.atr),price*.0001)
    levels=_shadow_cluster_levels(_shadow_pivot_levels(d,"SUPPORT"),atr)+_shadow_cluster_levels(_shadow_pivot_levels(d,"RESISTANCE"),atr)
    levels=sorted(levels,key=lambda z:abs(safe_float(z["price"])-price))[:12]
    support=[z for z in levels if z["kind"]=="SUPPORT" and safe_float(z["price"])<=price+atr*SHADOW_INTEL_NEAR_ATR]
    resistance=[z for z in levels if z["kind"]=="RESISTANCE" and safe_float(z["price"])>=price-atr*SHADOW_INTEL_NEAR_ATR]
    sup=min(support,key=lambda z:abs(z["price"]-price)) if support else None
    res=min(resistance,key=lambda z:abs(z["price"]-price)) if resistance else None
    for z in (sup,res):
        if z is not None:
            z["distance_atr"]=abs(safe_float(z["price"])-price)/atr
            z["price_distance"]=abs(safe_float(z["price"])-price)
    return {"support":sup,"resistance":res,"levels":levels}

def _shadow_intel_order_blocks(df, direction):
    if not isinstance(df,pd.DataFrame) or len(df)<12:return []
    d=prepare(df).iloc[-SHADOW_INTEL_LOOKBACK:].reset_index(drop=True); n=len(d); blocks=[]
    for i in range(1,max(1,n-4)):
        base=_shadow_intel_candle_features(d,i)
        confirm_end=min(n,i+4); future=d.iloc[i+1:confirm_end]
        if future.empty:continue
        if direction=="LONG" and base["bearish"]:
            displacement=safe_float(future.high.max())-base["high"]
            if displacement>=base["atr"]*SHADOW_INTEL_OB_CONFIRM_ATR:
                zone_low=base["low"]; zone_high=base["open"]
                reaction=clamp(displacement/max(base["atr"],1e-9))
                confirm_idx=i+1+int(future.high.values.argmax())
                blocks.append({"direction":"LONG","timeframe":None,"zone_low":zone_low,"zone_high":zone_high,
                               "origin_ts":base["ts"],"confirm_ts":int(safe_float(d.iloc[min(confirm_idx,n-1)].ts)),
                               "age_bars":n-1-i,"reaction_atr":reaction,"volume_ratio":base["volume_ratio"]})
        elif direction=="SHORT" and base["bullish"]:
            displacement=base["low"]-safe_float(future.low.min())
            if displacement>=base["atr"]*SHADOW_INTEL_OB_CONFIRM_ATR:
                zone_low=base["open"]; zone_high=base["high"]
                reaction=clamp(displacement/max(base["atr"],1e-9))
                confirm_idx=i+1+int(future.low.values.argmin())
                blocks.append({"direction":"SHORT","timeframe":None,"zone_low":zone_low,"zone_high":zone_high,
                               "origin_ts":base["ts"],"confirm_ts":int(safe_float(d.iloc[min(confirm_idx,n-1)].ts)),
                               "age_bars":n-1-i,"reaction_atr":reaction,"volume_ratio":base["volume_ratio"]})
    out=[]
    price=safe_float(d.iloc[-1].close)
    for b in reversed(blocks):
        if b["age_bars"]>SHADOW_INTEL_OB_MAX_AGE_BARS:continue
        in_zone=b["zone_low"]-0.05*safe_float(d.iloc[-1].atr)<=price<=b["zone_high"]+0.05*safe_float(d.iloc[-1].atr)
        if in_zone or b["age_bars"]<=12:
            b["in_zone"] = bool(in_zone); b["freshness"] = clamp(1.0-b["age_bars"]/max(SHADOW_INTEL_OB_MAX_AGE_BARS,1)); out.append(b)
        if len(out)>=5:break
    return out

def _shadow_intel_liquidity_and_structure(df, direction):
    if not isinstance(df,pd.DataFrame) or len(df)<25:return {"sweep":False,"sweep_strength":0.0,"choch":False,"bos":False,"displacement":0.0,"rejection":0.0}
    d=prepare(df); last=_shadow_intel_candle_features(d,len(d)-1); prev=d.iloc[-2]; price=last["close"]; atr=last["atr"]
    look=d.iloc[max(0,len(d)-SHADOW_INTEL_SWEEP_LOOKBACK-1):-1]
    swing_high=safe_float(look.high.max(),price); swing_low=safe_float(look.low.min(),price)
    sweep=False; sweep_strength=0.0
    if direction=="LONG":
        penetr=max(0.0,swing_low-last["low"])/atr; reclaim=max(0.0,last["close"]-swing_low)/atr
        sweep=penetr>=SHADOW_INTEL_SWEEP_PENETRATION_ATR and reclaim>=SHADOW_INTEL_SWEEP_RECLAIM_ATR
        sweep_strength=clamp(.55*clamp(penetr/.5)+.45*clamp(reclaim/.5))
    else:
        penetr=max(0.0,last["high"]-swing_high)/atr; reclaim=max(0.0,swing_high-last["close"])/atr
        sweep=penetr>=SHADOW_INTEL_SWEEP_PENETRATION_ATR and reclaim>=SHADOW_INTEL_SWEEP_RECLAIM_ATR
        sweep_strength=clamp(.55*clamp(penetr/.5)+.45*clamp(reclaim/.5))
    # Explicit directional structure shift: opposite-side break after a recent move, not the legacy Core choch flag.
    recent=d.iloc[max(0,len(d)-10):-1]
    prior=d.iloc[max(0,len(d)-22):max(0,len(d)-10)]
    rh=safe_float(prior.high.max(),price); rl=safe_float(prior.low.min(),price)
    bos=(price>rh) if direction=="LONG" else (price<rl)
    # A directional CHoCH proxy requires prior displacement in the opposite direction, then a break of its recent swing.
    prior_move=safe_float(prior.close.iloc[-1]-prior.close.iloc[0]) if len(prior)>1 else 0.0
    choch=((prior_move<0 and price>rh) if direction=="LONG" else (prior_move>0 and price<rl))
    displacement=clamp(last["body"]/atr)
    rejection=clamp((last["lower_wick_ratio"] if direction=="LONG" else last["upper_wick_ratio"])*1.5)
    return {"sweep":bool(sweep),"sweep_strength":sweep_strength,"choch":bool(choch),"bos":bool(bos),
            "displacement":displacement,"rejection":rejection,"swing_high":swing_high,"swing_low":swing_low,
            "close":price,"atr":atr,"volume_ratio":last["volume_ratio"]}

def _shadow_intel_mtf(F,direction):
    parts={}; vals=[]
    for tf in SHADOW_INTEL_TIMEFRAMES:
        df=F.get(tf)
        if not isinstance(df,pd.DataFrame) or len(df)<25:continue
        d=prepare(df); x=_tf_direction_score(df); st=_shadow_intel_liquidity_and_structure(df,direction)
        near=_shadow_intel_near_levels(df,direction); obs=_shadow_intel_order_blocks(df,direction)
        aligned=(x["trend"] if direction=="LONG" else 1-x["trend"])
        vals.append(aligned)
        parts[tf]={"trend":aligned,"near_support":near.get("support"),"near_resistance":near.get("resistance"),
                   "structure":st,"order_blocks":obs}
    return parts, (float(np.mean(vals)) if vals else .5)

def _shadow_intel_opportunity(F,direction,state,scan_identifier,orderbook=None):
    if not SHADOW_INTEL_ENABLED:return None
    try:
        parts,mtf=_shadow_intel_mtf(F,direction)
        x5=parts.get("5m",{}); x15=parts.get("15m",{}); x1h=parts.get("1h",{})
        st=x5.get("structure",{}) or {}; st15=x15.get("structure",{}) or {}; st1=x1h.get("structure",{}) or {}
        # Use the 5m frame for directional follow-through calculations.
        df5=F.get("5m")
        d=prepare(df5) if isinstance(df5,pd.DataFrame) and len(df5)>=4 else pd.DataFrame()
        if d.empty:
            raise ValueError("shadow_intel_requires_5m_data")
        last=_shadow_intel_candle_features(d,len(d)-1)
        atr=max(safe_float(last.get("atr")),safe_float(last.get("close"))*.0001)
        near_support=x5.get("near_support") or x15.get("near_support") or x1h.get("near_support")
        near_res=x5.get("near_resistance") or x15.get("near_resistance") or x1h.get("near_resistance")
        ob_candidates=[]
        for tfp in (x1h,x15,x5):
            for ob in tfp.get("order_blocks",[])[:3]:
                ob=dict(ob); ob["timeframe"]="1h" if tfp is x1h else "15m" if tfp is x15 else "5m"; ob_candidates.append(ob)
        ob_candidates=sorted(ob_candidates,key=lambda b:(b.get("in_zone",False),b.get("freshness",0),b.get("reaction_atr",0)),reverse=True)
        ob=ob_candidates[0] if ob_candidates else None
        near_zone=near_support if direction=="LONG" else near_res
        rejection=max(safe_float(st.get("rejection")),safe_float(st15.get("rejection")))
        sweep=max(safe_float(st.get("sweep_strength")),safe_float(st15.get("sweep_strength")))
        structure_shift=bool(st.get("choch") or st15.get("choch"))
        bos=bool(st.get("bos") or st15.get("bos"))
        displacement=max(safe_float(st.get("displacement")),safe_float(st15.get("displacement")))
        volume=max(safe_float(st.get("volume_ratio")),safe_float(st15.get("volume_ratio")))
        ob_quality=0.0
        if ob:
            ob_quality=clamp(.35*safe_float(ob.get("freshness"))+ .35*clamp(safe_float(ob.get("reaction_atr"))) + .20*clamp(safe_float(ob.get("volume_ratio"))/2) + .10*bool(ob.get("in_zone")))
        sr_quality=0.0
        if near_zone:
            # Near-level is evidence only; distance is normalized to the active timeframe ATR.
            zdist=safe_float(near_zone.get("distance_atr",0.0),0.0)
            sr_quality=clamp(1-zdist/max(SHADOW_INTEL_NEAR_ATR,1e-9))
        obook=orderbook if isinstance(orderbook,dict) else SHADOW_ORDERBOOK
        book_imbalance=clamp((safe_float(obook.get("imbalance"))+1.0)/2.0)
        book_long=clamp(.5+.5*safe_float(obook.get("imbalance")))
        book_short=clamp(.5-.5*safe_float(obook.get("imbalance")))
        book_pressure=book_long if direction=="LONG" else book_short
        # Direction-aware follow-through: the previous Shadow test used absolute
        # displacement, so the same bullish candle could mark BOTH LONG and SHORT
        # as having follow-through. Measure the move in the requested direction,
        # allowing a strong 1-candle impulse or an aligned 3-candle continuation.
        cur_body_signed=(safe_float(last["close"])-safe_float(last["open"]))/max(atr,1e-9)
        if direction=="SHORT": cur_body_signed=-cur_body_signed
        n3=max(0,len(d)-4)
        three_signed=(safe_float(d.iloc[-1].close)-safe_float(d.iloc[n3].close))/max(atr,1e-9)
        if direction=="SHORT": three_signed=-three_signed
        follow_through_strength=clamp(max(cur_body_signed,three_signed))
        follow_through=bool(follow_through_strength>=SHADOW_INTEL_DISPLACEMENT_ATR)
        evidence={
            "near_sr":bool(near_zone),"sr_quality":sr_quality,"order_block":bool(ob),"order_block_quality":ob_quality,
            "liquidity_sweep":bool(st.get("sweep") or st15.get("sweep")),"sweep_strength":sweep,
            "rejection":rejection,"choch":structure_shift,"bos":bos,"displacement":displacement,
            "volume_ratio":volume,"mtf_agreement":mtf,
            "reclaim_or_structure":bool(st.get("choch") or st.get("bos") or st15.get("choch") or st15.get("bos")),
            "follow_through":follow_through,
            "orderbook_imbalance":safe_float(obook.get("imbalance")),"orderbook_pressure":book_pressure,
            "orderbook_bid_depth":safe_float(obook.get("bid_depth")),"orderbook_ask_depth":safe_float(obook.get("ask_depth")),
            "orderbook_spread_bps":safe_float(obook.get("spread_bps")),"trade_delta":safe_float(obook.get("trade_delta")),
        }
        # Diagnostic evidence score only. It is NOT a trade score and is never passed to decision_engine().
        score=clamp(.18*sr_quality + .16*ob_quality + .16*sweep + .12*rejection + .16*clamp(displacement) + .10*clamp(volume/2) + .12*mtf)
        has_trigger=bool(evidence["near_sr"] or evidence["order_block"] or evidence["liquidity_sweep"] or evidence["choch"] or evidence["bos"])
        strong_confirm=bool(has_trigger and (evidence["reclaim_or_structure"] or evidence["liquidity_sweep"]) and evidence["follow_through"] and mtf>=.52)
        developing=bool(has_trigger and (score>=.38 or evidence["rejection"]>=.55 or evidence["order_block_quality"]>=.45))
        key=f"{direction}"
        root=state.setdefault("shadow_opportunity",{})
        old=root.get(key,{})
        candle_ts=max([safe_int((F.get(tf).iloc[-1].ts if isinstance(F.get(tf),pd.DataFrame) and len(F.get(tf)) else 0),0) for tf in ("5m","15m")])
        prev_candle_ts=safe_int(old.get("last_candle_ts"),0)
        candle_advanced=candle_ts>prev_candle_ts
        _shadow_intel_forward_outcome(F,direction,state,scan_identifier,candle_advanced)
        age=safe_int(old.get("age_bars"),0)+(1 if candle_advanced else 0)
        if strong_confirm: status="CONFIRMED"
        elif developing: status="CONFIRMING" if old.get("status") in {"DEVELOPING","CONFIRMING"} else "DEVELOPING"
        elif old.get("status") in {"DEVELOPING","CONFIRMING"} and age<=SHADOW_INTEL_STATE_TTL_BARS: status=old.get("status")
        else: status="NONE"
        if status in {"DEVELOPING","CONFIRMING"} and age>SHADOW_INTEL_STATE_TTL_BARS: status="FAILED"
        root[key]={"status":status,"age_bars":age if status!="NONE" else 0,"last_candle_ts":candle_ts,"last_score":score,"last_update":utc_now().isoformat()}
        if status in {"DEVELOPING","CONFIRMING","CONFIRMED"} and old.get("status") not in {"DEVELOPING","CONFIRMING","CONFIRMED"}:
            _shadow_intel_start_episode(F,direction,state,scan_identifier,status,score)
        row={"ts":utc_now().isoformat(),"scan_id":scan_identifier,"direction":direction,"status":status,"age_bars":root[key]["age_bars"],
             "evidence_score":round(score,6),"near_sr":evidence["near_sr"],"sr_quality":round(sr_quality,6),"order_block":evidence["order_block"],
             "order_block_quality":round(ob_quality,6),"liquidity_sweep":evidence["liquidity_sweep"],"sweep_strength":round(sweep,6),
             "rejection":round(rejection,6),"choch":evidence["choch"],"bos":evidence["bos"],"displacement":round(displacement,6),
             "volume_ratio":round(volume,6),"mtf_agreement":round(mtf,6),"reclaim_or_structure":evidence["reclaim_or_structure"],
             "follow_through":evidence["follow_through"],"orderbook_imbalance":evidence["orderbook_imbalance"],"orderbook_pressure":evidence["orderbook_pressure"],
             "orderbook_bid_depth":evidence["orderbook_bid_depth"],"orderbook_ask_depth":evidence["orderbook_ask_depth"],"orderbook_spread_bps":evidence["orderbook_spread_bps"],"trade_delta":evidence["trade_delta"],
             "price":safe_float((F.get("5m").iloc[-1].close if isinstance(F.get("5m"),pd.DataFrame) else 0)),
             "order_block_detail":ob,"near_sr_detail":near_zone,"timeframe_details":parts,"diagnostic_only":True}
        ensure_parent_dir(SHADOW_INTEL_CSV); new=not os.path.exists(SHADOW_INTEL_CSV)
        fields=["ts","scan_id","direction","status","age_bars","evidence_score","near_sr","sr_quality","order_block","order_block_quality","liquidity_sweep","sweep_strength","rejection","choch","bos","displacement","volume_ratio","mtf_agreement","reclaim_or_structure","follow_through","orderbook_imbalance","orderbook_pressure","orderbook_bid_depth","orderbook_ask_depth","orderbook_spread_bps","trade_delta","price","order_block_detail","near_sr_detail","timeframe_details","diagnostic_only"]
        with open(SHADOW_INTEL_CSV,"a",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
            if new:w.writeheader()
            w.writerow(row)
        journal("SHADOW_OPPORTUNITY_INTELLIGENCE",**row)
        return row
    except Exception as e:
        journal("SHADOW_OPPORTUNITY_INTELLIGENCE_ERROR",scan_id=scan_identifier,direction=direction,error=repr(e),diagnostic_only=True)
        return {"direction":direction,"status":"ERROR","error":repr(e),"diagnostic_only":True}

def _adaptive_opportunity_level_room(F,direction,price,atr):
    """Find volatility-normalized room to the next opposing structural level."""
    levels=[]
    for tf in ("5m","15m","1h"):
        df=F.get(tf)
        if not isinstance(df,pd.DataFrame) or len(df)<10: continue
        near=_shadow_intel_near_levels(df,direction)
        candidate=near.get("resistance") if direction=="LONG" else near.get("support")
        if candidate:
            lp=safe_float(candidate.get("price"),0.0)
            if (direction=="LONG" and lp>price) or (direction=="SHORT" and lp<price):
                levels.append((abs(lp-price)/max(atr,1e-9),lp,tf))
    if not levels:
        return 4.0,None,None
    return min(levels,key=lambda x:x[0])

def adaptive_opportunity_intelligence(F,state,market,shadow_intel,orderbook=None,scan_identifier=None):
    """Structure + Flow + Context intelligence and the Adaptive Signal Bridge. The two engines remain independent evidence sources. Correlated observations are grouped/capped. The bridge may strengthen a V8 candidate or resolve a specific stale-context gate, but it cannot execute, widen risk, or bypass final V8 score and minimum-agreement safety checks. """
    if not ADAPTIVE_OPPORTUNITY_ENABLED or not isinstance(shadow_intel,dict): return None
    sid=scan_identifier or scan_id(); out={}
    market_regime=str((market or {}).get("regime","UNSTABLE"))
    market_flow=(market or {}).get("flow",{}) or {}
    for direction in ("LONG","SHORT"):
        sh=shadow_intel.get(direction) or {}
        parts=sh.get("timeframe_details",{}) or {}
        x5=parts.get("5m",{}) or {}; x15=parts.get("15m",{}) or {}; x1h=parts.get("1h",{}) or {}
        st5=x5.get("structure",{}) or {}; st15=x15.get("structure",{}) or {}; st1=x1h.get("structure",{}) or {}
        mtf=safe_float(sh.get("mtf_agreement"),.5); price=safe_float(sh.get("price"),0.0)
        atr=max([safe_float(x.get("atr"),0.0) for x in (st5,st15,st1)] or [0.0])
        if atr<=0: atr=max(price*.0005,1e-9)

        # ENGINE 1 â€” Structure & Price Action. Primary families are capped to avoid
        # counting BOS+CHoCH+reclaim as three independent events.
        structure_components={
            "sr":safe_float(sh.get("sr_quality")),
            "order_block":safe_float(sh.get("order_block_quality")),
            "liquidity_sweep":safe_float(sh.get("sweep_strength")),
            "structure_shift":1.0 if sh.get("choch") else 0.0,
            "bos":1.0 if sh.get("bos") else 0.0,
            "rejection":safe_float(sh.get("rejection")),
            "displacement":clamp(safe_float(sh.get("displacement"))),
            "mtf":mtf,
        }
        structural_primary=max(structure_components["sr"],structure_components["order_block"],structure_components["liquidity_sweep"])
        structural_shift=max(structure_components["structure_shift"],structure_components["bos"])
        structure_score=clamp(.34*structural_primary+.24*structural_shift+.18*structure_components["rejection"]+.12*structure_components["displacement"]+.12*mtf)

        # ENGINE 2 â€” Flow & Liquidity. Core flow + Shadow L2 are corroborating views;
        # they are blended, not stacked as independent point buckets.
        ob=orderbook if isinstance(orderbook,dict) else SHADOW_ORDERBOOK
        raw_imb=safe_float(ob.get("imbalance"),0.0)
        pressure=clamp(.5+.5*(raw_imb if direction=="LONG" else -raw_imb))
        delta=safe_float(ob.get("trade_delta"),0.0)
        delta_side=clamp(.5+.5*math.tanh(delta/1.0));
        if direction=="SHORT": delta_side=1.0-delta_side
        volume_side=clamp(safe_float(sh.get("volume_ratio"),1.0)/2.0)
        core_flow=safe_float(market_flow.get("long_score" if direction=="LONG" else "short_score"),.5)
        flow_score=clamp(.35*core_flow+.30*pressure+.20*delta_side+.15*volume_side)
        if raw_imb==0 and abs(delta)<1e-12: flow_score=clamp(.70*core_flow+.30*volume_side)

        regime_dir=regime_direction(market_regime)
        if regime_dir==direction and mtf>=.55: context="TREND"
        elif regime_dir!=direction and regime_dir in {"LONG","SHORT"}: context="COUNTER_TREND_PULLBACK"
        elif regime_dir=="NEUTRAL": context="RANGE/TRANSITION"
        else: context="TRANSITION"
        if mtf<.45: context="COUNTER_TREND_PULLBACK" if regime_dir!=direction else "TRANSITION"

        relationship="ALIGNED" if abs(structure_score-flow_score)<.18 else "PARTIALLY_ALIGNED"
        if (direction=="LONG" and raw_imb<-.20) or (direction=="SHORT" and raw_imb>.20): relationship="CONFLICTED"
        if structure_score>.70 and flow_score>.70 and relationship!="CONFLICTED": relationship="ALIGNED"

        combined=clamp(.62*structure_score+.38*flow_score)
        room_atr,room_price,room_tf=_adaptive_opportunity_level_room(F,direction,price,atr)
        if combined>=ADAPTIVE_LARGE_STRENGTH and room_atr>=ADAPTIVE_LARGE_ROOM_ATR: size="LARGE"
        elif combined>=ADAPTIVE_MEDIUM_STRENGTH and room_atr>=ADAPTIVE_MEDIUM_ROOM_ATR: size="MEDIUM"
        else: size="SMALL"

        raw_status=str(sh.get("status","NONE"))
        follow_through=bool((sh.get("follow_through") is True))
        if raw_status=="CONFIRMED" and relationship=="ALIGNED" and combined>=ADAPTIVE_MEDIUM_STRENGTH: state_name="CONTINUING"
        elif raw_status in {"CONFIRMING","CONFIRMED"}: state_name="CONFIRMING"
        elif raw_status=="DEVELOPING": state_name="DEVELOPING"
        elif raw_status=="FAILED": state_name="FAILED"
        else: state_name="NONE"

        # Bridge qualification is intentionally stricter than classification. It is
        # designed to stop the old problem where a genuinely strong Shadow setup was
        # forever invisible to V8, while still refusing a single noisy observation.
        bridge_ready=bool(
            ADAPTIVE_BRIDGE_ENABLED and
            raw_status in {"CONFIRMING","CONFIRMED"} and
            relationship=="ALIGNED" and
            structure_score>=ADAPTIVE_BRIDGE_MIN_STRUCTURE and
            flow_score>=ADAPTIVE_BRIDGE_MIN_FLOW and
            combined>=ADAPTIVE_BRIDGE_MIN_STRENGTH and
            mtf>=ADAPTIVE_BRIDGE_MIN_MTF and
            follow_through and
            room_atr>=ADAPTIVE_BRIDGE_MIN_ROOM_ATR and
            not ((direction=="LONG" and raw_imb<-ADAPTIVE_BRIDGE_MAX_CONFLICT) or (direction=="SHORT" and raw_imb>ADAPTIVE_BRIDGE_MAX_CONFLICT))
        )
        bridge_score_bonus=ADAPTIVE_BRIDGE_MAX_SCORE_BONUS*clamp((combined-ADAPTIVE_BRIDGE_MIN_STRENGTH)/max(1-ADAPTIVE_BRIDGE_MIN_STRENGTH,1e-9)) if bridge_ready else 0.0
        bridge_agreement_boost=ADAPTIVE_BRIDGE_MAX_AGREEMENT_BOOST*clamp((combined-ADAPTIVE_BRIDGE_MIN_STRENGTH)/max(1-ADAPTIVE_BRIDGE_MIN_STRENGTH,1e-9)) if bridge_ready else 0.0
        if bridge_ready:
            bridge_state="QUALIFIED"
        elif raw_status in {"DEVELOPING","CONFIRMING","CONFIRMED"}:
            bridge_state="WATCHING"
        else:
            bridge_state="NONE"

        factors={"structure_primary":round(structural_primary,4),"structure_shift":bool(structural_shift),
                 "flow_core":round(core_flow,4),"flow_orderbook":round(pressure,4),"flow_delta":round(delta_side,4),
                 "flow_participation":round(volume_side,4),"mtf":round(mtf,4),"follow_through":follow_through}
        out[direction]={"direction":direction,"state":state_name,"context":context,"engine_relationship":relationship,
                        "opportunity_size":size,"structure_score":round(structure_score,6),"flow_score":round(flow_score,6),
                        "combined_strength":round(combined,6),"room_atr":round(room_atr,6),"room_price":room_price,"room_timeframe":room_tf,
                        "factor_count":sum(1 for v in factors.values() if (isinstance(v,bool) and v) or (not isinstance(v,bool) and safe_float(v)>=.55)),
                        "factors":factors,"price":price,"atr":atr,"regime":market_regime,"core_flow_direction":market_flow.get("confirmed_direction","NEUTRAL"),
                        "raw_shadow_status":raw_status,"follow_through":follow_through,
                        "bridge_state":bridge_state,"bridge_ready":bridge_ready,"bridge_score_bonus":round(bridge_score_bonus,6),
                        "bridge_agreement_boost":round(bridge_agreement_boost,6),"diagnostic_only":False}
        journal("ADAPTIVE_OPPORTUNITY_INTELLIGENCE",scan_id=sid,**out[direction])
        journal("ADAPTIVE_SIGNAL_BRIDGE",scan_id=sid,direction=direction,bridge_state=bridge_state,bridge_ready=bridge_ready,
                combined_strength=combined,structure_score=structure_score,flow_score=flow_score,mtf=mtf,
                follow_through=follow_through,room_atr=room_atr,score_bonus=bridge_score_bonus,
                agreement_boost=bridge_agreement_boost,context=context,relationship=relationship)
    state["adaptive_opportunity"]={"scan_id":sid,"ts":utc_now().isoformat(),"LONG":out.get("LONG"),"SHORT":out.get("SHORT")}
    try:
        ensure_parent_dir(ADAPTIVE_OPPORTUNITY_CSV); new=not os.path.exists(ADAPTIVE_OPPORTUNITY_CSV)
        fields=["ts","scan_id","direction","state","context","engine_relationship","opportunity_size","structure_score","flow_score","combined_strength","room_atr","room_price","room_timeframe","factor_count","price","atr","regime","core_flow_direction","raw_shadow_status","follow_through","bridge_state","bridge_ready","bridge_score_bonus","bridge_agreement_boost","diagnostic_only"]
        with open(ADAPTIVE_OPPORTUNITY_CSV,"a",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
            if new:w.writeheader()
            for direction in ("LONG","SHORT"):
                row=dict(out.get(direction,{}) or {}); row.update({"ts":utc_now().isoformat(),"scan_id":sid}); w.writerow(row)
    except Exception as e: journal("ADAPTIVE_OPPORTUNITY_CSV_ERROR",scan_id=sid,error=repr(e),diagnostic_only=True)
    return out


def _shadow_intel_forward_outcome(F,direction,state,scan_identifier,candle_advanced):
    """Measure forward price response for previously detected Shadow opportunities. Diagnostic only: no trading decision, score, veto, or execution side effect."""
    if not candle_advanced:return
    df=F.get("5m")
    if not isinstance(df,pd.DataFrame) or len(df)<2:return
    d=prepare(df); last=d.iloc[-1]; price=safe_float(last.close); hi=safe_float(last.high); lo=safe_float(last.low)
    episodes=state.setdefault("shadow_opportunity_episodes",{})
    ep=episodes.get(direction)
    if not isinstance(ep,dict):return
    entry=safe_float(ep.get("entry_price")); atr=max(safe_float(ep.get("entry_atr")),price*.0001)
    if entry<=0:return
    ep["age_bars"]=safe_int(ep.get("age_bars"),0)+1
    if direction=="LONG":
        mfe=max(safe_float(ep.get("mfe_atr"),0.0),(hi-entry)/atr)
        mae=max(safe_float(ep.get("mae_atr"),0.0),(entry-lo)/atr)
        net=(price-entry)/atr
    else:
        mfe=max(safe_float(ep.get("mfe_atr"),0.0),(entry-lo)/atr)
        mae=max(safe_float(ep.get("mae_atr"),0.0),(hi-entry)/atr)
        net=(entry-price)/atr
    ep.update({"mfe_atr":mfe,"mae_atr":mae,"net_move_atr":net,"last_candle_ts":safe_int(last.ts),"last_price":price})
    if ep["age_bars"]>=SHADOW_INTEL_CONFIRM_BARS:
        journal("SHADOW_OPPORTUNITY_FORWARD_OUTCOME",scan_id=scan_identifier,direction=direction,episode_id=ep.get("episode_id"),entry_price=entry,entry_atr=atr,age_bars=ep["age_bars"],mfe_atr=mfe,mae_atr=mae,net_move_atr=net,start_candle_ts=ep.get("start_candle_ts"),end_candle_ts=safe_int(last.ts),diagnostic_only=True)
        episodes.pop(direction,None)

def _shadow_intel_start_episode(F,direction,state,scan_identifier,status,score):
    if status not in {"DEVELOPING","CONFIRMING","CONFIRMED"}:return
    df=F.get("5m")
    if not isinstance(df,pd.DataFrame) or len(df)<1:return
    d=prepare(df); last=d.iloc[-1]; episodes=state.setdefault("shadow_opportunity_episodes",{})
    if direction in episodes:return
    episodes[direction]={"episode_id":f"{direction}_{safe_int(last.ts)}","start_candle_ts":safe_int(last.ts),"entry_price":safe_float(last.close),"entry_atr":max(safe_float(last.atr),safe_float(last.close)*.0001),"age_bars":0,"mfe_atr":0.0,"mae_atr":0.0,"initial_status":status,"initial_evidence_score":score}
    journal("SHADOW_OPPORTUNITY_EPISODE_START",scan_id=scan_identifier,direction=direction,episode_id=episodes[direction]["episode_id"],entry_price=episodes[direction]["entry_price"],entry_atr=episodes[direction]["entry_atr"],initial_status=status,initial_evidence_score=score,diagnostic_only=True)

def shadow_opportunity_intelligence(F,state,scan_identifier=None,orderbook=None):
    """Unified Shadow Opportunity Intelligence. Evidence only; no live decision, veto, override, or execution."""
    if not SHADOW_INTEL_ENABLED:return None
    sid=scan_identifier or scan_id(); out={}
    for direction in ("LONG","SHORT"):
        out[direction]=_shadow_intel_opportunity(F,direction,state,sid,orderbook)
    # Explicit conflict/validation telemetry: useful when Core blocks but Shadow sees a developing/confirmed setup.
    try:
        journal("SHADOW_OPPORTUNITY_SUMMARY",scan_id=sid,long=out.get("LONG"),short=out.get("SHORT"),diagnostic_only=True)
    except Exception:pass
    return out

def shadow_support_resistance_diagnostics(F,scan_identifier=None,state=None):
    """V8 SHADOW MODE diagnostics. Purely observational: no score, veto, state, trade, or Telegram side effect."""
    if not SHADOW_SR_ENABLED:return None
    try:
        rows=[]; scan_identifier=scan_identifier or globals()["scan_id"](); state=state if isinstance(state,dict) else {"shadow_state":{"levels":{}}}
        for tf in ("1h","15m","5m"):
            df=F.get(tf)
            if not isinstance(df,pd.DataFrame) or len(df)<10:continue
            d=prepare(df); last=d.iloc[-1]; prev=d.iloc[-2]; price=safe_float(last.close); atr=max(safe_float(last.atr),price*.0001)
            raw_support=_shadow_pivot_levels(d,"SUPPORT")
            raw_resistance=_shadow_pivot_levels(d,"RESISTANCE")
            zones=_shadow_cluster_levels(raw_support,atr)+_shadow_cluster_levels(raw_resistance,atr)
            zones=sorted(zones,key=lambda z:abs(z["price"]-price))[:8]
            for z in zones:
                event=_shadow_level_event(safe_float(prev.close),safe_float(last.open),safe_float(last.high),safe_float(last.low),price,z,atr)
                relation="BELOW" if price<z["price"] else "ABOVE" if price>z["price"] else "AT"
                distance_atr=abs(price-z["price"])/atr
                structure=_shadow_update_structure_state(state,tf,z,event,safe_float(prev.close),price,atr)
                row={"ts":utc_now().isoformat(),"scan_id":scan_identifier,"timeframe":tf,"level_type":z["kind"],"level":round(z["price"],8),"touches":z["touches"],"price":round(price,8),"distance_atr":round(distance_atr,4),"relation":relation,"event":event,"structure_state":structure.get("state"),"previous_state":structure.get("previous_state"),"structure_direction":structure.get("direction"),"confirmation_age_scans":structure.get("age_scans"),"state_changed":structure.get("changed"),"atr":round(atr,8),"source_ts":z["last_ts"]}
                rows.append(row)
        if not rows:return {"levels":0,"events":[]}
        ensure_parent_dir(SHADOW_SR_FILE); new=not os.path.exists(SHADOW_SR_FILE)
        fields=["ts","scan_id","timeframe","level_type","level","touches","price","distance_atr","relation","event","structure_state","previous_state","structure_direction","confirmation_age_scans","state_changed","atr","source_ts"]
        with open(SHADOW_SR_FILE,"a",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields); 
            if new:w.writeheader()
            w.writerows(rows)
        events=[r for r in rows if r["event"]!="NONE"]
        journal("SHADOW_SR",scan_id=scan_identifier,levels=len(rows),events=events,diagnostic_only=True)
        for e in events:
            event_name=e.get("event","NONE")
            # Do not forward fields that are supplied explicitly below; otherwise
            # Python raises duplicate-keyword TypeError and the whole Shadow-SR
            # diagnostic block is lost even though the levels were calculated.
            event_payload={k:v for k,v in e.items() if k not in {"event","structure_state","previous_state","structure_direction","confirmation_age_scans","diagnostic_only"}}
            journal("SHADOW_SR_EVENT",sr_event=event_name,
                    structure_state=e.get("structure_state"),
                    previous_state=e.get("previous_state"),
                    structure_direction=e.get("structure_direction"),
                    confirmation_age_scans=e.get("confirmation_age_scans"),
                    **event_payload,diagnostic_only=True)
        return {"levels":len(rows),"events":events,"file":SHADOW_SR_FILE}
    except Exception as e:
        journal("SHADOW_SR_ERROR",scan_id=scan_identifier,error=repr(e),diagnostic_only=True)
        return {"levels":0,"events":[],"error":repr(e)}


def shadow_dynamic_levels_simulation(F,direction,core_levels,scan_identifier=None):
    """Shadow-only S/R-aware SL/TP simulation. It never mutates live levels or trade state."""
    if not SHADOW_SR_ENABLED or not isinstance(core_levels,dict):
        return None
    try:
        entry=safe_float(core_levels.get("entry")); base_sl=safe_float(core_levels.get("stop_loss")); base_tp=safe_float(core_levels.get("target"))
        base_risk=abs(entry-base_sl); base_reward=abs(base_tp-entry)
        if entry<=0 or base_risk<=0 or base_reward<=0:return None
        zones=[]
        tf_weight={"1h":.50,"15m":.30,"5m":.20}
        for tf in ("1h","15m","5m"):
            df=F.get(tf)
            if not isinstance(df,pd.DataFrame) or len(df)<10:continue
            d=prepare(df); last=d.iloc[-1]; atr=max(safe_float(last.get("atr")),entry*.0001)
            raw=_shadow_pivot_levels(d,"SUPPORT")+_shadow_pivot_levels(d,"RESISTANCE")
            for z in _shadow_cluster_levels(raw,atr):
                z=dict(z); z["timeframe"]=tf; z["tf_weight"]=tf_weight[tf]
                z["distance_atr"]=abs(entry-safe_float(z["price"]))/atr
                z["event"]=_shadow_level_event(safe_float(d.iloc[-2].close),safe_float(last.open),safe_float(last.high),safe_float(last.low),safe_float(last.close),z,atr)
                z["strength"]=clamp(.45*tf_weight[tf]/.50+.30*clamp((safe_float(z.get("touches"))-1)/3)+.15*(z["event"] in {"REJECTION","RECLAIM"})+.10*(z["event"]=="RETEST"))
                zones.append(z)
        if not zones:return None
        if direction=="LONG":
            supports=[z for z in zones if z["kind"]=="SUPPORT" and safe_float(z["price"])<entry]
            resistances=[z for z in zones if z["kind"]=="RESISTANCE" and safe_float(z["price"])>entry]
        else:
            supports=[z for z in zones if z["kind"]=="RESISTANCE" and safe_float(z["price"])>entry]
            resistances=[z for z in zones if z["kind"]=="SUPPORT" and safe_float(z["price"])<entry]
        support=min(supports,key=lambda z:abs(entry-safe_float(z["price"]))) if supports else None
        resistance=min(resistances,key=lambda z:abs(entry-safe_float(z["price"]))) if resistances else None
        atr_values=[]
        for tf in ("1h","15m","5m"):
            frame=F.get(tf)
            if isinstance(frame,pd.DataFrame) and not frame.empty:
                prepared=prepare(frame)
                if not prepared.empty:
                    atr_values.append(safe_float(prepared.iloc[-1].get("atr")))
        atr_ref=max([x for x in atr_values if x>0] or [base_risk/1.2,entry*.0005])
        # Hard shadow guardrails: no more than +/-25% SL distance or +35%/-45% TP distance.
        max_risk=base_risk*1.25; min_risk=base_risk*.75; max_reward=base_reward*1.35; min_reward=base_reward*.55
        dyn_sl=base_sl; dyn_tp=base_tp; sl_reason="ATR_BASE"; tp_reason="ATR_BASE"
        if support:
            sp=safe_float(support["price"]); gap=abs(entry-sp); strength=safe_float(support.get("strength"))
            if strength>=.45 and gap>=atr_ref*.55:
                candidate_gap=gap+atr_ref*.10
                candidate_gap=min(max(candidate_gap,min_risk),max_risk)
                if candidate_gap>base_risk*1.02:
                    dyn_sl=entry-candidate_gap if direction=="LONG" else entry+candidate_gap
                    sl_reason=f"SR_{support['timeframe']}_{support['event'] or 'ZONE'}"
        if resistance:
            rp=safe_float(resistance["price"]); gap=abs(rp-entry); strength=safe_float(resistance.get("strength"))
            if strength>=.55 and gap<base_reward*1.12:
                candidate_gap=max(gap-atr_ref*.10,min_reward)
                candidate_gap=min(candidate_gap,max_reward)
                if candidate_gap<base_reward*.98:
                    dyn_tp=entry+candidate_gap if direction=="LONG" else entry-candidate_gap
                    tp_reason=f"NEAR_{resistance['timeframe']}_{resistance['event'] or 'RESISTANCE'}"
            elif strength<.70 and gap>base_reward*1.25:
                candidate_gap=min(gap-atr_ref*.10,max_reward)
                if candidate_gap>base_reward*1.02:
                    dyn_tp=entry+candidate_gap if direction=="LONG" else entry-candidate_gap
                    tp_reason=f"EXTEND_{resistance['timeframe']}_{resistance['event'] or 'OPEN_SPACE'}"
        dyn_risk=abs(entry-dyn_sl); dyn_reward=abs(dyn_tp-entry)
        out={"ts":utc_now().isoformat(),"scan_id":scan_identifier or scan_id(),"direction":direction,"entry":entry,
             "base_sl":base_sl,"shadow_sl":dyn_sl,"base_tp":base_tp,"shadow_tp":dyn_tp,"base_risk":base_risk,"shadow_risk":dyn_risk,
             "base_reward":base_reward,"shadow_reward":dyn_reward,"base_rr":safe_float(core_levels.get("rr")),"shadow_rr":dyn_reward/max(dyn_risk,1e-9),
             "sl_delta":dyn_sl-base_sl,"tp_delta":dyn_tp-base_tp,"sl_reason":sl_reason,"tp_reason":tp_reason,
             "support":support,"resistance":resistance,"diagnostic_only":True}
        ensure_parent_dir(SHADOW_SR_FILE)
        dynamic_file=SHADOW_SR_FILE.replace(".csv","_dynamic.csv")
        fields=["ts","scan_id","direction","entry","base_sl","shadow_sl","base_tp","shadow_tp","base_risk","shadow_risk","base_reward","shadow_reward","base_rr","shadow_rr","sl_delta","tp_delta","sl_reason","tp_reason","support","resistance","diagnostic_only"]
        new=not os.path.exists(dynamic_file)
        with open(dynamic_file,"a",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields);
            if new:w.writeheader()
            w.writerow({k:(json.dumps(out[k],default=str) if isinstance(out.get(k),dict) else out.get(k,"")) for k in fields})
        journal("SHADOW_DYNAMIC_LEVELS",**out)
        return out
    except Exception as e:
        journal("SHADOW_DYNAMIC_ERROR",scan_id=scan_identifier,error=repr(e),diagnostic_only=True)
        return None

def calculate_trade_levels(price,atr,direction,score):
    price=safe_float(price); atr=max(safe_float(atr),price*.0005); sf=clamp((score-MIN_SCORE)/max(STRONG_SCORE-MIN_SCORE,1)); sd=atr*(1.20-.20*sf); td=atr*(2+.75*sf)
    return {"entry":price,"stop_loss":price-sd if direction=="LONG" else price+sd,"target":price+td if direction=="LONG" else price-td,"risk":sd,"reward":td,"rr":td/max(sd,1e-9)}
def calculate_extended_target(trade,current_price,atr):
    dist=max(safe_float(atr),safe_float(current_price)*.0005)*1.25; return safe_float(current_price)+dist if trade.get("direction")=="LONG" else safe_float(current_price)-dist
def make_signal_id(direction,price,ts=None):return f"{direction}_{datetime.fromtimestamp(ts or now_ts(),timezone.utc).isoformat()}"
def cooldown_active(state):return now_ts()<safe_float(state.get("cooldown_until"))
def set_cooldown(state,minutes=COOLDOWN_MIN):state["cooldown_until"]=now_ts()+minutes*60
def active_trade_exists(state):return isinstance(state.get("active_trade"),dict) and state["active_trade"].get("status")=="OPEN"
def create_trade_record(signal,levels):
    return {"status":"OPEN","signal_id":signal["signal_id"],"opened_at":utc_now().isoformat(),"direction":signal["direction"],"setup":signal["setup"],"regime":signal["regime"],
            "entry":levels["entry"],"stop_loss":levels["stop_loss"],"target":levels["target"],"risk":levels["risk"],"reward":levels["reward"],"rr":levels["rr"],
            "score":signal["score"],"agreement":signal["agreement"],"features":signal.get("features",{}),
            "opportunity_size":signal.get("opportunity_size","SMALL"),
            "bridge_state":signal.get("adaptive_bridge",{}).get("bridge_state","NONE"),
            "bridge_strength":signal.get("adaptive_bridge",{}).get("combined_strength",0.0),
            "extensions":0,"last_extension_ts":0.0,
            "entry_notification_sent":False,"entry_notification_attempts":0,"entry_notification_last_attempt":None}


def regime_direction(regime):
    if str(regime).startswith("BULLISH"): return "LONG"
    if str(regime).startswith("BEARISH"): return "SHORT"
    return "NEUTRAL"

def regime_allows_direction(regime,direction):
    rd=regime_direction(regime)
    return rd in {"NEUTRAL",direction}

def decision_engine(direction,market,execution,sweep,continuation,adaptive_bridge=None):
    """Single authority for entry permission. Regime describes the higher-timeframe context; flow describes current participation. Adaptive Signal Bridge is an upstream evidence input. It may strengthen a candidate and resolve stale-regime/context conflicts only when both advanced engines are aligned. It never executes, widens risk, or bypasses the final V8 score/agreement checks. """
    regime=str(market.get("regime","UNSTABLE")); rd=regime_direction(regime)
    flow=market.get("flow",{}) or {}; fd=flow.get("confirmed_direction","NEUTRAL") or "NEUTRAL"
    transition=str(flow.get("transition","NORMAL")); flow_score=safe_float(flow.get("long_score" if direction=="LONG" else "short_score"),.5)
    base_agreement=safe_float(execution.get("agreement"),0.0)
    sweep_conf=safe_float(sweep.get("confirmation"),0.0)
    structure_score=safe_float((flow.get("long" if direction=="LONG" else "short") or {}).get("structure",{}).get("score"),.5)
    reasons=[]; allowed=False; mode="BLOCK"
    bridge=adaptive_bridge if isinstance(adaptive_bridge,dict) else {}
    bridge_ready=bool(bridge.get("bridge_ready"))
    bridge_strength=safe_float(bridge.get("combined_strength"),0.0)
    bridge_bonus=safe_float(bridge.get("bridge_score_bonus"),0.0)
    bridge_agreement_boost=safe_float(bridge.get("bridge_agreement_boost"),0.0)
    bridge_base_ok=base_agreement>=ADAPTIVE_BRIDGE_MIN_BASE_AGREEMENT
    bridge_context=str(bridge.get("context",""))

    # Hard market-context gates.
    if regime in {"RANGE","UNSTABLE"}:
        reasons.append("non_trending_regime")
        return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}

    if regime=="TRANSITION":
        bridge_override=bridge_ready and bridge_base_ok and bridge_context in {"COUNTER_TREND_PULLBACK","TRANSITION","TREND"}
        if (fd!=direction or transition not in {"TRANSITION","CONFIRMED"}) and not bridge_override:
            reasons.append("transition_requires_confirmed_flow_direction")
            return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}
        if flow_score < DECISION_MIN_FLOW_FOR_REVERSAL and not bridge_override:
            reasons.append("flow_below_reversal_threshold")
            return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}
        if sweep_conf < DECISION_MIN_SWEEP_FOR_REVERSAL and structure_score < DECISION_MIN_STRUCTURE_FOR_REVERSAL and not bridge_override:
            reasons.append("transition_needs_sweep_or_structure_confirmation")
            return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}
        allowed=(base_agreement>=MIN_AGREEMENT) or bridge_override and bridge_base_ok; mode="ADAPTIVE_BRIDGE" if bridge_override else ("REVERSAL" if allowed else mode)
        reasons.append("adaptive_bridge_confirmed" if bridge_override else "confirmed_flow_transition")
        return {"allowed":allowed,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd,"bridge_ready":bridge_ready}

    # A trend regime keeps authority unless flow is explicitly warning/reversing it.
    if rd!=direction:
        bridge_override=bridge_ready and bridge_base_ok and bridge_context=="COUNTER_TREND_PULLBACK"
        if fd==direction and transition=="CONFIRMED" and flow_score>=DECISION_MIN_FLOW_FOR_REVERSAL and sweep_conf>=DECISION_MIN_SWEEP_FOR_REVERSAL:
            allowed=base_agreement>=MIN_AGREEMENT or (bridge_ready and bridge_base_ok); mode="REVERSAL" if not bridge_override else "ADAPTIVE_BRIDGE"; reasons.append("flow_confirmed_counter_regime_reversal")
        elif bridge_override:
            allowed=True; mode="ADAPTIVE_BRIDGE"; reasons.append("adaptive_bridge_counter_regime_setup")
        else:
            reasons.append("opposite_regime_direction")
        return {"allowed":allowed,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd,"bridge_ready":bridge_ready}

    # Same-direction trend: flow must not be materially weak or reversed.
    if fd not in {"NEUTRAL",direction} and transition in {"TRANSITION","CONFIRMED"} and not bridge_ready:
        reasons.append("flow_conflicts_with_regime")
        return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd,"bridge_ready":bridge_ready}
    if flow_score < DECISION_MIN_FLOW_FOR_TREND and not bridge_ready:
        reasons.append("flow_not_supportive")
        return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd,"bridge_ready":bridge_ready}
    allowed=base_agreement>=MIN_AGREEMENT or (bridge_ready and bridge_base_ok)
    mode="ADAPTIVE_BRIDGE" if bridge_ready else ("TREND" if allowed else mode)
    reasons.append("adaptive_bridge_confirmed" if bridge_ready else "regime_and_flow_aligned")
    return {"allowed":allowed,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd,"bridge_ready":bridge_ready}


# ---------------- V8 FUSION / FINAL HARD GATE ----------------
FUSION_VERSION="V8-FUSION-1-HARD-GATE"
FUSION_MIN_ENTER_SCORE=.62
FUSION_MIN_CONFIRMING_SCORE=.56
FUSION_MIN_RR=1.20
FUSION_MIN_RISK_ATR=.25
FUSION_MAX_RISK_ATR=3.50
FUSION_MAX_SHADOW_CONFLICT=.20
FUSION_REQUIRE_FINITE_RISK=True
# A Core decision block is not always a terminal trade veto. These are the
# context/flow gates that the independent Fusion cross-check is allowed to
# re-evaluate when the candidate already clears the normal score/agreement floor.
# Structural/data-integrity vetoes (for example RANGE/UNSTABLE or invalid risk)
# remain terminal. Shadow remains evidence/risk context, never a second signal engine.
FUSION_RECOVERABLE_CORE_BLOCKS={
    "flow_not_supportive",
    "flow_below_reversal_threshold",
    "opposite_regime_direction",
    "transition_requires_confirmed_flow_direction",
    "transition_needs_sweep_or_structure_confirmation",
    "flow_conflicts_with_regime",
}

def _fusion_shadow_maturity(shadow):
    if not isinstance(shadow,dict): return {"status":"NONE","maturity":.40,"critical":False,"reason":"shadow_unavailable"}
    status=str(shadow.get("status","NONE"))
    if status=="CONFIRMED": return {"status":status,"maturity":1.0,"critical":False,"reason":"shadow_confirmed"}
    if status=="CONFIRMING": return {"status":status,"maturity":.80,"critical":False,"reason":"shadow_confirming"}
    if status=="DEVELOPING": return {"status":status,"maturity":.55,"critical":False,"reason":"shadow_developing"}
    if status=="FAILED": return {"status":status,"maturity":0.0,"critical":True,"reason":"shadow_failed"}
    if status=="ERROR": return {"status":status,"maturity":0.0,"critical":False,"reason":"shadow_error"}
    return {"status":status,"maturity":.40,"critical":False,"reason":"shadow_none"}

def fusion_final_trade_gate(signal,F,market,adaptive_bridge=None,shadow_intel=None):
    """Independent final pre-execution cross-check. Emits ENTER/WAIT/BLOCK."""
    s=signal if isinstance(signal,dict) else {}; direction=str(s.get("direction",""))
    if direction not in {"LONG","SHORT"}: return {"state":"WAIT","reason":"invalid_direction","fusion_score":0.0}
    price=safe_float(s.get("price"),0.0); atr=safe_float(s.get("atr"),0.0); score=safe_float(s.get("score"),-1.0); agreement=safe_float(s.get("agreement"),-1.0)
    decision=s.get("decision",{}) if isinstance(s.get("decision"),dict) else {}
    if price<=0 or atr<=0 or not math.isfinite(price) or not math.isfinite(atr): return {"state":"WAIT","reason":"invalid_price_or_atr","fusion_score":0.0}
    core_allowed=bool(decision.get("allowed"))
    core_reason=str(decision.get("reason") or "unknown").split("|")[0]
    core_block_recoverable=(not core_allowed and core_reason in FUSION_RECOVERABLE_CORE_BLOCKS)
    # Do not let a premature Core block erase a strong candidate before Fusion
    # has inspected Shadow evidence. Non-recoverable Core blocks stay terminal.
    if not core_allowed and not core_block_recoverable:
        return {"state":"BLOCK","reason":"core_decision_not_allowed","core_gate_reason":core_reason,"fusion_score":0.0}
    if score<MIN_SCORE or agreement<MIN_AGREEMENT:
        return {"state":"WAIT","reason":"core_score_or_agreement_below_min","core_gate_reason":core_reason,"core_gate_blocked":not core_allowed,"fusion_score":0.0}
    levels=calculate_trade_levels(price,atr,direction,score); risk=abs(safe_float(levels.get("entry"))-safe_float(levels.get("stop_loss"))); reward=abs(safe_float(levels.get("target"))-safe_float(levels.get("entry"))); rr=reward/max(risk,1e-9); risk_atr=risk/max(atr,1e-9)
    if FUSION_REQUIRE_FINITE_RISK and not all(math.isfinite(safe_float(levels.get(k))) for k in ("entry","stop_loss","target","risk","reward","rr")): return {"state":"WAIT","reason":"invalid_core_risk_levels","fusion_score":0.0,"levels":levels}
    if risk<=0 or reward<=0 or risk_atr<FUSION_MIN_RISK_ATR or risk_atr>FUSION_MAX_RISK_ATR: return {"state":"WAIT","reason":"core_risk_out_of_bounds","fusion_score":0.0,"levels":levels,"rr":rr}
    if rr<FUSION_MIN_RR: return {"state":"WAIT","reason":"core_rr_below_min","fusion_score":0.0,"levels":levels,"rr":rr}
    shadow=(shadow_intel or {}).get(direction) if isinstance(shadow_intel,dict) else None; maturity=_fusion_shadow_maturity(shadow)
    if maturity["critical"]: return {"state":"BLOCK","reason":"shadow_structural_failure","fusion_score":0.0,"shadow_status":maturity["status"],"levels":levels,"rr":rr}
    if maturity["status"]=="ERROR": return {"state":"WAIT","reason":"shadow_critical_data_error","fusion_score":0.0,"shadow_status":"ERROR","levels":levels,"rr":rr}
    bridge=adaptive_bridge if isinstance(adaptive_bridge,dict) else {}; bridge_strength=clamp(safe_float(bridge.get("combined_strength"),0.0)); bridge_rel=str(bridge.get("engine_relationship","PARTIALLY_ALIGNED")); bridge_context=str(bridge.get("context","")); bridge_ready=bool(bridge.get("bridge_ready"))
    relationship_factor=1.0 if bridge_rel=="ALIGNED" else .72 if bridge_rel=="PARTIALLY_ALIGNED" else .45; follow_factor=1.0 if bool(bridge.get("follow_through")) else .82; bridge_factor=clamp(.65+.35*bridge_strength)*relationship_factor*follow_factor
    dynamic=None
    try: dynamic=shadow_dynamic_levels_simulation(F,direction,levels,s.get("signal_id")) if SHADOW_SR_ENABLED else None
    except Exception as e: journal("FUSION_DYNAMIC_LEVEL_ERROR",direction=direction,error=repr(e),diagnostic_only=True)
    if dynamic:
        shadow_rr=safe_float(dynamic.get("shadow_rr"),0.0); shadow_risk=safe_float(dynamic.get("shadow_risk"),0.0)
        if shadow_rr<=0 or shadow_risk<=0 or not math.isfinite(shadow_rr) or not math.isfinite(shadow_risk): return {"state":"WAIT","reason":"invalid_dynamic_risk_context","fusion_score":0.0,"levels":levels,"rr":rr,"dynamic":dynamic}
        if shadow_rr<FUSION_MIN_RR: return {"state":"WAIT","reason":"dynamic_rr_below_min","fusion_score":0.0,"levels":levels,"rr":rr,"dynamic":dynamic}
        dynamic_factor=clamp(shadow_rr/2.0)
    else: dynamic_factor=.70
    core_factor=clamp(.55*clamp((score-MIN_SCORE)/max(STRONG_SCORE-MIN_SCORE,1e-9))+.45*clamp((agreement-MIN_AGREEMENT)/max(STRONG_AGREEMENT-MIN_AGREEMENT,1e-9)))
    fusion_score=clamp(.62*core_factor+.16*maturity["maturity"]+.12*bridge_factor+.10*dynamic_factor)
    recovery_ready=(not core_block_recoverable) or maturity["status"]=="CONFIRMED" or (maturity["status"]=="CONFIRMING" and score>=STRONG_SCORE and agreement>=STRONG_AGREEMENT and bridge_rel!="CONFLICTED")
    if maturity["status"]=="DEVELOPING" and not (score>=STRONG_SCORE and agreement>=STRONG_AGREEMENT): state="WAIT"; reason="shadow_not_mature_enough_for_core_strength"
    elif bridge_rel=="CONFLICTED" and not bridge_ready and score<STRONG_SCORE: state="WAIT"; reason="bridge_context_conflict_without_strong_core"
    elif core_block_recoverable and not recovery_ready: state="WAIT"; reason="core_gate_block_requires_shadow_confirmation"
    elif fusion_score<FUSION_MIN_CONFIRMING_SCORE: state="WAIT"; reason="fusion_evidence_below_enter_floor"
    elif core_block_recoverable: state="ENTER"; reason="fusion_recovered_core_gate_with_shadow_evidence"
    else: state="ENTER"; reason="fusion_hard_gate_passed"
    out={"state":state,"reason":reason,"fusion_score":fusion_score,"shadow_status":maturity["status"],"shadow_maturity":maturity["maturity"],"core_gate_blocked":not core_allowed,"core_gate_reason":core_reason,"core_gate_recoverable":core_block_recoverable,"recovery_ready":recovery_ready,"bridge_ready":bridge_ready,"bridge_context":bridge_context,"bridge_relationship":bridge_rel,"bridge_factor":bridge_factor,"dynamic_factor":dynamic_factor,"core_factor":core_factor,"levels":levels,"rr":rr,"dynamic":dynamic,"version":FUSION_VERSION}
    journal("FUSION_HARD_GATE",direction=direction,signal_id=s.get("signal_id"),state=state,reason=reason,fusion_score=fusion_score,score=score,agreement=agreement,core_gate_blocked=not core_allowed,core_gate_reason=core_reason,core_gate_recoverable=core_block_recoverable,recovery_ready=recovery_ready,shadow_status=maturity["status"],shadow_maturity=maturity["maturity"],bridge_ready=bridge_ready,bridge_relationship=bridge_rel,bridge_context=bridge_context,follow_through=bool(bridge.get("follow_through")),dynamic_rr=safe_float((dynamic or {}).get("shadow_rr")),core_rr=rr,diagnostic_only=False)
    return out

def build_signal(F,state,calibration,market,news,adaptive_intel=None,shadow_intel=None):
    candidates=[]; regime=market.get("regime","UNSTABLE"); rc=market.get("confidence",0.0)
    entry=_tf_direction_score(F["5m"]); price=entry["close"]; atr=entry["atr"]
    news_available=bool(news.get("articles")) and not news.get("error")
    if price<=0 or atr<=0:return None

    for direction in ("LONG","SHORT"):
        execution=execution_score(F,direction)
        parts=execution["parts"]
        sweep=detect_liquidity_sweep(F["1h"],direction)
        sc=sweep_confirmation_score(sweep,execution,market,direction)
        cont=detect_continuation(F["1h"],direction)
        setup="SWEEP_REVERSAL" if sweep.get("confirmed") and sweep.get("quality",0)>=SWEEP_REVERSAL_MIN_QUALITY else "CONTINUATION" if cont.get("ready") else "TREND"
        adaptive_bridge=(adaptive_intel or {}).get(direction) if isinstance(adaptive_intel,dict) else None
        decision=decision_engine(direction,market,execution,sweep,cont,adaptive_bridge)
        # Adaptive Bridge is now an explicit V8 input, not a standalone Shadow decision.
        # It contributes only a bounded score/agreement lift when both engines qualify.
        bridge_ready=bool((adaptive_bridge or {}).get("bridge_ready")) if isinstance(adaptive_bridge,dict) else False
        bridge_bonus=safe_float((adaptive_bridge or {}).get("bridge_score_bonus"),0.0) if isinstance(adaptive_bridge,dict) else 0.0
        bridge_agreement_boost=safe_float((adaptive_bridge or {}).get("bridge_agreement_boost"),0.0) if isinstance(adaptive_bridge,dict) else 0.0
        if bridge_ready:
            # Preserve V8's existing thresholds while allowing qualified advanced
            # evidence to supply the missing margin, never more than the bridge caps.
            bridge_bonus=max(bridge_bonus,min(ADAPTIVE_BRIDGE_MAX_SCORE_BONUS,max(0.0,MIN_SCORE-safe_float(execution.get("score"),0.0))))
            bridge_agreement_boost=max(bridge_agreement_boost,min(ADAPTIVE_BRIDGE_MAX_AGREEMENT_BOOST,max(0.0,MIN_AGREEMENT-safe_float(execution.get("agreement"),0.0))))
        # Diagnostic-only gate inspection. This computes what the candidate looked like
        # before the hard decision gate, without changing the live decision path.
        diagnostic_features=build_signal_features(F,direction,parts,regime,setup,execution["score"],execution["agreement"])
        diagnostic_adaptive=adaptive_signal_score(execution["score"],calibration,diagnostic_features)
        diagnostic_news_available=bool(news.get("articles")) and not news.get("error")
        diagnostic_setup_bonus=0.0
        if setup=="SWEEP_REVERSAL":
            diagnostic_setup_bonus=DECISION_SETUP_BONUS_MAX*clamp((sc-DECISION_MIN_SWEEP_FOR_REVERSAL)/max(1-DECISION_MIN_SWEEP_FOR_REVERSAL,1e-9))
        elif setup=="CONTINUATION":
            diagnostic_setup_bonus=DECISION_SETUP_BONUS_MAX*clamp((safe_float(cont.get("score"))-.62)/.38)
        diagnostic_news_bonus=((safe_float(news.get("score"),.5)-.5)*NEWS_SUPPORT_MAX_POINTS) if diagnostic_news_available else 0.0
        diagnostic_score=clamp(diagnostic_adaptive["score"]+diagnostic_setup_bonus+diagnostic_news_bonus+bridge_bonus,0,105)
        diagnostic_gate_reason=str(decision.get("reason") or "unknown")
        diagnostic_gate=diagnostic_gate_reason.split("|")[0]
        diagnostic_threshold=None
        if diagnostic_gate=="flow_not_supportive": diagnostic_threshold=DECISION_MIN_FLOW_FOR_TREND
        elif diagnostic_gate=="flow_below_reversal_threshold": diagnostic_threshold=DECISION_MIN_FLOW_FOR_REVERSAL
        elif diagnostic_gate=="transition_needs_sweep_or_structure_confirmation": diagnostic_threshold={"sweep":DECISION_MIN_SWEEP_FOR_REVERSAL,"structure":DECISION_MIN_STRUCTURE_FOR_REVERSAL}
        elif diagnostic_gate=="opposite_regime_direction": diagnostic_threshold={"flow":DECISION_MIN_FLOW_FOR_REVERSAL,"sweep":DECISION_MIN_SWEEP_FOR_REVERSAL}
        elif diagnostic_gate=="transition_requires_confirmed_flow_direction": diagnostic_threshold="confirmed_flow_direction_and_transition"
        elif diagnostic_gate=="flow_conflicts_with_regime": diagnostic_threshold="flow_direction_must_be_neutral_or_candidate_direction"
        elif diagnostic_gate=="non_trending_regime": diagnostic_threshold="regime_not_RANGE_or_UNSTABLE"
        core_gate_blocked=not bool(decision.get("allowed"))
        if core_gate_blocked:
            journal("DECISION_BLOCK",direction=direction,regime=regime,regime_confidence=rc,flow_direction=decision.get("direction"),flow_transition=market.get("flow",{}).get("transition"),flow_score=decision.get("flow_score"),mode=decision.get("mode"),reason=decision.get("reason"),setup=setup)
            journal("HARD_GATE_DIAGNOSTIC",direction=direction,regime=regime,regime_confidence=rc,setup=setup,gate=diagnostic_gate,gate_reason=diagnostic_gate_reason,gate_threshold=diagnostic_threshold,
                    flow_direction=decision.get("direction"),flow_transition=market.get("flow",{}).get("transition"),flow_score=decision.get("flow_score"),
                    execution_score=execution.get("score"),execution_agreement=execution.get("agreement"),execution_parts=parts,
                    sweep_confirmation=sc,sweep_quality=sweep.get("quality"),sweep_confirmed=sweep.get("confirmed"),continuation_score=cont.get("score",0),
                    structure_score=(market.get("flow",{}).get("long" if direction=="LONG" else "short") or {}).get("structure",{}).get("score"),
                    diagnostic_adaptive_score=diagnostic_adaptive.get("score"),diagnostic_setup_bonus=diagnostic_setup_bonus,diagnostic_news_bonus=diagnostic_news_bonus,adaptive_bridge=adaptive_bridge,bridge_bonus=bridge_bonus,bridge_agreement_boost=bridge_agreement_boost,
                    diagnostic_score=diagnostic_score,diagnostic_score_threshold=MIN_SCORE,diagnostic_agreement_threshold=MIN_AGREEMENT,
                    diagnostic_score_pass=diagnostic_score>=MIN_SCORE,diagnostic_agreement_pass=execution.get("agreement",0)>=MIN_AGREEMENT,
                    price=price,atr=atr,diagnostic_only=True)
            # Do not discard a candidate solely because a recoverable Core flow/context
            # gate fired. Fusion must inspect Shadow evidence first. Weak candidates
            # still stop here to avoid needless downstream processing.
            core_gate_reason=diagnostic_gate
            recoverable_core_gate=core_gate_reason in FUSION_RECOVERABLE_CORE_BLOCKS
            if (not recoverable_core_gate) or diagnostic_score<MIN_SCORE or execution.get("agreement",0)<MIN_AGREEMENT:
                continue

        features=diagnostic_features
        adaptive=diagnostic_adaptive

        # Score layers are deliberately non-overlapping:
        # 1) execution score = primary evidence;
        # 2) adaptive calibration = historical adjustment;
        # 3) setup bonus = one-time liquidity/continuation confirmation;
        # 4) news = tiny optional support. Flow/OB do NOT get added again.
        setup_bonus=0.0
        if setup=="SWEEP_REVERSAL": setup_bonus=DECISION_SETUP_BONUS_MAX*clamp((sc-DECISION_MIN_SWEEP_FOR_REVERSAL)/max(1-DECISION_MIN_SWEEP_FOR_REVERSAL,1e-9))
        elif setup=="CONTINUATION": setup_bonus=DECISION_SETUP_BONUS_MAX*clamp((safe_float(cont.get("score"))-.62)/.38)
        news_bonus=((safe_float(news.get("score"),.5)-.5)*NEWS_SUPPORT_MAX_POINTS) if news_available else 0.0
        score=clamp(adaptive["score"]+setup_bonus+news_bonus+bridge_bonus,0,105)
        final_agreement=clamp(execution["agreement"]+bridge_agreement_boost)
        if score<MIN_SCORE or final_agreement<MIN_AGREEMENT:
            rejection_reasons=[]
            if score<MIN_SCORE: rejection_reasons.append("score_below_min")
            if final_agreement<MIN_AGREEMENT: rejection_reasons.append("agreement_below_min")
            journal("SIGNAL_REJECTED",direction=direction,setup=setup,regime=regime,regime_confidence=rc,base_score=execution["score"],adaptive_score=adaptive["score"],final_score=score,base_agreement=execution["agreement"],final_agreement=final_agreement,sweep_confirmation=sc,continuation_score=cont.get("score",0),flow_direction=decision.get("direction"),flow_transition=market.get("flow",{}).get("transition"),flow_score=decision.get("flow_score"),news_available=news_available,news_error=news.get("error"))
            journal("POST_GATE_REJECTION_DIAGNOSTIC",direction=direction,regime=regime,regime_confidence=rc,setup=setup,rejection_reasons=rejection_reasons,
                    execution_score=execution.get("score"),execution_agreement=execution.get("agreement"),execution_parts=parts,flow_direction=decision.get("direction"),flow_transition=market.get("flow",{}).get("transition"),flow_score=decision.get("flow_score"),
                    sweep_confirmation=sc,sweep_quality=sweep.get("quality"),sweep_confirmed=sweep.get("confirmed"),continuation_score=cont.get("score",0),
                    final_score=score,score_threshold=MIN_SCORE,final_agreement=final_agreement,agreement_threshold=MIN_AGREEMENT,
                    score_pass=score>=MIN_SCORE,agreement_pass=final_agreement>=MIN_AGREEMENT,price=price,atr=atr,diagnostic_only=True)
            continue

        # FINAL FUSION CROSS-CHECK: last independent safety gate before execution.
        fusion_signal={"direction":direction,"setup":setup,"regime":regime,"regime_confidence":rc,"score":score,"agreement":final_agreement,"base_score":execution["score"],"adaptive_score":adaptive["score"],"adaptive_adjustment":adaptive.get("adjustment",0),"sweep_confirmation":sc,"sweep":sweep,"continuation":cont,"news_score":safe_float(news.get("score"),.5),"news_confirmation":safe_float(news.get("confirmation")),"news_available":news_available,"parts":parts,"features":features,"price":price,"atr":atr,"flow":market.get("flow",{}),"flow_score":decision.get("flow_score",.5),"decision":decision,"core_gate_blocked":core_gate_blocked,"order_block":sweep.get("order_block",{}),"adaptive_bridge":adaptive_bridge or {},"opportunity_size":(adaptive_bridge or {}).get("opportunity_size","SMALL")}
        fusion=fusion_final_trade_gate(fusion_signal,F,market,adaptive_bridge,shadow_intel=shadow_intel)
        if fusion.get("state")!="ENTER":
            journal("FUSION_NON_ENTRY",direction=direction,setup=setup,regime=regime,score=score,agreement=final_agreement,fusion_state=fusion.get("state"),fusion_reason=fusion.get("reason"),fusion_score=fusion.get("fusion_score"),shadow_status=fusion.get("shadow_status"),bridge_state=(adaptive_bridge or {}).get("bridge_state","NONE"),diagnostic_only=False)
            continue

        candidates.append({"direction":direction,"setup":setup,"regime":regime,"regime_confidence":rc,"score":score,"base_score":execution["score"],"adaptive_score":adaptive["score"],
            "adaptive_adjustment":adaptive.get("adjustment",0),"agreement":final_agreement,"base_agreement":execution["agreement"],"sweep_confirmation":sc,"sweep":sweep,"continuation":cont,
            "news_score":safe_float(news.get("score"),.5),"news_confirmation":safe_float(news.get("confirmation")),"news_available":news_available,"parts":parts,"features":features,"price":price,"atr":atr,
            "flow":market.get("flow",{}),"flow_score":decision.get("flow_score",.5),"decision":decision,"order_block":sweep.get("order_block",{}),
            "adaptive_bridge":adaptive_bridge or {},"opportunity_size":(adaptive_bridge or {}).get("opportunity_size","SMALL"),
            "strong":score>=STRONG_SCORE and final_agreement>=STRONG_AGREEMENT and rc>=REGIME_MIN_CONFIDENCE,"fusion":fusion})

    if not candidates:return None
    candidates.sort(key=lambda x:(x["score"],x["agreement"]),reverse=True)
    if len(candidates)>1:
        gap=candidates[0]["score"]-candidates[1]["score"]
        if gap<2:
            journal("DECISION_AMBIGUOUS",first=candidates[0]["direction"],second=candidates[1]["direction"],score_gap=gap)
            return None
    best=candidates[0]; best["signal_id"]=make_signal_id(best["direction"],best["price"]); return best

def format_signal_message(s,levels):
    d="ðŸŸ¢ LONG ENTRY" if s["direction"]=="LONG" else "ðŸ”´ SHORT ENTRY"
    return f"{d}\n\nEntry: {levels['entry']:.2f}\nStop Loss: {levels['stop_loss']:.2f}\nTarget: {levels['target']:.2f}\nRR: {levels['rr']:.2f}\n\nWhy: Trend, structure, momentum and risk gates aligned.\n\nSetup: {s['setup']}\nSweep: {'CONFIRMED' if s.get('sweep',{}).get('confirmed') else 'NONE'}\n\nScore: {s['score']:.1f}/105\nAgreement: {s['agreement']*100:.0f}%\n\nTrend: {s['parts'].get('trend15',0):.2f} | Structure: {s['parts'].get('structure',0):.2f}\nPrice: {s['parts'].get('price',0):.2f} | Volume: {s['parts'].get('volume',0):.2f}\nMomentum: {s['parts'].get('momentum',0):.2f} | Orderflow: {s['parts'].get('orderflow',0):.2f}\nDecision: {s.get('decision',{}).get('mode','BLOCK')} | Flow: {s.get('flow',{}).get('transition','NORMAL')} | FlowScore: {s.get('flow_score',.5):.2f}\nOpportunity: {s.get('opportunity_size','SMALL')} | Bridge: {s.get('adaptive_bridge',{}).get('bridge_state','NONE')} | Fusion: {s.get('fusion',{}).get('state','NONE')}\nOB: {s.get('order_block',{}).get('quality',0):.2f} | Adaptive: {s.get('adaptive_adjustment',0):+.2f} | News: {s.get('news_score',.5):.2f}"

def open_trade_from_signal(state,signal):
    levels=calculate_trade_levels(signal["price"],signal["atr"],signal["direction"],signal["score"]); trade=create_trade_record(signal,levels)
    state["active_trade"]=trade; state["last_signal"]=signal["signal_id"]; state["last_signal_ts"]=now_ts(); state["last_direction"]=signal["direction"]; set_cooldown(state); return trade,levels

def close_trade(state,calibration,reason,price):
    trade=state.get("active_trade")
    if not trade:return None
    entry=safe_float(trade.get("entry")); risk=max(safe_float(trade.get("risk")),entry*.0005); pnl=((price-entry)/risk) if trade.get("direction")=="LONG" else ((entry-price)/risk); outcome="WIN" if pnl>0 else "LOSS" if pnl<0 else "FLAT"
    trade.update({"status":"CLOSED","closed_at":utc_now().isoformat(),"exit":price,"close_reason":reason,"pnl_r":pnl,"outcome":outcome}); learn_from_closed_trade(calibration,trade.get("features",{}),pnl,outcome)
    state["active_trade"]=None; state["last_closed_trade"]=trade; set_cooldown(state); journal("TRADE_CLOSED",**trade); return trade

def manage_active_trade(state,calibration,adaptive_intel=None):
    trade=state.get("active_trade")
    if not trade:return None
    d=prepare(fetch_candles(PRODUCT,300,50))
    if d.empty:return None
    price=safe_float(d.iloc[-1].close); atr=max(safe_float(d.iloc[-1].atr),price*.0005); direction=trade["direction"]
    hit=(direction=="LONG" and (price<=trade["stop_loss"] or price>=trade["target"])) or (direction=="SHORT" and (price>=trade["stop_loss"] or price<=trade["target"]))
    reason="TARGET_HIT" if (direction=="LONG" and price>=trade["target"]) or (direction=="SHORT" and price<=trade["target"]) else "SL_HIT"
    if hit:return close_trade(state,calibration,reason,price)
    age=(now_ts()-datetime.fromisoformat(trade["opened_at"]).timestamp())/3600
    if age>=MAX_TRADE_AGE_HOURS:return close_trade(state,calibration,"TIME_EXPIRY",price)

    # Adaptive monitoring is post-entry management only. It cannot create a new trade,
    # veto the existing trade, or widen its stop. It may extend the target when evidence strengthens.
    ai=(adaptive_intel or {}).get(direction) if isinstance(adaptive_intel,dict) else None
    if isinstance(ai,dict):
        previous_strength=safe_float(trade.get("last_adaptive_strength"),0.0)
        strength=safe_float(ai.get("combined_strength"),0.0)
        room=safe_float(ai.get("room_atr"),0.0)
        mfe=((price-safe_float(trade.get("entry")))/atr) if direction=="LONG" else ((safe_float(trade.get("entry"))-price)/atr)
        ready=(ai.get("state") in {"CONTINUING","CONFIRMED"} and ai.get("opportunity_size") in {"MEDIUM","LARGE"}
               and strength>=ADAPTIVE_EXTENSION_MIN_STRENGTH and room>=ADAPTIVE_EXTENSION_MIN_ROOM_ATR and mfe>=ADAPTIVE_EXTENSION_MIN_MFE_ATR
               and strength>previous_strength+.03)
        if ready and int(trade.get("extensions",0))<MAX_TARGET_EXTENSIONS and now_ts()-safe_float(trade.get("last_extension_ts"))>EXTENSION_COOLDOWN_MIN*60:
            old_target=safe_float(trade.get("target"))
            ext=calculate_extended_target(trade,price,atr)
            valid=(direction=="LONG" and ext>old_target and price>safe_float(trade.get("entry"))) or (direction=="SHORT" and ext<old_target and price<safe_float(trade.get("entry")))
            if valid:
                trade["target"]=ext; trade["extensions"]=int(trade.get("extensions",0))+1; trade["last_extension_ts"]=now_ts()
                trade["last_adaptive_strength"]=strength; trade["last_adaptive_size"]=ai.get("opportunity_size")
                ok=telegram_send(format_target_extension_notification(trade,ai,old_target,ext))
                journal("TARGET_EXTENSION",signal_id=trade.get("signal_id"),direction=direction,old_target=old_target,new_target=ext,extensions=trade["extensions"],adaptive=ai,telegram_sent=bool(ok),diagnostic_only=False)
        else:
            trade["last_adaptive_strength"]=max(previous_strength,strength)
            trade["last_adaptive_size"]=ai.get("opportunity_size")
    return None

def scan_id(): return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
def journal_wait(reason,**kw):
    journal("WAIT",reason=reason,**kw)
    append_scan_csv({"ts":utc_now().isoformat(),"scan_id":kw.get("scan_id",scan_id()),"event":"WAIT","reason":reason,**kw})
def record_signal(signal,action="SIGNAL"):
    row={"ts":utc_now().isoformat(),"scan_id":signal.get("signal_id"),"event":"SIGNAL","signal_id":signal.get("signal_id"),"direction":signal.get("direction"),"setup":signal.get("setup"),"regime":signal.get("regime"),
         "confidence":signal.get("regime_confidence"),"score":signal.get("score"),"agreement":signal.get("agreement"),"price":signal.get("price"),"adaptive_adjustment":signal.get("adaptive_adjustment"),
         "sweep_confirmation":signal.get("sweep_confirmation"),"news_confirmation":signal.get("news_confirmation"),"flow_direction":signal.get("flow",{}).get("confirmed_direction"),"flow_transition":signal.get("flow",{}).get("transition"),"flow_score":signal.get("flow_score"),"action":action,"opportunity_size":signal.get("opportunity_size"),"bridge_state":signal.get("adaptive_bridge",{}).get("bridge_state"),"bridge_strength":signal.get("adaptive_bridge",{}).get("combined_strength")}
    append_signal_csv(row)
    # `event` is part of the CSV row but must not be passed twice to journal().
    journal_row=dict(row); journal_row.pop("event",None)
    journal("SIGNAL",**journal_row)

def run_scan(state,calibration):
    print("V8_TRACE: run_scan_enter", flush=True)
    sid=scan_id(); scan_started=utc_now().isoformat(); journal("SCAN_START",scan_id=sid)
    append_scan_csv({"ts":scan_started,"scan_id":sid,"event":"SCAN_START","action":"SCAN"})
    print(f"V8_TRACE: scan_started scan_id={sid}", flush=True)
    F={"1m":fetch_candles(PRODUCT,60,120) if USE_MICRO_CONFIRMATION else pd.DataFrame(),"5m":fetch_candles(PRODUCT,300,HISTORY_5M_BARS),"15m":fetch_candles(PRODUCT,900,500)}
    print("V8_TRACE: initial_candles_fetched", flush=True)
    F["1h"]=fetch_candles(PRODUCT,3600,min(HISTORY_1H_BARS,300))
    print("V8_TRACE: one_hour_fetched", flush=True)
    F["2h"]=aggregate_candles(F["1h"],7200); F["4h"]=aggregate_candles(F["1h"],14400)
    print(f"V8_TRACE: higher_timeframes_ready sizes={{k: len(v) for k,v in F.items()}}", flush=True)
    if any(len(F[k])<50 for k in ("5m","15m","1h","2h","4h")):
        print("V8_TRACE: insufficient_history_return", flush=True)
        journal_wait("insufficient_history",scan_id=sid); journal("SCAN_DONE",scan_id=sid,status="WAIT",reason="insufficient_history"); return
    print("V8_TRACE: history_ok", flush=True)
    update_orderflow(); print("V8_TRACE: orderflow_updated", flush=True)
    # 5-minute-compatible Level-2 snapshot: diagnostic substitute for a persistent live WebSocket.
    # This is deliberately isolated from V8 Core scoring/decision/execution.
    shadow_orderbook=fetch_shadow_orderbook(safe_float(F["5m"].iloc[-1].close) if len(F["5m"]) else 0.0) if SHADOW_ORDERBOOK_ENABLED else None
    if shadow_orderbook:
        log_shadow_orderbook_snapshot(shadow_orderbook,sid,safe_float(F["5m"].iloc[-1].close) if len(F["5m"]) else 0.0)
        print(f"V8_SHADOW_ORDERBOOK: imbalance={safe_float(shadow_orderbook.get('imbalance')):.3f} spread_bps={safe_float(shadow_orderbook.get('spread_bps')):.2f} bid={safe_float(shadow_orderbook.get('bid_depth')):.4f} ask={safe_float(shadow_orderbook.get('ask_depth')):.4f}", flush=True)
    market=market_state_engine(F,state); print(f"V8_TRACE: market_ready regime={market.get('regime')} confidence={market.get('confidence')}", flush=True)
    shadow_sr=shadow_support_resistance_diagnostics(F,sid,state)
    if shadow_sr is not None:
        print(f"V8_SHADOW_SR: levels={shadow_sr.get('levels',0)} events={len(shadow_sr.get('events',[]))}", flush=True)
    # Unified Shadow Opportunity Intelligence: S/R + OB + liquidity + structure + reaction + MTF.
    # Its structured output is consumed by the Adaptive Signal Bridge below; Shadow never executes.
    shadow_intel=shadow_opportunity_intelligence(F,state,sid,shadow_orderbook) if SHADOW_INTEL_ENABLED else None
    if shadow_intel:
        print(f"V8_SHADOW_INTEL: LONG={shadow_intel.get('LONG',{}).get('status')} SHORT={shadow_intel.get('SHORT',{}).get('status')}", flush=True)
    # Adaptive Opportunity Intelligence + Signal Bridge: advanced evidence is prepared
    # before V8 Core. The bridge is bounded and V8 still performs final entry checks.
    adaptive_intel=adaptive_opportunity_intelligence(F,state,market,shadow_intel,shadow_orderbook,sid) if shadow_intel and ADAPTIVE_OPPORTUNITY_ENABLED else None
    if adaptive_intel:
        print(f"V8_ADAPTIVE: LONG={adaptive_intel.get('LONG',{}).get('opportunity_size')}/{adaptive_intel.get('LONG',{}).get('state')} "
              f"SHORT={adaptive_intel.get('SHORT',{}).get('opportunity_size')}/{adaptive_intel.get('SHORT',{}).get('state')}", flush=True)
    # Shadow opportunity study runs independently for both directions on every valid scan.
    # It is observational only; these hypothetical levels never enter build_signal or execution.
    if SHADOW_SR_ENABLED:
        try:
            shadow_entry=_tf_direction_score(F["5m"])
            for shadow_direction in ("LONG","SHORT"):
                shadow_exec=execution_score(F,shadow_direction)
                shadow_core=calculate_trade_levels(shadow_entry["close"],shadow_entry["atr"],shadow_direction,shadow_exec["score"])
                shadow_dynamic_levels_simulation(F,shadow_direction,shadow_core,sid)
        except Exception as e:
            journal("SHADOW_OPPORTUNITY_ERROR",scan_id=sid,error=repr(e),diagnostic_only=True)
    news=fetch_news(); print(f"V8_TRACE: news_ready articles={news.get('articles')} error={news.get('error')}", flush=True)
    if active_trade_exists(state):
        print("V8_TRACE: active_trade_branch", flush=True)
        # Entry notification is persisted and retried before any close notification.
        # This fixes the observed case where an exit arrived but the entry Telegram
        # message was lost/failed on the opening scan.
        ensure_entry_notification(state)
        closed=manage_active_trade(state,calibration,adaptive_intel)
        print(f"V8_TRACE: active_trade_managed closed={bool(closed)}", flush=True)
        if closed:
            # Entry must be acknowledged before the exit message is sent. If entry
            # delivery failed, the closed trade is retained for retry on later scans.
            state["last_closed_trade"]=closed
            ensure_closed_trade_notifications(state)
            save_state(state); save_calibration(calibration)
            append_scan_csv({"ts":utc_now().isoformat(),"scan_id":sid,"event":"TRADE_CLOSED","active_trade":False,"direction":closed.get("direction"),"action":"CLOSE"})
        journal("SCAN_DONE",scan_id=sid,status="TRADE_MANAGED",closed=bool(closed))
        print("V8_TRACE: run_scan_exit trade_managed", flush=True)
        return
    if state.get("last_closed_trade"):
        ensure_closed_trade_notifications(state)
    if cooldown_active(state):
        print("V8_TRACE: cooldown_return", flush=True)
        journal_wait("cooldown",scan_id=sid); journal("SCAN_DONE",scan_id=sid,status="WAIT",reason="cooldown"); return
    print("V8_TRACE: build_signal_enter", flush=True)
    signal=build_signal(F,state,calibration,market,news,adaptive_intel,shadow_intel)
    print(f"V8_TRACE: build_signal_returned signal={bool(signal)}", flush=True)
    if not signal:
        journal_wait("no_valid_signal",scan_id=sid,regime=market.get("regime"),confidence=market.get("confidence"),flow_direction=market.get("flow",{}).get("direction"),flow_transition=market.get("flow",{}).get("transition"),flow_long=market.get("flow",{}).get("long_score"),flow_short=market.get("flow",{}).get("short_score"),news_error=news.get("error"),news_articles=news.get("articles")); journal("SCAN_DONE",scan_id=sid,status="WAIT",reason="no_valid_signal"); print("V8_TRACE: wait_no_signal_exit", flush=True); return
    print("V8_TRACE: opening_trade", flush=True)
    # Live execution remains V8-FINAL-2 ATR-only. The following comparison is Shadow-only.
    core_levels_shadow=calculate_trade_levels(signal["price"],signal["atr"],signal["direction"],signal["score"])
    shadow_dynamic=shadow_dynamic_levels_simulation(F,signal["direction"],core_levels_shadow,sid)
    if shadow_dynamic:
        print(f"V8_SHADOW_DYNAMIC: {signal['direction']} base_rr={shadow_dynamic['base_rr']:.2f} shadow_rr={shadow_dynamic['shadow_rr']:.2f} sl={shadow_dynamic['sl_reason']} tp={shadow_dynamic['tp_reason']}", flush=True)
    trade,levels=open_trade_from_signal(state,signal); record_signal(signal,"OPEN")
    # Persist the trade BEFORE sending Telegram. If Telegram fails, the OPEN trade
    # remains recoverable and the next scan retries the missing entry notification.
    save_state(state); save_calibration(calibration); journal("TRADE_OPENED",**trade)
    entry_sent=ensure_entry_notification(state)
    if not entry_sent:
        journal("TRADE_ENTRY_NOTIFICATION_NOT_CONFIRMED",signal_id=trade.get("signal_id"),diagnostic="trade_is_open_and_notification_will_retry")
    journal("SCAN_DONE",scan_id=sid,status="TRADE_OPENED",signal_id=signal.get("signal_id"))
    print("V8_TRACE: run_scan_exit trade_opened", flush=True)

def initialize():
    state=load_state(); calibration=load_calibration(); ensure_parent_dir(JOURNAL_FILE); ensure_parent_dir(CSV_LOG_FILE); ensure_parent_dir(SCAN_CSV_FILE); ensure_parent_dir(SHADOW_SR_FILE); ensure_parent_dir(SHADOW_INTEL_CSV); ensure_parent_dir(SHADOW_ORDERBOOK_CSV); ensure_parent_dir(ADAPTIVE_OPPORTUNITY_CSV); return state,calibration
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--once",action="store_true"); args=ap.parse_args()
    print("V8_DIAGNOSTIC: main_started", flush=True)
    state,calibration=initialize()
    print(f"V8_DIAGNOSTIC: initialized once={args.once}", flush=True)
    while True:
        state["last_scan_ts"]=now_ts()
        print("V8_DIAGNOSTIC: scan_dispatch", flush=True)
        try:
            run_scan(state,calibration)
            print("V8_DIAGNOSTIC: scan_returned", flush=True)
        except KeyboardInterrupt:raise
        except Exception as e:
            err=repr(e); tb=traceback.format_exc(); journal("SCAN_ERROR",error=err,traceback=tb)
            append_scan_csv({"ts":utc_now().isoformat(),"scan_id":"ERROR_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"),"event":"SCAN_ERROR","action":"ERROR","error":err})
            print(f"V8_DIAGNOSTIC: scan_exception {err}", flush=True)
        save_state(state); save_calibration(calibration)
        print("V8_DIAGNOSTIC: state_saved", flush=True)
        if args.once:
            print("V8_DIAGNOSTIC: once_complete", flush=True)
            break
        time.sleep(SCAN_SECONDS)
if __name__=="__main__":main()

import os,sys,time,json,math,argparse,traceback,csv,copy,re,hashlib
from datetime import datetime,timezone,timedelta
import requests,pandas as pd,numpy as np

PRODUCT="BTC-USD"; REST="https://api.exchange.coinbase.com"
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN","")
CHAT_ID=os.getenv("TELEGRAM_CHAT_ID","7500472109")
JOURNAL_FILE="logs/journal/btc_v8_journal.jsonl"; STATE_FILE="logs/state/btc_v8_state.json"
CALIBRATION_FILE="logs/calibration/btc_v8_calibration.json"; CSV_LOG_FILE="logs/csv/btc_v8_signals.csv"
SCAN_CSV_FILE="logs/csv/btc_v8_scans.csv"
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
            "regime_candidate_count":0,"last_closed_trade":None,"flow_state":{}}
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
    if not TOKEN or not CHAT_ID:return False
    try:
        r=S.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text},timeout=REQUEST_TIMEOUT)
        r.raise_for_status(); return True
    except Exception as e: journal("TELEGRAM_ERROR",error=repr(e)); return False

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

SIGNAL_CSV_FIELDS=["ts","scan_id","event","signal_id","direction","setup","regime","confidence","score","agreement","price","adaptive_adjustment","sweep_confirmation","news_confirmation","flow_direction","flow_transition","flow_score","action"]

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
            "score":signal["score"],"agreement":signal["agreement"],"features":signal.get("features",{}),"extensions":0,"last_extension_ts":0.0}

def regime_direction(regime):
    if str(regime).startswith("BULLISH"): return "LONG"
    if str(regime).startswith("BEARISH"): return "SHORT"
    return "NEUTRAL"

def regime_allows_direction(regime,direction):
    rd=regime_direction(regime)
    return rd in {"NEUTRAL",direction}

def decision_engine(direction,market,execution,sweep,continuation):
    """Single authority for entry permission.

    Regime describes the higher-timeframe context; flow describes current participation.
    Flow never adds points on top of execution. It acts as a veto/confirmation layer.
    During TRANSITION, both directions are NOT allowed: only a confirmed flow direction
    may qualify, and a reversal also needs structural/liquidity confirmation.
    """
    regime=str(market.get("regime","UNSTABLE")); rd=regime_direction(regime)
    flow=market.get("flow",{}) or {}; fd=flow.get("confirmed_direction","NEUTRAL") or "NEUTRAL"
    transition=str(flow.get("transition","NORMAL")); flow_score=safe_float(flow.get("long_score" if direction=="LONG" else "short_score"),.5)
    base_agreement=safe_float(execution.get("agreement"),0.0)
    sweep_conf=safe_float(sweep.get("confirmation"),0.0)
    structure_score=safe_float((flow.get("long" if direction=="LONG" else "short") or {}).get("structure",{}).get("score"),.5)
    reasons=[]; allowed=False; mode="BLOCK"

    # Hard market-context gates.
    if regime in {"RANGE","UNSTABLE"}:
        reasons.append("non_trending_regime")
        return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}

    if regime=="TRANSITION":
        if fd!=direction or transition not in {"TRANSITION","CONFIRMED"}:
            reasons.append("transition_requires_confirmed_flow_direction")
            return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}
        if flow_score < DECISION_MIN_FLOW_FOR_REVERSAL:
            reasons.append("flow_below_reversal_threshold")
            return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}
        if sweep_conf < DECISION_MIN_SWEEP_FOR_REVERSAL and structure_score < DECISION_MIN_STRUCTURE_FOR_REVERSAL:
            reasons.append("transition_needs_sweep_or_structure_confirmation")
            return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}
        allowed=base_agreement>=MIN_AGREEMENT; mode="REVERSAL" if allowed else mode
        reasons.append("confirmed_flow_transition")
        return {"allowed":allowed,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}

    # A trend regime keeps authority unless flow is explicitly warning/reversing it.
    if rd!=direction:
        if fd==direction and transition=="CONFIRMED" and flow_score>=DECISION_MIN_FLOW_FOR_REVERSAL and sweep_conf>=DECISION_MIN_SWEEP_FOR_REVERSAL:
            allowed=base_agreement>=MIN_AGREEMENT; mode="REVERSAL" if allowed else mode; reasons.append("flow_confirmed_counter_regime_reversal")
        else:
            reasons.append("opposite_regime_direction")
        return {"allowed":allowed,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}

    # Same-direction trend: flow must not be materially weak or reversed.
    if fd not in {"NEUTRAL",direction} and transition in {"TRANSITION","CONFIRMED"}:
        reasons.append("flow_conflicts_with_regime")
        return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}
    if flow_score < DECISION_MIN_FLOW_FOR_TREND:
        reasons.append("flow_not_supportive")
        return {"allowed":False,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}
    allowed=base_agreement>=MIN_AGREEMENT; mode="TREND" if allowed else mode; reasons.append("regime_and_flow_aligned")
    return {"allowed":allowed,"mode":mode,"reason":"|".join(reasons),"flow_score":flow_score,"direction":fd}

def build_signal(F,state,calibration,market,news):
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
        decision=decision_engine(direction,market,execution,sweep,cont)
        if not decision["allowed"]:
            journal("DECISION_BLOCK",direction=direction,regime=regime,flow_direction=decision.get("direction"),flow_score=decision.get("flow_score"),mode=decision.get("mode"),reason=decision.get("reason"),setup=setup)
            continue

        features=build_signal_features(F,direction,parts,regime,setup,execution["score"],execution["agreement"])
        adaptive=adaptive_signal_score(execution["score"],calibration,features)

        # Score layers are deliberately non-overlapping:
        # 1) execution score = primary evidence;
        # 2) adaptive calibration = historical adjustment;
        # 3) setup bonus = one-time liquidity/continuation confirmation;
        # 4) news = tiny optional support. Flow/OB do NOT get added again.
        setup_bonus=0.0
        if setup=="SWEEP_REVERSAL": setup_bonus=DECISION_SETUP_BONUS_MAX*clamp((sc-DECISION_MIN_SWEEP_FOR_REVERSAL)/max(1-DECISION_MIN_SWEEP_FOR_REVERSAL,1e-9))
        elif setup=="CONTINUATION": setup_bonus=DECISION_SETUP_BONUS_MAX*clamp((safe_float(cont.get("score"))-.62)/.38)
        news_bonus=((safe_float(news.get("score"),.5)-.5)*NEWS_SUPPORT_MAX_POINTS) if news_available else 0.0
        score=clamp(adaptive["score"]+setup_bonus+news_bonus,0,105)
        final_agreement=clamp(execution["agreement"])
        if score<MIN_SCORE or final_agreement<MIN_AGREEMENT:
            journal("SIGNAL_REJECTED",direction=direction,setup=setup,regime=regime,regime_confidence=rc,base_score=execution["score"],adaptive_score=adaptive["score"],final_score=score,base_agreement=execution["agreement"],final_agreement=final_agreement,sweep_confirmation=sc,continuation_score=cont.get("score",0),flow_direction=decision.get("direction"),flow_transition=market.get("flow",{}).get("transition"),flow_score=decision.get("flow_score"),news_available=news_available,news_error=news.get("error"))
            continue

        candidates.append({"direction":direction,"setup":setup,"regime":regime,"regime_confidence":rc,"score":score,"base_score":execution["score"],"adaptive_score":adaptive["score"],
            "adaptive_adjustment":adaptive.get("adjustment",0),"agreement":final_agreement,"base_agreement":execution["agreement"],"sweep_confirmation":sc,"sweep":sweep,"continuation":cont,
            "news_score":safe_float(news.get("score"),.5),"news_confirmation":safe_float(news.get("confirmation")),"news_available":news_available,"parts":parts,"features":features,"price":price,"atr":atr,
            "flow":market.get("flow",{}),"flow_score":decision.get("flow_score",.5),"decision":decision,"order_block":sweep.get("order_block",{}),
            "strong":score>=STRONG_SCORE and final_agreement>=STRONG_AGREEMENT and rc>=REGIME_MIN_CONFIDENCE})

    if not candidates:return None
    candidates.sort(key=lambda x:(x["score"],x["agreement"]),reverse=True)
    if len(candidates)>1:
        gap=candidates[0]["score"]-candidates[1]["score"]
        if gap<2:
            journal("DECISION_AMBIGUOUS",first=candidates[0]["direction"],second=candidates[1]["direction"],score_gap=gap)
            return None
    best=candidates[0]; best["signal_id"]=make_signal_id(best["direction"],best["price"]); return best

def format_signal_message(s,levels):
    d="🟢 LONG ENTRY" if s["direction"]=="LONG" else "🔴 SHORT ENTRY"
    return f"{d}\n\nEntry: {levels['entry']:.2f}\nStop Loss: {levels['stop_loss']:.2f}\nTarget: {levels['target']:.2f}\nRR: {levels['rr']:.2f}\n\nWhy: Trend, structure, momentum and risk gates aligned.\n\nSetup: {s['setup']}\nSweep: {'CONFIRMED' if s.get('sweep',{}).get('confirmed') else 'NONE'}\n\nScore: {s['score']:.1f}/105\nAgreement: {s['agreement']*100:.0f}%\n\nTrend: {s['parts'].get('trend15',0):.2f} | Structure: {s['parts'].get('structure',0):.2f}\nPrice: {s['parts'].get('price',0):.2f} | Volume: {s['parts'].get('volume',0):.2f}\nMomentum: {s['parts'].get('momentum',0):.2f} | Orderflow: {s['parts'].get('orderflow',0):.2f}\nDecision: {s.get("decision",{}).get("mode","BLOCK")} | Flow: {s.get("flow",{}).get("transition","NORMAL")} | FlowScore: {s.get("flow_score",.5):.2f}\nOB: {s.get('order_block',{}).get('quality',0):.2f} | Adaptive: {s.get('adaptive_adjustment',0):+.2f} | News: {s.get('news_score',.5):.2f}"

def open_trade_from_signal(state,signal):
    levels=calculate_trade_levels(signal["price"],signal["atr"],signal["direction"],signal["score"]); trade=create_trade_record(signal,levels)
    state["active_trade"]=trade; state["last_signal"]=signal["signal_id"]; state["last_signal_ts"]=now_ts(); state["last_direction"]=signal["direction"]; set_cooldown(state); return trade,levels

def close_trade(state,calibration,reason,price):
    trade=state.get("active_trade")
    if not trade:return None
    entry=safe_float(trade.get("entry")); risk=max(safe_float(trade.get("risk")),entry*.0005); pnl=((price-entry)/risk) if trade.get("direction")=="LONG" else ((entry-price)/risk); outcome="WIN" if pnl>0 else "LOSS" if pnl<0 else "FLAT"
    trade.update({"status":"CLOSED","closed_at":utc_now().isoformat(),"exit":price,"close_reason":reason,"pnl_r":pnl,"outcome":outcome}); learn_from_closed_trade(calibration,trade.get("features",{}),pnl,outcome)
    state["active_trade"]=None; state["last_closed_trade"]=trade; set_cooldown(state); journal("TRADE_CLOSED",**trade); return trade

def manage_active_trade(state,calibration):
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
    if trade.get("extensions",0)<MAX_TARGET_EXTENSIONS and now_ts()-safe_float(trade.get("last_extension_ts"))>EXTENSION_COOLDOWN_MIN*60:
        ext=calculate_extended_target(trade,price,atr)
        if (direction=="LONG" and ext>trade["target"] and price>trade["entry"]) or (direction=="SHORT" and ext<trade["target"] and price<trade["entry"]):
            trade["target"]=ext; trade["extensions"]=int(trade.get("extensions",0))+1; trade["last_extension_ts"]=now_ts()
    return None

def scan_id(): return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
def journal_wait(reason,**kw):
    journal("WAIT",reason=reason,**kw)
    append_scan_csv({"ts":utc_now().isoformat(),"scan_id":kw.get("scan_id",scan_id()),"event":"WAIT","reason":reason,**kw})
def record_signal(signal,action="SIGNAL"):
    row={"ts":utc_now().isoformat(),"scan_id":signal.get("signal_id"),"event":"SIGNAL","signal_id":signal.get("signal_id"),"direction":signal.get("direction"),"setup":signal.get("setup"),"regime":signal.get("regime"),
         "confidence":signal.get("regime_confidence"),"score":signal.get("score"),"agreement":signal.get("agreement"),"price":signal.get("price"),"adaptive_adjustment":signal.get("adaptive_adjustment"),
         "sweep_confirmation":signal.get("sweep_confirmation"),"news_confirmation":signal.get("news_confirmation"),"flow_direction":signal.get("flow",{}).get("confirmed_direction"),"flow_transition":signal.get("flow",{}).get("transition"),"flow_score":signal.get("flow_score"),"action":action}; append_signal_csv(row); journal("SIGNAL",**row)

def run_scan(state,calibration):
    sid=scan_id(); scan_started=utc_now().isoformat(); journal("SCAN_START",scan_id=sid)
    append_scan_csv({"ts":scan_started,"scan_id":sid,"event":"SCAN_START","action":"SCAN"})
    F={"1m":fetch_candles(PRODUCT,60,120) if USE_MICRO_CONFIRMATION else pd.DataFrame(),"5m":fetch_candles(PRODUCT,300,HISTORY_5M_BARS),"15m":fetch_candles(PRODUCT,900,500)}
    F["1h"]=fetch_candles(PRODUCT,3600,min(HISTORY_1H_BARS,300))
    F["2h"]=aggregate_candles(F["1h"],7200); F["4h"]=aggregate_candles(F["1h"],14400)
    if any(len(F[k])<50 for k in ("5m","15m","1h","2h","4h")):journal_wait("insufficient_history",scan_id=sid); journal("SCAN_DONE",scan_id=sid,status="WAIT",reason="insufficient_history"); return
    update_orderflow(); market=market_state_engine(F,state); news=fetch_news()
    if active_trade_exists(state):
        closed=manage_active_trade(state,calibration)
        if closed:
            telegram_send(f"TRADE CLOSED\n{closed['direction']} {closed['outcome']}\nExit: {closed['exit']:.2f}\nP/L: {closed['pnl_r']:+.2f}R\nReason: {closed['close_reason']}")
            save_state(state); save_calibration(calibration)
            append_scan_csv({"ts":utc_now().isoformat(),"scan_id":sid,"event":"TRADE_CLOSED","active_trade":False,"direction":closed.get("direction"),"action":"CLOSE"})
        journal("SCAN_DONE",scan_id=sid,status="TRADE_MANAGED",closed=bool(closed))
        return
    if cooldown_active(state):journal_wait("cooldown",scan_id=sid); journal("SCAN_DONE",scan_id=sid,status="WAIT",reason="cooldown"); return
    signal=build_signal(F,state,calibration,market,news)
    if not signal:
        journal_wait("no_valid_signal",scan_id=sid,regime=market.get("regime"),confidence=market.get("confidence"),flow_direction=market.get("flow",{}).get("direction"),flow_transition=market.get("flow",{}).get("transition"),flow_long=market.get("flow",{}).get("long_score"),flow_short=market.get("flow",{}).get("short_score"),news_error=news.get("error"),news_articles=news.get("articles")); journal("SCAN_DONE",scan_id=sid,status="WAIT",reason="no_valid_signal"); return
    trade,levels=open_trade_from_signal(state,signal); record_signal(signal,"OPEN"); telegram_send(format_signal_message(signal,levels)); journal("TRADE_OPENED",**trade); save_state(state); save_calibration(calibration)
    journal("SCAN_DONE",scan_id=sid,status="TRADE_OPENED",signal_id=signal.get("signal_id"))

def initialize():
    state=load_state(); calibration=load_calibration(); ensure_parent_dir(JOURNAL_FILE); ensure_parent_dir(CSV_LOG_FILE); ensure_parent_dir(SCAN_CSV_FILE); return state,calibration
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--once",action="store_true"); args=ap.parse_args(); state,calibration=initialize()
    while True:
        state["last_scan_ts"]=now_ts()
        try:run_scan(state,calibration)
        except KeyboardInterrupt:raise
        except Exception as e:
            err=repr(e); tb=traceback.format_exc(); journal("SCAN_ERROR",error=err,traceback=tb)
            append_scan_csv({"ts":utc_now().isoformat(),"scan_id":"ERROR_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"),"event":"SCAN_ERROR","action":"ERROR","error":err})
        save_state(state); save_calibration(calibration)
        if args.once:break
        time.sleep(SCAN_SECONDS)
if __name__=="__main__":main()

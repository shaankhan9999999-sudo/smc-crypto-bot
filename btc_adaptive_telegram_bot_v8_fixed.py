import os,sys,time,json,math,argparse,traceback,csv,copy,re,hashlib
from datetime import datetime,timezone,timedelta
import requests,pandas as pd,numpy as np

PRODUCT="BTC-USD"; REST="https://api.exchange.coinbase.com"
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN","")
CHAT_ID=os.getenv("TELEGRAM_CHAT_ID","7500472109")
JOURNAL_FILE="btc_v8_journal.jsonl"; STATE_FILE="btc_v8_state.json"
CALIBRATION_FILE="btc_v8_calibration.json"; CSV_LOG_FILE="logs/btc_v8_signals.csv"
SCAN_SECONDS=60; COOLDOWN_MIN=45; MIN_SCORE=62; STRONG_SCORE=76
MIN_AGREEMENT=.58; STRONG_AGREEMENT=.68; REQUEST_TIMEOUT=20
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
            "regime_candidate_count":0,"last_closed_trade":None}
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
    g=int(granularity); end=int(time.time()); start=end-g*300
    data=api_get(f"/products/{product}/candles",{"granularity":g,"start":datetime.fromtimestamp(start,timezone.utc).isoformat(),"end":datetime.fromtimestamp(end,timezone.utc).isoformat()})
    rows=[[int(x[0]),safe_float(x[1]),safe_float(x[2]),safe_float(x[3]),safe_float(x[4]),safe_float(x[5])] for x in (data or []) if len(x)>=6]
    return pd.DataFrame(sorted(rows,key=lambda x:x[0]),columns=["ts","low","high","open","close","volume"]).tail(limit).reset_index(drop=True)

def aggregate_candles(df,seconds):
    d=df.copy()
    if d.empty:return d
    d["dt"]=pd.to_datetime(d.ts,unit="s",utc=True)
    rule=f"{int(seconds//3600)}H" if seconds%3600==0 else f"{int(seconds//60)}min"
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

def market_state_engine(F,state=None):
    tf={k:_tf_direction_score(v) for k,v in F.items() if isinstance(v,pd.DataFrame)}
    if not tf:return {"regime":"UNSTABLE","confidence":0.0,"bull":.0,"bear":.0,"transition":1.0,"timeframes":{}}
    bull=[]
    for k in ("2h","1h","15m","5m"):
        if k in tf:
            x=tf[k]; bull.append(.55*x["trend"]+.25*x["structure"]+.20*x["momentum"])
    b=float(np.mean(bull)) if bull else .5; br=1-b; t1=tf.get("1h",{}); t2=tf.get("2h",t1); t15=tf.get("15m",t1)
    macro_bull=b>.60 and t2.get("trend",.5)>.55; macro_bear=br>.60 and t2.get("trend",.5)<.45
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
    if state is not None:
        prev=state.get("regime")
        if prev and prev!=regime and conf<REGIME_MIN_CONFIDENCE:regime=prev
        state["regime"]=regime
    return {"regime":regime,"confidence":conf,"bull":b,"bear":br,"transition":transition,"timeframes":tf,
            "macro_bull":macro_bull,"macro_bear":macro_bear,"roles":{"2h":"macro","1h":"transition_liquidity","15m":"setup_confirmation","5m":"entry_execution"}}

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
def append_signal_csv(row):
    fields=["ts","signal_id","direction","setup","regime","score","agreement","price","adaptive_adjustment","sweep_confirmation","news_confirmation","action"]
    try:
        ensure_parent_dir(CSV_LOG_FILE); new=not os.path.exists(CSV_LOG_FILE)
        with open(CSV_LOG_FILE,"a",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
            if new:w.writeheader()
            w.writerow({k:row.get(k,"") for k in fields})
    except Exception as e:journal("CSV_ERROR",error=repr(e))

def calculate_agreement(parts):
    vals=[clamp(v) for v in parts.values()] if isinstance(parts,dict) else []
    if not vals:return 0.0
    m=float(np.mean(vals)); return clamp(1-min(1,float(np.mean([abs(x-m) for x in vals]))*2))

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

def detect_liquidity_sweep(df,direction):
    d=prepare(df)
    if len(d)<SWEEP_LOOKBACK+2:return {"detected":False,"confirmed":False,"quality":0.0,"confirmation":0.0,"reason":"insufficient_data","age":99}
    r=d.iloc[-1]; atr=max(safe_float(r.atr),safe_float(r.close)*.0001); prev=d.iloc[-SWEEP_LOOKBACK-1:-1]; hi=safe_float(prev.high.max()); lo=safe_float(prev.low.min()); rng=max(safe_float(r.high-r.low),atr*.1)
    if direction=="LONG":pen=max(0,(lo-safe_float(r.low))/atr); rec=max(0,(safe_float(r.close)-lo)/atr); wick=clamp((safe_float(r.close)-r.low)/rng); detected=bool(r.low<lo and pen>=SWEEP_MIN_PENETRATION_ATR and rec>=SWEEP_MIN_RECLAIM)
    else:pen=max(0,(safe_float(r.high)-hi)/atr); rec=max(0,(hi-safe_float(r.close))/atr); wick=clamp((r.high-safe_float(r.close))/rng); detected=bool(r.high>hi and pen>=SWEEP_MIN_PENETRATION_ATR and rec>=SWEEP_MIN_RECLAIM)
    vol=clamp(safe_float(r.volume)/(safe_float(r.vol_ma20) or 1)); of=FLOW; flow=clamp(abs(safe_float(of.get("delta")))/(safe_float(of.get("buy"))+safe_float(of.get("sell")) or 1))
    q=.40*clamp(pen/.50)+.30*clamp(rec)+.20*wick+.10*vol; conf=.40*q+.20*vol+.20*flow+.20*clamp(rec) if detected else 0
    ok=detected and conf>=SWEEP_MIN_CONFIRMATION; return {"detected":detected,"confirmed":ok,"quality":q,"confirmation":conf,"age":0,"penetration":pen,"reclaim":rec,"reason":"confirmed" if ok else ("sweep_not_confirmed" if detected else "no_sweep")}
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

def regime_allows_direction(regime,direction):
    if regime in {"RANGE","UNSTABLE","TRANSITION"}:return True
    return regime.startswith("BULLISH") if direction=="LONG" else regime.startswith("BEARISH")
def opposite_direction(d):return "SHORT" if d=="LONG" else "LONG"

def build_signal(F,state,calibration,market,news):
    candidates=[]; regime=market.get("regime","UNSTABLE"); rc=market.get("confidence",0.0); entry=_tf_direction_score(F["5m"]); price=entry["close"]; atr=entry["atr"]
    if price<=0 or atr<=0:return None
    for direction in ("LONG","SHORT"):
        if not regime_allows_direction(regime,direction):continue
        execution=execution_score(F,direction); base=execution["score"]; agreement=execution["agreement"]; parts=execution["parts"]
        sweep=detect_liquidity_sweep(F["1h"],direction); sc=sweep_confirmation_score(sweep,execution,market,direction); cont=detect_continuation(F["1h"],direction)
        setup="SWEEP_REVERSAL" if sweep.get("confirmed") and sweep.get("quality",0)>=SWEEP_REVERSAL_MIN_QUALITY else "CONTINUATION" if cont.get("ready") else "TREND"
        features=build_signal_features(F,direction,parts,regime,setup,base,agreement); adaptive=adaptive_signal_score(base,calibration,features)
        ns=safe_float(news.get("score"),.5); nc=safe_float(news.get("confirmation")); adj=((sc-.5)*10 if setup=="SWEEP_REVERSAL" else (cont.get("score",0)-.62)*5 if setup=="CONTINUATION" else 0)+(ns-.5)*4*nc
        score=clamp(adaptive["score"]+adj,0,105); final_agreement=clamp(.70*agreement+.15*sc+.15*nc) if setup=="SWEEP_REVERSAL" else clamp(.75*agreement+.25*nc*.5)
        if score<MIN_SCORE or final_agreement<MIN_AGREEMENT:continue
        if regime=="TRANSITION" and rc>=TRANSITION_BLOCK_CONFIDENCE and score<STRONG_SCORE:continue
        candidates.append({"direction":direction,"setup":setup,"regime":regime,"regime_confidence":rc,"score":score,"base_score":base,"adaptive_score":adaptive["score"],
            "adaptive_adjustment":adaptive.get("adjustment",0),"agreement":final_agreement,"base_agreement":agreement,"sweep_confirmation":sc,"sweep":sweep,"continuation":cont,
            "news_score":ns,"news_confirmation":nc,"parts":parts,"features":features,"price":price,"atr":atr,"strong":score>=STRONG_SCORE and final_agreement>=STRONG_AGREEMENT and rc>=REGIME_MIN_CONFIDENCE})
    if not candidates:return None
    candidates.sort(key=lambda x:(x["score"],x["agreement"]),reverse=True)
    if len(candidates)>1 and candidates[0]["score"]-candidates[1]["score"]<2:return None
    best=candidates[0]; best["signal_id"]=make_signal_id(best["direction"],best["price"]); return best

def format_signal_message(s,levels):
    d="🟢 LONG ENTRY" if s["direction"]=="LONG" else "🔴 SHORT ENTRY"
    return f"{d}\n\nEntry: {levels['entry']:.2f}\nStop Loss: {levels['stop_loss']:.2f}\nTarget: {levels['target']:.2f}\nRR: {levels['rr']:.2f}\n\nWhy: Trend, structure, momentum and risk gates aligned.\n\nSetup: {s['setup']}\nSweep: {'CONFIRMED' if s.get('sweep',{}).get('confirmed') else 'NONE'}\n\nScore: {s['score']:.1f}/105\nAgreement: {s['agreement']*100:.0f}%\n\nTrend: {s['parts'].get('trend15',0):.2f} | Structure: {s['parts'].get('structure',0):.2f}\nPrice: {s['parts'].get('price',0):.2f} | Volume: {s['parts'].get('volume',0):.2f}\nMomentum: {s['parts'].get('momentum',0):.2f} | Orderflow: {s['parts'].get('orderflow',0):.2f}\nAdaptive: {s.get('adaptive_adjustment',0):+.2f} | News: {s.get('news_score',.5):.2f}"

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

def journal_wait(reason,**kw):journal("WAIT",reason=reason,**kw)
def record_signal(signal,action="SIGNAL"):
    row={"ts":utc_now().isoformat(),"signal_id":signal.get("signal_id"),"direction":signal.get("direction"),"setup":signal.get("setup"),"regime":signal.get("regime"),
         "score":signal.get("score"),"agreement":signal.get("agreement"),"price":signal.get("price"),"adaptive_adjustment":signal.get("adaptive_adjustment"),
         "sweep_confirmation":signal.get("sweep_confirmation"),"news_confirmation":signal.get("news_confirmation"),"action":action}; append_signal_csv(row); journal("SIGNAL",**row)

def run_scan(state,calibration):
    F={"5m":fetch_candles(PRODUCT,300,HISTORY_5M_BARS),"15m":fetch_candles(PRODUCT,900,500)}
    F["1h"]=fetch_candles(PRODUCT,3600,min(HISTORY_1H_BARS,300))
    F["2h"]=aggregate_candles(F["1h"],7200); F["4h"]=aggregate_candles(F["1h"],14400)
    if any(len(F[k])<50 for k in ("5m","15m","1h")):journal_wait("insufficient_history");return
    update_orderflow(); market=market_state_engine(F,state); news=fetch_news()
    if active_trade_exists(state):
        closed=manage_active_trade(state,calibration)
        if closed:telegram_send(f"TRADE CLOSED\n{closed['direction']} {closed['outcome']}\nExit: {closed['exit']:.2f}\nP/L: {closed['pnl_r']:+.2f}R\nReason: {closed['close_reason']}"); save_state(state); save_calibration(calibration)
        return
    if cooldown_active(state):journal_wait("cooldown");return
    signal=build_signal(F,state,calibration,market,news)
    if not signal:journal_wait("no_valid_signal",regime=market.get("regime"),confidence=market.get("confidence"));return
    trade,levels=open_trade_from_signal(state,signal); record_signal(signal,"OPEN"); telegram_send(format_signal_message(signal,levels)); journal("TRADE_OPENED",**trade); save_state(state); save_calibration(calibration)

def initialize():
    state=load_state(); calibration=load_calibration(); ensure_parent_dir(JOURNAL_FILE); ensure_parent_dir(CSV_LOG_FILE); return state,calibration
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--once",action="store_true"); args=ap.parse_args(); state,calibration=initialize()
    while True:
        state["last_scan_ts"]=now_ts()
        try:run_scan(state,calibration)
        except KeyboardInterrupt:raise
        except Exception as e:journal("SCAN_ERROR",error=repr(e),traceback=traceback.format_exc())
        save_state(state); save_calibration(calibration)
        if args.once:break
        time.sleep(SCAN_SECONDS)
if __name__=="__main__":main()

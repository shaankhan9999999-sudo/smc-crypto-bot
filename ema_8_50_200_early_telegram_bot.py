#!/usr/bin/env python3
import os, sys, json, lzma, struct
from pathlib import Path
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd

EMA8, EMA50, EMA200 = 8, 50, 200
ATR_N = 14
ZONE_ATR, EARLY_ATR, DUAL_CANDLES = 0.20, 0.30, 3
SWING_LOOKBACK, ATR_SL_BUFFER = 5, 0.20
TP1_R, TP2_R = 1.5, 2.0
COINBASE_PRODUCT = 'BTC-USD'
DUKA_SYMBOL = 'XAUUSD'
XAU_LOOKBACK_HOURS = int(os.getenv('XAU_LOOKBACK_HOURS', '270'))
DUKA_WORKERS = int(os.getenv('DUKA_WORKERS', '16'))
STATE_FILE = Path(os.getenv('STATE_FILE', 'ema_bot_state.json'))
S = requests.Session()
S.headers.update({'User-Agent':'EMA-8-50-200-Telegram-Bot/1.0'})


def now(): return datetime.now(timezone.utc)
def log(x): print(f"[{now():%Y-%m-%d %H:%M:%S UTC}] {x}", flush=True)

def tg(msg):
    token=os.environ['TELEGRAM_BOT_TOKEN']; chat=os.environ['TELEGRAM_CHAT_ID']
    r=S.post(f'https://api.telegram.org/bot{token}/sendMessage',data={'chat_id':chat,'text':msg},timeout=20)
    r.raise_for_status()

def load_state():
    try: return json.loads(STATE_FILE.read_text())
    except Exception: return {'symbols':{}}

def save_state(x): STATE_FILE.write_text(json.dumps(x,indent=2,sort_keys=True))

def sym_state(st,symbol):
    st.setdefault('symbols',{}); st['symbols'].setdefault(symbol,{
        'active_trade':None,'last_zone_event':None,'last_early_event':None,
        'last_entry_event':None,'last_m15_event':None,'last_h1_event':None,
        'last_weak_event':None,'last_invalid_event':None})
    return st['symbols'][symbol]

# ---------------- BTC: Coinbase native candles ----------------
G={'M5':300,'M15':900,'H1':3600}
def coinbase(tf, needed=430):
    sec=G[tf]; end=int(now().timestamp()); rows=[]; remaining=needed
    url=f'https://api.exchange.coinbase.com/products/{COINBASE_PRODUCT}/candles'
    while remaining>0:
        count=min(280,remaining); start=end-count*sec
        p={'granularity':sec,'start':datetime.fromtimestamp(start,timezone.utc).isoformat(),
           'end':datetime.fromtimestamp(end,timezone.utc).isoformat()}
        r=S.get(url,params=p,timeout=20); r.raise_for_status(); data=r.json()
        if not data: break
        rows += data; oldest=min(int(x[0]) for x in data)
        if oldest>=end: break
        end=oldest-sec; remaining-=len(data)
        if len(data)<count: break
    if not rows: raise RuntimeError(f'Coinbase returned no {tf} candles')
    d=pd.DataFrame(rows,columns=['ts','low','high','open','close','volume']).drop_duplicates('ts').sort_values('ts')
    d['ts']=pd.to_datetime(d.ts,unit='s',utc=True); d=d.set_index('ts')
    for c in ['open','high','low','close','volume']: d[c]=pd.to_numeric(d[c],errors='coerce')
    d=d.dropna()
    if len(d)>1: d=d.iloc[:-1]
    if len(d)<EMA200+30: raise RuntimeError(f'Not enough Coinbase {tf} candles: {len(d)}')
    return d

# ---------------- XAUUSD: Dukascopy public tick feed -> native candles ----------------
def duka_url(dt):
    # Dukascopy datafeed uses zero-based month in this path.
    return f'https://datafeed.dukascopy.com/datafeed/{DUKA_SYMBOL}/{dt.year:04d}/{dt.month-1:02d}/{dt.day:02d}/{dt.hour:02d}h_ticks.bi5'

def duka_hour(dt):
    r=S.get(duka_url(dt),timeout=20)
    if r.status_code==404: return []
    r.raise_for_status()
    raw=lzma.decompress(r.content)
    if len(raw)%20: raise RuntimeError(f'bad bi5 size {len(raw)}')
    out=[]
    for ms,ask_i,bid_i,av,bv in struct.iter_unpack('>Iiiii',raw):
        ask=ask_i/1000.0; bid=bid_i/1000.0
        if ask>0 and bid>0: out.append((dt+timedelta(milliseconds=ms),(ask+bid)/2.0))
    return out

def xau_frames():
    end=now().replace(minute=0,second=0,microsecond=0)
    start=end-timedelta(hours=XAU_LOOKBACK_HOURS)
    hours=[start+timedelta(hours=i) for i in range(XAU_LOOKBACK_HOURS)]
    ticks=[]; errors=0
    with ThreadPoolExecutor(max_workers=DUKA_WORKERS) as ex:
        fs={ex.submit(duka_hour,h):h for h in hours}
        for f in as_completed(fs):
            try: ticks += f.result()
            except Exception as e: errors += 1; log(f'XAU hour {fs[f]:%Y-%m-%d %H}: {e}')
    if errors: log(f'XAUUSD: {errors} hour requests failed')
    if not ticks: raise RuntimeError('Dukascopy returned no XAUUSD ticks')
    d=pd.DataFrame(ticks,columns=['ts','price']); d.ts=pd.to_datetime(d.ts,utc=True)
    d=d.drop_duplicates('ts').sort_values('ts').set_index('ts'); d.price=pd.to_numeric(d.price,errors='coerce'); d=d.dropna()
    med=float(d.price.median())
    if not 100<med<10000: raise RuntimeError(f'XAUUSD price sanity check failed: {med}')
    out={}
    for tf,rule in [('M5','5min'),('M15','15min'),('H1','1h')]:
        o=d.price.resample(rule,label='left',closed='left').ohlc(); o['volume']=d.price.resample(rule,label='left',closed='left').count(); o=o.dropna()
        if len(o)>1: o=o.iloc[:-1]
        if len(o)<EMA200+30: raise RuntimeError(f'Not enough XAUUSD {tf} candles: {len(o)}')
        out[tf]=o
    return out

# ---------------- indicators / signals ----------------
def indicators(d):
    x=d.copy(); x['ema8']=x.close.ewm(span=EMA8,adjust=False).mean(); x['ema50']=x.close.ewm(span=EMA50,adjust=False).mean(); x['ema200']=x.close.ewm(span=EMA200,adjust=False).mean()
    pc=x.close.shift(1); tr=pd.concat([x.high-x.low,(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1); x['atr']=tr.rolling(ATR_N).mean()
    return x.dropna()

def cu(ap,an,bp,bn): return ap<=bp and an>bn
def cd(ap,an,bp,bn): return ap>=bp and an<bn

def dual(d,direction):
    a=[]; b=[]
    for i in range(1,len(d)):
        p=d.iloc[i-1]; c=d.iloc[i]
        if direction=='LONG':
            if cu(p.ema8,c.ema8,p.ema200,c.ema200): a.append(i)
            if cu(p.ema50,c.ema50,p.ema200,c.ema200): b.append(i)
        else:
            if cd(p.ema8,c.ema8,p.ema200,c.ema200): a.append(i)
            if cd(p.ema50,c.ema50,p.ema200,c.ema200): b.append(i)
    pairs=[(max(i,j),i,j) for i in a for j in b if abs(i-j)<=DUAL_CANDLES]
    if not pairs: return None
    ei,i,j=max(pairs); return {'event_ts':d.index[ei].isoformat(),'i':ei}

def zone(d):
    c=d.iloc[-1]; return c if max(c.ema8,c.ema50,c.ema200)-min(c.ema8,c.ema50,c.ema200)<=ZONE_ATR*c.atr else None

def early(d,direction):
    p,c=d.iloc[-2],d.iloc[-1]
    if direction=='LONG': ok=cu(p.ema8,c.ema8,p.ema200,c.ema200) and abs(c.ema50-c.ema200)<=EARLY_ATR*c.atr and c.ema50>p.ema50 and c.ema50>=c.ema200
    else: ok=cd(p.ema8,c.ema8,p.ema200,c.ema200) and abs(c.ema50-c.ema200)<=EARLY_ATR*c.atr and c.ema50<p.ema50 and c.ema50<=c.ema200
    return c if ok else None

def trade(d,direction):
    c=d.iloc[-1]; look=d.iloc[-(SWING_LOOKBACK+1):-1]; atr=float(c.atr); entry=float(c.close)
    if direction=='LONG': sl=float(look.low.min())-ATR_SL_BUFFER*atr; risk=entry-sl; tp1=entry+TP1_R*risk; tp2=entry+TP2_R*risk
    else: sl=float(look.high.max())+ATR_SL_BUFFER*atr; risk=sl-entry; tp1=entry-TP1_R*risk; tp2=entry-TP2_R*risk
    if risk<=0: return None
    return {'direction':direction,'entry':entry,'sl':sl,'tp1':tp1,'tp2':tp2,'entry_ts':c.name.isoformat(),'m15_confirmed':False,'h1_confirmed':False}

def fp(symbol,x): return f'{x:.2f}'

def process(symbol,frames,st):
    s=sym_state(st,symbol); m5=indicators(frames['M5']); m15=indicators(frames['M15']); h1=indicators(frames['H1'])
    t=s['active_trade']
    if t:
        direction=t['direction']; c=m5.iloc[-1]
        e=dual(m15,direction)
        if e and e['event_ts']!=s['last_m15_event']:
            s['last_m15_event']=e['event_ts']; t['m15_confirmed']=True
            tg(f'🟢 M15 CONFIRMATION — EXTEND TRADE\n\n{symbol} {direction}\nM15 EMA 8 + EMA 50 confirmed same direction through EMA 200.\nExisting M5 trade: HOLD / EXTEND.')
        e=dual(h1,direction)
        if e and e['event_ts']!=s['last_h1_event']:
            s['last_h1_event']=e['event_ts']; t['h1_confirmed']=True
            tg(f'🔵 H1 CONFIRMATION — LONG HOLD MODE\n\n{symbol} {direction}\nH1 EMA 8 + EMA 50 confirmed same direction through EMA 200.\nExisting M5 trade: LONG HOLD MODE.')
        if direction=='LONG': weak=c.ema8<c.ema50 or c.close<c.ema200; invalid=c.ema8<c.ema50 and c.ema50<c.ema200
        else: weak=c.ema8>c.ema50 or c.close>c.ema200; invalid=c.ema8>c.ema50 and c.ema50>c.ema200
        if invalid:
            ev=c.name.isoformat()
            if ev!=s['last_invalid_event']:
                s['last_invalid_event']=ev; tg(f'🔴 M5 TRADE INVALIDATED — EXIT\n\n{symbol} {direction}\nM5 EMA structure flipped against the active trade.')
            s['active_trade']=None; return
        if weak:
            ev=c.name.isoformat()
            if ev!=s['last_weak_event']:
                s['last_weak_event']=ev; tg(f'⚠️ M5 TRADE WEAKENING\n\n{symbol} {direction}\nM5 structure is weakening. Monitor/protect the active trade.')
        return
    z=zone(m5)
    if z is not None and z.name.isoformat()!=s['last_zone_event']:
        s['last_zone_event']=z.name.isoformat(); tg(f'🟡 M5 EMA ZONE\n\n{symbol}\nEMA 8 / EMA 50 / EMA 200 are compressed near a decision zone.')
    for direction in ('LONG','SHORT'):
        e=early(m5,direction)
        if e is not None and e.name.isoformat()!=s['last_early_event']:
            s['last_early_event']=e.name.isoformat(); tg(f'🟠 M5 EMA EARLY CROSS\n\n{symbol} {direction}\nEMA 8 crossed EMA 200; EMA 50 is close and moving toward confirmation.')
        ev=dual(m5,direction)
        if not ev or ev['event_ts']==s['last_entry_event']: continue
        age=now()-pd.Timestamp(ev['event_ts']).to_pydatetime()
        if age.total_seconds()<0 or age>timedelta(minutes=8): continue
        t=trade(m5,direction)
        if not t: continue
        s['last_entry_event']=ev['event_ts']; s['active_trade']=t
        tg(f"🚨 M5 EMA DUAL CROSS — ENTRY\n\n{'🟢' if direction=='LONG' else '🔴'} {symbol} {direction}\nEntry: {fp(symbol,t['entry'])}\nSL: {fp(symbol,t['sl'])}\nTP1 (1.5R): {fp(symbol,t['tp1'])}\nTP2 (2R): {fp(symbol,t['tp2'])}\n\nEMA 8 + EMA 50 crossed EMA 200 within {DUAL_CANDLES} M5 candles.")
        return

def main():
    if not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID'): raise RuntimeError('Telegram secrets are required')
    st=load_state(); btc={}
    for tf in G:
        try: btc[tf]=coinbase(tf)
        except Exception as e: log(f'BTC {tf}: {e}'); btc={}; break
    if btc:
        try: process('BTC',btc,st)
        except Exception as e: log(f'BTC processing error: {e}')
    try: process('XAUUSD',xau_frames(),st)
    except Exception as e: log(f'XAUUSD: {e}')
    save_state(st); log('Run complete.')

if __name__=='__main__':
    try: main()
    except Exception as e: log(f'FATAL: {e}'); sys.exit(1)

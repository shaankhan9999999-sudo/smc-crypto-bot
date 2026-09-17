import os
import requests
import pandas as pd
import numpy as np

# Telegram Credentials (Fixed & Hardcoded as requested)
TELEGRAM_BOT_TOKEN = '8662975391:AAG86xC9Dcx-Ec9ljKveAnnNqtk4jqDkdrO'
TELEGRAM_CHAT_ID = '7500472109'

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"Telegram API Status Code: {response.status_code}")
        return response.json()
    except Exception as e:
        print(f"Error sending telegram message: {e}")

def fetch_coinbase_klines(product_id="BTC-USD", granularity=60):
    # granularity 60 = 1 Minute candles
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity={granularity}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if not isinstance(data, list) or len(data) == 0:
            print(f"Coinbase API error or empty data for granularity {granularity}")
            return pd.DataFrame()
        
        df = pd.DataFrame(data, columns=['time', 'low', 'high', 'open', 'close', 'volume'])
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df.sort_values('time').reset_index(drop=True)
        
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
            
        return df
    except Exception as e:
        print(f"Error fetching Coinbase data: {e}")
        return pd.DataFrame()

def run_ultimate_professional_scalper():
    print("Scanning BTC-USD on Coinbase (Ultimate Multi-Timeframe SMC + POC + EMAs + VWAP)...")
    
    # 1. Fetch 1-Minute data for execution & FVG/EMAs
    df_1m = fetch_coinbase_klines("BTC-USD", granularity=60)
    
    # 2. Fetch 15-Minute data for Macro Trend (200 EMA)
    df_15m = fetch_coinbase_klines("BTC-USD", granularity=900)

    if df_1m.empty or df_15m.empty or len(df_1m) < 100 or len(df_15m) < 50:
        print("Insufficient market data from Coinbase!")
        return

    # Resample 1M data into 3-Minute data for Liquidity Sweep Matrix
    df_1m.set_index('time', inplace=True)
    df_3m = df_1m.resample('3min').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna().reset_index()
    df_1m = df_1m.reset_index()

    # --- A. 15-Minute Macro Trend Filter (200 EMA) ---
    df_15m['ema200'] = df_15m['close'].ewm(span=200).mean()
    htf_last = df_15m.iloc[-1]
    macro_bullish = htf_last['close'] > htf_last['ema200']
    macro_bearish = htf_last['close'] < htf_last['ema200']

    # --- B. 1M Momentum Indicators (9 EMA, 50 EMA, VWAP) ---
    df_1m['ema9'] = df_1m['close'].ewm(span=9).mean()
    df_1m['ema50'] = df_1m['close'].ewm(span=50).mean()
    
    typical_price = (df_1m['high'] + df_1m['low'] + df_1m['close']) / 3
    df_1m['vwap'] = (typical_price * df_1m['volume']).cumsum() / df_1m['volume'].cumsum()

    # --- C. Volume Profile & POC (Point of Control) Calculation ---
    price_bins = pd.cut(df_1m['close'], bins=20)
    volume_profile = df_1m.groupby(price_bins, observed=False)['volume'].sum()
    poc_bin = volume_profile.idxmax()
    poc_price = (poc_bin.left + poc_bin.right) / 2 if pd.notna(poc_bin) else df_1m['close'].iloc[-1]

    # --- D. 3-Minute Liquidity Sweep Logic ---
    recent_low_3m = df_3m['low'].tail(15).min()
    recent_high_3m = df_3m['high'].tail(15).max()
    
    current_3m_low = df_3m.iloc[-1]['low']
    current_3m_high = df_3m.iloc[-1]['high']
    
    sweep_long_3m = current_3m_low < recent_low_3m  
    sweep_short_3m = current_3m_high > recent_high_3m 

    # --- E. 1-Minute FVG (Fair Value Gap) & Volume Imbalance ---
    m1_prev2 = df_1m.iloc[-3]
    m1_prev1 = df_1m.iloc[-2]
    m1_curr = df_1m.iloc[-1]

    fvg_bullish = m1_curr['low'] > m1_prev2['high']
    fvg_bearish = m1_curr['high'] < m1_prev2['low']

    vol_ma = df_1m['volume'].rolling(window=20).mean().iloc[-1]
    aggressive_volume = m1_curr['volume'] > (vol_ma * 1.6)

    # --- F. ATR (14) Risk Tool for Stop Loss ---
    high_low = df_1m['high'] - df_1m['low']
    high_close = np.abs(df_1m['high'] - df_1m['close'].shift())
    low_close = np.abs(df_1m['low'] - df_1m['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]

    price = m1_curr['close']
    ema9 = m1_curr['ema9']
    ema50 = m1_curr['ema50']
    vwap = m1_curr['vwap']

    # Proximity checks
    near_vwap_long = abs(price - vwap) / vwap < 0.005 and price >= vwap
    near_vwap_short = abs(price - vwap) / vwap < 0.005 and price <= vwap
    near_poc = abs(price - poc_price) / poc_price < 0.004

    # --- G. Final Comprehensive Strategy Matrix ---
    bullish_setup = (
        macro_bullish and
        (ema9 > ema50) and
        sweep_long_3m and
        (near_vwap_long or near_poc) and
        (fvg_bullish or aggressive_volume)
    )

    bearish_setup = (
        macro_bearish and
        (ema9 < ema50) and
        sweep_short_3m and
        (near_vwap_short or near_poc) and
        (fvg_bearish or aggressive_volume)
    )

    if bullish_setup:
        sl = round(price - (1.5 * atr), 2)
        risk = price - sl
        tp = round(price + (risk * 2.0), 2)

        msg = (
            f"🚀 [ULTIMATE SMC & VOLUME PROFILE LONG]\n"
            f"Asset: BTC-USD (Coinbase)\n"
            f"• 15M Trend: Bullish (Above 200 EMA)\n"
            f"• 5M/1M Momentum: 9 EMA > 50 EMA & VWAP Confirmed\n"
            f"• 3M Liquidity Sweep: Confirmed (Support Trap Caught)\n"
            f"• Volume POC: {round(poc_price, 2)} | FVG / Imbalance: Active\n\n"
            f"🟢 Entry Price: {price}\n"
            f"🛑 Stop Loss (SL): {sl} (1.5x ATR)\n"
            f"🎯 Take Profit (TP): {tp}"
        )
        send_telegram_message(msg)
        print("Ultimate Bullish Signal Sent to Telegram!")

    elif bearish_setup:
        sl = round(price + (1.5 * atr), 2)
        risk = sl - price
        tp = round(price - (risk * 2.0), 2)

        msg = (
            f"🩸 [ULTIMATE SMC & VOLUME PROFILE SHORT]\n"
            f"Asset: BTC-USD (Coinbase)\n"
            f"• 15M Trend: Bearish (Below 200 EMA)\n"
            f"• 5M/1M Momentum: 9 EMA < 50 EMA & VWAP Confirmed\n"
            f"• 3M Liquidity Sweep: Confirmed (Resistance Trap Caught)\n"
            f"• Volume POC: {round(poc_price, 2)} | FVG / Imbalance: Active\n\n"
            f"🔴 Entry Price: {price}\n"
            f"🛑 Stop Loss (SL): {sl} (1.5x ATR)\n"
            f"🎯 Take Profit (TP): {tp}"
        )
        send_telegram_message(msg)
        print("Ultimate Bearish Signal Sent to Telegram!")
    else:
        print(f"Market scanning... Price: {price} | POC: {round(poc_price, 2)} | Macro Bullish: {macro_bullish} | Sweep L: {sweep_long_3m} | No setup.")

if __name__ == "__main__":
    run_ultimate_professional_scalper()

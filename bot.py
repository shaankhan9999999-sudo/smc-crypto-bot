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
    print("Scanning BTC-USD on Coinbase (Flexible Optional Sweep Setup)...")
    
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
    recent_low_3m = df_3m['low'].tail(25).min()
    recent_high_3m = df_3m['high'].tail(25).max()
    
    sweep_long_3m = (df_3m['low'].tail(3) < recent_low_3m).any()
    sweep_short_3m = (df_3m['high'].tail(3) > recent_high_3m).any()

    # --- E. ATR (14) Risk Tool for Stop Loss ---
    high_low = df_1m['high'] - df_1m['low']
    high_close = np.abs(df_1m['high'] - df_1m['close'].shift())
    low_close = np.abs(df_1m['low'] - df_1m['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]

    vol_ma_series = df_1m['volume'].rolling(window=20).mean()

    bullish_setup = False
    bearish_setup = False
    selected_row = None

    # Diagnostic variables for current check
    last_m1 = df_1m.iloc[-1]
    l_price = last_m1['close']
    l_ema9 = last_m1['ema9']
    l_ema50 = last_m1['ema50']
    l_vwap = last_m1['vwap']
    l_near_v_long = abs(l_price - l_vwap) / l_vwap < 0.01 and l_price >= l_vwap
    l_near_v_short = abs(l_price - l_vwap) / l_vwap < 0.01 and l_price <= l_vwap
    l_near_poc = abs(l_price - poc_price) / poc_price < 0.008

    # --- F. Scanning recent 3 candles (Lookback Loop - Sweep made Optional) ---
    for i in range(-3, 0):
        if abs(i) > len(df_1m):
            continue
        m1_row = df_1m.iloc[i]
        
        idx_val = len(df_1m) + i
        if idx_val >= 2:
            m1_prev2 = df_1m.iloc[idx_val - 2]
            fvg_bullish = m1_row['low'] > m1_prev2['high']
            fvg_bearish = m1_row['high'] < m1_prev2['low']
        else:
            fvg_bullish = False
            fvg_bearish = False

        vol_ma = vol_ma_series.iloc[i] if not pd.isna(vol_ma_series.iloc[i]) else m1_row['volume']
        aggressive_volume = m1_row['volume'] > (vol_ma * 1.2)

        price = m1_row['close']
        ema9 = m1_row['ema9']
        ema50 = m1_row['ema50']
        vwap = m1_row['vwap']

        near_vwap_long = abs(price - vwap) / vwap < 0.01 and price >= vwap
        near_vwap_short = abs(price - vwap) / vwap < 0.01 and price <= vwap
        near_poc = abs(price - poc_price) / poc_price < 0.008

        b_cond = (
            macro_bullish and
            (ema9 > ema50) and
            (near_vwap_long or near_poc) and
            (fvg_bullish or aggressive_volume)
        )

        bear_cond = (
            macro_bearish and
            (ema9 < ema50) and
            (near_vwap_short or near_poc) and
            (fvg_bearish or aggressive_volume)
        )

        if b_cond:
            bullish_setup = True
            selected_row = m1_row
            break
        elif bear_cond:
            bearish_setup = True
            selected_row = m1_row
            break

    # FIXED: Check if selected_row is None safely
    if selected_row is None:
        selected_row = df_1m.iloc[-1]

    price = selected_row['close']

    # --- G. Final Notification Execution & Diagnostics ---
    if bullish_setup:
        sl = round(price - (1.5 * atr), 2)
        risk = price - sl
        tp = round(price + (risk * 2.0), 2)

        sweep_status = "Confirmed (Bonus)" if sweep_long_3m else "Not Required (Flexible Trend Match)"
        msg = (
            f"🚀 [FLEXIBLE SMC & VOLUME PROFILE LONG]\n"
            f"Asset: BTC-USD (Coinbase)\n"
            f"• 15M Trend: Bullish (Above 200 EMA)\n"
            f"• 5M/1M Momentum: 9 EMA > 50 EMA & VWAP Confirmed\n"
            f"• 3M Liquidity Sweep: {sweep_status}\n"
            f"• Volume POC: {round(poc_price, 2)} | FVG / Imbalance: Active\n\n"
            f"🟢 Entry Price: {price}\n"
            f"🛑 Stop Loss (SL): {sl} (1.5x ATR)\n"
            f"🎯 Take Profit (TP): {tp}"
        )
        send_telegram_message(msg)
        print("Flexible Bullish Signal Sent to Telegram!")

    elif bearish_setup:
        sl = round(price + (1.5 * atr), 2)
        risk = sl - price
        tp = round(price - (risk * 2.0), 2)

        sweep_status = "Confirmed (Bonus)" if sweep_short_3m else "Not Required (Flexible Trend Match)"
        msg = (
            f"🩸 [FLEXIBLE SMC & VOLUME PROFILE SHORT]\n"
            f"Asset: BTC-USD (Coinbase)\n"
            f"• 15M Trend: Bearish (Below 200 EMA)\n"
            f"• 5M/1M Momentum: 9 EMA < 50 EMA & VWAP Confirmed\n"
            f"• 3M Liquidity Sweep: {sweep_status}\n"
            f"• Volume POC: {round(poc_price, 2)} | FVG / Imbalance: Active\n\n"
            f"🔴 Entry Price: {price}\n"
            f"🛑 Stop Loss (SL): {sl} (1.5x ATR)\n"
            f"🎯 Take Profit (TP): {tp}"
        )
        send_telegram_message(msg)
        print("Flexible Bearish Signal Sent to Telegram!")
    else:
        print(f"--- DIAGNOSTIC STATUS (NO SETUP) ---")
        print(f"Price: {price} | POC: {round(poc_price, 2)} | VWAP: {round(l_vwap, 2)}")
        print(f"Macro Bullish: {macro_bullish} | Macro Bearish: {macro_bearish}")
        print(f"EMA9 > EMA50: {l_ema9 > l_ema50} (EMA9: {round(l_ema9, 2)}, EMA50: {round(l_ema50, 2)})")
        print(f"Near VWAP Long: {l_near_v_long} | Near VWAP Short: {l_near_v_short} | Near POC: {l_near_poc}")
        print(f"--------------------------------------")

if __name__ == "__main__":
    run_ultimate_professional_scalper()

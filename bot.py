import os
import requests
import pandas as pd
import numpy as np

# Telegram Credentials
TELEGRAM_BOT_TOKEN = '8662975391:AAG86xC9Dcx-Ec9ljKveAnnNqtk4jqDkdr0'
TELEGRAM_CHAT_ID = '7500472109'

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing!")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"Telegram Error Response: {response.text}")
            return False
    except Exception as e:
        print(f"Error sending telegram message: {e}")
        return False

def fetch_coinbase_klines(product_id="BTC-USD", granularity=60):
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity={granularity}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if not isinstance(data, list) or len(data) == 0:
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

def run_multi_timeframe_scalper():
    print("Scanning BTC-USD with 4-Timeframe Confluence (15M + 5M + 3M + 1M)...")
    
    # Base 1M data fetch
    df_1m = fetch_coinbase_klines("BTC-USD", granularity=60)
    if df_1m.empty or len(df_1m) < 200:
        print("Insufficient 1M market data!")
        return

    # Resample 1M to 3M, 5M and 15M locally to ensure perfect synchronization
    df_1m.set_index('time', inplace=True)
    
    df_3m = df_1m.resample('3min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna().reset_index()

    df_5m = df_1m.resample('5min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna().reset_index()

    df_15m = df_1m.resample('15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna().reset_index()
    
    df_1m = df_1m.reset_index()

    if len(df_15m) < 50 or len(df_5m) < 50:
        print("Insufficient aggregated timeframe data!")
        return

    # --- 1. 15M Macro Trend Filter ---
    df_15m['ema200'] = df_15m['close'].ewm(span=200).mean()
    htf_15m = df_15m.iloc[-1]
    macro_bullish = htf_15m['close'] > htf_15m['ema200']
    macro_bearish = htf_15m['close'] < htf_15m['ema200']

    # --- 2. 5M Confirmation Filter ---
    df_5m['ema9'] = df_5m['close'].ewm(span=9).mean()
    df_5m['ema50'] = df_5m['close'].ewm(span=50).mean()
    htf_5m = df_5m.iloc[-1]
    conf_bullish = htf_5m['ema9'] > htf_5m['ema50']
    conf_bearish = htf_5m['ema9'] < htf_5m['ema50']

    # --- 3. 1M / 3M Execution & Momentum ---
    df_1m['ema9'] = df_1m['close'].ewm(span=9).mean()
    df_1m['ema50'] = df_1m['close'].ewm(span=50).mean()
    
    typical_price = (df_1m['high'] + df_1m['low'] + df_1m['close']) / 3
    df_1m['vwap'] = (typical_price * df_1m['volume']).cumsum() / df_1m['volume'].cumsum()

    recent_resistance = df_1m['high'].tail(50).max()
    current_price = df_1m['close'].iloc[-1]
    near_resistance = (recent_resistance - current_price) / current_price < 0.0015

    # ATR Risk Tool (1M)
    high_low = df_1m['high'] - df_1m['low']
    high_close = np.abs(df_1m['high'] - df_1m['close'].shift())
    low_close = np.abs(df_1m['low'] - df_1m['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]

    vol_ma_series = df_1m['volume'].rolling(window=20).mean()

    bullish_setup = False
    bearish_setup = False
    selected_row = None

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
        aggressive_volume = m1_row['volume'] > (vol_ma * 1.5)

        price = m1_row['close']
        ema9 = m1_row['ema9']
        ema50 = m1_row['ema50']
        vwap = m1_row['vwap']

        near_vwap_long = abs(price - vwap) / vwap < 0.01 and price >= vwap
        near_vwap_short = abs(price - vwap) / vwap < 0.01 and price <= vwap

        skip_long_due_to_resistance = near_resistance and not aggressive_volume

        # 4-Timeframe Confluence Conditions Combined
        b_cond = (
            macro_bullish and             # 15M Trend Check
            conf_bullish and              # 5M Confirmation Check
            (ema9 > ema50) and            # 1M/3M Momentum Check
            near_vwap_long and
            (fvg_bullish or aggressive_volume) and
            not skip_long_due_to_resistance
        )

        bear_cond = (
            macro_bearish and             # 15M Trend Check
            conf_bearish and              # 5M Confirmation Check
            (ema9 < ema50) and            # 1M/3M Momentum Check
            near_vwap_short and
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

    if selected_row is None:
        print("No valid 4-Timeframe confluence setup found.")
        return

    price = selected_row['close']

    if bullish_setup:
        sl = round(price - (1.5 * atr), 2)
        risk = price - sl
        tp = round(price + (risk * 2.0), 2) # Maintaining 1:2 R:R for high win rate

        msg = (
            f"🚀 [4-TIMEFRAME CONFLUENCE LONG]\n"
            f"Asset: BTC-USD (Coinbase)\n"
            f"• 15M Macro Trend: Bullish (Above 200 EMA)\n"
            f"• 5M Confirmation: EMA 9 > 50 Confirmed\n"
            f"• 1M/3M Execution: VWAP, FVG & Volume Supported\n\n"
            f"🟢 Entry Price: {price}\n"
            f"🛑 Stop Loss (SL): {sl} (1.5x ATR)\n"
            f"🎯 Take Profit (TP): {tp} (1:2 Ratio)"
        )
        if send_telegram_message(msg):
            print("4M Confluence Bullish Signal Sent Successfully!")

    elif bearish_setup:
        sl = round(price + (1.5 * atr), 2)
        risk = sl - price
        tp = round(price - (risk * 2.0), 2)

        msg = (
            f"🩸 [4-TIMEFRAME CONFLUENCE SHORT]\n"
            f"Asset: BTC-USD (Coinbase)\n"
            f"• 15M Macro Trend: Bearish (Below 200 EMA)\n"
            f"• 5M Confirmation: EMA 9 < 50 Confirmed\n"
            f"• 1M/3M Execution: VWAP & Momentum Confirmed\n\n"
            f"🔴 Entry Price: {price}\n"
            f"🛑 Stop Loss (SL): {sl} (1.5x ATR)\n"
            f"🎯 Take Profit (TP): {tp} (1:2 Ratio)"
        )
        if send_telegram_message(msg):
            print("4M Confluence Bearish Signal Sent Successfully!")

if __name__ == "__main__":
    run_multi_timeframe_scalper()

import os
import requests
import pandas as pd
import numpy as np

# Telegram Configurations (GitHub Secrets से ऑटोमैटिक कनेक्ट होगा)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    if not TOKEN or not CHAT_ID:
        print("Telegram Secrets not found!")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Error sending telegram: {e}")

def fetch_binance_data(symbol="BTCUSDT", interval="5m", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url)
        data = response.json()
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['open'] = df['open'].astype(float)
        df['volume'] = df['volume'].astype(float)
        return df
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

def calculate_indicators(df):
    # 1. Fast EMAs for Scalping
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()

    # 2. VWAP Calculation (Noise Filter)
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    df['vwap'] = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()

    # 3. Fast RSI (Period 7)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=7).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=7).mean()
    rs = gain / loss
    df['rsi_7'] = 100 - (100 / (1 + rs))

    # 4. Volume Moving Average (Volume Spike Filter)
    df['vol_ma'] = df['volume'].rolling(window=20).mean()

    return df

def analyze_scalp_market():
    df = fetch_binance_data("BTCUSDT", "5m", 100)
    if df is None or len(df) < 30:
        print("Data insufficient.")
        return

    df = calculate_indicators(df)
    
    # Current and previous candles data
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    current_price = curr['close']
    ema9 = curr['ema_9']
    ema21 = curr['ema_21']
    vwap = curr['vwap']
    rsi = curr['rsi_7']
    vol = curr['volume']
    vol_ma = curr['vol_ma']

    # --- NOISE & FAKE ENTRY FILTERS ---
    # Condition A: Volume must be higher than average (Volume Spike)
    is_volume_valid = vol > (vol_ma * 1.2)

    # LONG SETUP: EMA 9 crosses above EMA 21, Price > VWAP, RSI healthy, Volume Spike active
    is_long_signal = (
        (prev['ema_9'] <= prev['ema_21']) and (ema9 > ema21) and
        (current_price > vwap) and
        (45 < rsi < 75) and
        is_volume_valid
    )

    # SHORT SETUP: EMA 9 crosses below EMA 21, Price < VWAP, RSI healthy, Volume Spike active
    is_short_signal = (
        (prev['ema_9'] >= prev['ema_21']) and (ema9 < ema21) and
        (current_price < vwap) and
        (25 < rsi < 55) and
        is_volume_valid
    )

    if is_long_signal:
        sl = round(current_price - (current_price * 0.003), 2)  # Tight SL ~0.3%
        tp1 = round(current_price + (current_price * 0.004), 2) # TP1 ~0.4%
        tp2 = round(current_price + (current_price * 0.008), 2) # TP2 ~0.8%
        
        msg = (
            f"⚡ **[SMC SCALPING ALERT - LONG]** ⚡\n\n"
            f"🪙 **Pair:** BTC/USDT (5m Chart)\n"
            f"📈 **Action:** BUY / LONG\n"
            f"💵 **Entry Price Zone:** `{current_price}`\n"
            f"🛡️ **Stop Loss (SL):** `{sl}`\n"
            f"🎯 **Target 1 (TP1):** `{tp1}`\n"
            f"🎯 **Target 2 (TP2):** `{tp2}`\n\n"
            f"📊 *Filters Passed:* VWAP Confirmed, EMA Cross, Volume Spike! 🚀"
        )
        send_telegram_message(msg)
        print("Long Scalp Signal Sent!")

    elif is_short_signal:
        sl = round(current_price + (current_price * 0.003), 2)
        tp1 = round(current_price - (current_price * 0.004), 2)
        tp2 = round(current_price - (current_price * 0.008), 2)
        
        msg = (
            f"⚡ **[SMC SCALPING ALERT - SHORT]** ⚡\n\n"
            f"🪙 **Pair:** BTC/USDT (5m Chart)\n"
            f"📉 **Action:** SELL / SHORT\n"
            f"💵 **Entry Price Zone:** `{current_price}`\n"
            f"🛡️ **Stop Loss (SL):** `{sl}`\n"
            f"🎯 **Target 1 (TP1):** `{tp1}`\n"
            f"🎯 **Target 2 (TP2):** `{tp2}`\n\n"
            f"📊 *Filters Passed:* VWAP Confirmed, EMA Cross, Volume Spike! 📉"
        )
        send_telegram_message(msg)
        print("Short Scalp Signal Sent!")
    else:
        print("Market is noisy or no strong setup. Skipping...")

if __name__ == "__main__":
    analyze_scalp_market()

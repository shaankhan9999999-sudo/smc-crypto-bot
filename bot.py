import os
import requests
import pandas as pd
import numpy as np

# Telegram Credentials (सीधे यहाँ पर पक्के तौर पर सेट हैं ताकि 'Credentials not found' एरर कभी न आए)
TELEGRAM_BOT_TOKEN = '8662975391:AAG86xC9Dcx-Ec9ljKveAnnNqtk4jqDkdrO'
TELEGRAM_CHAT_ID = '7500472109'

def send_telegram_message(message):
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not found!")
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
            print("Coinbase API error or empty data")
            return pd.DataFrame()
        
        # Coinbase format: [time, low, high, open, close, volume]
        df = pd.DataFrame(data, columns=['time', 'low', 'high', 'open', 'close', 'volume'])
        df = df.iloc[::-1].reset_index(drop=True)
        
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
            
        return df
    except Exception as e:
        print(f"Error fetching Coinbase data: {e}")
        return pd.DataFrame()

def calculate_scalp_indicators(df):
    if df.empty:
        return df
    
    # Indicators
    df['ema9'] = df['close'].ewm(span=9).mean()
    df['ema21'] = df['close'].ewm(span=21).mean()
    
    # VWAP Calculation
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    df['vwap'] = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
    
    # RSI (7)
    delta = df['close'].diff()
    gain7 = (delta.where(delta > 0, 0)).rolling(window=7).mean()
    loss7 = (-delta.where(delta < 0, 0)).rolling(window=7).mean()
    rs7 = gain7 / loss7
    df['rsi7'] = 100 - (100 / (1 + rs7))
    
    # Volume Spike
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    df['vol_spike'] = df['volume'] > (df['vol_ma'] * 1.5)
    
    return df

def run_scalping_bot():
    print("Scanning BTC/USDT on Coinbase (Flexible Multi-Indicator Scalping Mode)...")
    
    df_5m = fetch_coinbase_klines("BTC-USD", granularity=300)
    df_1m = fetch_coinbase_klines("BTC-USD", granularity=60)

    if df_5m is None or df_5m.empty or df_1m is None or df_1m.empty or len(df_5m) < 21 or len(df_1m) < 21:
        print("Market data is empty or insufficient!")
        return

    df_5m = calculate_scalp_indicators(df_5m)
    df_1m = calculate_scalp_indicators(df_1m)

    m5_last = df_5m.iloc[-1]
    m1_last = df_1m.iloc[-1]

    price = m1_last['close']
    vwap = m1_last['vwap']
    ema9 = m1_last['ema9']
    vol_spike = m1_last['vol_spike']

    print(f"Price: {price:.2f} | VWAP: {vwap:.2f} | EMA9: {ema9:.2f} | Volume Spike: {vol_spike}")

    # Trend and Strategy Conditions
    trend_bullish = m5_last['ema9'] > m5_last['ema21']
    trend_bearish = m5_last['ema9'] < m5_last['ema21']

    bullish_scalp = (
        trend_bullish and
        (m1_last['ema9'] > m1_last['ema21']) and
        (35 < m1_last['rsi7'] < 75) and
        (price > vwap) and
        vol_spike
    )

    bearish_scalp = (
        trend_bearish and
        (m1_last['ema9'] < m1_last['ema21']) and
        (25 < m1_last['rsi7'] < 65) and
        (price < vwap) and
        vol_spike
    )

    if bullish_scalp:
        msg = (
            f"⚡ [SCALP LONG SIGNAL - COINBASE]\n"
            f"Asset: BTC-USD\n"
            f"Entry Price: {price}\n"
            f"VWAP: {round(vwap, 2)}\n"
            f"EMA(9): {round(ema9, 2)}\n"
            f"RSI(7): {round(m1_last['rsi7'], 2)}\n"
            f"Volume Spike: True\n"
            f"Action: High-Probability Scalp Long"
        )
        send_telegram_message(msg)
        print("Bullish Scalping Signal Sent!")

    elif bearish_scalp:
        msg = (
            f"⚡ [SCALP SHORT SIGNAL - COINBASE]\n"
            f"Asset: BTC-USD\n"
            f"Entry Price: {price}\n"
            f"VWAP: {round(vwap, 2)}\n"
            f"EMA(9): {round(ema9, 2)}\n"
            f"RSI(7): {round(m1_last['rsi7'], 2)}\n"
            f"Volume Spike: True\n"
            f"Action: High-Probability Scalp Short"
        )
        send_telegram_message(msg)
        print("Bearish Scalping Signal Sent!")
    else:
        print("No actionable setup found in this run.")

if __name__ == "__main__":
    run_scalping_bot()

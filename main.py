import os
import requests
import pandas as pd
import numpy as np

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Error sending telegram message: {e}")

def fetch_binance_klines(symbol="BTCUSDT", interval="1h", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    response = requests.get(url)
    data = response.json()
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    return df

def calculate_indicators(df):
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # RSI 7 & 14
    delta = df['close'].diff()
    gain14 = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss14 = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs14 = gain14 / loss14
    df['rsi14'] = 100 - (100 / (1 + rs14))
    
    gain7 = (delta.where(delta > 0, 0)).rolling(window=7).mean()
    loss7 = (-delta.where(delta < 0, 0)).rolling(window=7).mean()
    rs7 = gain7 / loss7
    df['rsi7'] = 100 - (100 / (1 + rs7))
    
    # Bollinger Bands
    df['bb_mid'] = df['close'].rolling(window=20).mean()
    df['bb_std'] = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_mid'] + (df['bb_std'] * 2)
    df['bb_lower'] = df['bb_mid'] - (df['bb_std'] * 2)
    
    # Volume Spike (1.15x)
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    df['vol_spike'] = df['volume'] > (df['vol_ma'] * 1.15)
    
    return df

def run_swing_bot():
    df_1h = fetch_binance_klines("BTCUSDT", "1h", 50)
    df_15m = fetch_binance_klines("BTCUSDT", "15m", 50)
    df_5m = fetch_binance_klines("BTCUSDT", "5m", 100)
    
    df_1h = calculate_indicators(df_1h)
    df_15m = calculate_indicators(df_15m)
    df_5m = calculate_indicators(df_5m)
    
    h1_last = df_1h.iloc[-1]
    m15_last = df_15m.iloc[-1]
    m5_last = df_5m.iloc[-1]
    
    price = m5_last['close']
    
    macro_bullish = h1_last['ema9'] > h1_last['ema21']
    macro_bearish = h1_last['ema9'] < h1_last['ema21']
    
    struct_bullish = m15_last['ema9'] > m15_last['ema21']
    struct_bearish = m15_last['ema9'] < m15_last['ema21']
    
    # Flexible 3-candle Liquidity Sweep window
    recent_low_5m = df_5m['low'].tail(20).min()
    recent_high_5m = df_5m['high'].tail(20).max()
    
    wick_low_3 = df_5m['low'].tail(3).min()
    wick_high_3 = df_5m['high'].tail(3).max()
    
    liquidity_sweep_long = (wick_low_3 < recent_low_5m) and (price > recent_low_5m)
    liquidity_sweep_short = (wick_high_3 > recent_high_5m) and (price < recent_high_5m)
    
    bullish_confluence = (
        macro_bullish and 
        struct_bullish and 
        (m5_last['ema9'] > m5_last['ema21']) and
        (40 <= m5_last['rsi7'] <= 70) and
        (liquidity_sweep_long or m5_last['vol_spike'])
    )
    
    bearish_confluence = (
        macro_bearish and 
        struct_bearish and 
        (m5_last['ema9'] < m5_last['ema21']) and
        (30 <= m5_last['rsi7'] <= 60) and
        (liquidity_sweep_short or m5_last['vol_spike'])
    )
    
    if bullish_confluence:
        msg = f"🚀 *[SWING BULLISH SIGNAL]* 🚀\n\nAsset: BTC/USDT\nPrice: {price}\nRSI(7): {round(m5_last['rsi7'], 2)}\nAction: Long Opportunity Detected via SMC & Volume/Sweep!"
        send_telegram_message(msg)
        print("Swing Bullish alert sent.")
    elif bearish_confluence:
        msg = f"📉 *[SWING BEARISH SIGNAL]* 📉\n\nAsset: BTC/USDT\nPrice: {price}\nRSI(7): {round(m5_last['rsi7'], 2)}\nAction: Short Opportunity Detected via SMC & Volume/Sweep!"
        send_telegram_message(msg)
        print("Swing Bearish alert sent.")
    else:
        print("Swing market scanned: No clear confluence yet.")

if __name__ == "__main__":
    run_swing_bot()

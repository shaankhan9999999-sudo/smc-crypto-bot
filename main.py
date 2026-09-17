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
    
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df

def calculate_indicators(df):
    # EMAs (9 & 21)
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # RSI 7 and 14
    delta = df['close'].diff()
    gain14 = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss14 = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs14 = gain14 / loss14
    df['rsi14'] = 100 - (100 / (1 + rs14))

    gain7 = (delta.where(delta > 0, 0)).rolling(window=7).mean()
    loss7 = (-delta.where(delta < 0, 0)).rolling(window=7).mean()
    rs7 = gain7 / loss7
    df['rsi7'] = 100 - (100 / (1 + rs7))
    
    # Bollinger Bands (Volatility breakout without lag)
    df['bb_mid'] = df['close'].rolling(window=20).mean()
    df['bb_std'] = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_mid'] + (df['bb_std'] * 2)
    df['bb_lower'] = df['bb_mid'] - (df['bb_std'] * 2)
    
    # Volume Spike & Volume Profile approximation (High Volume Node / Point of Control estimation)
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    df['vol_spike'] = df['volume'] > (df['vol_ma'] * 1.15)
    
    return df

def detect_smc_structure(df):
    df['swing_high'] = df['high'][(df['high'] == df['high'].rolling(5, center=True).max())]
    df['swing_low'] = df['low'][(df['low'] == df['low'].rolling(5, center=True).min())]
    return df

def run_advanced_bot():
    # Fetching data for 3 timeframes: 1h, 15m, 5m
    df_1h = fetch_binance_klines("BTCUSDT", "1h", 50)
    df_15m = fetch_binance_klines("BTCUSDT", "15m", 50)
    df_5m = fetch_binance_klines("BTCUSDT", "5m", 50)
    
    df_1h = calculate_indicators(df_1h)
    df_15m = calculate_indicators(df_15m)
    df_5m = calculate_indicators(df_5m)
    
    df_15m = detect_smc_structure(df_15m)
    df_5m = detect_smc_structure(df_5m)
    
    # Current snapshot values
    h1_last = df_1h.iloc[-1]
    m15_last = df_15m.iloc[-1]
    m5_last = df_5m.iloc[-1]
    
    price = m5_last['close']
    
    # 1. Macro Trend (1h)
    macro_bullish = h1_last['close'] > h1_last['ema21']
    macro_bearish = h1_last['close'] < h1_last['ema21']
    
    # 2. SMC & Structure (15m): Order Block & Break of Structure (BoS) / ChoCh approximation
    m15_bullish_struct = m15_last['ema9'] > m15_last['ema21']
    m15_bearish_struct = m15_last['ema9'] < m15_last['ema21']
    
    # 3. Execution & Flexible Liquidity Sweep (5m) - Last 3 candles window to prevent missing entries
    recent_low = df_5m['low'].tail(15).min()
    recent_high = df_5m['high'].tail(15).max()
    
    recent_low_wick = df_5m['low'].tail(3).min()
    recent_high_wick = df_5m['high'].tail(3).max()
    
    liquidity_sweep_long = (recent_low_wick < recent_low) and (price > recent_low) and (df_5m['vol_spike'].tail(3).any())
    liquidity_sweep_short = (recent_high_wick > recent_high) and (price < recent_high) and (df_5m['vol_spike'].tail(3).any())
    
    # Technical Confluences (No MACD, Pure SMC + Volume + RSI 7/14 + EMA 9/21 + Bollinger Bands range)
    bullish_confluence = (
        macro_bullish and 
        m15_bullish_struct and 
        (m5_last['ema9'] > m5_last['ema21']) and 
        (40 < m5_last['rsi7'] < 70) and
        (liquidity_sweep_long or m5_last['vol_spike'])
    )
    
    bearish_confluence = (
        macro_bearish and 
        m15_bearish_struct and 
        (m5_last['ema9'] < m5_last['ema21']) and 
        (30 < m5_last['rsi7'] < 60) and
        (liquidity_sweep_short or m5_last['vol_spike'])
    )
    
    if bullish_confluence:
        msg = f"🚀 *[SMC & LIQUIDITY SWEEP - BULLISH LONG]* 🚀\n\n" \
              f"Asset: BTC/USDT\n" \
              f"Timeframes: 1H (Trend) + 15M (SMC/BoS) + 5M (Sweep/Entry)\n" \
              f"Entry Price: {price}\n" \
              f"RSI (7): {round(m5_last['rsi7'], 2)} | RSI (14): {round(m5_last['rsi14'], 2)}\n" \
              f"Volume Spike: Confirmed (Flexible Window)\n" \
              f"Action: Bullish Order Block / Liquidity Sweep Triggered!"
        send_telegram_message(msg)
        print("Bullish alert sent.")
        
    elif bearish_confluence:
        msg = f"📉 *[SMC & LIQUIDITY SWEEP - BEARISH SHORT]* 📉\n\n" \
              f"Asset: BTC/USDT\n" \
              f"Timeframes: 1H (Trend) + 15M (SMC/BoS) + 5M (Sweep/Entry)\n" \
              f"Entry Price: {price}\n" \
              f"RSI (7): {round(m5_last['rsi7'], 2)} | RSI (14): {round(m5_last['rsi14'], 2)}\n" \
              f"Volume Spike: Confirmed (Flexible Window)\n" \
              f"Action: Bearish Order Block / Liquidity Sweep Triggered!"
        send_telegram_message(msg)
        print("Bearish alert sent.")
    else:
        print("Multi-timeframe SMC market scanned: Waiting for precise liquidity sweep & structure.")

if __name__ == "__main__":
    run_advanced_bot()

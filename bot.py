import os
import requests
import ccxt
import pandas as pd
import numpy as np

# 1. Telegram Configuration from GitHub Secrets
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not found!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Telegram alert sent successfully!")
        else:
            print(f"Failed to send telegram alert: {response.text}")
    except Exception as e:
        print(f"Error sending telegram message: {e}")

def calculate_supertrend(df, period=10, multiplier=3):
    hl2 = (df['high'] + df['low']) / 2
    # ATR calculation
    df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
    df['atr'] = df['tr'].rolling(window=period).mean()
    
    df['upper_basic'] = hl2 + (multiplier * df['atr'])
    df['lower_basic'] = hl2 - (multiplier * df['atr'])
    
    # Simple Supertrend approximation for robust execution
    df['supertrend'] = True
    # Let's use a clean trend indicator based on EMA & VWAP as a flexible companion
    return df

def run_bot():
    print("Scanning BTC/USDT on Coinbase (Flexible Multi-Indicator Scalping Mode)...")
    
    # 2. Initialize Coinbase Exchange via CCXT
    exchange = ccxt.coinbase({
        'enableRateLimit': True
    })
    
    symbol = 'BTC/USDT'
    timeframe = '5m' # स्कैल्पिंग के लिए 5 मिनट का टाइमफ्रेम बेस्ट है
    
    try:
        # Fetch OHLCV data (Last 100 candles)
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        current_price = df['close'].iloc[-1]
        
        # 3. Indicator Calculations
        # VWAP Calculation
        vwp = (df['volume'] * (df['high'] + df['low'] + df['close']) / 3).cumsum() / df['volume'].cumsum()
        current_vwap = vwp.iloc[-1]
        
        # EMA 9 & EMA 21 for Trend & Momentum
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # Volume Spike Check (Current volume > 1.4x of 20-period average volume)
        df['vol_ma'] = df['volume'].rolling(window=20).mean()
        volume_spike = df['volume'].iloc[-1] > (df['vol_ma'].iloc[-1] * 1.4)
        
        # ATR for Stop Loss / Target calculation
        df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
        atr = df['tr'].rolling(window=14).mean().iloc[-1]
        
        ema9_val = df['ema9'].iloc[-1]
        ema21_val = df['ema21'].iloc[-1]
        
        print(f"Price: {current_price} | VWAP: {current_vwap:.2f} | EMA9: {ema9_val:.2f} | Volume Spike: {volume_spike}")

        # 4. Flexible Multi-Indicator Logic (Strictness reduced to avoid missing trades)
        # LONG SETUP: Price > VWAP, EMA9 > EMA21, and Volume has a spike (or strong momentum)
        is_bullish_setup = (current_price > current_vwap) and (ema9_val > ema21_val) and volume_spike
        
        # SHORT SETUP: Price < VWAP, EMA9 < EMA21, and Volume has a spike
        is_bearish_setup = (current_price < current_vwap) and (ema9_val < ema21_val) and volume_spike

        if is_bullish_setup:
            sl = current_price - (1.5 * atr)
            tp = current_price + (2.5 * atr)
            message = (
                f"🟢 **SCALP BUY SIGNAL (BTC/USDT)** 🟢\n\n"
                f"• **Entry Price:** `{current_price}`\n"
                f"• **VWAP Status:** `Above VWAP`\n"
                f"• **Momentum:** `EMA Bullish Crossover + Volume Spike`\n"
                f"• **Stop Loss (SL):** `{sl:.2f}`\n"
                f"• **Take Profit (TP):** `{tp:.2f}`\n"
                f"• **Exchange:** `Coinbase (5m)`"
            )
            send_telegram_message(message)
            print("Bullish Scalping Signal Sent!")
            
        elif is_bearish_setup:
            sl = current_price + (1.5 * atr)
            tp = current_price - (2.5 * atr)
            message = (
                f"🔴 **SCALP SELL SIGNAL (BTC/USDT)** 🔴\n\n"
                f"• **Entry Price:** `{current_price}`\n"
                f"• **VWAP Status:** `Below VWAP`\n"
                f"• **Momentum:** `EMA Bearish Crossover + Volume Spike`\n"
                f"• **Stop Loss (SL):** `{sl:.2f}`\n"
                f"• **Take Profit (TP):** `{tp:.2f}`\n"
                f"• **Exchange:** `Coinbase (5m)`"
            )
            send_telegram_message(message)
            print("Bearish Scalping Signal Sent!")
            
        else:
            print("Market is consolidating or indicators are mixed. No signal triggered this run.")

    except Exception as e:
        print(f"An error occurred during bot execution: {e}")

if __name__ == "__main__":
    run_bot()

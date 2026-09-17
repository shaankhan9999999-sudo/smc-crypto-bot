import os
import requests
import pandas as pd
import numpy as np

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8662975391:AAG86xC9Dcx-Ec9ljKveAnnNqtk4jqDkdrO')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7500472109')

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing!")
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

def fetch_coinbase_klines(product_id="BTC-USD", granularity=60):
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity={granularity}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if not isinstance(data, list) or len(data) == 0:
            print("Coinbase API error or empty data")
            return pd.DataFrame()
        
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
    df['ema9'] = df['close'].ewm(span=9).mean()
    df['ema21'] = df['close'].ewm(span=21).mean()
    
    delta = df['close'].diff()
    gain7 = (delta.where(delta > 0, 0)).rolling(window=7).mean()
    loss7 = (-delta.where(delta < 0, 0)).rolling(window=7).mean()
    rs7 = gain7 / loss7
    df['rsi7'] = 100 - (100 / (1 + rs7))
    
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    df['vol_spike'] = df['volume'] > (df['vol_ma'] * 1.5)
    return df

def run_scalping_bot():
    df_5m = fetch_coinbase_klines("BTC-USD", granularity=300)
    df_1m = fetch_coinbase_klines("BTC-USD", granularity=60)

    if df_5m is None or df_5m.empty or df_1m is None or df_1m.empty or len(df_5m) < 21 or len(df_1m) < 21:
        print("Market data is empty or insufficient!")
        send_telegram_message("🤖 *[SCALP BOT]*\nWarning: Market data from Coinbase is empty or insufficient.")
        return

    df_5m = calculate_scalp_indicators(df_5m)
    df_1m = calculate_scalp_indicators(df_1m)

    m5_last = df_5m.iloc[-1]
    m1_last = df_1m.iloc[-1]

    price = m1_last['close']

    trend_bullish = m5_last['ema9'] > m5_last['ema21']
    trend_bearish = m5_last['ema9'] < m5_last['ema21']

    recent_low_1m = df_1m['low'].tail(20)
    recent_high_1m = df_1m['high'].tail(20)

    recent_low_wick = df_1m['low'].tail(3).min()
    recent_high_wick = df_1m['high'].tail(3).max()

    sweep_long = recent_low_wick < recent_low_1m.iloc[:-3].min()
    sweep_short = recent_high_wick > recent_high_1m.iloc[:-3].max()

    bullish_scalp = (
        trend_bullish and
        (m1_last['ema9'] > m1_last['ema21']) and
        (35 < m1_last['rsi7'] < 75) and
        sweep_long and
        m1_last['vol_spike']
    )

    bearish_scalp = (
        trend_bearish and
        (m1_last['ema9'] < m1_last['ema21']) and
        (25 < m1_last['rsi7'] < 65) and
        sweep_short and
        m1_last['vol_spike']
    )

    if bullish_scalp:
        sl = round(recent_low_wick - 4, 2)
        risk = price - sl
        tp = round(price + (risk * 1.8), 2)

        msg = (
            f"⚡ *[SCALP LONG - 1M LIQUIDITY SWEEP]*\n"
            f"Asset: BTC-USD (Coinbase)\n"
            f"Entry Price: {price}\n"
            f"🛑 Stop Loss (SL): {sl}\n"
            f"🎯 Take Profit (TP): {tp}\n"
            f"📊 RSI(7): {round(m1_last['rsi7'], 2)}\n"
            f"💡 Action: High-Probability Scalp Long Trade"
        )
        send_telegram_message(msg)
        print("Scalp Long alert sent.")

    elif bearish_scalp:
        sl = round(recent_high_wick + 4, 2)
        risk = sl - price
        tp = round(price - (risk * 1.8), 2)

        msg = (
            f"⚡ *[SCALP SHORT - 1M LIQUIDITY SWEEP]*\n"
            f"Asset: BTC-USD (Coinbase)\n"
            f"Entry Price: {price}\n"
            f"🛑 Stop Loss (SL): {sl}\n"
            f"🎯 Take Profit (TP): {tp}\n"
            f"📊 RSI(7): {round(m1_last['rsi7'], 2)}\n"
            f"💡 Action: High-Probability Scalp Short Trade"
        )
        send_telegram_message(msg)
        print("Scalp Short alert sent.")
    else:
        msg = (
            f"🤖 *[SCALP BOT STATUS]*\n"
            f"Market Scanned (Coinbase): No trade setup found.\n"
            f"Current BTC Price: {price}\n"
            f"Status: Waiting for clear setup..."
        )
        send_telegram_message(msg)
        print("Scalp market scanned: Status message sent to Telegram.")

if __name__ == "__main__":
    run_scalping_bot()

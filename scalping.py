import subprocess
import sys


# यह कोड खुद चेक करेगा और जरूरी लाइब्रेरीज अपने आप इंस्टॉल कर लेगा
def install_packages():
  required = {'ccxt', 'pandas', 'requests', 'numpy'}
  for pkg in required:
    try:
      __import__(pkg)
    except ImportError:
      print(f'Installing missing package: {pkg}...')
      subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])


install_packages()

import time
import ccxt
import numpy as np
import pandas as pd
import requests

# आपकी टेलीग्राम क्रेडेंशियल्स (बोट टोकन और चैट आईडी)
TELEGRAM_BOT_TOKEN = '8662975391:AAG86xC9Dcx-Ec9ljKveAnnNqtk4jqDkdrO'
TELEGRAM_CHAT_ID = '7500472109'

# Coinbase Exchange Setup (Public Data - No Restriction)
exchange = ccxt.coinbase({
    'enableRateLimit': True,
})

SYMBOL = 'BTC/USDT'
TIMEFRAME_FAST = '1m'  # तुरंत एंट्री के लिए 1-मिनट कैंडल
TIMEFRAME_CONF = '5m'  # ट्रेंड कन्फर्मेशन के लिए 5-मिनट कैंडल


def send_telegram_message(message):
  url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
  payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
  try:
    response = requests.post(url, json=payload, timeout=10)
    if not response.ok:
      print(f'Telegram Error: {response.text}')
  except Exception as e:
    print(f'Failed to send telegram message: {e}')


def fetch_data(symbol, timeframe, limit=100):
  try:
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(
        ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
    )
    return df
  except Exception as e:
    print(f'Error fetching data for {symbol}: {e}')
    return None


def calculate_volume_profile(df, bins=20):
  """Fixed Volume Profile: POC (Point of Control) ढूँढने के लिए"""
  if df is None or len(df) == 0:
    return 0
  price_min = df['low'].min()
  price_max = df['high'].max()
  if price_min == price_max:
    return price_min
  hist, bin_edges = np.histogram(
      df['close'], bins=bins, weights=df['volume'], range=(price_min, price_max)
  )
  poc_index = np.argmax(hist)
  poc_price = (bin_edges[poc_index] + bin_edges[poc_index + 1]) / 2
  return poc_price


def detect_liquidity_sweep_and_smc(df):
  """Liquidity Sweep, Order Blocks और फेकआउट से बचने का एडवांस्ड लॉजिक"""
  if df is None or len(df) < 20:
    return False, False, 0, 0

  # पिछले स्विंग हाई और लो
  recent_high = df['high'].iloc[-15:-2].max()
  recent_low = df['low'].iloc[-15:-2].min()

  latest = df.iloc[-1]
  prev = df.iloc[-2]

  # 1. Liquidity Sweep Detection (स्टॉप हंटिंग कैप्चर करने के लिए)
  bullish_sweep = (prev['low'] <= recent_low) and (
      latest['close'] > recent_low
  )
  bearish_sweep = (prev['high'] >= recent_high) and (
      latest['close'] < recent_high
  )

  # 2. Strong Momentum / Order Block Check
  body_size = abs(latest['close'] - latest['open'])
  avg_body = abs(df['close'] - df['open']).rolling(10).mean().iloc[-1]
  if pd.isna(avg_body) or avg_body == 0:
    avg_body = body_size

  is_strong_move = body_size > (1.2 * avg_body)

  bullish_signal = (
      bullish_sweep and is_strong_move and (latest['close'] > latest['open'])
  )
  bearish_signal = (
      bearish_sweep and is_strong_move and (latest['close'] < latest['open'])
  )

  return bullish_signal, bearish_signal, recent_high, recent_low


def scalp_bot_engine():
  print(
      f'🚀 Advanced SMC & Liquidity Scalp Bot Started for {SYMBOL} on Coinbase...'
  )
  send_telegram_message(
      f'🧠 *SMC & Liquidity Scalp Bot Active*\nPair: {SYMBOL}\nTimeframe:'
      f' {TIMEFRAME_FAST} & {TIMEFRAME_CONF}'
  )

  last_signal = None

  while True:
    try:
      df_1m = fetch_data(SYMBOL, TIMEFRAME_FAST, limit=50)
      df_5m = fetch_data(SYMBOL, TIMEFRAME_CONF, limit=50)

      if df_1m is not None and df_5m is not None and not df_1m.empty:
        poc_1m = calculate_volume_profile(df_1m)
        bull_signal, bear_signal, high_lvl, low_lvl = (
            detect_liquidity_sweep_and_smc(df_1m)
        )

        current_price = df_1m.iloc[-1]['close']

        print(
            f'Price: {current_price} | POC: {poc_1m:.2f} | 1m Sweep Checked...'
        )

        # 🟢 BUY SETUP (Liquidity Sweep at Low + POC Support)
        if bull_signal and current_price >= poc_1m:
          signal = 'BUY'
          if signal != last_signal:
            stop_loss = low_lvl - 10
            take_profit = current_price + (current_price - stop_loss) * 2
            msg = (
                f'🟢 *SMC BUY ENTRY (Scalp)*\nCoin: {SYMBOL}\nPrice:'
                f' `{current_price}`\n🎯 *Target:* `{take_profit:.2f}`\n🛑'
                f' *Stop Loss:* `{stop_loss:.2f}`\n💧 *Reason:* Liquidity Sweep'
                f' at Low & POC Rebound'
            )
            send_telegram_message(msg)
            last_signal = signal

        # 🔴 SELL SETUP (Liquidity Sweep at High + POC Resistance)
        elif bear_signal and current_price <= poc_1m:
          signal = 'SELL'
          if signal != last_signal:
            stop_loss = high_lvl + 10
            take_profit = current_price - (stop_loss - current_price) * 2
            msg = (
                f'🔴 *SMC SELL ENTRY (Scalp)*\nCoin: {SYMBOL}\nPrice:'
                f' `{current_price}`\n🎯 *Target:* `{take_profit:.2f}`\n🛑'
                f' *Stop Loss:* `{stop_loss:.2f}`\n💧 *Reason:* Liquidity Sweep'
                f' at High & POC Rejection'
            )
            send_telegram_message(msg)
            last_signal = signal

      time.sleep(20)

    except KeyboardInterrupt:
      print('Bot stopped.')
      break
    except Exception as e:
      print(f'Error in main loop: {e}')
      time.sleep(10)


if __name__ == '__main__':
  scalp_bot_engine()

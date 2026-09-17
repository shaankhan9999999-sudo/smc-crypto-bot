import time
import ccxt
import numpy as np
import pandas as pd
import requests

# आपकी टेलीग्राम क्रेडेंशियल्स
TELEGRAM_BOT_TOKEN = '8662975391:AAG86xC9Dcx-Ec9ljKveAnnNqtk4jqDkdrO'
TELEGRAM_CHAT_ID = '7500472109'

# Coinbase Exchange Setup (Public Data - No Restriction)
exchange = ccxt.coinbase({
    'enableRateLimit': True,
})

SYMBOL = 'BTC/USDT'
TIMEFRAME_FAST = '1m'  # तुरंत एंट्री के लिए
TIMEFRAME_CONF = '5m'  # ट्रेंड कन्फर्मेशन के लिए


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
  price_min = df['low'].min()
  price_max = df['high'].max()
  hist, bin_edges = np.histogram(
      df['close'], bins=bins, weights=df['volume'], range=(price_min, price_max)
  )
  poc_index = np.argmax(hist)
  poc_price = (bin_edges[poc_index] + bin_edges[poc_index + 1]) / 2
  return poc_price


def detect_liquidity_sweep_and_smc(df):
  """Liquidity Sweep, Order Blocks और ChoCH/BOS डिटेक्ट करना"""
  if len(df) < 20:
    return None, None, None

  # Recent Swing Highs and Lows (पिछले 10 कैंडल्स का एक्सट्रीम)
  recent_high = df['high'].iloc[-15:-2].max()
  recent_low = df['low'].iloc[-15:-2].min()

  latest = df.iloc[-1]
  prev = df.iloc[-2]

  # 1. Liquidity Sweep Detection (फेकआउट से बचने के लिए)
  # बुलिश स्वीप: प्राइस ने पुराना लो तोड़ा (स्टॉप लॉस खाया) लेकिन तुरंत ऊपर आकर क्लोज हो गया
  bullish_sweep = (prev['low'] <= recent_low) and (
      latest['close'] > recent_low
  )
  # बेयरिश स्वीप: प्राइस ने पुराना हाई तोड़ा लेकिन तुरंत नीचे क्लोज हो गया
  bearish_sweep = (prev['high'] >= recent_high) and (
      latest['close'] < recent_high
  )

  # 2. Order Block (SMC) & Momentum Check
  # बुलिश ऑर्डर ब्लॉक: बड़ा ग्रीन कैंडल जो पिछले स्विंग को ब्रेक करे (BOS)
  body_size = abs(latest['close'] - latest['open'])
  avg_body = abs(df['close'] - df['open']).rolling(10).mean().iloc[-1]

  is_strong_move = body_size > (1.5 * avg_body)

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
      # 1m और 5m का डेटा फेच करें
      df_1m = fetch_data(SYMBOL, TIMEFRAME_FAST, limit=50)
      df_5m = fetch_data(SYMBOL, TIMEFRAME_CONF, limit=50)

      if df_1m is not None and df_5m is not None:
        # Volume Profile POC calculate करें
        poc_1m = calculate_volume_profile(df_1m)

        # SMC और Liquidity Sweep चेक करें
        bull_signal, bear_signal, high_lvl, low_lvl = (
            detect_liquidity_sweep_and_smc(df_1m)
        )

        current_price = df_1m.iloc[-1]['close']

        print(
            f'Price: {current_price} | POC: {poc_1m:.2f} | 1m Sweep Checked...'
        )

        # 🟢 BUY SETUP (Bullish Liquidity Sweep + POC Support + Strong OB)
        if bull_signal and current_price >= poc_1m:
          signal = 'BUY'
          if signal != last_signal:
            stop_loss = low_lvl - 10  # लिक्विडिटी लो के नीचे स्टॉप लॉस
            take_profit = current_price + (
                current_price - stop_loss
            ) * 1.5  * 2  # Risk:Reward 1:2
            msg = (
                f'🟢 *SMC BUY ENTRY (Scalp)*\nCoin: {SYMBOL}\nPrice:'
                f' `{current_price}`\n🎯 *Target:* `{take_profit:.2f}`\n🛑'
                f' *Stop Loss:* `{stop_loss:.2f}`\n💧 *Reason:* Liquidity Sweep'
                f' at Low & POC Rebound'
            )
            send_telegram_message(msg)
            last_signal = signal

        # 🔴 SELL SETUP (Bearish Liquidity Sweep + POC Resistance + Strong OB)
        elif bear_signal and current_price <= poc_1m:
          signal = 'SELL'
          if signal != last_signal:
            stop_loss = high_lvl + 10  # लिक्विडिटी हाई के ऊपर स्टॉप लॉस
            take_profit = current_price - (
                stop_loss - current_price
            ) * 1.5  * 2
            msg = (
                f'🔴 *SMC SELL ENTRY (Scalp)*\nCoin: {SYMBOL}\nPrice:'
                f' `{current_price}`\n🎯 *Target:* `{take_profit:.2f}`\n🛑'
                f' *Stop Loss:* `{stop_loss:.2f}`\n💧 *Reason:* Liquidity Sweep'
                f' at High & POC Rejection'
            )
            send_telegram_message(msg)
            last_signal = signal

      time.sleep(20)  # हर 20 सेकंड में मार्केट को स्कैन करेगा

    except KeyboardInterrupt:
      print('Bot stopped.')
      break
    except Exception as e:
      print(f'Error in main loop: {e}')
      time.sleep(10)


if __name__ == '__main__':
  scalp_bot_engine()

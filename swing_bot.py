import os
import pandas as pd
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_telegram_message(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {
      "chat_id": TELEGRAM_CHAT_ID,
      "text": message,
      "parse_mode": "Markdown",
  }
  try:
    response = requests.post(url, json=payload, timeout=10)
    return response.json()
  except Exception as e:
    print(f"❌ Telegram Error: {e}")


def fetch_coinbase_klines(product_id="BTC-USD", granularity=3600):
  # Swing bot ke liye granularity 3600 (1 Hour) rakhi gayi hai
  url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity={granularity}"
  headers = {"User-Agent": "Mozilla/5.0"}
  try:
    response = requests.get(url, headers=headers, timeout=10)
    data = response.json()
    if not isinstance(data, list) or len(data) == 0:
      return pd.DataFrame()
    df = pd.DataFrame(
        data, columns=["time", "Low", "High", "Open", "Close", "Volume"]
    )
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.sort_values("time").reset_index(drop=True)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
      df[col] = df[col].astype(float)
    return df
  except Exception as e:
    print(f"❌ Error fetching data: {e}")
    return pd.DataFrame()


def calculate_rsi(series, period=14):
  delta = series.diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
  rs = gain / loss
  return 100 - (100 / (1 + rs))


def run_swing_bot():
  print("=" * 60)
  print(
      "🔍 [SWING SCAN START] Scanning BTC-USD 1-Hour Chart with Smart Hybrid"
      " Logic..."
  )
  print("=" * 60)

  df = fetch_coinbase_klines("BTC-USD", granularity=3600)  # 1-Hour Candles

  if df.empty or len(df) < 100:
    print("❌ Insufficient data received from API.")
    return

  # Indicator calculations
  df["EMA_9"] = df["Close"].ewm(span=9, adjust=False).mean()
  df["EMA_50"] = df["Close"].ewm(span=50, adjust=False).mean()
  df["EMA_200"] = df["Close"].ewm(span=200, adjust=False).mean()
  df["Volume_Avg"] = df["Volume"].rolling(window=10).mean()

  high_low = df["High"] - df["Low"]
  df["ATR"] = high_low.rolling(window=14).mean()
  df["RSI"] = calculate_rsi(df["Close"], 14)

  latest = df.iloc[-1]
  prev = df.iloc[-2]

  current_price = latest["Close"]
  current_open = latest["Open"]
  current_high = latest["High"]
  current_low = latest["Low"]
  current_atr = df["ATR"].iloc[-1]
  current_rsi = df["RSI"].iloc[-1]
  current_vol = latest["Volume"]
  avg_vol = latest["Volume_Avg"]
  candle_body = abs(current_price - current_open)

  ema_9 = latest["EMA_9"]
  ema_50 = latest["EMA_50"]
  prev_ema_9 = prev["EMA_9"]
  prev_ema_50 = prev["EMA_50"]

  # True Support & Resistance (Excluding current candle)
  prior_window = df.iloc[-31:-1]
  resistance_level = prior_window["High"].max()
  support_level = prior_window["Low"].min()

  # --- PRINT DETAILED DEBUG ANALYSIS ---
  print(f"📊 [SWING MARKET STATS - 1H]")
  print(f" • Current Price (Close) : {current_price:.2f}")
  print(f" • Current Candle High   : {current_high:.2f}")
  print(f" • Candle Open / Close   : {current_open:.2f} / {current_price:.2f}")
  print(f" • ATR (14)              : {current_atr:.2f}")
  print(f" • RSI (14)              : {current_rsi:.2f}")
  print(f" • Volume / Avg Volume   : {current_vol:.2f} / {avg_vol:.2f}")
  print(f" • EMA 9 / EMA 50        : {ema_9:.2f} / {ema_50:.2f}")
  print(f" • Prior Resistance      : {resistance_level:.2f}")
  print(f" • Prior Support         : {support_level:.2f}")
  print("-" * 60)

  # 1. SMART HYBRID LONG SETUP
  print("🔎 Checking Smart Hybrid Swing Long Setup...")
  is_bullish_trend = (
      (current_price > ema_50) and (ema_9 > ema_50) and (current_rsi < 70)
  )
  ema_cross_bull = (prev_ema_9 <= prev_ema_50) and (ema_9 > ema_50)
  support_wick_sweep = (current_low < support_level) and (
      current_price > support_level
  )

  long_signal_triggered = (
      is_bullish_trend and ema_cross_bull
  ) or support_wick_sweep

  if long_signal_triggered:
    print("🚀 Smart Hybrid Swing LONG Conditions Met!")
    stop_loss = current_price - (2.0 * current_atr)
    target = (
        resistance_level
        if resistance_level > current_price
        else current_price + (4.0 * current_atr)
    )
    reason = (
        "Support Wick Sweep / Liquidity Sweep"
        if support_wick_sweep
        else "Bullish EMA Trend & Crossover"
    )

    msg = (
        f"🟢 *[SWING SMART HYBRID LONG] BTC-USD* 🟢\n\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Dynamic Target (Resistance):* `{target:.2f}`\n\n"
        f"💡 *Debug Analysis:*\n"
        f"✅ Trigger Type: {reason}\n"
        f"✅ Safe RSI: {current_rsi:.1f}\n"
        f"✅ 1-Hour Trend Confirmation"
    )
    send_telegram_message(msg)
    print("📤 Telegram Alert Sent: Swing Smart Hybrid Long")
    return

  # 2. SMART HYBRID SHORT SETUP
  print("🔎 Checking Smart Hybrid Swing SHORT Setup...")
  trend_bearish = (current_price < ema_50) and (current_rsi < 70)
  ema_bearish_crossover = ema_9 < ema_50
  top_wick_sweep = (current_high > resistance_level) and (
      current_price < resistance_level
  )

  short_signal_triggered = (
      trend_bearish and ema_bearish_crossover
  ) or top_wick_sweep

  if short_signal_triggered:
    print("📉 Smart Hybrid Swing SHORT Conditions Met!")
    stop_loss = current_price + (2.0 * current_atr)
    target = (
        support_level
        if support_level < current_price
        else current_price - (4.0 * current_atr)
    )
    reason = (
        "Resistance Top Wick Sweep / Liquidity Trap"
        if top_wick_sweep
        else "Bearish EMA Trend & Crossover"
    )

    msg = (
        f"🔻 *[SWING SMART HYBRID SHORT] BTC-USD* 🔻\n\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Dynamic Target (Support):* `{target:.2f}`\n\n"
        f"💡 *Debug Analysis:*\n"
        f"✅ Trigger Type: {reason}\n"
        f"✅ Safe RSI: {current_rsi:.1f}\n"
        f"✅ 1-Hour Trend Confirmation"
    )
    send_telegram_message(msg)
    print("📤 Telegram Alert Sent: Swing Smart Hybrid Short")
    return

  print("❌ No valid swing setup generated in this cycle.")
  print("=" * 60)


if __name__ == "__main__":
  run_swing_bot()

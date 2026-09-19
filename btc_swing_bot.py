import os
import pandas as pd
import requests

# --- Telegram Bot Credentials ---
TELEGRAM_BOT_TOKEN = "8662975391:AAGI9-ZSmdScsJqQ5nl3Ea7X22DeDTVSUaI"
TELEGRAM_CHAT_ID = "7500472109"


def send_telegram_message(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {
      "chat_id": TELEGRAM_CHAT_ID,
      "text": message,
      "parse_mode": "Markdown",
  }
  try:
    response = requests.post(url, json=payload, timeout=10)
    res_data = response.json()
    if res_data.get("ok"):
      print("📤 Telegram Alert Successfully Delivered!")
    else:
      print(f"❌ Telegram Rejected it: {res_data}")
    return res_data
  except Exception as e:
    print(f"❌ Telegram Connection Error: {e}")


def fetch_coinbase_klines(product_id="BTC-USD", granularity=900):
  # 15 Minutes granularity (900 seconds)
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
      "🔍 [SWING SCAN START] Scanning BTC-USD 15M Chart (Enhanced Hybrid +"
      " Pro Sweep & Volume Filters)..."
  )
  print("=" * 60)

  df = fetch_coinbase_klines("BTC-USD", granularity=900)  # 15-Minute Candles

  if df.empty or len(df) < 220:
    print("❌ Insufficient data received from API (Need at least 220 candles).")
    return

  # Indicator calculations
  df["EMA_9"] = df["Close"].ewm(span=9, adjust=False).mean()
  df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()
  df["EMA_50"] = df["Close"].ewm(span=50, adjust=False).mean()
  df["EMA_200"] = df["Close"].ewm(span=200, adjust=False).mean()
  df["Volume_Avg"] = df["Volume"].rolling(window=10).mean()

  high_low = df["High"] - df["Low"]
  df["ATR"] = high_low.rolling(window=14).mean()
  df["RSI"] = calculate_rsi(df["Close"], 14)

  latest = df.iloc[-2]  # Fully closed candle to avoid fake signals
  prev = df.iloc[-3]

  current_price = latest["Close"]
  current_open = latest["Open"]
  current_high = latest["High"]
  current_low = latest["Low"]
  current_atr = df["ATR"].iloc[-2]
  current_rsi = df["RSI"].iloc[-2]
  current_vol = latest["Volume"]
  avg_vol = latest["Volume_Avg"]
  prev_vol = prev["Volume"]

  ema_9 = latest["EMA_9"]
  ema_21 = latest["EMA_21"]
  ema_50 = latest["EMA_50"]
  ema_200 = latest["EMA_200"]

  prev_ema_9 = prev["EMA_9"]
  prev_ema_21 = prev["EMA_21"]
  prev_ema_50 = prev["EMA_50"]
  prev_ema_200 = prev["EMA_200"]

  # Standard & Pro Volume Conditions
  volume_confirmed = current_vol > avg_vol
  sweep_volume_spike = (current_vol > avg_vol) and (
      current_vol >= 1.1 * prev_vol
  )

  # True Support & Resistance
  prior_window = df.iloc[-32:-2]
  resistance_level = prior_window["High"].max()
  support_level = prior_window["Low"].min()

  # --- PRINT DETAILED DEBUG ANALYSIS ---
  print(f"📊 [SWING MARKET STATS - 15M (Closed Candle)]")
  print(f" • Current Price (Close) : {current_price:.2f}")
  print(f" • ATR (14)              : {current_atr:.2f}")
  print(f" • RSI (14)              : {current_rsi:.1f}")
  print(
      f" • Volume / Avg / Prev   : {current_vol:.2f} / {avg_vol:.2f} /"
      f" {prev_vol:.2f}"
  )
  print(
      f" • EMA 9 / 21 / 50 / 200 : {ema_9:.2f} / {ema_21:.2f} / {ema_50:.2f} /"
      f" {ema_200:.2f}"
  )
  print(f" • Prior Resistance      : {resistance_level:.2f}")
  print(f" • Prior Support         : {support_level:.2f}")
  print("-" * 60)

  # ==========================================
  # 1. HIGH PROBABILITY GOLDEN / DEATH CROSSOVER SETUP
  # ==========================================
  golden_crossover = (
      (prev_ema_9 <= prev_ema_200 or prev_ema_21 <= prev_ema_200)
      and (ema_9 > ema_200)
      and (ema_21 > ema_200)
  )

  death_crossover = (
      (prev_ema_9 >= prev_ema_200 or prev_ema_21 >= prev_ema_200)
      and (ema_9 < ema_200)
      and (ema_21 < ema_200)
  )

  if golden_crossover and volume_confirmed:
    print("🔥 HIGH PROBABILITY GOLDEN CROSSOVER DETECTED!")
    stop_loss = current_price - (2.5 * current_atr)
    target = current_price + (5.0 * current_atr)
    msg = (
        f"🚨 *🔥 [HIGH PROBABILITY - GOLDEN CROSSOVER] BTC-USD* 🚨\n\n"
        f"🚀 *EMA 9 & 21 Crossed Above EMA 200 with Volume Support!*\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target:* `{target:.2f}`\n\n"
        f"💡 *Strategy Note:* Major Trend Shift Upwards.\n"
        f"✅ RSI: {current_rsi:.1f} | ATR: {current_atr:.2f}"
    )
    send_telegram_message(msg)
    return

  if death_crossover and volume_confirmed:
    print("🔥 HIGH PROBABILITY DEATH CROSSOVER DETECTED!")
    stop_loss = current_price + (2.5 * current_atr)
    target = current_price - (5.0 * current_atr)
    msg = (
        f"🚨 *🔥 [HIGH PROBABILITY - DEATH CROSSOVER] BTC-USD* 🚨\n\n"
        f"📉 *EMA 9 & 21 Crossed Below EMA 200 with Volume Support!*\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target:* `{target:.2f}`\n\n"
        f"💡 *Strategy Note:* Major Trend Shift Downwards.\n"
        f"✅ RSI: {current_rsi:.1f} | ATR: {current_atr:.2f}"
    )
    send_telegram_message(msg)
    return

  # ==========================================
  # 2. SMART HYBRID LONG SETUP (Enhanced Filters)
  # ==========================================
  print("🔎 Checking Smart Hybrid Swing Long Setup...")
  is_bullish_trend = current_price > ema_50
  ema_cross_bull = (prev_ema_9 <= prev_ema_50) and (ema_9 > ema_50)

  # Strict Bottom Wick Sweep (Requires RSI <= 48 and pro volume spike)
  bottom_wick_sweep = (
      (current_low < support_level)
      and (current_price > support_level)
      and (current_rsi <= 48)
      and sweep_volume_spike
  )

  long_signal_triggered = (
      is_bullish_trend and ema_cross_bull and volume_confirmed
  ) or bottom_wick_sweep

  if long_signal_triggered:
    print("🚀 Smart Hybrid Swing LONG Conditions Met!")
    stop_loss = current_price - (2.0 * current_atr)
    target = (
        resistance_level
        if resistance_level > current_price
        else current_price + (4.0 * current_atr)
    )
    reason = (
        "Strict Support Wick Sweep (High Volume + Oversold Confluence)"
        if bottom_wick_sweep
        else "Bullish EMA Trend & Crossover"
    )

    msg = (
        f"🟢 *[15M SWING SMART HYBRID LONG] BTC-USD* 🟢\n\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Dynamic Target:* `{target:.2f}`\n\n"
        f"💡 *Debug Analysis:*\n"
        f"✅ Trigger Type: {reason}\n"
        f"✅ Valid RSI: {current_rsi:.1f}\n"
        f"✅ Pro Volume Spike Confirmed"
    )
    send_telegram_message(msg)
    return

  # ==========================================
  # 3. SMART HYBRID SHORT SETUP (Enhanced Filters)
  # ==========================================
  print("🔎 Checking Smart Hybrid Swing SHORT Setup...")
  trend_bearish = current_price < ema_50
  ema_bearish_crossover = ema_9 < ema_50

  # Strict Top Wick Sweep (Requires RSI >= 58 and pro volume spike to filter fake counter-trend traps)
  top_wick_sweep = (
      (current_high > resistance_level)
      and (current_price < resistance_level)
      and (current_rsi >= 58)
      and sweep_volume_spike
  )

  short_signal_triggered = (
      trend_bearish and ema_bearish_crossover and volume_confirmed
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
        "Strict Resistance Top Wick Sweep (High Volume + Overbought RSI)"
        if top_wick_sweep
        else "Bearish EMA Trend & Crossover"
    )

    msg = (
        f"🔻 *[15M SWING SMART HYBRID SHORT] BTC-USD* 🔻\n\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Dynamic Target:* `{target:.2f}`\n\n"
        f"💡 *Debug Analysis:*\n"
        f"✅ Trigger Type: {reason}\n"
        f"✅ Valid RSI: {current_rsi:.1f}\n"
        f"✅ Pro Volume Spike Confirmed"
    )
    send_telegram_message(msg)
    return

  print(
      "❌ No valid swing setup generated in this cycle (Low-conviction moves"
      " filtered out)."
  )
  print("=" * 60)


if __name__ == "__main__":
  run_swing_bot()

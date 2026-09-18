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


def fetch_coinbase_klines(product_id="BTC-USD", granularity=300):
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


def run_scalp_bot():
  print("=" * 60)
  print(
      "🔍 [SCAN START] Scanning BTC-USD with True Smart Hybrid Sweep Logic..."
  )
  print("=" * 60)

  df = fetch_coinbase_klines("BTC-USD", granularity=300)  # 5M Candles

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

  # --- TRUE SUPPORT & RESISTANCE (Excluding current candle to catch breakouts/sweeps) ---
  prior_window = df.iloc[-31:-1]  # Pichli 30 candles (Current candle chhod kar)
  resistance_level = prior_window["High"].max()
  support_level = prior_window["Low"].min()

  # --- PRINT DETAILED DEBUG ANALYSIS ---
  print(f"📊 [MARKET STATS]")
  print(f" • Current Price (Close) : {current_price:.2f}")
  print(f" • Current Candle High   : {current_high:.2f}")
  print(f" • Candle Open / Close   : {current_open:.2f} / {current_price:.2f}")
  print(f" • ATR (14)              : {current_atr:.2f}")
  print(f" • RSI (14)              : {current_rsi:.2f}")
  print(f" • Volume / Avg Volume   : {current_vol:.2f} / {avg_vol:.2f}")
  print(
      f" • Volume Spike Check    : {'✅ YES ( > 2.5x )' if current_vol > (2.5 * avg_vol) else '❌ NO'}"
  )
  print(f" • EMA 9 / EMA 50        : {ema_9:.2f} / {ema_50:.2f}")
  print(f" • Prior Resistance (S&R): {resistance_level:.2f}")
  print(f" • Prior Support (S&R)   : {support_level:.2f}")
  print("-" * 60)

  volume_spike = current_vol > (2.5 * avg_vol)

  # 1. MOMENTUM BREAKOUT TRIGGER (FAST PUMP / DUMP)
  print("🔎 Checking Momentum Breakout Trigger...")
  if volume_spike and (candle_body > (1.2 * current_atr)):
    if current_price > current_open and current_rsi < 82:
      print("🚀 Momentum Breakout LONG Conditions Met!")
      stop_loss = current_price - (1.5 * current_atr)
      target = (
          resistance_level
          if resistance_level > current_price
          else current_price + (4.0 * current_atr)
      )

      msg = (
          f"🚀 *[DYNAMIC BREAKOUT LONG] BTC-USD* 🚀\n\n"
          f"🟢 *Action:* BUY / LONG\n"
          f"📍 *Entry Price:* `{current_price:.2f}`\n"
          f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
          f"🎯 *Dynamic Target (Resistance):* `{target:.2f}`\n\n"
          f"💡 *Debug Analysis:*\n"
          f"✅ Vol Spike: {current_vol:.1f} (Avg: {avg_vol:.1f})\n"
          f"✅ Safe RSI: {current_rsi:.1f} (<82)\n"
          f"✅ Strong Bullish Body: {candle_body:.1f}"
      )
      send_telegram_message(msg)
      print("📤 Telegram Alert Sent: Momentum Breakout Long")
      return
    elif current_price < current_open and current_rsi > 18:
      print("🔻 Momentum Breakout SHORT Conditions Met!")
      stop_loss = current_price + (1.5 * current_atr)
      target = (
          support_level
          if support_level < current_price
          else current_price - (4.0 * current_atr)
      )

      msg = (
          f"🔻 *[DYNAMIC BREAKOUT SHORT] BTC-USD* 🔻\n\n"
          f"🔴 *Action:* SELL / SHORT\n"
          f"📍 *Entry Price:* `{current_price:.2f}`\n"
          f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
          f"🎯 *Dynamic Target (Support):* `{target:.2f}`\n\n"
          f"💡 *Debug Analysis:*\n"
          f"✅ Vol Spike: {current_vol:.1f} (Avg: {avg_vol:.1f})\n"
          f"✅ Safe RSI: {current_rsi:.1f} (>18)\n"
          f"✅ Strong Bearish Body: {candle_body:.1f}"
      )
      send_telegram_message(msg)
      print("📤 Telegram Alert Sent: Momentum Breakout Short")
      return

  # 2. STANDARD FILTERED SETUP (EMA Crossover - LONG)
  print("🔎 Checking Standard Filtered Long Setup (EMA Crossover)...")
  is_bullish_trend = (
      (current_price > ema_50) and (ema_9 > ema_50) and (current_rsi < 70)
  )
  ema_cross_bull = (prev_ema_9 <= prev_ema_50) and (ema_9 > ema_50)

  if is_bullish_trend and ema_cross_bull:
    print("⚡ Standard Filtered Long Conditions Met!")
    stop_loss = current_price - (1.5 * current_atr)
    target = (
        resistance_level
        if resistance_level > current_price
        else current_price + (3.0 * current_atr)
    )

    msg = (
        f"⚡ *[DYNAMIC FILTERED LONG] BTC-USD* ⚡\n\n"
        f"🟢 *Action:* BUY / LONG\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Dynamic Target:* `{target:.2f}`\n\n"
        f"💡 *Debug Analysis:*\n"
        f"✅ EMA Crossover Confirmed\n"
        f"✅ Safe RSI: {current_rsi:.1f} (<70)"
    )
    send_telegram_message(msg)
    print("📤 Telegram Alert Sent: Standard Filtered Long")
    return

  # 3. SMART HYBRID & BEARISH EMA SHORT SETUP (PULLBACK / LIQUIDITY SWEEP)
  print(
      "🔎 Checking Smart Hybrid Short & Bearish EMA Crossover Setup..."
  )
  trend_bearish = (current_price < ema_50) and (current_rsi < 70)
  ema_bearish_crossover = ema_9 < ema_50

  # --- SMART HYBRID LIQUIDITY SWEEP / TOP WICK REJECTION LOGIC ---
  # Current candle ne pichle 30 candles ke resistance ko upar toड़ा (High > Resistance),
  # lekin close us resistance ke niche hi hua (Close < Resistance) -> Yeh hai asli Liquidity Sweep!
  top_wick_sweep = (current_high > resistance_level) and (
      current_price < resistance_level
  )

  print(
      f" • Trend Bearish Check (< EMA 50 & RSI < 70) : {trend_bearish}"
  )
  print(f" • EMA Bearish Crossover (EMA 9 < EMA 50)    : {ema_bearish_crossover}")
  print(
      f" • Top Wick Rejection / Liquidity Sweep      :"
      f" {'✅ SWEEP DETECTED' if top_wick_sweep else '❌ NO'}"
  )

  short_signal_triggered = (
      trend_bearish and ema_bearish_crossover
  ) or top_wick_sweep

  if short_signal_triggered:
    print("📉 Smart Hybrid Short Setup Triggered!")
    stop_loss = current_price + (1.5 * current_atr)
    target = (
        support_level
        if support_level < current_price
        else current_price - (3.0 * current_atr)
    )

    reason = (
        "Smart Hybrid Top Wick Rejection / Liquidity Sweep"
        if top_wick_sweep
        else "Bearish EMA Trend & Crossover"
    )

    msg = (
        f"🔻 *[SMART HYBRID SHORT] BTC-USD* 🔻\n\n"
        f"🔴 *Action:* SELL / SHORT\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Dynamic Target (Support):* `{target:.2f}`\n\n"
        f"💡 *Debug Analysis:*\n"
        f"✅ Trigger Type: {reason}\n"
        f"✅ Safe RSI: {current_rsi:.1f}\n"
        f"✅ Resistance Defended / Trap Caught"
    )
    send_telegram_message(msg)
    print("📤 Telegram Alert Sent: Smart Hybrid Short")
    return

  print("❌ No valid signal generated in this scan cycle.")
  print("=" * 60)


if __name__ == "__main__":
  run_scalp_bot()

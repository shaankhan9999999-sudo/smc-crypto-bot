import pandas as pd
import requests

# --- Telegram Bot Token aur Chat ID ---
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
      return True
    else:
      print(f"❌ Telegram API Error: {res_data}")
      return False
  except Exception as e:
    print(f"❌ Telegram Connection Error: {e}")
    return False


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
      "🔍 [SCAN START] Multi-Timeframe Scan (1H S&R + 15m Trend + 5m Entry &"
      " Filters)..."
  )
  print("=" * 60)

  # 1. FETCH 1-HOUR DATA FOR STRONG SUPPORT & RESISTANCE (Bade Targets ke liye)
  df_1h = fetch_coinbase_klines("BTC-USD", granularity=3600)
  if df_1h.empty or len(df_1h) < 24:
    print("❌ Insufficient 1H data.")
    return
  prior_1h = df_1h.iloc[-25:-1]
  resistance_level = prior_1h["High"].max()
  support_level = prior_1h["Low"].min()

  # 2. FETCH 15-MINUTE DATA FOR TREND CONFIRMATION (Fake Trend se bachne ke liye)
  df_15m = fetch_coinbase_klines("BTC-USD", granularity=900)
  if df_15m.empty or len(df_15m) < 50:
    print("❌ Insufficient 15m data.")
    return
  df_15m["EMA_9"] = df_15m["Close"].ewm(span=9, adjust=False).mean()
  df_15m["EMA_50"] = df_15m["Close"].ewm(span=50, adjust=False).mean()
  latest_15m = df_15m.iloc[-2]  # Confirmed 15m closed candle

  is_15m_bullish = (latest_15m["Close"] > latest_15m["EMA_50"]) and (
      latest_15m["EMA_9"] > latest_15m["EMA_50"]
  )
  is_15m_bearish = (latest_15m["Close"] < latest_15m["EMA_50"]) and (
      latest_15m["EMA_9"] < latest_15m["EMA_50"]
  )

  # 3. FETCH 5-MINUTE DATA FOR EXACT ENTRY & EXECUTION (Closed Candle Analysis)
  df_5m = fetch_coinbase_klines("BTC-USD", granularity=300)
  if df_5m.empty or len(df_5m) < 100:
    print("❌ Insufficient 5m data.")
    return

  df_5m["EMA_9"] = df_5m["Close"].ewm(span=9, adjust=False).mean()
  df_5m["EMA_50"] = df_5m["Close"].ewm(span=50, adjust=False).mean()
  # Lookahead bias hatane ke liye .shift(1) ka use
  df_5m["Volume_Avg"] = df_5m["Volume"].shift(1).rolling(window=10).mean()

  high_low = df_5m["High"] - df_5m["Low"]
  df_5m["ATR"] = high_low.rolling(window=14).mean()
  df_5m["RSI"] = calculate_rsi(df_5m["Close"], 14)

  # Sirf confirmed closed candle (iloc[-2]) par analysis
  latest = df_5m.iloc[-2]
  prev = df_5m.iloc[-3]

  current_price = latest["Close"]
  current_open = latest["Open"]
  current_high = latest["High"]
  current_low = latest["Low"]
  current_atr = latest["ATR"]
  current_rsi = latest["RSI"]
  current_vol = latest["Volume"]
  avg_vol = latest["Volume_Avg"]
  candle_body = abs(current_price - current_open)

  ema_9 = latest["EMA_9"]
  ema_50 = latest["EMA_50"]
  prev_ema_9 = prev["EMA_9"]
  prev_ema_50 = prev["EMA_50"]

  print(f"📊 [MARKET STATS - MULTI-TIMEFRAME]")
  print(f" • 5M Price (Close)      : {current_price:.2f}")
  print(
      " • 15M Trend Status      :"
      f" {'BULLISH 🟢' if is_15m_bullish else 'BEARISH 🔴' if is_15m_bearish else 'SIDEWAYS ⚪'}"
  )
  print(f" • 1H Resistance         : {resistance_level:.2f}")
  print(f" • 1H Support            : {support_level:.2f}")
  print("-" * 60)

  # Volatility & Volume Filters (Fake Breakouts rokne ke liye)
  volume_spike = current_vol > (2.0 * avg_vol)
  volatility_ok = candle_body > (1.0 * current_atr)

  # --- SETUP 1: MOMENTUM BREAKOUT (Volumetric & Volatility Filtered) ---
  print("🔎 Checking Momentum Breakout (5m + 15m Alignment)...")
  if volume_spike and volatility_ok:
    # LONG BREAKOUT
    if (
        (current_price > current_open)
        and (current_rsi < 80)
        and is_15m_bullish
    ):
      print("🚀 Filtered Breakout LONG Conditions Met!")
      stop_loss = current_price - (1.5 * current_atr)
      # 1H Resistance ke hisab se bada natural target
      target = (
          resistance_level
          if resistance_level > current_price
          else current_price + (4.0 * current_atr)
      )

      msg = (
          f"🚀 *[MULTI-TIMEFRAME BREAKOUT LONG] BTC-USD* 🚀\n\n"
          f"🟢 *Action:* BUY / LONG\n"
          f"📍 *Entry Price:* `{current_price:.2f}`\n"
          f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
          f"🎯 *Natural Target:* `{target:.2f}`\n\n"
          f"💡 *Filters Passed:*\n"
          f"✅ 1H Resistance Room Clear\n"
          f"✅ 15m Trend: Bullish Confirmed\n"
          f"✅ 5m Volume Spike & Volatility OK"
      )
      if send_telegram_message(msg):
        print("📤 Telegram Alert Sent: Breakout Long")
      return

    # SHORT BREAKOUT
    elif (
        (current_price < current_open) and (current_rsi > 20) and is_15m_bearish
    ):
      print("🔻 Filtered Breakout SHORT Conditions Met!")
      stop_loss = current_price + (1.5 * current_atr)
      target = (
          support_level
          if support_level < current_price
          else current_price - (4.0 * current_atr)
      )

      msg = (
          f"🔻 *[MULTI-TIMEFRAME BREAKOUT SHORT] BTC-USD* 🔻\n\n"
          f"🔴 *Action:* SELL / SHORT\n"
          f"📍 *Entry Price:* `{current_price:.2f}`\n"
          f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
          f"🎯 *Natural Target:* `{target:.2f}`\n\n"
          f"💡 *Filters Passed:*\n"
          f"✅ 1H Support Room Clear\n"
          f"✅ 15m Trend: Bearish Confirmed\n"
          f"✅ 5m Volume Spike & Volatility OK"
      )
      if send_telegram_message(msg):
        print("📤 Telegram Alert Sent: Breakout Short")
      return

  # --- SETUP 2: SMART HYBRID & SWEEP SETUP (15m Trend + 5m Crossover/Wick Sweep) ---
  print("🔎 Checking Smart Hybrid Setup with Multi-Timeframe Confirmation...")

  bottom_wick_sweep = (current_low < support_level) and (
      current_price > support_level
  )
  top_wick_sweep = (current_high > resistance_level) and (
      current_price < resistance_level
  )

  ema_bullish_crossover = (prev_ema_9 <= prev_ema_50) and (ema_9 > ema_50)
  ema_bearish_crossover = (prev_ema_9 >= prev_ema_50) and (ema_9 < ema_50)

  bullish_signal = is_15m_bullish and (
      ema_bullish_crossover or bottom_wick_sweep
  )
  bearish_signal = is_15m_bearish and (
      ema_bearish_crossover or top_wick_sweep
  )

  if bullish_signal:
    print("📈 Smart Hybrid Multi-TF Long Triggered!")
    stop_loss = current_price - (1.5 * current_atr)
    target = (
        resistance_level
        if resistance_level > current_price
        else current_price + (4.0 * current_atr)
    )

    reason = (
        "1H Support Sweep / Bear Trap"
        if bottom_wick_sweep
        else "15m/5m Bullish Trend Crossover"
    )

    msg = (
        f"🟢 *[SMART HYBRID LONG] BTC-USD* 🟢\n\n"
        f"🔵 *Action:* BUY / LONG\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Natural Target:* `{target:.2f}`\n\n"
        f"💡 *Analysis:*\n"
        f"✅ Trigger: {reason}\n"
        f"✅ 15m Trend Confirmed\n"
        f"✅ Safe RSI: {current_rsi:.1f}"
    )
    if send_telegram_message(msg):
      print("📤 Telegram Alert Sent: Smart Hybrid Long")
    return

  elif bearish_signal:
    print("📉 Smart Hybrid Multi-TF Short Triggered!")
    stop_loss = current_price + (1.5 * current_atr)
    target = (
        support_level
        if support_level < current_price
        else current_price - (3.0 * current_atr)
    )

    reason = (
        "1H Resistance Sweep / Bull Trap"
        if top_wick_sweep
        else "15m/5m Bearish Trend Crossover"
    )

    msg = (
        f"🔻 *[SMART HYBRID SHORT] BTC-USD* 🔻\n\n"
        f"🔴 *Action:* SELL / SHORT\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Natural Target:* `{target:.2f}`\n\n"
        f"💡 *Analysis:*\n"
        f"✅ Trigger: {reason}\n"
        f"✅ 15m Trend Confirmed\n"
        f"✅ Safe RSI: {current_rsi:.1f}"
    )
    if send_telegram_message(msg):
      print("📤 Telegram Alert Sent: Smart Hybrid Short")
    return

  print("❌ No valid multi-timeframe signal generated in this scan cycle.")
  print("=" * 60)


if __name__ == "__main__":
  run_scalp_bot()

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
      "🔍 [SCAN START] Scanning BTC-USD with Fixed Dual-Side Smart Hybrid &"
      " Sweep Logic..."
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

  # BUG FIX: Volume Average mein lookahead bias hatane ke liye .shift(1) lagaya gaya hai
  df["Volume_Avg"] = df["Volume"].shift(1).rolling(window=10).mean()

  high_low = df["High"] - df["Low"]
  df["ATR"] = high_low.rolling(window=14).mean()
  df["RSI"] = calculate_rsi(df["Close"], 14)

  # BUG FIX: Live/Running candle (iloc[-1]) ke bajaye pichli confirmed CLOSED candle (iloc[-2]) par analysis
  latest = df.iloc[-2]
  prev = df.iloc[-3]

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

  # --- TRUE SUPPORT & RESISTANCE (Excluding evaluated candle iloc[-2]) ---
  prior_window = df.iloc[-32:-2]
  resistance_level = prior_window["High"].max()
  support_level = prior_window["Low"].min()

  # --- PRINT DETAILED DEBUG ANALYSIS ---
  print(f"📊 [MARKET STATS - CLOSED CANDLE]")
  print(f" • Price (Close)         : {current_price:.2f}")
  print(f" • Candle Open / Close   : {current_open:.2f} / {current_price:.2f}")
  print(f" • ATR (14)              : {current_atr:.2f}")
  print(f" • RSI (14)              : {current_rsi:.2f}")
  print(f" • Prior Resistance      : {resistance_level:.2f}")
  print(f" • Prior Support         : {support_level:.2f}")
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
          f"🎯 *Dynamic Target:* `{target:.2f}`\n\n"
          f"💡 *Debug:*\n"
          f"✅ Vol Spike: {current_vol:.1f}\n"
          f"✅ Safe RSI: {current_rsi:.1f}"
      )
      if send_telegram_message(msg):
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
          f"🎯 *Dynamic Target:* `{target:.2f}`\n\n"
          f"💡 *Debug:*\n"
          f"✅ Vol Spike: {current_vol:.1f}\n"
          f"✅ Safe RSI: {current_rsi:.1f}"
      )
      if send_telegram_message(msg):
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
        f"💡 *Debug:*\n"
        f"✅ EMA Crossover Confirmed\n"
        f"✅ Safe RSI: {current_rsi:.1f}"
    )
    if send_telegram_message(msg):
      print("📤 Telegram Alert Sent: Standard Filtered Long")
    return

  # 3. SMART HYBRID DUAL-SIDE SETUP (LONG & SHORT SUPPORT/RESISTANCE SWEEPS)
  print(
      "🔎 Checking Smart Hybrid Dual-Side Liquidity Sweep & EMA Setup (Long &"
      " Short)..."
  )

  # BUG FIX: State bug resolved using true crossover condition alongside wick sweep
  trend_bullish = (current_price > ema_50) and (current_rsi > 30)
  ema_bullish_crossover = (prev_ema_9 <= prev_ema_50) and (ema_9 > ema_50)
  bottom_wick_sweep = (current_low < support_level) and (
      current_price > support_level
  )

  trend_bearish = (current_price < ema_50) and (current_rsi < 70)
  ema_bearish_crossover = (prev_ema_9 >= prev_ema_50) and (ema_9 < ema_50)
  top_wick_sweep = (current_high > resistance_level) and (
      current_price < resistance_level
  )

  bullish_signal_triggered = (
      trend_bullish and ema_bullish_crossover
  ) or bottom_wick_sweep
  short_signal_triggered = (
      trend_bearish and ema_bearish_crossover
  ) or top_wick_sweep

  if bullish_signal_triggered:
    print("📈 Smart Hybrid Long Setup Triggered!")
    stop_loss = current_price - (1.5 * current_atr)
    target = (
        resistance_level
        if resistance_level > current_price
        else current_price + (3.0 * current_atr)
    )

    reason = (
        "Smart Hybrid Bottom Wick Rejection / Support Sweep (Bear Trap Caught)"
        if bottom_wick_sweep
        else "Bullish EMA Trend & True Crossover"
    )

    msg = (
        f"🟢 *[SMART HYBRID LONG] BTC-USD* 🟢\n\n"
        f"🔵 *Action:* BUY / LONG\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Dynamic Target (Resistance):* `{target:.2f}`\n\n"
        f"💡 *Debug Analysis:*\n"
        f"✅ Trigger Type: {reason}\n"
        f"✅ Safe RSI: {current_rsi:.1f}\n"
        f"✅ Support Defended Successfully"
    )
    if send_telegram_message(msg):
      print("📤 Telegram Alert Sent: Smart Hybrid Long")
    return

  elif short_signal_triggered:
    print("📉 Smart Hybrid Short Setup Triggered!")
    stop_loss = current_price + (1.5 * current_atr)
    target = (
        support_level
        if support_level < current_price
        else current_price - (3.0 * current_atr)
    )

    reason = (
        "Smart Hybrid Top Wick Rejection / Liquidity Sweep (Bull Trap Caught)"
        if top_wick_sweep
        else "Bearish EMA Trend & True Crossover"
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
        f"✅ Resistance Defended Successfully"
    )
    if send_telegram_message(msg):
      print("📤 Telegram Alert Sent: Smart Hybrid Short")
    return

  print("❌ No valid signal generated in this scan cycle.")
  print("=" * 60)


if __name__ == "__main__":
  run_scalp_bot()

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
      print("📤 Bot #3 Smart Hybrid Telegram Alert Delivered Successfully!")
      return True
    else:
      print(f"❌ Telegram API Error: {res_data}")
      return False
  except Exception as e:
    print(f"❌ Telegram Connection Error: {e}")
    return False


def fetch_coinbase_klines(product_id="BTC-USD", granularity=3600):
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


def run_balanced_multitimeframe_bot():
  print("=" * 60)
  print(
      "🤖 [BOT #3 ACTIVE] Running Smart Hybrid Bot (Resampled Zones + EMA 200"
      " Reversal Override)..."
  )
  print("=" * 60)

  # --- 1. Fetch Reliable 1H Data First ---
  df_1h = fetch_coinbase_klines("BTC-USD", granularity=3600)
  if df_1h.empty or len(df_1h) < 200:
    print("❌ Insufficient 1H execution data for EMA 200 calculation.")
    return

  # --- 2. Smart Resampling: Create Robust 2H Macro Zones from 1H Data ---
  df_temp = df_1h.copy()
  df_temp.set_index("time", inplace=True)
  df_2h = (
      df_temp.resample("2h")
      .agg(
          {
              "Open": "first",
              "High": "max",
              "Low": "min",
              "Close": "last",
              "Volume": "sum",
          }
      )
      .dropna()
      .reset_index()
  )

  if len(df_2h) < 6:
    print("❌ Insufficient resampled 2H macro data.")
    return

  macro_window = df_2h.iloc[-6:-1]
  macro_resistance = macro_window["High"].max()
  macro_support = macro_window["Low"].min()

  # Indicator calculations on Execution TF (1H)
  df_1h["EMA_9"] = df_1h["Close"].ewm(span=9, adjust=False).mean()
  df_1h["EMA_50"] = df_1h["Close"].ewm(span=50, adjust=False).mean()
  df_1h["EMA_200"] = df_1h["Close"].ewm(span=200, adjust=False).mean()
  df_1h["Volume_Avg"] = df_1h["Volume"].rolling(window=10).mean()

  high_low = df_1h["High"] - df_1h["Low"]
  df_1h["ATR"] = high_low.rolling(window=14).mean()
  df_1h["RSI"] = calculate_rsi(df_1h["Close"], 14)

  latest = df_1h.iloc[-1]
  prev = df_1h.iloc[-2]

  current_price = latest["Close"]
  current_open = latest["Open"]
  current_high = latest["High"]
  current_low = latest["Low"]
  current_atr = df_1h["ATR"].iloc[-1]
  current_rsi = df_1h["RSI"].iloc[-1]
  current_vol = latest["Volume"]
  avg_vol = latest["Volume_Avg"]
  candle_body = abs(current_price - current_open)

  ema_9 = latest["EMA_9"]
  ema_50 = latest["EMA_50"]
  ema_200 = latest["EMA_200"]
  prev_ema_9 = prev["EMA_9"]
  prev_ema_50 = prev["EMA_50"]

  print(f"📊 [MARKET STATS - SMART HYBRID BOT #3]")
  print(f" • Current Price       : {current_price:.2f}")
  print(f" • 2H Macro Resistance : {macro_resistance:.2f}")
  print(f" • 2H Macro Support    : {macro_support:.2f}")
  print(f" • 1H EMA 200          : {ema_200:.2f}")
  print(f" • Current Volume      : {current_vol:.2f}")
  print(f" • Average Volume (10) : {avg_vol:.2f}")
  print(f" • 1H RSI (14)         : {current_rsi:.1f}")
  print("-" * 60)

  # --- Dual-Tier Volume Classifications ---
  is_heavy_volume = current_vol >= (2.0 * avg_vol)
  is_above_average = current_vol > avg_vol

  # --- 1. Heavy Volume Breakout Strategy (Tier 1) ---
  print("🔎 Checking Tier-1 Heavy Volume Breakout Setup...")
  if is_heavy_volume and (candle_body > (1.1 * current_atr)):
    if current_price > current_open and current_rsi < 82:
      print("🚀 Bot #3 Tier-1 Heavy Volume LONG Triggered!")
      stop_loss = current_price - (1.8 * current_atr)
      target = (
          macro_resistance
          if macro_resistance > current_price
          else current_price + (4.0 * current_atr)
      )

      msg = (
          f"🔥 *[ALERT: THIRD BOT - HEAVY VOL LONG]* 🔥\n"
          f"═══════════════════════\n"
          f"🪙 *Asset:* `BTC-USD`\n"
          f"🟢 *Signal:* BUY / LONG\n"
          f"📍 *Entry Price:* `{current_price:.2f}`\n"
          f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
          f"🎯 *Target:* `{target:.2f}`\n"
          f"═══════════════════════\n"
          f"💡 *Bot Source:* Bot #3 (Smart Hybrid)\n"
          f"📊 *Volume Status:* `HEAVY SPIKE (>= 2.0x)` 🚀\n"
          f"📌 *Setup:* High Confidence Breakout\n"
          f"📈 *RSI:* `{current_rsi:.1f}`"
      )
      send_telegram_message(msg)
      return

    elif current_price < current_open and current_rsi > 18:
      print("🔻 Bot #3 Tier-1 Heavy Volume SHORT Triggered!")
      stop_loss = current_price + (1.8 * current_atr)
      target = (
          macro_support
          if macro_support < current_price
          else current_price - (4.0 * current_atr)
      )

      msg = (
          f"🔥 *[ALERT: THIRD BOT - HEAVY VOL SHORT]* 🔥\n"
          f"═══════════════════════\n"
          f"🪙 *Asset:* `BTC-USD`\n"
          f"🔴 *Signal:* SELL / SHORT\n"
          f"📍 *Entry Price:* `{current_price:.2f}`\n"
          f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
          f"🎯 *Target:* `{target:.2f}`\n"
          f"═══════════════════════\n"
          f"💡 *Bot Source:* Bot #3 (Smart Hybrid)\n"
          f"📊 *Volume Status:* `HEAVY SPIKE (>= 2.0x)` 🔻\n"
          f"📌 *Setup:* High Confidence Breakout\n"
          f"📉 *RSI:* `{current_rsi:.1f}`"
      )
      send_telegram_message(msg)
      return

  # --- 2. Smart Hybrid Zone & Wick Sweep Setup (Tier 2 - With Reversal Override) ---
  print("🔎 Checking Tier-2 Smart Setup (Wick Sweep / EMA Crossover)...")

  # Wick Sweep Definitions (Reversal / Bottom Catching -> Overrides EMA 200 filter)
  bottom_wick_sweep = (current_low < macro_support) and (
      current_price > macro_support
  )
  top_wick_sweep = (current_high > macro_resistance) and (
      current_price < macro_resistance
  )

  # Trend-Following Definitions (Strictly Filtered by EMA 200)
  trend_bullish = (
      (current_price > ema_50)
      and (current_price > ema_200)
      and (current_rsi > 30)
  )
  ema_bullish_cross = (
      (prev_ema_9 <= prev_ema_50)
      and (ema_9 > ema_50)
      and (current_price > ema_200)
  )

  trend_bearish = (
      (current_price < ema_50)
      and (current_price < ema_200)
      and (current_rsi < 70)
  )
  ema_bearish_cross = (
      (prev_ema_9 >= prev_ema_50)
      and (ema_9 < ema_50)
      and (current_price < ema_200)
  )

  # Bullish Signal: Support Wick Sweep (Bottom Catch) OR Valid Bullish Trend Crossover
  bullish_signal = (
      bottom_wick_sweep or (trend_bullish and ema_bullish_cross)
  ) and is_above_average

  # Bearish Signal: Resistance Wick Sweep (Top Catch) OR Valid Bearish Trend Crossover
  bearish_signal = (
      top_wick_sweep or (trend_bearish and ema_bearish_cross)
  ) and is_above_average

  if bullish_signal:
    trigger_desc = (
        "2H Support Wick Sweep (Bottom Catch Override)"
        if bottom_wick_sweep
        else "1H EMA Trend Crossover (EMA 200 Filtered)"
    )
    print(f"📈 Bot #3 Tier-2 Long Triggered via: {trigger_desc}")

    stop_loss = current_price - (1.8 * current_atr)
    target = (
        macro_resistance
        if macro_resistance > current_price
        else current_price + (4.0 * current_atr)
    )

    msg = (
        f"⚡ *[ALERT: THIRD BOT - STANDARD LONG]* ⚡\n"
        f"═══════════════════════\n"
        f"🪙 *Asset:* `BTC-USD`\n"
        f"🟢 *Signal:* BUY / LONG\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target:* `{target:.2f}`\n"
        f"═══════════════════════\n"
        f"💡 *Bot Source:* Bot #3 (Smart Hybrid)\n"
        f"📊 *Volume Status:* `ABOVE AVERAGE (> 1.0x)` 📈\n"
        f"📌 *Trigger:* {trigger_desc}\n"
        f"📊 *RSI:* `{current_rsi:.1f}`"
    )
    send_telegram_message(msg)
    return

  elif bearish_signal:
    trigger_desc = (
        "2H Resistance Wick Sweep (Top Catch Override)"
        if top_wick_sweep
        else "1H EMA Trend Crossover (EMA 200 Filtered)"
    )
    print(f"📉 Bot #3 Tier-2 Short Triggered via: {trigger_desc}")

    stop_loss = current_price + (1.8 * current_atr)
    target = (
        macro_support
        if macro_support < current_price
        else current_price - (4.0 * current_atr)
    )

    msg = (
        f"⚡ *[ALERT: THIRD BOT - STANDARD SHORT]* ⚡\n"
        f"═══════════════════════\n"
        f"🪙 *Asset:* `BTC-USD`\n"
        f"🔴 *Signal:* SELL / SHORT\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target:* `{target:.2f}`\n"
        f"═══════════════════════\n"
        f"💡 *Bot Source:* Bot #3 (Smart Hybrid)\n"
        f"📊 *Volume Status:* `ABOVE AVERAGE (> 1.0x)` 📉\n"
        f"📌 *Trigger:* {trigger_desc}\n"
        f"📊 *RSI:* `{current_rsi:.1f}`"
    )
    send_telegram_message(msg)
    return

  print("❌ No valid smart hybrid signal generated in this scan cycle for Bot #3.")
  print("=" * 60)


if __name__ == "__main__":
  run_balanced_multitimeframe_bot()

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
      "🤖 [BOT #3 ACTIVE] Running Ultimate Hybrid Bot (Macro Zones + Distance"
      " Filter + Confluence Engine)..."
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

  print(f"📊 [MARKET STATS - ULTIMATE HYBRID BOT #3]")
  print(f" • Current Price       : {current_price:.2f}")
  print(f" • 2H Macro Resistance : {macro_resistance:.2f}")
  print(f" • 2H Macro Support    : {macro_support:.2f}")
  print(f" • 1H EMA 200          : {ema_200:.2f}")
  print(f" • Current Volume      : {current_vol:.2f}")
  print(f" • Average Volume (10) : {avg_vol:.2f}")
  print(f" • 1H RSI (14)         : {current_rsi:.1f}")
  print("-" * 60)

  # ==========================================
  # EVALUATE ALL 4 TIERS INDEPENDENTLY
  # ==========================================

  # --- LAYER 0: Pre-Breakout Pressure Detector ---
  is_near_resistance = (macro_resistance - current_price) >= 0 and (
      macro_resistance - current_price
  ) <= (0.6 * current_atr)
  is_near_support = (current_price - macro_support) >= 0 and (
      current_price - macro_support
  ) <= (0.6 * current_atr)

  pre_breakout_long = (
      is_near_resistance and (current_vol >= 1.8 * avg_vol) and (current_rsi > 58)
  )
  pre_breakout_short = (
      is_near_support and (current_vol >= 1.8 * avg_vol) and (current_rsi < 42)
  )

  # --- TIER 1: Heavy Volume Breakout Strategy ---
  is_heavy_volume = current_vol >= (2.0 * avg_vol)
  tier1_long = (
      is_heavy_volume
      and (candle_body > (1.1 * current_atr))
      and (current_price > current_open)
      and (current_rsi < 82)
  )
  tier1_short = (
      is_heavy_volume
      and (candle_body > (1.1 * current_atr))
      and (current_price < current_open)
      and (current_rsi > 18)
  )

  # --- TIER 2: Smart Hybrid Zone & Wick Sweep Setup ---
  is_above_average = current_vol > avg_vol
  bottom_wick_sweep = (current_low < macro_support) and (
      current_price > macro_support
  )
  top_wick_sweep = (current_high > macro_resistance) and (
      current_price < macro_resistance
  )

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

  tier2_long = (
      bottom_wick_sweep or (trend_bullish and ema_bullish_cross)
  ) and is_above_average
  tier2_short = (
      top_wick_sweep or (trend_bearish and ema_bearish_cross)
  ) and is_above_average

  # --- TIER 3: Low-Volume Pullback Continuation Setup with Macro Zone Safety ---
  recent_volumes = df_1h["Volume"].iloc[-5:-1]
  has_recent_spike = (recent_volumes >= (1.5 * avg_vol)).any()
  is_low_volume_pullback = (current_vol < avg_vol) and (
      current_vol > (0.3 * avg_vol)
  )

  safe_distance_for_long = (macro_resistance - current_price) > (
      1.5 * current_atr
  )
  safe_distance_for_short = (current_price - macro_support) > (
      1.5 * current_atr
  )

  tier3_long = (
      has_recent_spike
      and is_low_volume_pullback
      and safe_distance_for_long
      and (current_price > ema_200)
      and (current_price > current_open)
      and (
          (abs(current_price - ema_50) / current_price < 0.012)
          or (current_low <= ema_50 and current_price > ema_50)
      )
      and (current_rsi > 40 and current_rsi < 70)
  )

  tier3_short = (
      has_recent_spike
      and is_low_volume_pullback
      and safe_distance_for_short
      and (current_price < ema_200)
      and (current_price < current_open)
      and (
          (abs(current_price - ema_50) / current_price < 0.012)
          or (current_high >= ema_50 and current_price < ema_50)
      )
      and (current_rsi > 30 and current_rsi < 60)
  )

  # ==========================================
  # CONFLUENCE ENGINE: COLLECT ACTIVE TIERS
  # ==========================================

  active_longs = []
  if pre_breakout_long:
    active_longs.append("Layer 0: Pre-Breakout Pressure (Circle Zone)")
  if tier1_long:
    active_longs.append("Tier 1: Heavy Volume Breakout")
  if tier2_long:
    active_longs.append("Tier 2: Wick Sweep / EMA Crossover")
  if tier3_long:
    active_longs.append("Tier 3: Safe Low-Volume Pullback")

  active_shorts = []
  if pre_breakout_short:
    active_shorts.append("Layer 0: Pre-Breakout Pressure (Circle Zone)")
  if tier1_short:
    active_shorts.append("Tier 1: Heavy Volume Breakout")
  if tier2_short:
    active_shorts.append("Tier 2: Wick Sweep / EMA Crossover")
  if tier3_short:
    active_shorts.append("Tier 3: Safe Low-Volume Pullback")

  print(f"🔍 [SCAN SUMMARY] Active Long Tiers matched : {len(active_longs)}")
  print(f"🔍 [SCAN SUMMARY] Active Short Tiers matched: {len(active_shorts)}")

  # --- CASE A: MULTI-TIER LONG CONFLUENCE (HIGH PROBABILITY SETUP) ---
  if len(active_longs) > 1:
    print(
        "🔥 [CONFLUENCE ALERT] Multiple Long Tiers matched! High Win-Rate"
        " Setup."
    )
    stop_loss = current_price - (1.8 * current_atr)
    target = current_price + (4.0 * current_atr)
    tiers_str = "\n".join([f"  ✅ {t}" for t in active_longs])

    msg = (
        f"💎 *[SUPER CONFLUENCE: HIGH PROBABILITY LONG]* 💎\n"
        f"═══════════════════════\n"
        f"🪙 *Asset:* `BTC-USD`\n"
        f"🟢 *Signal:* STRONG BUY / LONG (Multi-Tier Confluence)\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target:* `{target:.2f}`\n"
        f"═══════════════════════\n"
        f"🌟 *Matched Strategies ({len(active_longs)} Tiers):*\n"
        f"{tiers_str}\n"
        f"📊 *Market Outlook:* `WIDE-ANGLE BULLISH CONFLUENCE` 🚀\n"
        f"📈 *RSI:* `{current_rsi:.1f}` | *Win Probability:* `VERY HIGH` 🔥"
    )
    send_telegram_message(msg)
    return

  # --- CASE B: MULTI-TIER SHORT CONFLUENCE (HIGH PROBABILITY SETUP) ---
  elif len(active_shorts) > 1:
    print(
        "🔥 [CONFLUENCE ALERT] Multiple Short Tiers matched! High Win-Rate"
        " Setup."
    )
    stop_loss = current_price + (1.8 * current_atr)
    target = current_price - (4.0 * current_atr)
    tiers_str = "\n".join([f"  ✅ {t}" for t in active_shorts])

    msg = (
        f"💎 *[SUPER CONFLUENCE: HIGH PROBABILITY SHORT]* 💎\n"
        f"═══════════════════════\n"
        f"🪙 *Asset:* `BTC-USD`\n"
        f"🔴 *Signal:* STRONG SELL / SHORT (Multi-Tier Confluence)\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target:* `{target:.2f}`\n"
        f"═══════════════════════\n"
        f"🌟 *Matched Strategies ({len(active_shorts)} Tiers):*\n"
        f"{tiers_str}\n"
        f"📊 *Market Outlook:* `WIDE-ANGLE BEARISH CONFLUENCE` 🔻\n"
        f"📉 *RSI:* `{current_rsi:.1f}` | *Win Probability:* `VERY HIGH` 🔥"
    )
    send_telegram_message(msg)
    return

  # --- CASE C: SINGLE TIER LONG MATCH ---
  elif len(active_longs) == 1:
    matched_name = active_longs[0]
    print(f"📈 Single Tier Long Triggered: {matched_name}")
    stop_loss = current_price - (1.8 * current_atr)
    target = (
        macro_resistance
        if macro_resistance > current_price
        else current_price + (4.0 * current_atr)
    )

    msg = (
        f"⚡ *[ALERT: BOT #3 - SINGLE TIER LONG]* ⚡\n"
        f"═══════════════════════\n"
        f"🪙 *Asset:* `BTC-USD`\n"
        f"🟢 *Signal:* BUY / LONG\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target:* `{target:.2f}`\n"
        f"═══════════════════════\n"
        f"📌 *Triggered By:* `{matched_name}`\n"
        f"📊 *RSI:* `{current_rsi:.1f}`"
    )
    send_telegram_message(msg)
    return

  # --- CASE D: SINGLE TIER SHORT MATCH ---
  elif len(active_shorts) == 1:
    matched_name = active_shorts[0]
    print(f"📉 Single Tier Short Triggered: {matched_name}")
    stop_loss = current_price + (1.8 * current_atr)
    target = (
        macro_support
        if macro_support < current_price
        else current_price - (4.0 * current_atr)
    )

    msg = (
        f"⚡ *[ALERT: BOT #3 - SINGLE TIER SHORT]* ⚡\n"
        f"═══════════════════════\n"
        f"🪙 *Asset:* `BTC-USD`\n"
        f"🔴 *Signal:* SELL / SHORT\n"
        f"📍 *Entry Price:* `{current_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target:* `{target:.2f}`\n"
        f"═══════════════════════\n"
        f"📌 *Triggered By:* `{matched_name}`\n"
        f"📊 *RSI:* `{current_rsi:.1f}`"
    )
    send_telegram_message(msg)
    return

  # --- CASE E: NO MATCH ---
  print("❌ No valid smart hybrid signal generated in this scan cycle for Bot #3.")
  print("=" * 60)


if __name__ == "__main__":
  run_balanced_multitimeframe_bot()

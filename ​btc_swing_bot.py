import pandas as pd
import requests

# --- TELEGRAM CONFIGURATION (Permanently Fixed) ---
TELEGRAM_BOT_TOKEN = "8662975391:AAG86xC9Dcx-Ec9ljKveAnnNqtk4jqDkdr0"
TELEGRAM_CHAT_ID = "7500472109"


def send_telegram_message(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
  try:
    response = requests.post(url, json=payload, timeout=10)
    return response.json()
  except Exception as e:
    print(f"Telegram Error: {e}")


def fetch_coinbase_klines(product_id="BTC-USD", granularity=3600):
  url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity={granularity}"
  headers = {"User-Agent": "Mozilla/5.0"}
  try:
    response = requests.get(url, headers=headers, timeout=10)
    data = response.json()
    if not isinstance(data, list) or len(data) == 0:
      return pd.DataFrame()

    df = pd.DataFrame(data, columns=["time", "Low", "High", "Open", "Close", "Volume"])
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.sort_values("time").reset_index(drop=True)

    for col in ["Open", "High", "Low", "Close", "Volume"]:
      df[col] = df[col].astype(float)
    return df
  except Exception as e:
    print(f"Error fetching data: {e}")
    return pd.DataFrame()


def run_smart_bot():
  print("Scanning BTC-USD 1-Hour chart for Smart Tiered Setups...")
  df = fetch_coinbase_klines("BTC-USD", granularity=3600)

  if df.empty or len(df) < 250:
    print("Insufficient data from Coinbase.")
    return

  # EMA Calculation (9, 20, 21, 50, 200)
  df["EMA_9"] = df["Close"].ewm(span=9, adjust=False).mean()
  df["EMA_20"] = df["Close"].ewm(span=20, adjust=False).mean()
  df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()
  df["EMA_50"] = df["Close"].ewm(span=50, adjust=False).mean()
  df["EMA_200"] = df["Close"].ewm(span=200, adjust=False).mean()

  # Volume Average (पिछले 10 घंटे का औसत)
  df["Volume_Avg"] = df["Volume"].rolling(window=10).mean()

  latest = df.iloc[-1]
  prev = df.iloc[-2]

  # ATR Calculation for Stop Loss
  high_low = df["High"] - df["Low"]
  df["ATR"] = high_low.rolling(window=14).mean()
  current_atr = df["ATR"].iloc[-1]

  entry_price = latest["Close"]
  volume_ok = latest["Volume"] > latest["Volume_Avg"]

  # ==========================================
  # 1. A+ HIGH PROBABILITY SETUPS (EMA 200 Cross)
  # ==========================================

  # Bullish A+ Cross: EMA 9 & 20 crossing 200 EMA from Below to Above
  bullish_cross_200 = (
      (prev["EMA_9"] <= prev["EMA_200"]) and (latest["EMA_9"] > latest["EMA_200"])
  ) and (
      (prev["EMA_20"] <= prev["EMA_200"]) and (latest["EMA_20"] > latest["EMA_200"])
  )

  if bullish_cross_200 and volume_ok:
    stop_loss = entry_price - (1.5 * current_atr)
    risk = entry_price - stop_loss
    target_1 = entry_price + (2.0 * risk)

    alert_message = (
        f"🔥 *[A+ SETUP] BTC BULLISH GOLDEN CROSS* 🔥\n\n"
        f"🟢 *Action:* BUY / LONG (High Fund Size)\n"
        f"⭐ *Quality:* A+ High Win Probability\n\n"
        f"📍 *Entry Price:* `{entry_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target (1:2):* `{target_1:.2f}`\n\n"
        f"💡 *Conditions Met:*\n"
        f"✅ EMA 9 & 20 Crossed Above EMA 200 Together\n"
        f"✅ Volume Above 10-Hour Average"
    )
    send_telegram_message(alert_message)
    print("A+ Bullish Cross Alert sent to Telegram!")
    return

  # Bearish A+ Cross: EMA 9 & 20 crossing 200 EMA from Above to Below
  bearish_cross_200 = (
      (prev["EMA_9"] >= prev["EMA_200"]) and (latest["EMA_9"] < latest["EMA_200"])
  ) and (
      (prev["EMA_20"] >= prev["EMA_200"]) and (latest["EMA_20"] < latest["EMA_200"])
  )

  if bearish_cross_200 and volume_ok:
    stop_loss = entry_price + (1.5 * current_atr)
    risk = stop_loss - entry_price
    target_1 = entry_price - (2.0 * risk)

    alert_message = (
        f"🔻 *[A+ SETUP] BTC BEARISH DEATH CROSS* 🔻\n\n"
        f"🔴 *Action:* SELL / SHORT (High Fund Size)\n"
        f"⭐ *Quality:* A+ High Win Probability\n\n"
        f"📍 *Entry Price:* `{entry_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target (1:2):* `{target_1:.2f}`\n\n"
        f"💡 *Conditions Met:*\n"
        f"✅ EMA 9 & 20 Crossed Below EMA 200 Together\n"
        f"✅ Volume Above 10-Hour Average"
    )
    send_telegram_message(alert_message)
    print("A+ Bearish Cross Alert sent to Telegram!")
    return

  # ==========================================
  # 2. STANDARD SETUPS (9/21 EMA Crossover + Trend)
  # ==========================================

  # Standard Long Setup
  is_in_uptrend = (
      (latest["Close"] > latest["EMA_50"])
      and (latest["Close"] > latest["EMA_200"])
      and (latest["EMA_50"] > latest["EMA_200"])
  )
  ema_bullish_crossover = (prev["EMA_9"] <= prev["EMA_21"]) and (
      latest["EMA_9"] > latest["EMA_21"]
  )

  if is_in_uptrend and ema_bullish_crossover and volume_ok:
    stop_loss = entry_price - (1.5 * current_atr)
    risk = entry_price - stop_loss
    target_1 = entry_price + (2.0 * risk)

    alert_message = (
        f"⚡ *[STANDARD SETUP] BTC LONG ALERT* ⚡\n\n"
        f"🟢 *Action:* BUY / LONG (Normal/Low Fund Size)\n"
        f"⭐ *Quality:* Standard Trend Opportunity\n\n"
        f"📍 *Entry Price:* `{entry_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target (1:2):* `{target_1:.2f}`\n\n"
        f"💡 *Conditions Met:*\n"
        f"✅ Market in Uptrend (Above EMA 50 & 200)\n"
        f"✅ Standard 9/21 EMA Crossover\n"
        f"✅ Volume Above 10-Hour Average"
    )
    send_telegram_message(alert_message)
    print("Standard Long Alert sent to Telegram!")
    return

  # Standard Short Setup
  is_in_downtrend = (
      (latest["Close"] < latest["EMA_50"])
      and (latest["Close"] < latest["EMA_200"])
      and (latest["EMA_50"] < latest["EMA_200"])
  )
  ema_bearish_crossover = (prev["EMA_9"] >= prev["EMA_21"]) and (
      latest["EMA_9"] < latest["EMA_21"]
  )

  if is_in_downtrend and ema_bearish_crossover and volume_ok:
    stop_loss = entry_price + (1.5 * current_atr)
    risk = stop_loss - entry_price
    target_1 = entry_price - (2.0 * risk)

    alert_message = (
        f"⚠️ *[STANDARD SETUP] BTC SHORT ALERT* ⚠️\n\n"
        f"🔴 *Action:* SELL / SHORT (Normal/Low Fund Size)\n"
        f"⭐ *Quality:* Standard Trend Opportunity\n\n"
        f"📍 *Entry Price:* `{entry_price:.2f}`\n"
        f"🛑 *Stop Loss:* `{stop_loss:.2f}`\n"
        f"🎯 *Target (1:2):* `{target_1:.2f}`\n\n"
        f"💡 *Conditions Met:*\n"
        f"✅ Market in Downtrend (Below EMA 50 & 200)\n"
        f"✅ Standard 9/21 EMA Crossover\n"
        f"✅ Volume Above 10-Hour Average"
    )
    send_telegram_message(alert_message)
    print("Standard Short Alert sent to Telegram!")
    return

  print("No valid A+ or Standard setup found on this candle.")


if __name__ == "__main__":
  run_smart_bot()

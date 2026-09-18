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
    print(f"Telegram Error: {e}")


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
    print(f"Error fetching data: {e}")
    return pd.DataFrame()


def calculate_rsi(series, period=14):
  delta = series.diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
  rs = gain / loss
  return 100 - (100 / (1 + rs))


def run_scalp_bot():
  print(
      "Scanning BTC-USD with Dynamic Support/Resistance & Momentum Targets..."
  )
  df = fetch_coinbase_klines("BTC-USD", granularity=300)  # 5M Candles

  if df.empty or len(df) < 100:
    print("Insufficient data.")
    return

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
  current_atr = df["ATR"].iloc[-1]
  current_rsi = df["RSI"].iloc[-1]

  # --- DYNAMIC TARGET CALCULATION (Support & Resistance Based) ---
  # pichle 30 candles me se nearest Resistance aur Support nikalna
  recent_window = df.iloc[-30:]
  resistance_level = recent_window["High"].max()
  support_level = recent_window["Low"].min()

  candle_body = abs(latest["Close"] - latest["Open"])
  volume_spike = latest["Volume"] > (2.5 * latest["Volume_Avg"])

  # 1. MOMENTUM BREAKOUT TRIGGER
  if volume_spike and (candle_body > (1.2 * current_atr)):
    if latest["Close"] > latest["Open"] and current_rsi < 82:
      stop_loss = current_price - (1.5 * current_atr)

      # Target calculation: Agar resistance current price se upar hai toh wahan tak ka target, warna ATR ka 3x-4x bada target
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
          f"🎯 *Dynamic Target (Resistance/Potential):* `{target:.2f}`\n\n"
          f"💡 *Analysis:*\n"
          f"✅ Volume Spike & Strong Bullish Candle\n"
          f"✅ Target set to major Resistance/Swing High"
      )
      send_telegram_message(msg)
      print("Dynamic Breakout Long sent!")
      return

    elif latest["Close"] < latest["Open"] and current_rsi > 18:
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
          f"🎯 *Dynamic Target (Support/Potential):* `{target:.2f}`\n\n"
          f"💡 *Analysis:*\n"
          f"✅ Volume Spike & Strong Bearish Candle\n"
          f"✅ Target set to major Support/Swing Low"
      )
      send_telegram_message(msg)
      print("Dynamic Breakout Short sent!")
      return

  # 2. STANDARD FILTERED SETUP (With Dynamic S&R Target)
  is_bullish_trend = (
      (current_price > latest["EMA_50"])
      and (latest["EMA_9"] > latest["EMA_50"])
      and (current_rsi < 70)
  )
  ema_cross_bull = (prev["EMA_9"] <= prev["EMA_50"]) and (
      latest["EMA_9"] > latest["EMA_50"]
  )

  if is_bullish_trend and ema_cross_bull:
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
        f"💡 *Conditions Met:*\n"
        f"✅ EMA Crossover & Safe RSI\n"
        f"✅ Target mapped to Market Resistance"
    )
    send_telegram_message(msg)
    print("Dynamic Filtered Long Alert sent!")
    return

  print("No valid setup found.")


if __name__ == "__main__":
  run_scalp_bot()

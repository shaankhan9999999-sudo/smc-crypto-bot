import json
import urllib.request
import urllib.error

# ----------------- CREDENTIALS -----------------
TELEGRAM_BOT_TOKEN = "8662975391:AAG86xC9Dcx-Ec9ljKveAnnNqtk4jqDkdr0"
TELEGRAM_CHAT_ID = "7500472109"
# ------------------------------------------------

def calculate_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * 13 + gains[i]) / 14
        avg_loss = (avg_loss * 13 + losses[i]) / 14
        
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_ema(closes, period):
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price * k) + (ema * (1 - k))
    return round(ema, 2)

def calculate_macd(closes):
    ema_12 = calculate_ema(closes, 12)
    ema_26 = calculate_ema(closes, 26)
    macd_line = ema_12 - ema_26
    signal_line = macd_line * 0.8  
    return round(macd_line, 2), round(signal_line, 2)

def calculate_bollinger(closes, period=20):
    recent = closes[-period:]
    sma = sum(recent) / period
    variance = sum((x - sma) ** 2 for x in recent) / period
    std_dev = variance ** 0.5
    return round(sma + (std_dev * 2), 2), round(sma - (std_dev * 2), 2), round(sma, 2)

def detect_fvg(highs, lows):
    if len(highs) >= 3 and lows[-1] > highs[-3]:
        return "Bullish FVG"
    elif len(highs) >= 3 and highs[-1] < lows[-3]:
        return "Bearish FVG"
    return "None"

def detect_liquidity_sweep(highs, lows, closes):
    if len(highs) >= 10:
        prev_high = max(highs[-10:-1])
        prev_low = min(lows[-10:-1])
        
        if highs[-1] > prev_high and closes[-1] < prev_high:
            return "Bearish Sweep"
        elif lows[-1] < prev_low and closes[-1] > prev_low:
            return "Bullish Sweep"
    return "None"

def calculate_volume_profile(closes, volumes, bins=10):
    if not closes or not volumes:
        return closes[-1]
    min_p, max_p = min(closes[-20:]), max(closes[-20:])
    step = (max_p - min_p) / bins if max_p != min_p else 1
    
    vol_buckets = {}
    for p, v in zip(closes[-20:], volumes[-20:]):
        bucket = round((p - min_p) / step) * step + min_p
        vol_buckets[bucket] = vol_buckets.get(bucket, 0) + v
        
    poc_price = max(vol_buckets, key=vol_buckets.get) if vol_buckets else closes[-1]
    return round(poc_price, 2)

def detect_divergence(prices, rsi_values):
    if len(prices) >= 10 and len(rsi_values) >= 10:
        if prices[-1] < prices[-10] and rsi_values[-1] > rsi_values[-10]:
            return "Bullish Divergence"
        elif prices[-1] > prices[-10] and rsi_values[-1] < rsi_values[-10]:
            return "Bearish Divergence"
    return "None"

def fetch_tf_data(granularity=3600):
    """
    300 = 5 Minute
    900 = 15 Minute
    3600 = 1 Hour
    14400 = 4 Hour
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        url = f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity={granularity}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            candles = json.loads(response.read().decode('utf-8'))
            
        candles.reverse()
        closes = [float(c[4]) for c in candles]
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        volumes = [float(c[5]) for c in candles]
        
        rsi_14 = calculate_rsi(closes, 14)
        ema_20 = calculate_ema(closes, 20)
        ema_50 = calculate_ema(closes, 50)
        macd_line, signal_line = calculate_macd(closes)
        bb_upper, bb_lower, bb_mid = calculate_bollinger(closes)
        fvg = detect_fvg(highs, lows)
        sweep = detect_liquidity_sweep(highs, lows, closes)
        poc = calculate_volume_profile(closes, volumes)
        
        rsi_series = [calculate_rsi(closes[:i], 14) for i in range(30, len(closes)+1)]
        divergence = detect_divergence(closes[-len(rsi_series):], rsi_series)
        
        return {
            "price": closes[-1],
            "rsi": rsi_14,
            "ema20": ema_20,
            "ema50": ema_50,
            "macd": macd_line,
            "macd_signal": signal_line,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "fvg": fvg,
            "sweep": sweep,
            "poc": poc,
            "divergence": divergence
        }
    except Exception as e:
        print(f"❌ Granularity {granularity} Error:", e)
        return None

def analyze_confluence(d_5m, d_15m, d_1h, d_4h):
    bull, bear = 0, 0
    
    # 1. 4H Major Trend (Weight: 2)
    if d_4h["ema20"] > d_4h["ema50"]: bull += 2
    else: bear += 2

    # 2. 1H Structure & POC (Weight: 1.5)
    if d_1h["price"] >= d_1h["poc"]: bull += 1.5
    else: bear += 1.5

    # 3. 15M Trend & Momentum (Weight: 2)
    if d_15m["macd"] > d_15m["macd_signal"]: bull += 1
    else: bear += 1
    if "Bullish FVG" in d_15m["fvg"]: bull += 1
    elif "Bearish FVG" in d_15m["fvg"]: bear += 1

    # 4. 5M Micro Trigger (Sweep & Divergence) (Weight: 2.5)
    if "Bullish Sweep" in d_5m["sweep"]: bull += 1.5
    elif "Bearish Sweep" in d_5m["sweep"]: bear += 1.5
    
    if d_5m["divergence"] == "Bullish Divergence": bull += 1
    elif d_5m["divergence"] == "Bearish Divergence": bear += 1

    # 5. RSI Alignment across 5m and 15m (Weight: 2)
    if d_5m["rsi"] <= 45 and d_15m["rsi"] <= 50: bull += 2
    elif d_5m["rsi"] >= 55 and d_15m["rsi"] >= 50: bear += 2

    total_score = max(bull, bear)
    if bull >= 8.0:
        return True, "BULLISH", round((bull/10)*100, 1)
    elif bear >= 8.0:
        return True, "BEARISH", round((bear/10)*100, 1)

    return False, "NEUTRAL", round((total_score/10)*100, 1)

def generate_trading_signal(d_5m, d_15m, d_1h, d_4h, signal_type, confidence):
    is_bullish = (signal_type == "BULLISH")
    trend = "Quad-Timeframe Bullish Breakout 🚀" if is_bullish else "Quad-Timeframe Bearish Breakdown 📉"
    price = d_5m["price"]

    entry_start = round(price * (0.998 if is_bullish else 1.001), 2)
    entry_end = round(price * (1.002 if is_bullish else 1.003), 2)
    
    if is_bullish:
        tp1 = round(price * 1.012, 2)
        tp2 = round(price * 1.025, 2)
        sl = round(price * 0.994, 2)
    else:
        tp1 = round(price * 0.988, 2)
        tp2 = round(price * 0.975, 2)
        sl = round(price * 1.006, 2)

    return f"""🎯 **QUAD-TIMEFRAME SMC SIGNAL ({confidence}% Agreement)**

📈 **Bias:** {trend}
🕒 **Timeframes:** 5m | 15m | 1h | 4h

📊 **4H Trend:** {"Bullish" if d_4h["ema20"] > d_4h["ema50"] else "Bearish"} | **1H POC:** ${d_1h["poc"]}
⚡ **15M FVG:** {d_15m["fvg"]}
💧 **5M Liquidity Sweep:** {d_5m["sweep"]}
🔍 **5M RSI:** {d_5m["rsi"]} | **Divergence:** {d_5m["divergence"]}

🎯 **Entry Zone:** ${entry_start:,.2f} - ${entry_end:,.2f}
🎯 **Targets:** TP1: ${tp1:,.2f} | TP2: ${tp2:,.2f}
🛑 **Stop Loss:** ${sl:,.2f}"""

def send_telegram_alert(message):
    try:
        send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        data_bytes = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(send_url, data=data_bytes, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            print("✅ Telegram Alert Sent!")
    except Exception as e:
        print("❌ Telegram Error:", e)

if __name__ == "__main__":
    print("🔎 Scanning Market across 5M, 15M, 1H, and 4H Timeframes...")
    d_5m  = fetch_tf_data(300)    # 5 min
    d_15m = fetch_tf_data(900)    # 15 min
    d_1h  = fetch_tf_data(3600)   # 1 hour
    d_4h  = fetch_tf_data(14400)  # 4 hour
    
    if d_5m and d_15m and d_1h and d_4h:
        has_signal, signal_type, confidence = analyze_confluence(d_5m, d_15m, d_1h, d_4h)
        
        if has_signal:
            print(f"🔥 High Confluence Signal ({confidence}%). Sending Alert...")
            msg = generate_trading_signal(d_5m, d_15m, d_1h, d_4h, signal_type, confidence)
            send_telegram_alert(msg)
        else:
            print(f"⏳ Normal Market (Confidence: {confidence}% < 80%). Skipped.")

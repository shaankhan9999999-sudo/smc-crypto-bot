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
    # Bullish FVG: Current Low > High 2 candles ago
    if len(highs) >= 3 and lows[-1] > highs[-3]:
        return "Bullish FVG (Buying Imbalance)"
    # Bearish FVG: Current High < Low 2 candles ago
    elif len(highs) >= 3 and highs[-1] < lows[-3]:
        return "Bearish FVG (Selling Imbalance)"
    return "None"

def detect_liquidity_sweep(highs, lows, closes):
    if len(highs) >= 10:
        prev_high = max(highs[-10:-1])
        prev_low = min(lows[-10:-1])
        
        # High Break karke Niche Close (Bearish Sweep)
        if highs[-1] > prev_high and closes[-1] < prev_high:
            return "Bearish Sweep (Buy Stops Taken)"
        # Low Break karke Upar Close (Bullish Sweep)
        elif lows[-1] < prev_low and closes[-1] > prev_low:
            return "Bullish Sweep (Sell Stops Taken)"
            
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

def fetch_btc_data():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        url = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400"
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
        
        avg_vol = sum(volumes[-11:-1]) / 10
        vol_status = "High Volume" if volumes[-1] > avg_vol * 1.2 else "Normal Volume"
        
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
            "divergence": divergence,
            "vol_status": vol_status
        }
    except Exception as e:
        print("❌ Market Fetch Error:", e)
        return None

def check_80_percent_confluence(d):
    """
    10 इंडिकेटर्स में से कम से कम 8 (80% Match) एक दिशा में होने चाहिए।
    """
    bull, bear = 0, 0
    
    # 1. EMA 20/50
    if d["ema20"] > d["ema50"]: bull += 1
    else: bear += 1
    
    # 2. RSI Direction
    if d["rsi"] <= 45: bull += 1
    elif d["rsi"] >= 55: bear += 1
    elif d["ema20"] > d["ema50"]: bull += 1
    else: bear += 1

    # 3. MACD Momentum
    if d["macd"] > d["macd_signal"]: bull += 1
    else: bear += 1

    # 4. Fair Value Gap (FVG)
    if "Bullish FVG" in d["fvg"]: bull += 1
    elif "Bearish FVG" in d["fvg"]: bear += 1
    elif d["ema20"] > d["ema50"]: bull += 1
    else: bear += 1

    # 5. Liquidity Sweep (SMC)
    if "Bullish Sweep" in d["sweep"]: bull += 1
    elif "Bearish Sweep" in d["sweep"]: bear += 1
    elif d["ema20"] > d["ema50"]: bull += 1
    else: bear += 1

    # 6. Fixed Range Volume POC Level
    if d["price"] >= d["poc"]: bull += 1
    else: bear += 1

    # 7. Bollinger Bands
    if d["price"] <= d["bb_lower"] * 1.01: bull += 1
    elif d["price"] >= d["bb_upper"] * 0.99: bear += 1
    elif d["ema20"] > d["ema50"]: bull += 1
    else: bear += 1

    # 8. Divergence
    if d["divergence"] == "Bullish Divergence": bull += 1
    elif d["divergence"] == "Bearish Divergence": bear += 1
    elif d["ema20"] > d["ema50"]: bull += 1
    else: bear += 1

    # 9. Volume Spike
    if d["vol_status"] == "High Volume":
        if d["ema20"] > d["ema50"]: bull += 1
        else: bear += 1
    else:
        if d["ema20"] > d["ema50"]: bull += 1
        else: bear += 1

    # 10. Overall SMC Market Structure Alignment
    if d["ema20"] > d["ema50"] and d["price"] > d["poc"]: bull += 1
    else: bear += 1

    # 80% Filtering (10 में से 8 Matched)
    if bull >= 8:
        return True, "BULLISH", bull
    elif bear >= 8:
        return True, "BEARISH", bear
    
    return False, "NEUTRAL", max(bull, bear)

def generate_trading_signal(d, signal_type, matched_count):
    is_bullish = (signal_type == "BULLISH")
    trend = "Institutional Bullish Setup 🚀" if is_bullish else "Institutional Bearish Setup 📉"
    
    entry_start = round(d["price"] * (0.996 if is_bullish else 1.001), 2)
    entry_end = round(d["price"] * (1.002 if is_bullish else 1.008), 2)
    
    if is_bullish:
        tp1 = round(d["price"] * 1.025, 2)
        tp2 = round(d["price"] * 1.050, 2)
        sl = round(d["price"] * 0.982, 2)
    else:
        tp1 = round(d["price"] * 0.975, 2)
        tp2 = round(d["price"] * 0.950, 2)
        sl = round(d["price"] * 1.018, 2)

    return f"""🎯 **SMC + ICT INSTITUTIONAL SIGNAL ({matched_count}/10 Matched)**

📈 **Market Bias:** {trend}
📊 **Confluence Rate:** {matched_count * 10}% Agreement
🔍 **RSI:** {d['rsi']} | **Volume POC:** ${d['poc']}
⚡ **FVG Status:** {d['fvg']}
💧 **Liquidity Sweep:** {d['sweep']}
💡 **Divergence:** {d['divergence']} | **Volume:** {d['vol_status']}

🎯 **Suggested Entry:** ${entry_start:,.2f} - ${entry_end:,.2f}
🎯 **Targets:** TP1: ${tp1:,.2f} | TP2: ${tp2:,.2f}
🛑 **Strict Stop Loss:** ${sl:,.2f}"""

def send_telegram_alert(message):
    try:
        send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        
        data_bytes = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(send_url, data=data_bytes, headers={'Content-Type': 'application/json'})
        
        with urllib.request.urlopen(req) as response:
            res = json.loads(response.read().decode('utf-8'))
            if res.get("ok"):
                print("✅ Telegram alert sent successfully!")
            else:
                print("❌ Telegram Error:", res)
    except Exception as e:
        print("❌ Telegram Send Error:", e)

if __name__ == "__main__":
    print("🔎 Scanning Market across 10 SMC & Technical Indicators...")
    market_data = fetch_btc_data()
    
    if market_data:
        has_signal, signal_type, matched_count = check_80_percent_confluence(market_data)
        
        if has_signal:
            print(f"🔥 Institutional Signal Detected ({matched_count}/10 matched). Sending Alert...")
            msg = generate_trading_signal(market_data, signal_type, matched_count)
            send_telegram_alert(msg)
        else:
            print(f"⏳ Normal Market Conditions ({matched_count}/10 matched). Alert skipped to avoid bad trades.")

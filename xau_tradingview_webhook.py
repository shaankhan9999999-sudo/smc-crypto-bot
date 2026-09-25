#!/usr/bin/env python3
"""TradingView XAUUSD webhook state service. TradingView Pine sends one completed M5 candle on every M5 bar close. This service stores a rolling M5 history and exposes server-built M5/M15/H1 OHLC candles to the GitHub Actions EMA bot. Endpoints: POST /webhook?token=... GET /state?token=... GET /health """

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request

app = Flask(__name__)
LOCK = threading.Lock()
STATE_FILE = Path(os.getenv("XAU_WEBHOOK_STATE_FILE", "xau_tv_m5.csv"))
TOKEN = os.getenv("XAU_WEBHOOK_TOKEN", "").strip()
MAX_M5 = max(260, int(os.getenv("XAU_MAX_M5", "700")))


def log(msg):
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S UTC}] {msg}", flush=True)


def empty_frame():
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def load_frame():
    if not STATE_FILE.exists():
        return empty_frame()
    try:
        d = pd.read_csv(STATE_FILE)
        need = {"ts", "open", "high", "low", "close", "volume"}
        if not need.issubset(d.columns):
            raise ValueError("state file columns are invalid")
        d["ts"] = pd.to_datetime(d["ts"], utc=True)
        for c in ["open", "high", "low", "close", "volume"]:
            d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d.dropna(subset=["ts", "open", "high", "low", "close"])
        return d.drop_duplicates("ts").sort_values("ts").set_index("ts").tail(MAX_M5)[["open","high","low","close","volume"]]
    except Exception as exc:
        log(f"state load failed: {exc}")
        return empty_frame()


def save_frame(d):
    x = d.copy().sort_index().tail(MAX_M5)
    x.index.name = "ts"
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    x.reset_index().to_csv(tmp, index=False)
    tmp.replace(STATE_FILE)


def authorized():
    if not TOKEN:
        return True
    supplied = request.args.get("token", "")
    if supplied != TOKEN:
        return False
    return True


def clean_payload(data):
    # Pine sends Unix milliseconds and completed-bar OHLCV.
    if not isinstance(data, dict):
        raise ValueError("JSON object required")
    symbol = str(data.get("symbol", "")).strip()
    tickerid = str(data.get("tickerid", "")).strip()
    timeframe = str(data.get("timeframe", "")).strip()
    if not symbol or not tickerid:
        raise ValueError("symbol/tickerid missing")
    if timeframe != "5":
        raise ValueError("webhook accepts only M5 payloads")
    ts = pd.to_datetime(int(data["time"]), unit="ms", utc=True)
    row = {
        "ts": ts,
        "open": float(data["open"]),
        "high": float(data["high"]),
        "low": float(data["low"]),
        "close": float(data["close"]),
        "volume": float(data.get("volume", 0.0)),
    }
    if not (0 < row["low"] <= row["high"] < 10000):
        raise ValueError("XAU price sanity check failed")
    if not (row["low"] <= row["open"] <= row["high"] and row["low"] <= row["close"] <= row["high"]):
        raise ValueError("OHLC relationship invalid")
    return symbol, tickerid, pd.DataFrame([row]).set_index("ts")


def resampled(d, rule):
    x = d[["open", "high", "low", "close", "volume"]].sort_index()
    o = x["open"].resample(rule, label="left", closed="left").first()
    h = x["high"].resample(rule, label="left", closed="left").max()
    l = x["low"].resample(rule, label="left", closed="left").min()
    c = x["close"].resample(rule, label="left", closed="left").last()
    v = x["volume"].resample(rule, label="left", closed="left").sum()
    out = pd.concat([o,h,l,c,v], axis=1)
    out.columns = ["open","high","low","close","volume"]
    return out.dropna(subset=["open","high","low","close"])


def rows(d):
    x = d.copy().sort_index()
    # Only completed bars: for M5 the latest received bar is complete by definition;
    # for M15/H1, drop the currently forming bucket.
    return [
        {"ts": idx.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close), "volume": float(r.volume)}
        for idx, r in x.iterrows()
    ]


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "xau-tradingview-webhook"})


@app.post("/webhook")
def webhook():
    if not authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        symbol, tickerid, one = clean_payload(request.get_json(silent=False))
        with LOCK:
            d = load_frame()
            d = pd.concat([d, one]).sort_index()
            d = d[~d.index.duplicated(keep="last")].tail(MAX_M5)
            save_frame(d)
            count = len(d)
        return jsonify({"ok": True, "symbol": symbol, "tickerid": tickerid, "m5_count": count, "ts": one.index[0].isoformat()})
    except Exception as exc:
        log(f"webhook rejected: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/state")
def state():
    if not authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    with LOCK:
        d = load_frame()
    if len(d) < 230:
        return jsonify({"ok": False, "error": f"warming up: {len(d)}/230 M5 candles", "frames": {}}), 503
    # The newest M5 bar is closed because TradingView sends it via once-per-bar-close.
    # Therefore the newest M15/H1 buckets are also closed only when their endpoint
    # has passed. Drop buckets whose close-time has not yet arrived.
    now_ts = pd.Timestamp.now(tz="UTC")
    m5 = d
    m15 = resampled(d, "15min")
    h1 = resampled(d, "1h")
    m15 = m15[m15.index + pd.Timedelta(minutes=15) <= now_ts]
    h1 = h1[h1.index + pd.Timedelta(hours=1) <= now_ts]
    return jsonify({"ok": True, "source": "TradingView", "frames": {"M5": rows(m5), "M15": rows(m15), "H1": rows(h1)}})


if __name__ == "__main__":
    # Local development only. Production should use gunicorn xau_tradingview_webhook:app.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

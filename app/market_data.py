"""Market data: historical bootstrap + Binance WebSocket streams."""

import time
import json as _json
import threading
from datetime import datetime

import requests
import websocket

from app.config import MANIPULATION_THRESHOLD, CANDLE_LIMIT, SYMBOL_DISPLAY
from app.state import (SYMBOLS, candles, ticker, daily_atr, daily_atr_threshold,
                        indicators, state_lock, ws_stop_event, ws_thread_ref,
                        ws_lock, broadcast_sse)
from app.indicators import atr


# ─── Dynamic decimal precision for low-price tokens ────────────────────────
def _price_precision(price):
    """Pick decimal places for display — 8 for sub-dollar, 4 otherwise."""
    if price <= 0:
        return 8
    return 8 if price < 1.0 else 4


# ─── Historical Bootstrap ─────────────────────────────────────────────────────

def bootstrap_historical_candles(sym):
    from app.strategy import log_event
    symbol = sym.upper()
    for tf_key, interval in [("5m", "5m"), ("15m", "15m")]:
        try:
            url = (f"https://api.binance.com/api/v3/klines"
                   f"?symbol={symbol}&interval={interval}&limit={CANDLE_LIMIT}")
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                log_event(f"Bootstrap {sym} {tf_key} failed: "
                          f"HTTP {resp.status_code}", "WARN")
                continue
            data = resp.json()
            boot_candles = []
            for k in data:
                boot_candles.append({
                    "time": datetime.fromtimestamp(k[0] / 1000).strftime(
                        "%Y-%m-%d %H:%M"),
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "volume": float(k[5]),
                })
            with state_lock:
                existing = candles[sym][tf_key]
                existing.extend(boot_candles)
                seen = set()
                deduped = []
                for c in existing:
                    if c["time"] not in seen:
                        seen.add(c["time"])
                        deduped.append(c)
                deduped.sort(key=lambda x: x["time"])
                candles[sym][tf_key] = deduped[-CANDLE_LIMIT:]
            log_event(f"Bootstrapped {len(boot_candles)} {tf_key} candles "
                      f"for {symbol}", "SYSTEM")
        except Exception as e:
            log_event(f"Bootstrap {sym} {tf_key} error: {e}", "WARN")
    compute_daily_atr(sym)


def bootstrap_all():
    for sym in SYMBOLS:
        bootstrap_historical_candles(sym)


# ─── Daily ATR ─────────────────────────────────────────────────────────────────

def compute_daily_atr(sym):
    from app.strategy import log_event
    symbol = sym.upper()
    try:
        url = (f"https://api.binance.com/api/v3/klines"
               f"?symbol={symbol}&interval=1d&limit=30")
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            log_event(f"Daily ATR {sym} fetch failed: "
                      f"HTTP {resp.status_code}", "WARN")
            return _fallback_atr(sym)
        data = resp.json()
        if len(data) < 15:
            log_event(f"Daily ATR {sym}: only {len(data)} daily candles, "
                      f"falling back to 15m calc", "WARN")
            return _fallback_atr(sym)
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        closes = [float(k[4]) for k in data]
        d_atr = atr(highs, lows, closes, 14)
        if d_atr <= 0:
            log_event(f"Daily ATR {sym} = 0, falling back to 15m calc", "WARN")
            return _fallback_atr(sym)
        threshold = d_atr * MANIPULATION_THRESHOLD
        _set_atr(sym, d_atr, threshold)
        return
    except Exception as e:
        log_event(f"Daily ATR {sym} error: {e}", "WARN")
        _fallback_atr(sym)


def _fallback_atr(sym):
    """Compute ATR from bootstrapped 15m candles as fallback for low-price tokens."""
    from app.strategy import log_event
    with state_lock:
        c15 = list(candles.get(sym, {}).get("15m", []))
    if len(c15) < 15:
        log_event(f"Fallback ATR {sym}: only {len(c15)} 15m candles, "
                  f"need 15", "WARN")
        return
    highs = [c["high"] for c in c15]
    lows = [c["low"] for c in c15]
    closes = [c["close"] for c in c15]
    # Compute ATR on 15m candles
    atr_val = atr(highs, lows, closes, 14)
    if atr_val <= 0:
        log_event(f"Fallback ATR {sym} still 0", "WARN")
        return
    # Scale: 15m ATR ≈ daily ATR / sqrt(96) since there are 96 15m bars per day
    daily_est = atr_val * (96 ** 0.5)
    threshold = daily_est * MANIPULATION_THRESHOLD
    _set_atr(sym, daily_est, threshold)
    log_event(f"{sym} Daily ATR(14)=${daily_est:.{_price_precision(daily_est)}f} "
              f"(from 15m fallback) | "
              f"Threshold=${threshold:.{_price_precision(threshold)}f}", "SYSTEM")


def _set_atr(sym, d_atr, threshold):
    daily_atr[sym] = d_atr
    daily_atr_threshold[sym] = threshold
    with state_lock:
        indicators[sym]["daily_atr"] = round(d_atr, 8)
        indicators[sym]["daily_atr_threshold"] = round(threshold, 8)


# ─── WebSocket Streamer ───────────────────────────────────────────────────────

def make_ws_url():
    streams = []
    for sym in SYMBOLS:
        s = sym.lower()
        streams.append(f"{s}@kline_5m")
        streams.append(f"{s}@kline_15m")
        streams.append(f"{s}@miniTicker")
    return f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"


def on_message(ws, raw):
    from app.strategy import log_event
    try:
        data = _json.loads(raw)
        stream = data.get("stream", "")
        msg = data.get("data", {})
    except Exception:
        return
    try:
        matched_sym = None
        for sym in SYMBOLS:
            if stream.startswith(sym.lower()):
                matched_sym = sym
                break
        if matched_sym is None:
            return

        sym = matched_sym
        sl = sym.lower()

        if stream == f"{sl}@miniTicker":
            price = float(msg.get("c", 0))
            prev_price = 0.0
            with state_lock:
                prev_price = ticker[sym]["price"]
                ticker[sym]["price"] = price
                ticker[sym]["change_pct"] = float(msg.get("P", 0))
                ticker[sym]["high"] = float(msg.get("h", 0))
                ticker[sym]["low"] = float(msg.get("l", 0))
                ticker[sym]["volume"] = float(msg.get("v", 0))
            # Broadcast SSE price update
            if prev_price != price:
                broadcast_sse({
                    "event": "ticker",
                    "symbol": sym,
                    "price": price,
                    "change_pct": ticker[sym]["change_pct"],
                    "prev_price": prev_price,
                })
            return

        if "kline" in stream:
            k = msg.get("k", {})
            tf = None
            if stream == f"{sl}@kline_5m":
                tf = "5m"
            elif stream == f"{sl}@kline_15m":
                tf = "15m"
            if not tf:
                return

            candle = {
                "time": datetime.fromtimestamp(k["t"] / 1000).strftime(
                    "%Y-%m-%d %H:%M"),
                "open": float(k["o"]), "high": float(k["h"]),
                "low": float(k["l"]), "close": float(k["c"]),
                "volume": float(k["v"]),
            }
            is_closed = k.get("x", False)
            with state_lock:
                c_list = candles[sym][tf]
                c_list.append(candle)
                seen = set()
                deduped = []
                for c in c_list:
                    if c["time"] not in seen:
                        seen.add(c["time"])
                        deduped.append(c)
                deduped.sort(key=lambda x: x["time"])
                candles[sym][tf] = deduped[-CANDLE_LIMIT:]
            # Broadcast SSE kline update
            broadcast_sse({
                "event": "kline",
                "symbol": sym,
                "tf": tf,
                "candle": candle,
                "is_closed": is_closed,
            })
    except Exception as e:
        log_event(f"WS handler error: {e}", "ERROR")


def on_error(ws, error):
    from app.strategy import log_event
    log_event(f"WebSocket error: {error}", "ERROR")


def on_close(ws, code, msg):
    from app.strategy import log_event
    log_event(f"WebSocket closed (code={code}). Reconnecting in 5s...", "WARN")
    time.sleep(5)
    if not ws_stop_event.is_set():
        _run_ws_forever()


def on_open(ws):
    from app.strategy import log_event
    log_event(f"WebSocket connected — {len(SYMBOLS)} symbols, "
              f"{len(SYMBOLS) * 3} streams", "SYSTEM")


def _run_ws_forever():
    ws_url = make_ws_url()
    while not ws_stop_event.is_set():
        try:
            ws = websocket.WebSocketApp(ws_url,
                                        on_message=on_message,
                                        on_error=on_error,
                                        on_close=on_close,
                                        on_open=on_open)
            ws.run_forever(ping_interval=30, ping_timeout=20)
        except Exception as e:
            if ws_stop_event.is_set():
                break
            from app.strategy import log_event
            log_event(f"WebSocket connect failed: {e}. "
                      f"Retrying in 5s...", "ERROR")
            time.sleep(5)


def start_websocket():
    global ws_thread_ref
    with ws_lock:
        ws_thread_ref = threading.current_thread()
    _run_ws_forever()
    from app.strategy import log_event
    log_event("WebSocket thread exiting.", "SYSTEM")


def _trigger_ws_restart():
    """Signal WS to restart with new symbol list."""
    from app.strategy import log_event
    with ws_lock:
        ws_stop_event.set()
    time.sleep(0.5)
    ws_stop_event.clear()
    t = threading.Thread(target=start_websocket, daemon=True,
                         name="ws-streamer")
    t.start()

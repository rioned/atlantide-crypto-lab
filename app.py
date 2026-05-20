"""
ATLANTIDE CRYPTO LAB — Multi-Symbol Pattern Scalp Simulator
Per-symbol independent capital | 10% risk per trade | Dynamic symbol selection
Survives reboot via SQLite persistence.
"""
import time, math, threading, os, json, sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import websocket, requests

# ─── Configuration ────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "atlantide.db")
INITIAL_CAPITAL_PER_SYMBOL = 500.0
RISK_PCT = 0.10          # 10% of symbol capital at risk per trade
LEVERAGE = 5
MAX_OPEN_TRADES_PER_SYMBOL = 5
MANIPULATION_THRESHOLD = 0.20
JOHN_WICK_WICK_RATIO = 0.60
POWER_TOWER_RETRACE = 0.30
TP_RANGE_PCT = 0.50
CANDLE_LIMIT = 200
TRADE_ID_COUNTER = 0
TRADE_ID_LOCK = threading.Lock()

# ─── Active Symbols (loaded from DB, mutable via API) ─────────────────────────
SYMBOLS = []              # e.g. ["BTCUSDT", "ETHUSDT", ...]
SYMBOL_DISPLAY = {}       # e.g. {"BTCUSDT": "BTC/USDT"}

# ─── Per-Symbol Capital ───────────────────────────────────────────────────────
# capital[sym] = {balance, initial, peak, max_dd, total_trades, winning_trades, total_pnl}
capital = {}
capital_lock = threading.Lock()

# ─── Global State ─────────────────────────────────────────────────────────────
state_lock = threading.Lock()

candles = {}          # s -> {"5m": [], "15m": []}
ticker = {}           # s -> {price, change_pct, high, low, volume}
signal_state = {}     # s -> {signal, direction, tp, sl, manipulation_range, pattern_type, last_entry_signal}
daily_atr = {}        # s -> float
daily_atr_threshold = {}  # s -> float
manipulation_candle = {}  # s -> dict | None
manipulation_active = {}  # s -> bool
reversal_pattern = {}     # s -> dict | None
last_15m_time = {}        # s -> str | None
open_trades = {}     # s -> [trade, ...]
indicators = {}      # s -> {"daily_atr", "daily_atr_threshold", "5m_atr14"}

closed_trades = []
event_log = []

# WebSocket control
ws_stop_event = threading.Event()
ws_thread_ref = None
ws_lock = threading.Lock()

# ─── Market Sessions (UTC open/close times) ──────────────────────────────────
MARKET_SESSIONS = [
    {
        "id": "asian", "name": "Asian (Tokyo)",
        "open_utc_hour": 0, "open_utc_minute": 0,
        "close_utc_hour": 9, "close_utc_minute": 0,
        "emoji": "🇯🇵",
    },
    {
        "id": "european", "name": "European (London)",
        "open_utc_hour": 8, "open_utc_minute": 0,
        "close_utc_hour": 17, "close_utc_minute": 0,
        "emoji": "🇬🇧",
    },
    {
        "id": "us", "name": "US (New York)",
        "open_utc_hour": 13, "open_utc_minute": 30,
        "close_utc_hour": 21, "close_utc_minute": 0,
        "emoji": "🇺🇸",
    },
]

def compute_session_data(utc_now, tz_offset_hours):
    """Return session state dicts with countdown in seconds."""
    sessions = []
    for s in MARKET_SESSIONS:
        # Build today's open/close as UTC datetimes
        open_dt = utc_now.replace(hour=s["open_utc_hour"], minute=s["open_utc_minute"], second=0, microsecond=0)
        close_dt = utc_now.replace(hour=s["close_utc_hour"], minute=s["close_utc_minute"], second=0, microsecond=0)

        # If session already closed today, shift to tomorrow
        if utc_now >= close_dt:
            open_dt += timedelta(days=1)
            close_dt += timedelta(days=1)

        is_open = utc_now >= open_dt and utc_now < close_dt
        time_until_open = max(0, (open_dt - utc_now).total_seconds()) if not is_open else 0
        time_until_close = max(0, (close_dt - utc_now).total_seconds()) if is_open else 0

        # Convert to local time
        local_open = open_dt + timedelta(hours=tz_offset_hours)
        local_close = close_dt + timedelta(hours=tz_offset_hours)

        sessions.append({
            "id": s["id"],
            "name": s["name"],
            "emoji": s["emoji"],
            "is_open": is_open,
            "time_until_open": int(time_until_open),
            "time_until_close": int(time_until_close),
            "open_utc": open_dt.strftime("%H:%M UTC"),
            "close_utc": close_dt.strftime("%H:%M UTC"),
            "open_local": local_open.strftime("%H:%M"),
            "close_local": local_close.strftime("%H:%M"),
            "open_iso": open_dt.isoformat(),
            "close_iso": close_dt.isoformat(),
        })
    return sessions

# ─── Top 100 Available Symbols ────────────────────────────────────────────────
TOP100_USDT_SYMBOLS = [
    ("BTC", "BTC/USDT"), ("ETH", "ETH/USDT"), ("BNB", "BNB/USDT"),
    ("SOL", "SOL/USDT"), ("XRP", "XRP/USDT"), ("DOGE", "DOGE/USDT"),
    ("ADA", "ADA/USDT"), ("AVAX", "AVAX/USDT"), ("DOT", "DOT/USDT"),
    ("LINK", "LINK/USDT"), ("MATIC", "MATIC/USDT"), ("SHIB", "SHIB/USDT"),
    ("LTC", "LTC/USDT"), ("UNI", "UNI/USDT"), ("ATOM", "ATOM/USDT"),
    ("ETC", "ETC/USDT"), ("XLM", "XLM/USDT"), ("FIL", "FIL/USDT"),
    ("TRX", "TRX/USDT"), ("NEAR", "NEAR/USDT"), ("APT", "APT/USDT"),
    ("ARB", "ARB/USDT"), ("OP", "OP/USDT"), ("SUI", "SUI/USDT"),
    ("INJ", "INJ/USDT"), ("TIA", "TIA/USDT"), ("SEI", "SEI/USDT"),
    ("FTM", "FTM/USDT"), ("RUNE", "RUNE/USDT"), ("AAVE", "AAVE/USDT"),
    ("ALGO", "ALGO/USDT"), ("VET", "VET/USDT"), ("ICP", "ICP/USDT"),
    ("GRT", "GRT/USDT"), ("THETA", "THETA/USDT"), ("SAND", "SAND/USDT"),
    ("MANA", "MANA/USDT"), ("AXS", "AXS/USDT"), ("EGLD", "EGLD/USDT"),
    ("KLAY", "KLAY/USDT"), ("EOS", "EOS/USDT"), ("FLOW", "FLOW/USDT"),
    ("XTZ", "XTZ/USDT"), ("CRV", "CRV/USDT"), ("DYDX", "DYDX/USDT"),
    ("RNDR", "RNDR/USDT"), ("FET", "FET/USDT"), ("AGIX", "AGIX/USDT"),
    ("WLD", "WLD/USDT"), ("PEPE", "PEPE/USDT"), ("WIF", "WIF/USDT"),
    ("BONK", "BONK/USDT"), ("JUP", "JUP/USDT"), ("PYTH", "PYTH/USDT"),
    ("JTO", "JTO/USDT"), ("STRK", "STRK/USDT"), ("ENA", "ENA/USDT"),
    ("TAO", "TAO/USDT"), ("STX", "STX/USDT"), ("IMX", "IMX/USDT"),
    ("LDO", "LDO/USDT"), ("RAY", "RAY/USDT"), ("HNT", "HNT/USDT"),
    ("KAS", "KAS/USDT"), ("ONDO", "ONDO/USDT"), ("MKR", "MKR/USDT"),
    ("QNT", "QNT/USDT"), ("SNX", "SNX/USDT"), ("GALA", "GALA/USDT"),
    ("ORDI", "ORDI/USDT"), ("1000SATS", "1000SATS/USDT"),
    ("ZRO", "ZRO/USDT"), ("IO", "IO/USDT"), ("NOT", "NOT/USDT"),
    ("PEOPLE", "PEOPLE/USDT"), ("ENS", "ENS/USDT"), ("GMT", "GMT/USDT"),
    ("BLUR", "BLUR/USDT"), ("AEVO", "AEVO/USDT"), ("PORTAL", "PORTAL/USDT"),
    ("PENDLE", "PENDLE/USDT"), ("EIGEN", "EIGEN/USDT"),
    ("ZK", "ZK/USDT"), ("ZETA", "ZETA/USDT"), ("W", "W/USDT"),
    ("BOME", "BOME/USDT"), ("SLERF", "SLERF/USDT"),
    ("TURBO", "TURBO/USDT"), ("NEIRO", "NEIRO/USDT"),
    ("POPCAT", "POPCAT/USDT"), ("GOAT", "GOAT/USDT"),
    ("MEW", "MEW/USDT"), ("DOGS", "DOGS/USDT"),
    ("BRETT", "BRETT/USDT"), ("MOG", "MOG/USDT"),
    ("PNUT", "PNUT/USDT"), ("ACT", "ACT/USDT"),
    ("MOODENG", "MOODENG/USDT"), ("PENGU", "PENGU/USDT"),
]

# Display mapping for all known symbols
for code, display in TOP100_USDT_SYMBOLS:
    SYMBOL_DISPLAY[code + "USDT"] = display

# ─── Init Per-Symbol State ────────────────────────────────────────────────────
def init_symbol_state(sym):
    candles[sym] = {"5m": [], "15m": []}
    ticker[sym] = {"price": 0.0, "change_pct": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0}
    signal_state[sym] = {"signal": None, "direction": 0, "tp": 0.0, "sl": 0.0,
                         "manipulation_range": 0.0, "pattern_type": "", "last_entry_signal": None}
    daily_atr[sym] = 0.0
    daily_atr_threshold[sym] = 0.0
    manipulation_candle[sym] = None
    manipulation_active[sym] = False
    reversal_pattern[sym] = None
    last_15m_time[sym] = None
    open_trades[sym] = []
    indicators[sym] = {"daily_atr": 0.0, "daily_atr_threshold": 0.0, "5m_atr14": 0.0}

def remove_symbol_state(sym):
    for d in [candles, ticker, signal_state, daily_atr, daily_atr_threshold,
              manipulation_candle, manipulation_active, reversal_pattern,
              last_15m_time, open_trades, indicators]:
        d.pop(sym, None)

# ─── Helpers ──────────────────────────────────────────────────────────────────
def sym_lower(sym):
    return sym.lower()

def sym_upper(sym):
    return sym.upper()

# ─── SQLite Persistence ───────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS symbol_capital (
            symbol TEXT PRIMARY KEY,
            balance REAL,
            initial REAL,
            peak REAL,
            max_dd REAL,
            total_trades INTEGER DEFAULT 0,
            winning_trades INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0.0
        );
        CREATE TABLE IF NOT EXISTS symbol_config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT DEFAULT '',
            entry_time TEXT,
            exit_time TEXT,
            entry_price REAL,
            exit_price REAL,
            direction INTEGER,
            pnl REAL,
            reason TEXT
        );
        CREATE TABLE IF NOT EXISTS open_trades (
            trade_id INTEGER,
            symbol TEXT DEFAULT '',
            entry_time TEXT,
            entry_price REAL,
            direction INTEGER,
            tp REAL,
            sl REAL,
            notional REAL DEFAULT 250.0,
            margin REAL DEFAULT 50.0,
            unrealized_pnl REAL DEFAULT 0.0,
            PRIMARY KEY (trade_id, symbol)
        );
        CREATE TABLE IF NOT EXISTS event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            message TEXT,
            level TEXT
        );
    """)
    conn.commit()
    conn.close()

def save_state():
    with state_lock:
        conn = get_db()
        # Save per-symbol capital
        conn.execute("DELETE FROM symbol_capital")
        for sym, cap in capital.items():
            conn.execute(
                "INSERT OR REPLACE INTO symbol_capital (symbol, balance, initial, peak, max_dd, total_trades, winning_trades, total_pnl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sym, cap["balance"], cap["initial"], cap["peak"], cap["max_dd"],
                 cap["total_trades"], cap["winning_trades"], cap["total_pnl"]))
        # Save active symbols + trade_id_counter
        conn.execute("DELETE FROM symbol_config")
        conn.execute("INSERT OR REPLACE INTO symbol_config (key, value) VALUES (?, ?)",
                     ("active_symbols", ",".join(SYMBOLS)))
        conn.execute("INSERT OR REPLACE INTO symbol_config (key, value) VALUES (?, ?)",
                     ("trade_id_counter", str(TRADE_ID_COUNTER)))
        # Save open trades
        conn.execute("DELETE FROM open_trades")
        for sym in SYMBOLS:
            for t in open_trades[sym]:
                conn.execute(
                    "INSERT OR REPLACE INTO open_trades (trade_id, symbol, entry_time, entry_price, direction, tp, sl, notional, margin, unrealized_pnl) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (t["id"], sym, t["entry_time"], t["entry"], t["direction"],
                     t["tp"], t["sl"], t.get("notional", 250.0), t.get("margin", 50.0),
                     t.get("unrealized_pnl", 0.0)))
        conn.commit()
        conn.close()

def save_closed_trade(trade, sym):
    conn = get_db()
    conn.execute(
        "INSERT INTO closed_trades (symbol, entry_time, exit_time, entry_price, exit_price, direction, pnl, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sym, trade["entry_time"], trade["exit_time"], trade["entry"],
         trade["exit_price"], trade["direction"], trade["pnl"], trade["reason"]))
    conn.commit()
    conn.close()

def save_event(msg, level="INFO"):
    conn = get_db()
    conn.execute("INSERT INTO event_log (timestamp, message, level) VALUES (?, ?, ?)",
                 (datetime.now().isoformat(), msg, level))
    conn.commit()
    conn.close()

def load_state():
    global capital, closed_trades, event_log, TRADE_ID_COUNTER, SYMBOLS
    conn = get_db()

    # Load active symbols
    rows = conn.execute("SELECT key, value FROM symbol_config").fetchall()
    for row in rows:
        if row["key"] == "active_symbols" and row["value"]:
            loaded = [s.strip() for s in row["value"].split(",") if s.strip()]
            if loaded:
                SYMBOLS = loaded
        elif row["key"] == "trade_id_counter":
            TRADE_ID_COUNTER = int(row["value"])

    # Load per-symbol capital
    rows = conn.execute("SELECT * FROM symbol_capital").fetchall()
    for row in rows:
        sym = row["symbol"]
        capital[sym] = {
            "balance": row["balance"],
            "initial": row["initial"],
            "peak": row["peak"],
            "max_dd": row["max_dd"],
            "total_trades": row["total_trades"] or 0,
            "winning_trades": row["winning_trades"] or 0,
            "total_pnl": row["total_pnl"] or 0.0,
        }

    # Init state for active symbols that have no capital yet
    for sym in SYMBOLS:
        init_symbol_state(sym)
        if sym not in capital:
            capital[sym] = {
                "balance": INITIAL_CAPITAL_PER_SYMBOL,
                "initial": INITIAL_CAPITAL_PER_SYMBOL,
                "peak": INITIAL_CAPITAL_PER_SYMBOL,
                "max_dd": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "total_pnl": 0.0,
            }

    # Load open trades
    rows = conn.execute("SELECT * FROM open_trades ORDER BY trade_id ASC").fetchall()
    for row in rows:
        sym = row["symbol"]
        if sym in SYMBOLS:
            open_trades[sym].append({
                "id": row["trade_id"], "entry_time": row["entry_time"],
                "entry": row["entry_price"], "direction": row["direction"],
                "tp": row["tp"], "sl": row["sl"],
                "notional": row["notional"] if "notional" in row.keys() else 250.0,
                "margin": row["margin"] if "margin" in row.keys() else 50.0,
                "exit_price": None, "exit_time": None, "pnl": 0.0,
                "unrealized_pnl": row["unrealized_pnl"], "reason": "",
            })

    # Load closed trades
    rows = conn.execute("SELECT * FROM closed_trades ORDER BY id DESC LIMIT 500").fetchall()
    loaded = []
    for row in reversed(rows):
        loaded.append({
            "id": row["id"], "symbol": row["symbol"], "entry_time": row["entry_time"],
            "exit_time": row["exit_time"], "entry": row["entry_price"],
            "exit_price": row["exit_price"], "direction": row["direction"],
            "pnl": row["pnl"], "reason": row["reason"],
        })
    with state_lock:
        closed_trades = loaded

    # Load event log
    rows = conn.execute("SELECT * FROM event_log ORDER BY id DESC LIMIT 200").fetchall()
    loaded_events = []
    for row in reversed(rows):
        loaded_events.append({"time": row["timestamp"], "message": row["message"], "level": row["level"]})
    with state_lock:
        event_log = loaded_events

    conn.close()
    return len(SYMBOLS) > 0

# ─── Logging ──────────────────────────────────────────────────────────────────
def log_event(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = {"time": timestamp, "message": msg, "level": level}
    with state_lock:
        event_log.append(entry)
        if len(event_log) > 200:
            event_log.pop(0)
    save_event(msg, level)

# ─── Pure Python Indicators ───────────────────────────────────────────────────
def sma(values, period):
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1 : i + 1]) / period)
    return result

def ema(values, period):
    if not values: return []
    multiplier = 2.0 / (period + 1)
    result = [None] * len(values)
    first_valid = period - 1
    if first_valid >= len(values): return result
    seed = sum(values[:period]) / period
    result[first_valid] = seed
    for i in range(first_valid + 1, len(values)):
        result[i] = (values[i] - result[i - 1]) * multiplier + result[i - 1]
    return result

def rsi(closes, period=14):
    if len(closes) < period + 1: return 0.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = closes[-(period + 1) + i] - closes[-(period + 1) + i - 1]
        if delta > 0: gains += delta
        else: losses += abs(delta)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    rsi_val = 100.0 - (100.0 / (1.0 + rs))
    for i in range(2, len(closes) - period + 1):
        idx = -(period + 1) + i
        delta = closes[idx] - closes[idx - 1]
        gain = max(delta, 0); loss = abs(min(delta, 0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0: rsi_val = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi_val, 2)

def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1: return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if len(trs) < period: return sum(trs) / len(trs) if trs else 0.0
    atr_val = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
    return round(atr_val, 4)

def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal: return 0.0, 0.0, 0.0
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_vals = []
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_vals.append(ema_fast[i] - ema_slow[i])
        else: macd_vals.append(None)
    valid = [v for v in macd_vals if v is not None]
    if len(valid) < signal: return 0.0, 0.0, 0.0
    sig_ema = ema(valid, signal)
    return round(valid[-1], 4), round(sig_ema[-1], 4), round(valid[-1] - sig_ema[-1], 4)

# ─── Historical Bootstrap ─────────────────────────────────────────────────────
def bootstrap_historical_candles(sym):
    symbol = sym_upper(sym)
    for tf_key, interval in [("5m", "5m"), ("15m", "15m")]:
        try:
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=200"
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                log_event(f"Bootstrap {sym} {tf_key} failed: HTTP {resp.status_code}", "WARN")
                continue
            data = resp.json()
            boot_candles = []
            for k in data:
                boot_candles.append({
                    "time": datetime.fromtimestamp(k[0] / 1000).strftime("%Y-%m-%d %H:%M"),
                    "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                    "close": float(k[4]), "volume": float(k[5]),
                })
            with state_lock:
                existing = candles[sym][tf_key]
                existing.extend(boot_candles)
                seen = set(); deduped = []
                for c in existing:
                    if c["time"] not in seen:
                        seen.add(c["time"]); deduped.append(c)
                deduped.sort(key=lambda x: x["time"])
                candles[sym][tf_key] = deduped[-200:]
            log_event(f"Bootstrapped {len(boot_candles)} {tf_key} candles for {symbol}", "SYSTEM")
        except Exception as e:
            log_event(f"Bootstrap {sym} {tf_key} error: {e}", "WARN")
    compute_daily_atr(sym)

def bootstrap_all():
    for sym in SYMBOLS:
        bootstrap_historical_candles(sym)

# ─── Daily ATR ─────────────────────────────────────────────────────────────────
def compute_daily_atr(sym):
    symbol = sym_upper(sym)
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit=30"
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            log_event(f"Daily ATR {sym} fetch failed: HTTP {resp.status_code}", "WARN")
            return
        data = resp.json()
        if len(data) < 15: return
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        closes = [float(k[4]) for k in data]
        d_atr = atr(highs, lows, closes, 14)
        threshold = d_atr * MANIPULATION_THRESHOLD
        daily_atr[sym] = d_atr
        daily_atr_threshold[sym] = threshold
        with state_lock:
            indicators[sym]["daily_atr"] = round(d_atr, 4)
            indicators[sym]["daily_atr_threshold"] = round(threshold, 4)
        log_event(f"{sym} Daily ATR(14)=${d_atr:.4f} | Threshold=${threshold:.4f}", "SYSTEM")
    except Exception as e:
        log_event(f"Daily ATR {sym} error: {e}", "WARN")

# ─── Agent 1: Multi-Symbol WebSocket Streamer ──────────────────────────────────
def make_ws_url():
    streams = []
    for sym in SYMBOLS:
        s = sym_lower(sym)
        streams.append(f"{s}@kline_5m")
        streams.append(f"{s}@kline_15m")
        streams.append(f"{s}@miniTicker")
    return f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

def on_message(ws, raw):
    try:
        data = json.loads(raw)
        stream = data.get("stream", "")
        msg = data.get("data", {})
    except Exception:
        return
    try:
        matched_sym = None
        for sym in SYMBOLS:
            if stream.startswith(sym_lower(sym)):
                matched_sym = sym
                break
        if matched_sym is None:
            return

        sym = matched_sym
        sl = sym_lower(sym)

        if stream == f"{sl}@miniTicker":
            with state_lock:
                ticker[sym]["price"] = float(msg.get("c", ticker[sym]["price"]))
                ticker[sym]["change_pct"] = float(msg.get("P", 0))
                ticker[sym]["high"] = float(msg.get("h", 0))
                ticker[sym]["low"] = float(msg.get("l", 0))
                ticker[sym]["volume"] = float(msg.get("v", 0))
            return

        if "kline" in stream:
            k = msg.get("k", {})
            tf = None
            if stream == f"{sl}@kline_5m": tf = "5m"
            elif stream == f"{sl}@kline_15m": tf = "15m"
            if not tf: return

            candle = {
                "time": datetime.fromtimestamp(k["t"] / 1000).strftime("%Y-%m-%d %H:%M"),
                "open": float(k["o"]), "high": float(k["h"]),
                "low": float(k["l"]), "close": float(k["c"]), "volume": float(k["v"]),
            }
            with state_lock:
                c_list = candles[sym][tf]
                c_list.append(candle)
                seen = set(); deduped = []
                for c in c_list:
                    if c["time"] not in seen:
                        seen.add(c["time"]); deduped.append(c)
                deduped.sort(key=lambda x: x["time"])
                candles[sym][tf] = deduped[-200:]
    except Exception as e:
        log_event(f"WS handler error: {e}", "ERROR")

def on_error(ws, error):
    log_event(f"WebSocket error: {error}", "ERROR")

def on_close(ws, code, msg):
    log_event(f"WebSocket closed (code={code}). Reconnecting in 5s...", "WARN")
    time.sleep(5)
    if not ws_stop_event.is_set():
        _run_ws_forever()

def on_open(ws):
    log_event(f"WebSocket connected — {len(SYMBOLS)} symbols, {len(SYMBOLS)*3} streams", "SYSTEM")

def _run_ws_forever():
    ws_url = make_ws_url()
    while not ws_stop_event.is_set():
        try:
            ws = websocket.WebSocketApp(ws_url, on_message=on_message,
                                        on_error=on_error, on_close=on_close, on_open=on_open)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            if ws_stop_event.is_set(): break
            log_event(f"WebSocket connect failed: {e}. Retrying in 5s...", "ERROR")
            time.sleep(5)

def start_websocket():
    global ws_thread_ref
    with ws_lock:
        ws_thread_ref = threading.current_thread()
    _run_ws_forever()
    log_event("WebSocket thread exiting.", "SYSTEM")

# ─── Agent 2: Pattern Scalp Strategy Engine (per-symbol) ──────────────────────
def detect_john_wick(candle, manip_direction):
    total_range = candle["high"] - candle["low"]
    if total_range <= 0: return False, 0
    upper_wick = candle["high"] - max(candle["open"], candle["close"])
    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
    if manip_direction == "UP":
        if upper_wick / total_range >= JOHN_WICK_WICK_RATIO:
            return True, -1
    else:
        if lower_wick / total_range >= JOHN_WICK_WICK_RATIO:
            return True, 1
    return False, 0

def detect_power_tower(candles_5m, manip_direction):
    if len(candles_5m) < 2: return False, 0
    prev = candles_5m[-2]; curr = candles_5m[-1]
    prev_body = prev["close"] - prev["open"]
    curr_body = curr["close"] - curr["open"]
    if manip_direction == "UP":
        if prev_body > 0 and curr_body < 0:
            if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
                return True, -1
    else:
        if prev_body < 0 and curr_body > 0:
            if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
                return True, 1
    return False, 0

def check_new_manipulation(sym):
    with state_lock:
        c15 = list(candles[sym]["15m"])
    if len(c15) < 2: return None
    latest = c15[-1]
    candle_time = latest["time"]
    if candle_time == last_15m_time.get(sym): return None
    last_15m_time[sym] = candle_time
    candle_range = latest["high"] - latest["low"]
    threshold = daily_atr_threshold.get(sym, 0)
    if threshold <= 0: return None
    if candle_range < threshold:
        manipulation_active[sym] = False
        manipulation_candle[sym] = None
        reversal_pattern[sym] = None
        return None
    open_to_high = latest["high"] - latest["open"]
    open_to_low = latest["open"] - latest["low"]
    if latest["close"] < latest["open"] and open_to_low > open_to_high * 1.5:
        direction = "DOWN"
    elif latest["close"] > latest["open"] and open_to_high > open_to_low * 1.5:
        direction = "UP"
    else:
        direction = "UP" if latest["close"] > latest["open"] else "DOWN"
    manipulation_candle[sym] = {
        "time": candle_time, "high": latest["high"], "low": latest["low"],
        "open": latest["open"], "close": latest["close"],
        "direction": direction, "range": round(candle_range, 4),
    }
    manipulation_active[sym] = True
    reversal_pattern[sym] = None
    log_event(
        f"[{sym}] MANIPULATION: {direction} spike | Range=${candle_range:.4f} (≥${threshold:.4f})",
        "SIGNAL")
    return manipulation_candle[sym]

def evaluate_signal_for_symbol(sym):
    if daily_atr.get(sym, 0) <= 0: return
    with state_lock:
        c5_for_atr = list(candles[sym]["5m"])
    if len(c5_for_atr) >= 15:
        c5_cl = [c["close"] for c in c5_for_atr]
        c5_hi = [c["high"] for c in c5_for_atr]
        c5_lo = [c["low"] for c in c5_for_atr]
        a5 = atr(c5_hi, c5_lo, c5_cl, 14)
        with state_lock:
            indicators[sym]["5m_atr14"] = round(a5, 4)

    check_new_manipulation(sym)

    if not manipulation_active.get(sym) or manipulation_candle.get(sym) is None:
        with state_lock:
            signal_state[sym]["signal"] = None
            signal_state[sym]["direction"] = 0
            signal_state[sym]["tp"] = 0.0
            signal_state[sym]["sl"] = 0.0
            signal_state[sym]["manipulation_range"] = 0.0
            signal_state[sym]["pattern_type"] = ""
        return

    with state_lock:
        c5 = list(candles[sym]["5m"])
    if len(c5) < 3: return

    manip = manipulation_candle[sym]
    manip_dir = manip["direction"]
    manip_range = manip["range"]
    manip_high = manip["high"]
    manip_low = manip["low"]

    pattern_found = None
    pattern_type = ""
    direction = 0

    for offset in [1, 2]:
        idx = -(offset + 1) if offset < len(c5) else None
        if idx is None or abs(idx) > len(c5): continue
        is_wick, wick_dir = detect_john_wick(c5[idx], manip_dir)
        if is_wick:
            pattern_type = "JOHN_WICK"
            direction = wick_dir
            trigger = c5[idx]["high"] if direction == 1 else c5[idx]["low"]
            pattern_found = {"type": pattern_type, "trigger": round(trigger, 4),
                             "direction": direction, "candle_time": c5[idx]["time"]}
            break

    if pattern_found is None:
        is_pt, pt_dir = detect_power_tower(c5, manip_dir)
        if is_pt:
            pattern_type = "POWER_TOWER"
            direction = pt_dir
            last = c5[-1]
            trigger = last["high"] if direction == 1 else last["low"]
            pattern_found = {"type": pattern_type, "trigger": round(trigger, 4),
                             "direction": direction, "candle_time": last["time"]}

    if pattern_found:
        reversal_pattern[sym] = pattern_found
        if direction == 1:
            sl = manip_low
            tp = manip_low + manip_range * TP_RANGE_PCT
        else:
            sl = manip_high
            tp = manip_high - manip_range * TP_RANGE_PCT
        with state_lock:
            signal_state[sym]["signal"] = "LONG" if direction == 1 else "SHORT"
            signal_state[sym]["direction"] = direction
            signal_state[sym]["tp"] = round(tp, 4)
            signal_state[sym]["sl"] = round(sl, 4)
            signal_state[sym]["manipulation_range"] = round(manip_range, 4)
            signal_state[sym]["pattern_type"] = pattern_type
        log_event(
            f"[{sym}] REVERSAL: {pattern_type} → {'LONG' if direction==1 else 'SHORT'} | "
            f"TP=${tp:.4f} SL=${sl:.4f}", "SIGNAL")

    # Expire stale manipulations (>2h)
    try:
        manip_time = datetime.strptime(manipulation_candle[sym]["time"], "%Y-%m-%d %H:%M")
        if datetime.now() - manip_time > timedelta(hours=2):
            log_event(f"[{sym}] Manipulation expired (>2h). Reset.", "SIGNAL")
            manipulation_active[sym] = False
            manipulation_candle[sym] = None
            reversal_pattern[sym] = None
    except Exception:
        pass

def strategy_loop():
    while True:
        try:
            syms = list(SYMBOLS)
            for sym in syms:
                evaluate_signal_for_symbol(sym)
        except Exception as e:
            log_event(f"Strategy error: {e}", "ERROR")
        time.sleep(2)

# ─── Agent 3: Execution & Simulation Engine ───────────────────────────────────
def get_next_trade_id():
    global TRADE_ID_COUNTER
    with TRADE_ID_LOCK:
        TRADE_ID_COUNTER += 1
        return TRADE_ID_COUNTER

def execution_loop():
    last_save = time.time()
    while True:
        try:
            time.sleep(5)
            changed = False
            syms = list(SYMBOLS)

            for sym in syms:
                with state_lock:
                    sig = signal_state[sym]["signal"]
                    current_price = ticker[sym]["price"]
                    trades_local = list(open_trades[sym])

                if current_price <= 0: continue

                for trade in trades_local:
                    notional = trade.get("notional", 250.0)
                    pnl_pct = (current_price - trade["entry"]) / trade["entry"] * trade["direction"]
                    unrealized = pnl_pct * notional
                    if abs(trade.get("unrealized_pnl", 0.0) - round(unrealized, 2)) > 0.005:
                        changed = True
                    trade["unrealized_pnl"] = round(unrealized, 2)

                    close_reason = None
                    if trade["direction"] == 1:
                        if current_price >= trade["tp"]: close_reason = "TP"
                        elif current_price <= trade["sl"]: close_reason = "SL"
                    else:
                        if current_price <= trade["tp"]: close_reason = "TP"
                        elif current_price >= trade["sl"]: close_reason = "SL"

                    if close_reason:
                        trade["exit_price"] = current_price
                        trade["exit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        trade["pnl"] = round(pnl_pct * notional, 2)
                        trade["reason"] = close_reason

                        with capital_lock:
                            cap = capital.get(sym)
                            if cap:
                                cap["balance"] += trade["pnl"]
                                cap["total_pnl"] += trade["pnl"]
                                cap["total_trades"] += 1
                                if trade["pnl"] > 0:
                                    cap["winning_trades"] += 1
                                if cap["balance"] > cap["peak"]:
                                    cap["peak"] = cap["balance"]
                                if cap["peak"] > 0:
                                    dd = (cap["peak"] - cap["balance"]) / cap["peak"] * 100
                                    cap["max_dd"] = max(cap["max_dd"], dd)

                        with state_lock:
                            closed_trades.append(dict(trade))
                            closed_trades[-1]["symbol"] = sym
                            if len(closed_trades) > 500:
                                closed_trades.pop(0)
                            if trade in open_trades[sym]:
                                open_trades[sym].remove(trade)

                        save_closed_trade(trade, sym)
                        save_state()
                        sym_cap = capital.get(sym, {})
                        log_event(
                            f"[{sym}] TRADE {trade['id']} CLOSED ({close_reason}): "
                            f"{'LONG' if trade['direction']==1 else 'SHORT'} "
                            f"Entry=${trade['entry']:.4f} Exit=${trade['exit_price']:.4f} "
                            f"PnL=${trade['pnl']:.2f} | Cap=${sym_cap.get('balance', 0):.2f}",
                            "TRADE")

                # Open new trade on signal — 10% risk position sizing
                with state_lock:
                    last_entry = signal_state[sym].get("last_entry_signal")
                    current_sig = signal_state[sym]["signal"]
                    num_open = len(open_trades[sym])

                if current_sig in ("LONG", "SHORT") and current_sig != last_entry \
                        and num_open < MAX_OPEN_TRADES_PER_SYMBOL:
                    tp = signal_state[sym]["tp"]
                    sl = signal_state[sym]["sl"]
                    direction = signal_state[sym]["direction"]
                    if tp <= 0 or sl <= 0 or current_price <= 0: continue

                    with capital_lock:
                        sym_cap = capital.get(sym)
                        if not sym_cap or sym_cap["balance"] <= 0:
                            continue
                        risk_amount = sym_cap["balance"] * RISK_PCT
                        if risk_amount <= 0: continue

                    # SL distance in price terms
                    sl_distance = abs(current_price - sl)
                    if sl_distance <= 0: continue
                    sl_distance_pct = sl_distance / current_price

                    # Position notional = risk / SL_distance_pct
                    notional = risk_amount / sl_distance_pct
                    margin = notional / LEVERAGE

                    # Cap: don't let margin exceed available balance
                    with capital_lock:
                        sym_cap = capital.get(sym, {})
                        available = sym_cap.get("balance", 0)
                        # Reserve margin but allow up to 2x safety
                        if margin > available * 2:
                            margin = available * 2
                            notional = margin * LEVERAGE

                    trade = {
                        "id": get_next_trade_id(),
                        "symbol": sym,
                        "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "entry": current_price, "direction": direction,
                        "tp": round(tp, 4), "sl": round(sl, 4),
                        "notional": round(notional, 2),
                        "margin": round(margin, 2),
                        "exit_price": None, "exit_time": None, "pnl": 0.0,
                        "unrealized_pnl": 0.0, "reason": "",
                    }
                    with state_lock:
                        open_trades[sym].append(trade)
                        signal_state[sym]["last_entry_signal"] = current_sig
                    save_state()
                    pat = signal_state[sym].get("pattern_type", "")
                    log_event(
                        f"[{sym}] TRADE {trade['id']} OPENED ({current_sig}): "
                        f"Entry=${current_price:.4f} TP=${tp:.4f} SL=${sl:.4f} "
                        f"Notional=${notional:.0f} Margin=${margin:.2f} Risk=${risk_amount:.2f} Pattern={pat}",
                        "TRADE")

            now_save = time.time()
            total_open = sum(len(open_trades[s]) for s in SYMBOLS)
            if total_open > 0 and (changed or now_save - last_save > 30):
                save_state()
                last_save = now_save

        except Exception as e:
            log_event(f"Execution error: {e}", "ERROR")
            time.sleep(5)

# ─── Flask App ────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

@app.route("/")
def index():
    return render_template("dashboard.html")

def _aggregated_account():
    """Compute combined stats across all symbols."""
    total_bal = 0.0
    total_initial = 0.0
    total_peak = 0.0
    total_trades = 0
    total_wins = 0
    total_pnl = 0.0
    max_dd = 0.0
    with capital_lock:
        caps = dict(capital)
    for cap in caps.values():
        total_bal += cap["balance"]
        total_initial += cap["initial"]
        total_peak += cap["peak"]
        total_trades += cap["total_trades"]
        total_wins += cap["winning_trades"]
        total_pnl += cap["total_pnl"]
        max_dd = max(max_dd, cap["max_dd"])
    winrate = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
    return {
        "balance": round(total_bal, 2),
        "initial": round(total_initial, 2),
        "peak": round(total_peak, 2),
        "max_drawdown": round(max_dd, 2),
        "total_trades": total_trades,
        "winning_trades": total_wins,
        "winrate": round(winrate, 1),
        "total_pnl": round(total_pnl, 2),
    }

@app.route("/api/state")
def api_state():
    with state_lock:
        logs = list(event_log[-50:])
        all_closed = list(closed_trades[-50:])

    # Timezone: from query param or default UTC
    try:
        tz_offset = float(request.args.get("tz", "0"))
    except (ValueError, TypeError):
        tz_offset = 0.0
    utc_now = datetime.utcnow()
    sessions = compute_session_data(utc_now, tz_offset)

    symbols_data = {}
    for sym in SYMBOLS:
        with state_lock:
            sig = dict(signal_state[sym])
            ind = dict(indicators[sym])
            tick = dict(ticker[sym])
            open_t = list(open_trades[sym])
            c5 = list(candles[sym]["5m"][-60:])
            c15 = list(candles[sym]["15m"][-20:])
            mp = dict(manipulation_candle[sym]) if manipulation_candle[sym] else None
            rp = dict(reversal_pattern[sym]) if reversal_pattern[sym] else None

        open_pnl_sym = sum(
            (tick["price"] - t["entry"]) / t["entry"] * t.get("notional", 250.0) * t["direction"]
            for t in open_t) if tick["price"] > 0 else 0.0

        c5_js = [{"o": c["open"], "h": c["high"], "l": c["low"],
                  "c": c["close"], "v": c["volume"], "time": c["time"]} for c in c5]
        c15_js = [{"o": c["open"], "h": c["high"], "l": c["low"],
                   "c": c["close"], "v": c["volume"], "time": c["time"]} for c in c15]

        def trade_for_js(t, is_open=True):
            d = t["direction"]
            side = "LONG" if d == 1 else "SHORT"
            result = {
                "id": t["id"], "side": side, "direction": d,
                "entry_price": t.get("entry", t.get("entry_price", 0)),
                "tp": t.get("tp", 0), "sl": t.get("sl", 0),
                "notional": t.get("notional", 250.0),
                "margin": t.get("margin", 50.0),
            }
            if is_open:
                result["entry_time"] = t.get("entry_time", "")
                result["unrealized_pnl"] = t.get("unrealized_pnl", 0.0)
            else:
                result["close_price"] = t.get("exit_price", 0)
                result["pnl"] = t.get("pnl", 0.0)
                result["close_time"] = t.get("exit_time", "")
                result["close_reason"] = t.get("reason", "")
                result["symbol"] = t.get("symbol", "")
            return result

        open_t_js = [trade_for_js(t, is_open=True) for t in open_t]

        with capital_lock:
            sym_cap = dict(capital.get(sym, {
                "balance": INITIAL_CAPITAL_PER_SYMBOL,
                "initial": INITIAL_CAPITAL_PER_SYMBOL,
                "peak": INITIAL_CAPITAL_PER_SYMBOL,
                "max_dd": 0.0,
                "total_trades": 0, "winning_trades": 0, "total_pnl": 0.0,
            }))

        symbols_data[sym] = {
            "display": SYMBOL_DISPLAY.get(sym, sym),
            "ticker": tick,
            "signal_state": sig,
            "indicators": ind,
            "manipulation": mp,
            "reversal": rp,
            "open_trades": open_t_js,
            "candles_5m": c5_js,
            "candles_15m": c15_js,
            "open_pnl": round(open_pnl_sym, 2),
            "capital": sym_cap,
        }

    closed_t_js = [trade_for_js(t, is_open=False) for t in all_closed]
    acct = _aggregated_account()

    logs_js = [{"time": e["time"], "tag": e.get("level", "INFO"), "msg": e.get("message", "")} for e in logs]

    return jsonify({
        "symbols": SYMBOLS,
        "data": symbols_data,
        "account": acct,
        "closed_trades": closed_t_js,
        "event_log": logs_js,
        "market_sessions": sessions,
        "utc_time": utc_now.strftime("%H:%M:%S UTC"),
        "server_tz_offset": tz_offset,
    })

@app.route("/api/symbols")
def api_symbols():
    """List all 100 available symbols + which are active."""
    result = []
    for code, display in TOP100_USDT_SYMBOLS:
        sym = code + "USDT"
        result.append({
            "code": sym,
            "display": display,
            "active": sym in SYMBOLS,
        })
    return jsonify(result)

@app.route("/api/symbol/add", methods=["POST"])
def api_symbol_add():
    """Add a symbol to active trading."""
    data = request.get_json(silent=True) or {}
    symbol_input = (data.get("symbol") or "").strip().upper()

    # Accept both "BTC" and "BTCUSDT" formats
    if not symbol_input.endswith("USDT"):
        symbol_input += "USDT"

    if symbol_input in SYMBOLS:
        return jsonify({"status": "error", "message": f"{symbol_input} already active"}), 400

    # Validate against known symbols
    known = {code + "USDT" for code, _ in TOP100_USDT_SYMBOLS}
    if symbol_input not in known:
        return jsonify({"status": "error", "message": f"Unknown symbol: {symbol_input}"}), 400

    # Reset state first (clear any stale from previous remove)
    with state_lock:
        SYMBOLS.append(symbol_input)
        init_symbol_state(symbol_input)
    with capital_lock:
        if symbol_input not in capital:
            capital[symbol_input] = {
                "balance": INITIAL_CAPITAL_PER_SYMBOL,
                "initial": INITIAL_CAPITAL_PER_SYMBOL,
                "peak": INITIAL_CAPITAL_PER_SYMBOL,
                "max_dd": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "total_pnl": 0.0,
            }
    save_state()
    log_event(f"[SYSTEM] Added {symbol_input} to active symbols", "SYSTEM")

    # Bootstrap + restart WS
    threading.Thread(target=_bootstrap_and_restart_ws, args=(symbol_input,), daemon=True).start()

    return jsonify({"status": "ok", "message": f"Added {symbol_input}", "symbols": SYMBOLS})

@app.route("/api/symbol/remove", methods=["POST"])
def api_symbol_remove():
    """Remove a symbol from active trading."""
    data = request.get_json(silent=True) or {}
    symbol_input = (data.get("symbol") or "").strip().upper()

    if not symbol_input.endswith("USDT"):
        symbol_input += "USDT"

    if symbol_input not in SYMBOLS:
        return jsonify({"status": "error", "message": f"{symbol_input} not active"}), 400

    if len(SYMBOLS) <= 1:
        return jsonify({"status": "error", "message": "Cannot remove last symbol"}), 400

    with state_lock:
        SYMBOLS.remove(symbol_input)
        remove_symbol_state(symbol_input)

    save_state()
    log_event(f"[SYSTEM] Removed {symbol_input} from active symbols", "SYSTEM")

    # Restart WS with updated symbol list
    _trigger_ws_restart()

    return jsonify({"status": "ok", "message": f"Removed {symbol_input}", "symbols": SYMBOLS})

def _bootstrap_and_restart_ws(sym):
    """Bootstrap data for new symbol, then restart WS."""
    bootstrap_historical_candles(sym)
    _trigger_ws_restart()

def _trigger_ws_restart():
    """Signal WS to restart with new symbol list."""
    global ws_thread_ref
    with ws_lock:
        ws_stop_event.set()
    # Wait for old WS thread to die, then restart
    time.sleep(0.5)
    ws_stop_event.clear()
    t = threading.Thread(target=start_websocket, daemon=True, name="ws-streamer")
    t.start()

@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset all accounts to initial capital."""
    sym = request.args.get("symbol", "").upper()
    if sym:
        # Reset single symbol
        if not sym.endswith("USDT"):
            sym += "USDT"
        with capital_lock:
            if sym in capital:
                capital[sym] = {
                    "balance": INITIAL_CAPITAL_PER_SYMBOL,
                    "initial": INITIAL_CAPITAL_PER_SYMBOL,
                    "peak": INITIAL_CAPITAL_PER_SYMBOL,
                    "max_dd": 0.0,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "total_pnl": 0.0,
                }
        with state_lock:
            signal_state[sym]["last_entry_signal"] = None
            open_trades[sym].clear()
        msg = f"{sym} capital reset to ${INITIAL_CAPITAL_PER_SYMBOL:.0f}"
    else:
        # Reset all
        with capital_lock:
            for s in capital:
                capital[s] = {
                    "balance": INITIAL_CAPITAL_PER_SYMBOL,
                    "initial": INITIAL_CAPITAL_PER_SYMBOL,
                    "peak": INITIAL_CAPITAL_PER_SYMBOL,
                    "max_dd": 0.0,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "total_pnl": 0.0,
                }
        with state_lock:
            for s in SYMBOLS:
                signal_state[s]["last_entry_signal"] = None
                open_trades[s].clear()
            closed_trades.clear()
            event_log.clear()
        msg = f"All accounts reset to ${INITIAL_CAPITAL_PER_SYMBOL:.0f} each"

    # Clear DB
    conn = get_db()
    if not sym:
        conn.execute("DELETE FROM closed_trades")
        conn.execute("DELETE FROM event_log")
    conn.execute("DELETE FROM open_trades")
    conn.execute("DELETE FROM symbol_capital")
    conn.commit()
    conn.close()
    save_state()
    log_event(msg, "SYSTEM")
    return jsonify({"status": "ok", "message": msg})

@app.route("/api/capital/set", methods=["POST"])
def api_capital_set():
    """Set capital for a specific symbol."""
    data = request.get_json(silent=True) or {}
    sym = (data.get("symbol") or "").strip().upper()
    amount = float(data.get("amount", 0))
    if not sym.endswith("USDT"):
        sym += "USDT"
    if sym not in SYMBOLS:
        return jsonify({"status": "error", "message": f"{sym} not active"}), 400
    if amount <= 0:
        return jsonify({"status": "error", "message": "Amount must be > 0"}), 400
    with capital_lock:
        capital[sym] = {
            "balance": amount,
            "initial": amount,
            "peak": amount,
            "max_dd": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "total_pnl": 0.0,
        }
    with state_lock:
        signal_state[sym]["last_entry_signal"] = None
        open_trades[sym].clear()
    save_state()
    log_event(f"[{sym}] Capital set to ${amount:.2f}", "SYSTEM")
    return jsonify({"status": "ok", "message": f"{sym} capital set to ${amount:.2f}"})

# ─── Startup ──────────────────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT"]

if __name__ == "__main__":
    init_db()
    has_state = load_state()

    if not SYMBOLS:
        SYMBOLS = list(DEFAULT_SYMBOLS)
        for sym in SYMBOLS:
            init_symbol_state(sym)
            capital[sym] = {
                "balance": INITIAL_CAPITAL_PER_SYMBOL,
                "initial": INITIAL_CAPITAL_PER_SYMBOL,
                "peak": INITIAL_CAPITAL_PER_SYMBOL,
                "max_dd": 0.0,
                "total_trades": 0, "winning_trades": 0, "total_pnl": 0.0,
            }

    if has_state:
        total_bal = sum(c["balance"] for c in capital.values())
        total_t = sum(c["total_trades"] for c in capital.values())
        log_event(f"Loaded persisted state: {len(SYMBOLS)} symbols, ${total_bal:.2f} total, {total_t} trades", "SYSTEM")
    else:
        log_event(f"Fresh start: {len(SYMBOLS)} symbols, ${INITIAL_CAPITAL_PER_SYMBOL:.0f} each", "SYSTEM")
        save_state()

    log_event(f"Bootstrapping historical candles for {len(SYMBOLS)} symbols...", "SYSTEM")
    bootstrap_all()

    t_ws = threading.Thread(target=start_websocket, daemon=True, name="ws-streamer")
    t_ws.start()
    time.sleep(3)
    threading.Thread(target=strategy_loop, daemon=True, name="strategy-engine").start()
    threading.Thread(target=execution_loop, daemon=True, name="execution-engine").start()

    log_event(f"ATLANTIDE CRYPTO LAB started on 0.0.0.0:8080 — {len(SYMBOLS)} symbols, 10% risk/trade", "SYSTEM")
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

"""SQLite persistence layer for ATLANTIDE Crypto Lab.

Handles: schema init, state save/load, closed trades, event log.
"""

import os
import sqlite3

from app.state import (SYMBOLS, capital, capital_lock, state_lock,
                        closed_trades, event_log, open_trades,
                        TRADE_ID_COUNTER, TRADE_ID_LOCK,
                        init_symbol_state, ensure_capital)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "atlantide.db")


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
        with capital_lock:
            caps = dict(capital)
        for sym, cap in caps.items():
            conn.execute(
                "INSERT OR REPLACE INTO symbol_capital "
                "(symbol, balance, initial, peak, max_dd, total_trades,"
                " winning_trades, total_pnl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sym, cap["balance"], cap["initial"], cap["peak"],
                 cap["max_dd"], cap["total_trades"], cap["winning_trades"],
                 cap["total_pnl"]))
        # Save active symbols + trade_id_counter
        conn.execute("DELETE FROM symbol_config")
        conn.execute("INSERT OR REPLACE INTO symbol_config (key, value) "
                     "VALUES (?, ?)", ("active_symbols", ",".join(SYMBOLS)))
        with TRADE_ID_LOCK:
            tic = TRADE_ID_COUNTER
        conn.execute("INSERT OR REPLACE INTO symbol_config (key, value) "
                     "VALUES (?, ?)", ("trade_id_counter", str(tic)))
        # Save open trades
        conn.execute("DELETE FROM open_trades")
        for sym in SYMBOLS:
            for t in open_trades[sym]:
                conn.execute(
                    "INSERT OR REPLACE INTO open_trades "
                    "(trade_id, symbol, entry_time, entry_price, direction,"
                    " tp, sl, notional, margin, unrealized_pnl) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (t["id"], sym, t["entry_time"], t["entry"],
                     t["direction"], t["tp"], t["sl"],
                     t.get("notional", 250.0), t.get("margin", 50.0),
                     t.get("unrealized_pnl", 0.0)))
        conn.commit()
        conn.close()


def save_closed_trade(trade, sym):
    conn = get_db()
    conn.execute(
        "INSERT INTO closed_trades "
        "(symbol, entry_time, exit_time, entry_price, exit_price,"
        " direction, pnl, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sym, trade["entry_time"], trade["exit_time"], trade["entry"],
         trade["exit_price"], trade["direction"], trade["pnl"],
         trade["reason"]))
    conn.commit()
    conn.close()


def save_event(msg, level="INFO"):
    from datetime import datetime
    conn = get_db()
    conn.execute("INSERT INTO event_log (timestamp, message, level) "
                 "VALUES (?, ?, ?)",
                 (datetime.now().isoformat(), msg, level))
    conn.commit()
    conn.close()


def load_state():
    """Load persisted state from SQLite. Returns True if state was loaded."""
    global TRADE_ID_COUNTER

    conn = get_db()

    # Load active symbols
    rows = conn.execute("SELECT key, value FROM symbol_config").fetchall()
    for row in rows:
        if row["key"] == "active_symbols" and row["value"]:
            loaded = [s.strip() for s in row["value"].split(",") if s.strip()]
            if loaded:
                SYMBOLS.clear()
                SYMBOLS.extend(loaded)
        elif row["key"] == "trade_id_counter":
            with TRADE_ID_LOCK:
                TRADE_ID_COUNTER = int(row["value"])

    # Load per-symbol capital
    rows = conn.execute("SELECT * FROM symbol_capital").fetchall()
    with capital_lock:
        capital.clear()
        for row in rows:
            capital[row["symbol"]] = {
                "balance": row["balance"], "initial": row["initial"],
                "peak": row["peak"], "max_dd": row["max_dd"],
                "total_trades": row["total_trades"] or 0,
                "winning_trades": row["winning_trades"] or 0,
                "total_pnl": row["total_pnl"] or 0.0,
            }

    # Init state for active symbols
    for sym in SYMBOLS:
        init_symbol_state(sym)
        ensure_capital(sym)

    # Load open trades
    rows = conn.execute("SELECT * FROM open_trades ORDER BY trade_id ASC").fetchall()
    for row in rows:
        sym = row["symbol"]
        if sym in SYMBOLS:
            open_trades[sym].append({
                "id": row["trade_id"], "entry_time": row["entry_time"],
                "entry": row["entry_price"], "direction": row["direction"],
                "tp": row["tp"], "sl": row["sl"],
                "notional": dict(row).get("notional", 250.0),
                "margin": dict(row).get("margin", 50.0),
                "exit_price": None, "exit_time": None, "pnl": 0.0,
                "unrealized_pnl": dict(row).get("unrealized_pnl", 0.0),
                "reason": "",
            })

    # Load closed trades
    rows = conn.execute("SELECT * FROM closed_trades "
                        "ORDER BY id DESC LIMIT 500").fetchall()
    loaded = []
    for row in reversed(rows):
        loaded.append({
            "id": row["id"], "symbol": row["symbol"],
            "entry_time": row["entry_time"], "exit_time": row["exit_time"],
            "entry": row["entry_price"], "exit_price": row["exit_price"],
            "direction": row["direction"], "pnl": row["pnl"],
            "reason": row["reason"],
        })
    with state_lock:
        closed_trades.clear()
        closed_trades.extend(loaded)

    # Load event log
    rows = conn.execute("SELECT * FROM event_log "
                        "ORDER BY id DESC LIMIT 200").fetchall()
    loaded_events = []
    for row in reversed(rows):
        loaded_events.append({
            "time": row["timestamp"], "message": row["message"],
            "level": row["level"],
        })
    with state_lock:
        event_log.clear()
        event_log.extend(loaded_events)

    conn.close()
    return len(SYMBOLS) > 0

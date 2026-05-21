"""SQLite persistence layer for ATLANTIDE Crypto Lab.

Handles: schema init, state save/load, closed trades, event log.

Uses a SINGLE shared connection with a lock to eliminate
'database is locked' errors from concurrent threads.
"""

import os
import sqlite3
import threading

from app.state import (SYMBOLS, capital, capital_lock, state_lock,
                        closed_trades, event_log, open_trades,
                        TRADE_ID_COUNTER, TRADE_ID_LOCK,
                        init_symbol_state, ensure_capital)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "atlantide.db")

# ─── Single shared connection with lock ────────────────────────────────────
_db_lock = threading.Lock()
_db_conn = None


def get_db():
    """Return the shared single connection (thread-safe via lock)."""
    global _db_conn
    with _db_lock:
        if _db_conn is None:
            _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _db_conn.row_factory = sqlite3.Row
            _db_conn.execute("PRAGMA busy_timeout=10000")
            _db_conn.execute("PRAGMA journal_mode=WAL")
            _db_conn.execute("PRAGMA synchronous=NORMAL")
        return _db_conn


def close_db():
    """Close the shared connection (safe to call multiple times)."""
    global _db_conn
    with _db_lock:
        if _db_conn is not None:
            try:
                _db_conn.close()
            except Exception:
                pass
            _db_conn = None


def _with_db(fn):
    """Execute a DB operation under the write lock with retry."""
    for attempt in range(3):
        try:
            conn = get_db()
            with _db_lock:
                return fn(conn)
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < 2:
                import time
                time.sleep(1)
                continue
            raise


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
    # ── Migration: add columns missing from legacy DBs ─────────────────────
    for col, col_type in [
        ("symbol", "TEXT DEFAULT ''"),
        ("notional", "REAL DEFAULT 250.0"),
        ("margin", "REAL DEFAULT 50.0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE open_trades ADD COLUMN {col} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    for col, col_type in [("symbol", "TEXT DEFAULT ''")]:
        try:
            conn.execute(f"ALTER TABLE closed_trades ADD COLUMN {col} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists


def save_state():
    def _do(conn):
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
        conn.execute("DELETE FROM symbol_config")
        conn.execute("INSERT OR REPLACE INTO symbol_config (key, value) "
                     "VALUES (?, ?)", ("active_symbols", ",".join(SYMBOLS)))
        with TRADE_ID_LOCK:
            tic = TRADE_ID_COUNTER
        conn.execute("INSERT OR REPLACE INTO symbol_config (key, value) "
                     "VALUES (?, ?)", ("trade_id_counter", str(tic)))
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
    _with_db(_do)


def save_closed_trade(trade, sym):
    def _do(conn):
        conn.execute(
            "INSERT INTO closed_trades "
            "(symbol, entry_time, exit_time, entry_price, exit_price,"
            " direction, pnl, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sym, trade["entry_time"], trade["exit_time"], trade["entry"],
             trade["exit_price"], trade["direction"], trade["pnl"],
             trade["reason"]))
        conn.commit()
    _with_db(_do)


def save_event(msg, level="INFO"):
    from datetime import datetime
    def _do(conn):
        conn.execute("INSERT INTO event_log (timestamp, message, level) "
                     "VALUES (?, ?, ?)",
                     (datetime.now().isoformat(), msg, level))
        conn.commit()
    _with_db(_do)


def load_state():
    """Load persisted state from SQLite. Returns True if state was loaded."""
    global TRADE_ID_COUNTER

    def _do(conn):
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
            sym = row["symbol"] if row["symbol"] else ""
            # If symbol column is empty (legacy), try to infer from other data
            if not sym:
                continue
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

        return len(SYMBOLS) > 0

    return _with_db(_do)

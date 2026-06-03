"""Condition snapshots — captures full system state at every SIGNAL and TRADE event.
Hooks into strategy and execution to log conditions alongside events."""

from datetime import datetime

from app.database import _with_db


# ─── DB Table ──────────────────────────────────────────────────────────────

CONDITIONS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS condition_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        event_type TEXT,
        symbol TEXT,
        signal TEXT,
        score REAL,
        pattern_type TEXT,
        tp REAL,
        sl REAL,
        regime TEXT,
        vol_label TEXT,
        atr REAL,
        rsi REAL,
        capital REAL,
        capital_pnl REAL,
        open_trades_count INTEGER,
        open_pnl REAL,
        total_trades INTEGER,
        total_pnl REAL,
        win_rate REAL,
        profit_factor REAL,
        best_pattern TEXT,
        worst_pattern TEXT,
        wick_ratio REAL,
        entry_threshold REAL,
        rr_ratio REAL,
        risk_pct REAL,
        event_msg TEXT
    );
"""


def init_conditions_table():
    """Initialize the conditions snapshot table (called from init_db)."""
    from app.database import get_db
    conn = get_db()
    conn.execute(CONDITIONS_SCHEMA)
    conn.commit()


# ─── Capture Current Conditions ────────────────────────────────────────────

def capture_conditions(event_type, symbol, event_msg=""):
    """Capture the current full system state and save as a condition snapshot.
    
    Call this whenever a SIGNAL or TRADE event fires.
    Reads all state from the global app modules.
    """
    from app.state import (signal_state, indicators, capital, capital_lock,
                            state_lock, open_trades, closed_trades, SYMBOLS,
                            active_params, param_lock)
    from app.self_learning import get_effective_param

    sig_data = {}
    ind_data = {}
    cap_data = {}
    open_trades_list = []
    closed = []

    with state_lock:
        # Get per-symbol data for the given symbol (or first active)
        syms = list(SYMBOLS)
        sym = symbol if symbol in syms else (syms[0] if syms else "")
        if sym:
            sig_data = dict(signal_state.get(sym, {}))
            ind_data = dict(indicators.get(sym, {}))
            open_trades_list = list(open_trades.get(sym, []))
            closed = list(closed_trades)
        total_trades = len(closed)

    with capital_lock:
        caps = dict(capital)
        sym_cap = caps.get(sym, {})
        total_balance = sum(c.get("balance", 0) for c in caps.values())
        total_pnl = sum(c.get("total_pnl", 0) for c in caps.values())
        total_wins = sum(c.get("winning_trades", 0) for c in caps.values())

    # Compute win rate from capital data
    all_trades_count = sum(c.get("total_trades", 0) for c in caps.values())
    all_wins_count = sum(c.get("winning_trades", 0) for c in caps.values())
    win_rate = (all_wins_count / all_trades_count * 100) if all_trades_count > 0 else 0.0

    # Perf metrics from self-learning
    from app.state import perf_metrics, perf_lock
    with perf_lock:
        pf = perf_metrics.get("profit_factor", 0.0)
        best_pat = perf_metrics.get("best_pattern", "")
        worst_pat = perf_metrics.get("worst_pattern", "")

    # Active params
    wick = get_effective_param("wick_ratio")
    entry_thresh = get_effective_param("entry_threshold")
    rr = get_effective_param("rr_ratio")
    risk = get_effective_param("risk_pct")

    # Open trades PnL
    open_pnl = sum(
        t.get("unrealized_pnl", 0) for t in open_trades_list
    )

    snapshot = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event_type": event_type,
        "symbol": sym,
        "signal": sig_data.get("signal"),
        "score": sig_data.get("score", 0),
        "pattern_type": sig_data.get("pattern_type", ""),
        "tp": sig_data.get("tp", 0),
        "sl": sig_data.get("sl", 0),
        "regime": ind_data.get("trend", ""),
        "vol_label": ind_data.get("vol_label", ""),
        "atr": ind_data.get("15m_atr14", 0),
        "rsi": ind_data.get("rsi", 0),
        "capital": sym_cap.get("balance", 0),
        "capital_pnl": sym_cap.get("total_pnl", 0),
        "open_trades_count": len(open_trades_list),
        "open_pnl": round(open_pnl, 2),
        "total_trades": all_trades_count,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "best_pattern": best_pat,
        "worst_pattern": worst_pat,
        "wick_ratio": round(wick, 4),
        "entry_threshold": round(entry_thresh, 4),
        "rr_ratio": round(rr, 4),
        "risk_pct": round(risk, 4),
        "event_msg": event_msg[:200] if event_msg else "",
    }

    save_snapshot(snapshot)
    return snapshot


# ─── Persistence ───────────────────────────────────────────────────────────

def save_snapshot(snapshot):
    """Save a condition snapshot to the database."""
    def _do(conn):
        conn.execute(
            """INSERT INTO condition_snapshots
               (timestamp, event_type, symbol, signal, score, pattern_type,
                tp, sl, regime, vol_label, atr, rsi,
                capital, capital_pnl, open_trades_count, open_pnl,
                total_trades, total_pnl, win_rate, profit_factor,
                best_pattern, worst_pattern,
                wick_ratio, entry_threshold, rr_ratio, risk_pct,
                event_msg)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?)""",
            (snapshot["timestamp"], snapshot["event_type"],
             snapshot["symbol"], snapshot["signal"], snapshot["score"],
             snapshot["pattern_type"], snapshot["tp"], snapshot["sl"],
             snapshot["regime"], snapshot["vol_label"], snapshot["atr"],
             snapshot["rsi"],
             snapshot["capital"], snapshot["capital_pnl"],
             snapshot["open_trades_count"], snapshot["open_pnl"],
             snapshot["total_trades"], snapshot["total_pnl"],
             snapshot["win_rate"], snapshot["profit_factor"],
             snapshot["best_pattern"], snapshot["worst_pattern"],
             snapshot["wick_ratio"], snapshot["entry_threshold"],
             snapshot["rr_ratio"], snapshot["risk_pct"],
             snapshot["event_msg"]))
        conn.commit()
    _with_db(_do)


def get_conditions(limit=200):
    """Get recent condition snapshots from the database."""
    from app.database import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM condition_snapshots ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]

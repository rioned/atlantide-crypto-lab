"""Global state management for ATLANTIDE Crypto Lab.

All shared state lives here with proper locking.
Modules import from here, never write directly without locks.
"""

import threading

from app.config import INITIAL_CAPITAL_PER_SYMBOL

# ─── Active Symbols ────────────────────────────────────────────────────────────
SYMBOLS = []              # e.g. ["BTCUSDT", "ETHUSDT", ...]

# ─── Per-Symbol Capital ───────────────────────────────────────────────────────
capital = {}
capital_lock = threading.Lock()

# ─── Global State Lock ─────────────────────────────────────────────────────────
state_lock = threading.RLock()  # reentrant — log_event may be called from within a state_lock block

# ─── Per‑Symbol Data ──────────────────────────────────────────────────────────
candles = {}          # s -> {"5m": [], "15m": []}
ticker = {}           # s -> {price, change_pct, high, low, volume}
signal_state = {}     # s -> {signal, direction, tp, sl, manipulation_range, ...}
daily_atr = {}        # s -> float
daily_atr_threshold = {}  # s -> float
manipulation_candle = {}  # s -> dict | None
manipulation_active = {}  # s -> bool
reversal_pattern = {}     # s -> dict | None
last_15m_time = {}        # s -> str | None
open_trades = {}     # s -> [trade, ...]
indicators = {}      # s -> {"daily_atr", "daily_atr_threshold", "5m_atr14"}
last_trade_close_time = {}  # s -> float (time.time() of last trade close, for cooldown)
consecutive_sl_losses = {}  # s -> int (consecutive SL losses counter)
sl_pause_until = {}         # s -> float (time.time() when pause ends, 0 = not paused)

# ─── Global 5-Loss Suspension ────────────────────────────────────────────────
global_suspension_until = [0.0]  # float[0]: time.time() when suspension ends (0 = normal)
global_suspension_lock = threading.Lock()
# Fingerprint: trade count when suspension was triggered. Only re-check the
# 5-loss condition when new trades have been added beyond this count.
# Prevents infinite re-trigger loop on the same set of losing trades.
suspension_fingerprint = [0]  # int[0]: len(closed_trades) at trigger time

# ─── Global Lists ─────────────────────────────────────────────────────────────
closed_trades = []
event_log = []

# ─── Trade ID Counter ─────────────────────────────────────────────────────────
TRADE_ID_COUNTER = 0
TRADE_ID_LOCK = threading.Lock()

# ─── WebSocket Control ────────────────────────────────────────────────────────
ws_stop_event = threading.Event()
ws_thread_ref = None
ws_lock = threading.Lock()
ws_generation = 0  # incremented each restart — old threads check this to know they're stale

# ─── SSE Clients ──────────────────────────────────────────────────────────────
sse_clients = []           # list of queue.Queue for pending SSE events
sse_clients_lock = threading.Lock()

# ─── Self-Learning State (from video: goal tracking + param history) ──────────
self_learning_active = threading.Event()
self_learning_active.set()   # enabled by default

# Current active parameters (hot-reloadable by self_learning engine)
active_params = {
    "wick_ratio": None,       # HAMMER_MIN_WICK_RATIO override
    "entry_threshold": None,  # ENTRY_THRESHOLD override
    "rr_ratio": None,         # RR_RATIO override (reset to default 2.0)
    "risk_pct": None,         # RISK_PCT override
}

# Parameter version tracking (mutable list for cross-module updates)
param_version = [0]
param_lock = threading.Lock()

# Tune history: each entry is a dict with version, param changed, old val, new val, result
param_history = []

# Performance metrics per review cycle
perf_metrics = {
    "sharpe_ratio": 0.0,
    "profit_factor": 0.0,
    "win_rate": 0.0,
    "avg_win": 0.0,
    "avg_loss": 0.0,
    "total_trades": 0,
    "net_pnl": 0.0,
    "max_drawdown": 0.0,
    "expectancy": 0.0,
    "best_pattern": "",
    "worst_pattern": "",
    "last_review_trades": 0,
    "goal_sharpe_progress": 0.0,
    "goal_winrate_progress": 0.0,
    "goal_return_progress": 0.0,
    "goal_dd_status": "OK",
    "goal_dd_pct": 0.0,
}
perf_lock = threading.Lock()

# Hypothesis from last review cycle
last_hypothesis = ""
last_hypothesis_lock = threading.Lock()


# ─── Init / Teardown ─────────────────────────────────────────────────────────

def init_symbol_state(sym):
    """Create clean state entries for a new symbol."""
    with state_lock:
        candles[sym] = {"5m": [], "15m": []}
        ticker[sym] = {"price": 0.0, "change_pct": 0.0, "high": 0.0,
                       "low": 0.0, "volume": 0.0}
        signal_state[sym] = {
            "signal": None, "direction": 0, "tp": 0.0, "sl": 0.0,
            "manipulation_range": 0.0, "pattern_type": "",
            "last_entry_signal": None,
            "score": 0.0, "num_signals": 0,
            "trend": "", "vol_label": "",
        }
        daily_atr[sym] = 0.0
        daily_atr_threshold[sym] = 0.0
        manipulation_candle[sym] = None
        manipulation_active[sym] = False
        reversal_pattern[sym] = None
        last_15m_time[sym] = None
        open_trades[sym] = []
        indicators[sym] = {"daily_atr": 0.0, "daily_atr_threshold": 0.0,
                          "15m_atr14": 0.0}
        last_trade_close_time[sym] = 0.0
        consecutive_sl_losses[sym] = 0
        sl_pause_until[sym] = 0.0


def remove_symbol_state(sym):
    """Remove all state entries for a symbol."""
    for d in [candles, ticker, signal_state, daily_atr, daily_atr_threshold,
              manipulation_candle, manipulation_active, reversal_pattern,
              last_15m_time, open_trades, indicators, last_trade_close_time,
              consecutive_sl_losses, sl_pause_until]:
        d.pop(sym, None)
    with capital_lock:
        capital.pop(sym, None)


def ensure_capital(sym):
    """Ensure a symbol has a capital entry; create default if missing."""
    with capital_lock:
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
        return dict(capital[sym])


def broadcast_sse(data):
    """Push an event to all connected SSE clients."""
    with sse_clients_lock:
        for q in list(sse_clients):
            try:
                q.put_nowait(data)
            except Exception:
                pass

# -- Auto-trim: keep event log manageable -- (handled in strategy.py log_event)

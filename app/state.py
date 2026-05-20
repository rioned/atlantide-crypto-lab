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
state_lock = threading.Lock()

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

# ─── SSE Clients ──────────────────────────────────────────────────────────────
sse_clients = []           # list of queue.Queue for pending SSE events
sse_clients_lock = threading.Lock()


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
        }
        daily_atr[sym] = 0.0
        daily_atr_threshold[sym] = 0.0
        manipulation_candle[sym] = None
        manipulation_active[sym] = False
        reversal_pattern[sym] = None
        last_15m_time[sym] = None
        open_trades[sym] = []
        indicators[sym] = {"daily_atr": 0.0, "daily_atr_threshold": 0.0,
                          "5m_atr14": 0.0}


def remove_symbol_state(sym):
    """Remove all state entries for a symbol."""
    for d in [candles, ticker, signal_state, daily_atr, daily_atr_threshold,
              manipulation_candle, manipulation_active, reversal_pattern,
              last_15m_time, open_trades, indicators]:
        d.pop(sym, None)


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

"""Entry point — bootstraps and starts ATLANTIDE Crypto Lab."""

import time
import threading

from app.config import INITIAL_CAPITAL_PER_SYMBOL
from app.state import (SYMBOLS, capital, capital_lock, state_lock,
                        event_log, init_symbol_state, ensure_capital)
from app.database import init_db, load_state, save_state
from app.market_data import bootstrap_all, start_websocket
from app.strategy import strategy_loop, log_event
from app.execution import execution_loop

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT"]


def startup():
    """Initialize database, load state, bootstrap data, start threads."""
    init_db()
    has_state = load_state()

    if not SYMBOLS:
        SYMBOLS.extend(DEFAULT_SYMBOLS)
        for sym in SYMBOLS:
            init_symbol_state(sym)
            ensure_capital(sym)

    if has_state:
        total_bal = sum(c["balance"] for c in capital.values())
        total_t = sum(c["total_trades"] for c in capital.values())
        log_event(f"Loaded persisted state: {len(SYMBOLS)} symbols, "
                  f"${total_bal:.2f} total, {total_t} trades", "SYSTEM")
    else:
        log_event(f"Fresh start: {len(SYMBOLS)} symbols, "
                  f"${INITIAL_CAPITAL_PER_SYMBOL:.0f} each", "SYSTEM")
        save_state()

    log_event(f"Bootstrapping historical candles for "
              f"{len(SYMBOLS)} symbols...", "SYSTEM")
    bootstrap_all()

    threads = [
        threading.Thread(target=start_websocket, daemon=True,
                         name="ws-streamer"),
    ]
    threads[0].start()
    time.sleep(3)

    threads.append(threading.Thread(target=strategy_loop, daemon=True,
                                     name="strategy-engine"))
    threads.append(threading.Thread(target=execution_loop, daemon=True,
                                     name="execution-engine"))
    threads[1].start()
    threads[2].start()

    log_event(f"ATLANTIDE CRYPTO LAB started on 0.0.0.0:8080 — "
              f"{len(SYMBOLS)} symbols, 10% risk/trade", "SYSTEM")

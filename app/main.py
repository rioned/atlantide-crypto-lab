"""Entry point — bootstraps and starts CRYPTO LAB 2."""

import time
import threading

from app.config import INITIAL_CAPITAL_PER_SYMBOL
from app.state import (SYMBOLS, capital, capital_lock, state_lock,
                        event_log, init_symbol_state, ensure_capital)
from app.database import init_db, load_state, save_state
from app.market_data import bootstrap_all, start_websocket
from app.strategy import strategy_loop, log_event
from app.execution import execution_loop
from app.self_learning import self_learning_loop
from app.market_sessions import get_all_market_statuses, check_alerts, reset_alerts
from app.state import broadcast_sse

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "POLUSDT", "SHIBUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT",
    "ETCUSDT", "XLMUSDT", "FILUSDT", "TRXUSDT", "NEARUSDT",
]

_started = False


def startup():
    """Initialize database, load state, bootstrap data, start threads.
    
    Must return within a few seconds — runs in gunicorn worker init.
    """
    global _started
    if _started:
        return
    _started = True

    # Reset market session alert tracking on fresh start
    reset_alerts()

    # Phase 1: Fast local init (no network)
    init_db()
    has_state = load_state()

    if not SYMBOLS:
        SYMBOLS.extend(DEFAULT_SYMBOLS)
        for sym in SYMBOLS:
            init_symbol_state(sym)
            ensure_capital(sym)
    else:
        # Ensure all default symbols are in the active list (handles DB with < 20)
        for sym in DEFAULT_SYMBOLS:
            if sym not in SYMBOLS:
                SYMBOLS.append(sym)
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

    # Phase 2: Bootstrap in background with timeout
    log_event(f"Bootstrapping {len(SYMBOLS)} symbols in background...", "SYSTEM")

    def _bootstrap_and_start():
        try:
            bootstrap_all()
        except Exception as e:
            log_event(f"Bootstrap timeout/error: {e}", "WARN")
            # Continue anyway — WS will fill in candles over time
        
        # Start threads only after bootstrap (or after it fails)
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
        threads.append(threading.Thread(target=self_learning_loop, daemon=True,
                                         name="self-learning"))
        threads[1].start()
        threads[2].start()
        threads[3].start()

        # Market session alert poller (checks every 30s, broadcasts SSE)
        def _market_alert_poller():
            while True:
                try:
                    alerts = check_alerts()
                    for alert in alerts:
                        broadcast_sse({
                            "event": "market_alert",
                            "market": alert["market"],
                            "city": alert["city"],
                            "flag": alert["flag"],
                            "event_type": alert["event_type"],
                            "seconds_until": alert["seconds_until"],
                        })
                except Exception:
                    pass
                time.sleep(30)

        threading.Thread(target=_market_alert_poller, daemon=True,
                         name="market-alerts").start()

        log_event(f"CRYPTO LAB 2 started on 0.0.0.0:9090 — "
                  f"{len(SYMBOLS)} symbols, 10% risk/trade", "SYSTEM")
        log_event(f"[SELF-LEARN] Self-improving agent enabled — "
                  f"reviews every {10} trades, changes one parameter at a time",
                  "SYSTEM")

    # Run bootstrap in a daemon thread so it never blocks startup
    bg = threading.Thread(target=_bootstrap_and_start, daemon=True,
                          name="bg-bootstrap")
    bg.start()
    # Give bootstraps a moment to start, then return immediately
    # The worker will be fully ready once bootstrap finishes + WS connects
    time.sleep(0.5)

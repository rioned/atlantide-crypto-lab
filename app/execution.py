"""Trade execution engine — position management, TP/SL checks, PnL tracking."""

import time
from datetime import datetime

from app.config import (RISK_PCT, LEVERAGE, MAX_OPEN_TRADES_PER_SYMBOL,
                         MAX_CLOSED_TRADES)
from app.state import (SYMBOLS, capital, capital_lock, state_lock,
                        signal_state, ticker, open_trades, closed_trades,
                        TRADE_ID_COUNTER, TRADE_ID_LOCK)
from app.database import save_closed_trade, save_state
from app.strategy import log_event


# ─── Dynamic decimal precision ──────────────────────────────────────────────
def _dp(price):
    """Pick decimal places for display."""
    if price <= 0:
        return 8
    return 8 if price < 1.0 else 4


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

                if current_price <= 0:
                    continue

                # ── Update unrealized PnL ──────────────────────────────────
                for trade in trades_local:
                    notional = trade.get("notional", 250.0)
                    pnl_pct = ((current_price - trade["entry"]) /
                               trade["entry"] * trade["direction"])
                    unrealized = pnl_pct * notional
                    if abs(trade.get("unrealized_pnl", 0.0) -
                           round(unrealized, 2)) > 0.005:
                        changed = True
                    trade["unrealized_pnl"] = round(unrealized, 2)

                # ── Check TP / SL ──────────────────────────────────────────
                for trade in trades_local:
                    notional = trade.get("notional", 250.0)
                    pnl_pct = ((current_price - trade["entry"]) /
                               trade["entry"] * trade["direction"])
                    close_reason = None

                    if trade["direction"] == 1:
                        if current_price >= trade["tp"]:
                            close_reason = "TP"
                        elif current_price <= trade["sl"]:
                            close_reason = "SL"
                    else:
                        if current_price <= trade["tp"]:
                            close_reason = "TP"
                        elif current_price >= trade["sl"]:
                            close_reason = "SL"

                    if close_reason:
                        trade["exit_price"] = current_price
                        trade["exit_time"] = datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S")
                        trade["pnl"] = round(pnl_pct * notional, 2)
                        trade["reason"] = close_reason

                        # Update capital
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
                                    dd = ((cap["peak"] - cap["balance"]) /
                                          cap["peak"] * 100)
                                    cap["max_dd"] = max(cap["max_dd"], dd)

                        with state_lock:
                            closed_trades.append(dict(trade))
                            closed_trades[-1]["symbol"] = sym
                            if len(closed_trades) > MAX_CLOSED_TRADES:
                                closed_trades.pop(0)
                            if trade in open_trades[sym]:
                                open_trades[sym].remove(trade)
                            # Clear entry guard so a new signal can fire
                            signal_state[sym]["last_entry_signal"] = None

                        save_closed_trade(trade, sym)
                        save_state()
                        sym_cap = capital.get(sym, {})
                        log_event(
                            f"[{sym}] TRADE {trade['id']} CLOSED "
                            f"({close_reason}): "
                            f"{'LONG' if trade['direction'] == 1 else 'SHORT'} "
                        f"Entry=${trade['entry']:.{_dp(trade['entry'])}f} "
                        f"Exit=${trade['exit_price']:.{_dp(trade['exit_price'])}f} "
                        f"PnL=${trade['pnl']:.2f} | "
                        f"Cap=${sym_cap.get('balance', 0):.2f}",
                        "TRADE")

                # ── Open new trades on signal ──────────────────────────────
                with state_lock:
                    last_entry = signal_state[sym].get("last_entry_signal")
                    current_sig = signal_state[sym]["signal"]
                    num_open = len(open_trades[sym])

                if (current_sig in ("LONG", "SHORT") and
                        current_sig != last_entry and
                        num_open < MAX_OPEN_TRADES_PER_SYMBOL):
                    tp = signal_state[sym]["tp"]
                    sl = signal_state[sym]["sl"]
                    direction = signal_state[sym]["direction"]
                    if tp <= 0 or sl <= 0 or current_price <= 0:
                        continue

                    with capital_lock:
                        sym_cap = capital.get(sym)
                        if not sym_cap or sym_cap["balance"] <= 0:
                            continue
                        risk_amount = sym_cap["balance"] * RISK_PCT
                        if risk_amount <= 0:
                            continue

                    # Position sizing: 10% risk based on SL distance
                    sl_distance = abs(current_price - sl)
                    if sl_distance <= 0:
                        continue
                    sl_distance_pct = sl_distance / current_price
                    notional = risk_amount / sl_distance_pct
                    margin = notional / LEVERAGE

                    # Cap margin to 2x available balance (safety)
                    with capital_lock:
                        sym_cap = capital.get(sym, {})
                        available = sym_cap.get("balance", 0)
                        if margin > available * 2:
                            margin = available * 2
                            notional = margin * LEVERAGE

                    trade = {
                        "id": get_next_trade_id(),
                        "symbol": sym,
                        "entry_time": datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"),
                        "entry": current_price,
                        "direction": direction,
                        "tp": round(tp, 8),
                        "sl": round(sl, 8),
                        "notional": round(notional, 2),
                        "margin": round(margin, 2),
                        "exit_price": None,
                        "exit_time": None,
                        "pnl": 0.0,
                        "unrealized_pnl": 0.0,
                        "reason": "",
                    }
                    with state_lock:
                        open_trades[sym].append(trade)
                        signal_state[sym]["last_entry_signal"] = current_sig
                    save_state()
                    pat = signal_state[sym].get("pattern_type", "")
                    log_event(
                        f"[{sym}] TRADE {trade['id']} OPENED "
                        f"({current_sig}): "
                        f"Entry=${current_price:.{_dp(current_price)}f} "
                        f"TP=${tp:.{_dp(tp)}f} SL=${sl:.{_dp(sl)}f} "
                        f"Notional=${notional:.0f} Margin=${margin:.2f} "
                        f"Risk=${risk_amount:.2f} Pattern={pat}",
                        "TRADE")

            # ── Periodic state save ────────────────────────────────────────
            now_save = time.time()
            total_open = sum(len(open_trades[s]) for s in SYMBOLS)
            if total_open > 0 and (changed or now_save - last_save > 30):
                save_state()
                last_save = now_save

        except Exception as e:
            log_event(f"Execution error: {e}", "ERROR")
            time.sleep(5)

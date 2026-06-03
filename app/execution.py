"""Trade execution engine — trailing stops, confidence sizing, PnL tracking."""

import time
from datetime import datetime

from app.config import (RISK_PCT, LEVERAGE, MAX_OPEN_TRADES_PER_SYMBOL,
                         MAX_CLOSED_TRADES, TRADING_FEE,
                         TRAILING_ACTIVATE_PCT, TRAILING_DISTANCE,
                         SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER,
                         TRADE_COOLDOWN_MINUTES)
from app.state import (SYMBOLS, capital, capital_lock, state_lock,
                        signal_state, ticker, open_trades, closed_trades,
                        TRADE_ID_COUNTER, TRADE_ID_LOCK, indicators,
                        last_trade_close_time)
from app.database import save_closed_trade, save_state
from app.strategy import log_event
from app.self_learning import _save_trade_score_for_trade


def _dp(price):
    if price <= 0:
        return 8
    return 8 if price < 1.0 else 4


def get_next_trade_id():
    global TRADE_ID_COUNTER
    with TRADE_ID_LOCK:
        TRADE_ID_COUNTER += 1
        return TRADE_ID_COUNTER


def _confidence_multiplier(sym):
    """Get position size multiplier from signal confidence score.
    
    Score 0-3.5: no trade (filtered by strategy threshold)
    Score 3.5-5.0: 0.75x base risk (moderate confidence)
    Score 5.0-6.5: 1.0x base risk (good confidence)
    Score 6.5+: 1.5x base risk (high confidence)
    """
    with state_lock:
        score = abs(signal_state[sym].get("score", 0.0))
    if score < 3.5:
        return 0.0  # below threshold, shouldn't reach here but safety
    elif score < 5.0:
        return 0.75
    elif score < 6.5:
        return 1.0
    else:
        return 1.5


def execution_loop():
    last_save = time.time()
    while True:
        try:
            time.sleep(5)
            changed = False
            syms = list(SYMBOLS)

            for sym in syms:
                with state_lock:
                    sig = signal_state[sym].get("signal")
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

                # ── Check TP / SL / Trailing Stop ──────────────────────────
                for trade in trades_local:
                    notional = trade.get("notional", 250.0)
                    pnl_pct = ((current_price - trade["entry"]) /
                               trade["entry"] * trade["direction"])
                    close_reason = None

                    # Trailing stop logic
                    trade.setdefault("highest_price", trade["entry"])
                    trade.setdefault("trailing_active", False)

                    # Track price extremes
                    if trade["direction"] == 1:
                        if current_price > trade["highest_price"]:
                            trade["highest_price"] = current_price
                    else:
                        if current_price < trade["highest_price"]:
                            trade["highest_price"] = current_price

                    # Check if trailing should activate
                    tp_distance = abs(trade["tp"] - trade["entry"])
                    if tp_distance > 0 and not trade["trailing_active"]:
                        price_progress = abs(current_price - trade["entry"])
                        if price_progress >= tp_distance * TRAILING_ACTIVATE_PCT:
                            trade["trailing_active"] = True
                            log_event(
                                f"[{sym}] TRAIL ACTIVE: trade {trade['id']} "
                                f"({price_progress:.2f} >= "
                                f"{tp_distance * TRAILING_ACTIVATE_PCT:.2f})",
                                "TRADE")

                    # Apply trailing stop
                    if trade["trailing_active"]:
                        trail_distance = tp_distance * TRAILING_DISTANCE
                        if trade["direction"] == 1:
                            new_sl = trade["highest_price"] - trail_distance
                        else:
                            new_sl = trade["highest_price"] + trail_distance
                        new_sl = round(new_sl, 8)
                        if (trade["direction"] == 1 and new_sl > trade["sl"]) or \
                           (trade["direction"] == -1 and new_sl < trade["sl"]):
                            trade["sl"] = new_sl

                    # TP / SL check (now uses potentially updated trailing SL)
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

                    # Override SL → TRAILING when trailing stop closed a profitable trade
                    if close_reason == "SL" and trade.get("trailing_active") and trade.get("highest_price"):
                        # For LONG: trailing SL was above entry → price retraced but still profitable
                        # For SHORT: trailing SL was below entry → price rallied but still profitable
                        trailing_profited = (
                            (trade["direction"] == 1 and trade["highest_price"] > trade["entry"]) or
                            (trade["direction"] == -1 and trade["highest_price"] < trade["entry"])
                        )
                        if trailing_profited:
                            close_reason = "TRAILING"

                    if close_reason:
                        trade["exit_price"] = current_price
                        trade["exit_time"] = datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S")
                        gross_pnl = round(pnl_pct * notional, 2)

                        # Fees
                        entry_fee = trade.get("entry_fee",
                                              round(notional * TRADING_FEE, 2))
                        exit_fee = round(abs(notional + gross_pnl) * TRADING_FEE, 2)
                        total_fees = round(entry_fee + exit_fee, 2)
                        trade["entry_fee"] = entry_fee
                        trade["exit_fee"] = exit_fee
                        trade["total_fees"] = total_fees
                        net_pnl = round(gross_pnl - total_fees, 2)
                        trade["pnl"] = net_pnl

                        trade["reason"] = close_reason

                        # Update capital
                        with capital_lock:
                            cap = capital.get(sym)
                            if cap:
                                cap["balance"] += net_pnl
                                cap["total_pnl"] += net_pnl
                                cap["total_trades"] += 1
                                if net_pnl > 0:
                                    cap["winning_trades"] += 1
                                if cap["balance"] > cap["peak"]:
                                    cap["peak"] = cap["balance"]
                                if cap["peak"] > 0:
                                    dd = ((cap["peak"] - cap["balance"]) /
                                          cap["peak"] * 100)
                                    cap["max_dd"] = max(cap["max_dd"], dd)
                            sym_cap = dict(cap) if cap else {}

                        with state_lock:
                            trade["pattern_type"] = signal_state[sym].get("pattern_type", "")
                            trade["manip_range"] = signal_state[sym].get("score", 0)
                            closed_trades.append(dict(trade))
                            closed_trades[-1]["symbol"] = sym
                            if len(closed_trades) > MAX_CLOSED_TRADES:
                                closed_trades.pop(0)
                            if trade in open_trades[sym]:
                                open_trades[sym].remove(trade)
                            # Record trade close time for cooldown
                            last_trade_close_time[sym] = time.time()
                            # Don't clear last_entry_signal — prevents re-entry on same signal
                            # Strategy will reset it when generating a new signal

                        save_closed_trade(trade, sym)
                        save_state()
                    # Fire-and-forget trade scoring for self-learning
                        try:
                            _save_trade_score_for_trade(trade, sym)
                        except Exception:
                            pass

                        # Capture conditions snapshot on trade close
                        try:
                            from app.conditions import capture_conditions
                            capture_conditions("TRADE_CLOSE", sym,
                                f"Trade {trade['id']} CLOSED ({close_reason}) PnL=${net_pnl:.2f}")
                        except Exception:
                            pass

                        log_event(
                            f"[{sym}] TRADE {trade['id']} CLOSED "
                            f"({close_reason}): "
                            f"{'LONG' if trade['direction'] == 1 else 'SHORT'} "
                            f"Entry=${trade['entry']:.{_dp(trade['entry'])}f} "
                            f"Exit=${trade['exit_price']:.{_dp(trade['exit_price'])}f} "
                            f"PnL=${net_pnl:.2f} "
                            f"Fees=${total_fees:.2f} | "
                        f"Cap=${sym_cap.get('balance', 0):.2f}",
                        "TRADE")

                # ── Open new trades on signal ──────────────────────────────
                with state_lock:
                    last_entry = signal_state[sym].get("last_entry_signal")
                    current_sig = signal_state[sym].get("signal")
                    num_open = len(open_trades[sym])

                # Cooldown: prevent rapid re-entry on same symbol
                cooldown_secs = TRADE_COOLDOWN_MINUTES * 60
                time_since_close = time.time() - last_trade_close_time.get(sym, 0)
                if time_since_close < cooldown_secs:
                    continue

                if (current_sig in ("LONG", "SHORT") and
                        current_sig != last_entry and
                        num_open < MAX_OPEN_TRADES_PER_SYMBOL):
                    tp = signal_state[sym].get("tp", 0)
                    sl = signal_state[sym].get("sl", 0)
                    direction = signal_state[sym].get("direction", 0)
                    if tp <= 0 or sl <= 0 or current_price <= 0:
                        continue

                    # Recompute TP/SL from actual live entry price using ATR × RR_RATIO
                    # Fix: signal's TP/SL was computed from last closed candle close,
                    # but the actual entry is at the live price — if price moved since
                    # the candle closed, the SL could be paper-thin or even on the wrong side.
                    # TP is derived from SL distance × RR_RATIO to guarantee 1:2 ratio.
                    from app.self_learning import get_effective_param
                    rr = get_effective_param("rr_ratio")
                    a15 = indicators.get(sym, {}).get("15m_atr14", 0)
                    if a15 <= 0:
                        a15 = 0.0001
                    if direction == 1:
                        sl = round(current_price - SL_ATR_MULTIPLIER * a15, 8)
                        sl_distance = abs(current_price - sl)
                        tp = round(current_price + sl_distance * rr, 8)
                    else:
                        sl = round(current_price + SL_ATR_MULTIPLIER * a15, 8)
                        sl_distance = abs(current_price - sl)
                        tp = round(current_price - sl_distance * rr, 8)

                    # Confidence-based position sizing
                    conf_mult = _confidence_multiplier(sym)
                    if conf_mult <= 0:
                        continue

                    with capital_lock:
                        sym_cap = capital.get(sym)
                        if not sym_cap or sym_cap["balance"] <= 0:
                            continue
                        base_risk = sym_cap["balance"] * RISK_PCT
                        risk_amount = base_risk * conf_mult
                        if risk_amount <= 0:
                            continue

                    # Position sizing: risk-based SL distance
                    sl_distance = abs(current_price - sl)
                    if sl_distance <= 0:
                        continue
                    sl_distance_pct = sl_distance / current_price
                    notional = risk_amount / sl_distance_pct
                    margin = notional / LEVERAGE

                    with capital_lock:
                        sym_cap = capital.get(sym, {})
                        available = sym_cap.get("balance", 0)
                        if margin > available * 2:
                            margin = available * 2
                            notional = margin * LEVERAGE

                    entry_type = signal_state[sym].get("pattern_type", "")
                    score = signal_state[sym].get("score", 0)
                    trend = signal_state[sym].get("trend", "")
                    vol_label = signal_state[sym].get("vol_label", "")

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
                        "entry_fee": round(notional * TRADING_FEE, 2),
                        "exit_fee": 0.0,
                        "total_fees": 0.0,
                        "exit_price": None,
                        "exit_time": None,
                        "pnl": 0.0,
                        "unrealized_pnl": 0.0,
                        "reason": "",
                        "pattern_type": entry_type,
                        "manip_range": score,
                        "daily_atr": 0,
                        "risk_amount": round(risk_amount, 2),
                        "confidence_mult": conf_mult,
                        "highest_price": current_price,
                        "trailing_active": False,
                    }
                    with state_lock:
                        open_trades[sym].append(trade)
                        signal_state[sym]["last_entry_signal"] = current_sig
                    save_state()

                    log_event(
                        f"[{sym}] TRADE {trade['id']} OPENED "
                        f"({current_sig} | "
                        f"conf={conf_mult:.1f}x score={score:+.1f}): "
                        f"Entry=${current_price:.{_dp(current_price)}f} "
                        f"TP=${tp:.{_dp(tp)}f} SL=${sl:.{_dp(sl)}f} "
                        f"Notional=${notional:.0f} Margin=${margin:.2f} "
                        f"Risk=${risk_amount:.2f} | "
                        f"{entry_type} {trend} {vol_label}",
                        "TRADE")

                    # Capture conditions snapshot on trade open
                    try:
                        from app.conditions import capture_conditions
                        capture_conditions("TRADE_OPEN", sym,
                            f"Trade {trade['id']} OPENED ({current_sig}) Entry=${current_price:.{_dp(current_price)}f}")
                    except Exception:
                        pass

            # ── Periodic state save ────────────────────────────────────────
            now_save = time.time()
            total_open = sum(len(open_trades[s]) for s in SYMBOLS)
            if total_open > 0 and (changed or now_save - last_save > 30):
                save_state()
                last_save = now_save

        except Exception as e:
            log_event(f"Execution error: {e}", "ERROR")
            time.sleep(5)

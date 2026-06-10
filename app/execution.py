"""Trade execution engine — trailing stops, confidence sizing, PnL tracking."""

import time
from datetime import datetime

from app.config import (RISK_PCT, LEVERAGE, MAX_OPEN_TRADES_PER_SYMBOL,
                         MAX_CLOSED_TRADES, TRADING_FEE,
                         TRAILING_ACTIVATE_PCT, TRAILING_DISTANCE,
                         SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER,
                         RR_RATIO,
                         TRADE_COOLDOWN_MINUTES,
                         CONSECUTIVE_SL_LIMIT, SL_PAUSE_SECONDS)
from app.state import (SYMBOLS, capital, capital_lock, state_lock,
                        signal_state, ticker, open_trades, closed_trades,
                        TRADE_ID_COUNTER, TRADE_ID_LOCK, indicators,
                        last_trade_close_time,
                        consecutive_sl_losses, sl_pause_until,
                        global_suspension_until, global_suspension_lock,
                        suspension_fingerprint)
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
            time.sleep(1)
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
                            # Cap at breakeven — never let trailing SL go below entry
                            new_sl = max(new_sl, trade["entry"])
                        else:
                            new_sl = trade["highest_price"] + trail_distance
                            # Cap at breakeven — never let trailing SL go above entry
                            new_sl = min(new_sl, trade["entry"])
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
                    # BUT only if the actual PnL is positive (not gapped past SL into loss)
                    if close_reason == "SL" and trade.get("trailing_active") and trade.get("highest_price"):
                        trailing_profited = (
                            (trade["direction"] == 1 and trade["highest_price"] > trade["entry"]) or
                            (trade["direction"] == -1 and trade["highest_price"] < trade["entry"])
                        )
                        # Also verify the close is at a profitable level — prevents
                        # "TRAILING" label on trades that gapped past SL into loss
                        # LONG: profitable when current_price > entry (price went up)
                        # SHORT: profitable when current_price < entry (price went down)
                        pnl_profitable = (
                            (trade["direction"] == 1 and current_price > trade["entry"]) or
                            (trade["direction"] == -1 and current_price < trade["entry"])
                        )
                        if trailing_profited and pnl_profitable:
                            close_reason = "TRAILING"

                    # ── Consecutive SL Tracking ──────────────────────────────────
                    if close_reason == "SL":
                        consecutive_sl_losses[sym] = consecutive_sl_losses.get(sym, 0) + 1
                        if consecutive_sl_losses[sym] >= CONSECUTIVE_SL_LIMIT:
                            sl_pause_until[sym] = time.time() + SL_PAUSE_SECONDS
                            log_event(
                                f"[{sym}] PAUSED 1h after {consecutive_sl_losses[sym]} consecutive SLs",
                                "WARN")
                    elif close_reason in ("TP", "TRAILING"):
                        if consecutive_sl_losses.get(sym, 0) > 0:
                            consecutive_sl_losses[sym] = 0
                            log_event(
                                f"[{sym}] Consecutive SL counter reset ({close_reason})",
                                "INFO")

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

                # Consecutive SL pause: check if symbol is in cooldown
                pause_end = sl_pause_until.get(sym, 0)
                if pause_end > time.time():
                    continue

                # ── Open Positions Loss Filter ──────────────────────────────
                # If 50%+ of all open positions have negative unrealized PnL,
                # skip opening new trades to avoid stacking losses.
                # Requires at least 2 open positions — a single trade in loss
                # is normal price noise and shouldn't freeze the system.
                all_syms = list(SYMBOLS)
                total_open_positions = 0
                losing_positions = 0
                for s in all_syms:
                    with state_lock:
                        trades_s = list(open_trades.get(s, []))
                    for t in trades_s:
                        total_open_positions += 1
                        if t.get("unrealized_pnl", 0) < 0:
                            losing_positions += 1
                if total_open_positions >= 2 and losing_positions > total_open_positions * 0.5:
                    log_event(
                        f"[{sym}] SKIP OPEN: {losing_positions}/{total_open_positions} "
                        f"open positions in loss (≥50% threshold)", "WARN")
                    continue

                # ── Global 5-Loss Suspension ────────────────────────────────
                # If the last 5 closed trades are ALL losses, suspend new
                # entries for 2h. Existing open positions still resolve.
                #
                # Uses a TRADE-COUNT FINGERPRINT to prevent infinite re-trigger
                # loop: records len(closed_trades) when suspension fires, and
                # only re-evaluates the 5-loss condition when new trades have
                # been added beyond that count. This cleanly separates old loss
                # runs from new ones — once the fingerprint is set, the same
                # set of trades cannot trigger another suspension.
                with state_lock:
                    all_closed = list(closed_trades)
                    closed_count = len(all_closed)

                with global_suspension_lock:
                    guard = global_suspension_until[0]
                    fp = suspension_fingerprint[0]

                # Only check the 5-loss condition when new trades have been
                # added since the last suspension trigger (fingerprint guard).
                if closed_count > fp and closed_count >= 3:
                    last_n = all_closed[-3:]
                    all_loss = all(t.get("pnl", 0) < 0 for t in last_n)
                    if all_loss:
                        with global_suspension_lock:
                            # Guard values <= 0 mean "not suspended"
                            if global_suspension_until[0] <= 0.0:
                                global_suspension_until[0] = time.time() + 10800
                                suspension_fingerprint[0] = closed_count
                                log_event(
                                    f"[{sym}] 3-LOSS SUSPENSION: last 3 trades all negative "
                                    f"PnL — new trades suspended for 3h "
                                    f"(fingerprint={closed_count})", "WARN")
                                guard = global_suspension_until[0]
                                fp = suspension_fingerprint[0]

                # Check if suspension is active
                if guard > time.time():
                    remain = int(guard - time.time())
                    log_event(
                        f"[{sym}] SKIP OPEN: global suspension active "
                        f"({remain//3600}h {(remain%3600)//60}m remaining)", "WARN")
                    continue
                elif guard > 0:
                    # Suspension expired — clear guard (back to 0.0).
                    # ALSO advance fingerprint past current trade count so the
                    # same set of trades cannot re-trigger immediately. Trades
                    # that closed during the suspension increased closed_count,
                    # making closed_count > old_fp → false alarm re-trigger.
                    with global_suspension_lock:
                        if global_suspension_until[0] > 0 and \
                           global_suspension_until[0] <= time.time():
                            global_suspension_until[0] = 0.0
                            suspension_fingerprint[0] = closed_count
                            log_event(
                                f"[{sym}] Global suspension expired — "
                                f"resuming trades (fingerprint={closed_count})", "INFO")

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
                    rr = RR_RATIO  # Fixed 1:2 — not tunable by self-learning
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

                    # Volatility-based risk adjustment
                    with state_lock:
                        vol_label = signal_state[sym].get("vol_label", "normal")
                    if vol_label == "high":
                        vol_mult = 0.5  # Halve risk in high volatility
                    elif vol_label == "low":
                        vol_mult = 1.2  # Slightly increase in low vol
                    else:
                        vol_mult = 1.0
                    conf_mult *= vol_mult

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
            time.sleep(1)

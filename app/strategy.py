"""Pattern Scalp strategy engine.

Detection → Reversal → Entry signal.
"""

import threading
from datetime import datetime, timedelta

from app.config import (JOHN_WICK_WICK_RATIO, POWER_TOWER_RETRACE,
                         TP_RANGE_PCT, MAX_EVENT_LOG)
from app.state import (SYMBOLS, candles, signal_state, daily_atr,
                        daily_atr_threshold, manipulation_candle,
                        manipulation_active, reversal_pattern,
                        last_15m_time, open_trades, indicators,
                        state_lock, event_log)
from app.indicators import atr
from app.database import save_event as _save_event


# ─── Dynamic decimal precision ──────────────────────────────────────────────
def _dp(price):
    """Pick decimal places for display."""
    if price <= 0:
        return 8
    return 8 if price < 1.0 else 4


# ─── Logging ──────────────────────────────────────────────────────────────────

def log_event(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = {"time": timestamp, "message": msg, "level": level}
    with state_lock:
        event_log.append(entry)
        if len(event_log) > MAX_EVENT_LOG:
            event_log.pop(0)
    _save_event(msg, level)


# ─── Pattern Detection ────────────────────────────────────────────────────────

def detect_john_wick(candle, manip_direction):
    """Long-lower-wick (bullish) or long-upper-wick (bearish) hammer."""
    total_range = candle["high"] - candle["low"]
    if total_range <= 0:
        return False, 0
    upper_wick = candle["high"] - max(candle["open"], candle["close"])
    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
    if manip_direction == "UP":
        # Manipulation pushed price UP → expect upper wick (bearish reversal)
        if upper_wick / total_range >= JOHN_WICK_WICK_RATIO:
            return True, -1   # SHORT
    else:
        # Manipulation pushed price DOWN → expect lower wick (bullish reversal)
        if lower_wick / total_range >= JOHN_WICK_WICK_RATIO:
            return True, 1    # LONG
    return False, 0


def detect_power_tower(candles_5m, manip_direction):
    """Engulfing candle that retraces into manipulation range."""
    if len(candles_5m) < 2:
        return False, 0
    prev = candles_5m[-2]
    curr = candles_5m[-1]
    prev_body = prev["close"] - prev["open"]
    curr_body = curr["close"] - curr["open"]
    if manip_direction == "UP":
        # Bull trap → bearish engulfing
        if prev_body > 0 and curr_body < 0:
            if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
                return True, -1   # SHORT
    else:
        # Bear trap → bullish engulfing
        if prev_body < 0 and curr_body > 0:
            if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
                return True, 1    # LONG
    return False, 0


# ─── Manipulation Detection ───────────────────────────────────────────────────

def check_new_manipulation(sym):
    """Check the latest 15m candle for manipulation (liquidity sweep)."""
    with state_lock:
        c15 = list(candles[sym]["15m"])
    if len(c15) < 2:
        return None
    latest = c15[-1]
    candle_time = latest["time"]
    if candle_time == last_15m_time.get(sym):
        return None
    last_15m_time[sym] = candle_time
    candle_range = latest["high"] - latest["low"]
    threshold = daily_atr_threshold.get(sym, 0)
    if threshold <= 0:
        return None
    if candle_range < threshold:
        manipulation_active[sym] = False
        manipulation_candle[sym] = None
        reversal_pattern[sym] = None
        return None

    # Determine direction: which side got swept?
    open_to_high = latest["high"] - latest["open"]
    open_to_low = latest["open"] - latest["low"]
    close_to_high = latest["high"] - latest["close"]
    close_to_low = latest["close"] - latest["low"]
    # Upper wick = move above open/close = liquidity sweep UP
    # Lower wick = move below open/close = liquidity sweep DOWN
    upper_wick = max(open_to_high, close_to_high)
    lower_wick = max(open_to_low, close_to_low)
    total_range = latest["high"] - latest["low"]
    if total_range <= 0:
        return None
    upper_ratio = upper_wick / total_range
    lower_ratio = lower_wick / total_range
    if lower_ratio >= 0.55:       # at least 55% of range is lower wick → DOWN sweep
        direction = "DOWN"
    elif upper_ratio >= 0.55:     # at least 55% of range is upper wick → UP sweep
        direction = "UP"
    else:                         # ambiguous — use close direction
        direction = "UP" if latest["close"] > latest["open"] else "DOWN"

    manipulation_candle[sym] = {
        "time": candle_time, "high": latest["high"], "low": latest["low"],
        "open": latest["open"], "close": latest["close"],
        "direction": direction, "range": round(candle_range, 8),
    }
    manipulation_active[sym] = True
    reversal_pattern[sym] = None
    # Clear entry guard so new reversal signal can trigger a fresh trade
    with state_lock:
        signal_state[sym]["last_entry_signal"] = None
    log_event(
        f"[{sym}] MANIPULATION: {direction} spike | "
        f"Range=${candle_range:.{_dp(candle_range)}f} "
        f"(≥${threshold:.{_dp(threshold)}f})", "SIGNAL")
    return manipulation_candle[sym]


# ─── Signal Evaluation ────────────────────────────────────────────────────────

def evaluate_signal_for_symbol(sym):
    """Evaluate pattern scalp signal for one symbol."""
    if daily_atr.get(sym, 0) <= 0:
        return

    # Compute 5m ATR for display
    with state_lock:
        c5_for_atr = list(candles[sym]["5m"])
    if len(c5_for_atr) >= 15:
        c5_cl = [c["close"] for c in c5_for_atr]
        c5_hi = [c["high"] for c in c5_for_atr]
        c5_lo = [c["low"] for c in c5_for_atr]
        a5 = atr(c5_hi, c5_lo, c5_cl, 14)
        with state_lock:
            indicators[sym]["5m_atr14"] = round(a5, 8)

    check_new_manipulation(sym)

    if not manipulation_active.get(sym) or manipulation_candle.get(sym) is None:
        with state_lock:
            signal_state[sym]["signal"] = None
            signal_state[sym]["direction"] = 0
            signal_state[sym]["tp"] = 0.0
            signal_state[sym]["sl"] = 0.0
            signal_state[sym]["manipulation_range"] = 0.0
            signal_state[sym]["pattern_type"] = ""
        return

    with state_lock:
        c5 = list(candles[sym]["5m"])
    if len(c5) < 3:
        return

    manip = manipulation_candle[sym]
    manip_dir = manip["direction"]
    manip_range = manip["range"]
    manip_high = manip["high"]
    manip_low = manip["low"]

    # Phase 2: Detect reversal pattern
    pattern_found = None
    pattern_type = ""
    direction = 0

    # Priority 1: John Wick (long wick)
    for offset in [1, 2]:
        idx = -(offset + 1) if offset < len(c5) else None
        if idx is None or abs(idx) > len(c5):
            continue
        is_wick, wick_dir = detect_john_wick(c5[idx], manip_dir)
        if is_wick:
            pattern_type = "JOHN_WICK"
            direction = wick_dir
            trigger = c5[idx]["high"] if direction == 1 else c5[idx]["low"]
            pattern_found = {
                "type": pattern_type, "trigger": round(trigger, 8),
                "direction": direction, "candle_time": c5[idx]["time"],
            }
            break

    # Priority 2: Power Tower (engulfing)
    if pattern_found is None:
        is_pt, pt_dir = detect_power_tower(c5, manip_dir)
        if is_pt:
            pattern_type = "POWER_TOWER"
            direction = pt_dir
            last = c5[-1]
            trigger = last["high"] if direction == 1 else last["low"]
            pattern_found = {
                "type": pattern_type, "trigger": round(trigger, 8),
                "direction": direction, "candle_time": last["time"],
            }

    # Phase 3: Entry signal
    if pattern_found:
        reversal_pattern[sym] = pattern_found
        if direction == 1:
            sl = manip_low
            tp = manip_low + manip_range * TP_RANGE_PCT
        else:
            sl = manip_high
            tp = manip_high - manip_range * TP_RANGE_PCT
        with state_lock:
            signal_state[sym]["signal"] = "LONG" if direction == 1 else "SHORT"
            signal_state[sym]["direction"] = direction
            signal_state[sym]["tp"] = round(tp, 8)
            signal_state[sym]["sl"] = round(sl, 8)
            signal_state[sym]["manipulation_range"] = round(manip_range, 8)
            signal_state[sym]["pattern_type"] = pattern_type
        log_event(
            f"[{sym}] REVERSAL: {pattern_type} → "
            f"{'LONG' if direction == 1 else 'SHORT'} | "
            f"TP=${tp:.{_dp(tp)}f} SL=${sl:.{_dp(sl)}f}", "SIGNAL")

    # Don't reset last_entry_signal here — execution loop owns the entry guard.
    # Resetting it causes duplicate trade attempts on every strategy cycle
    # when save_state() fails due to DB schema issues.

    # Expire stale manipulations (>2h)
    try:
        manip_time = datetime.strptime(manip["time"], "%Y-%m-%d %H:%M")
        if datetime.now() - manip_time > timedelta(hours=2):
            log_event(f"[{sym}] Manipulation expired (>2h). Reset.", "SIGNAL")
            manipulation_active[sym] = False
            manipulation_candle[sym] = None
            reversal_pattern[sym] = None
    except Exception:
        pass


# ─── Strategy Loop ────────────────────────────────────────────────────────────

def strategy_loop():
    while True:
        try:
            syms = list(SYMBOLS)
            for sym in syms:
                evaluate_signal_for_symbol(sym)
        except Exception as e:
            log_event(f"Strategy error: {e}", "ERROR")
        import time
        time.sleep(2)


# ─── Symbol Addition Helper ───────────────────────────────────────────────────

def _bootstrap_and_restart_ws(sym):
    """Bootstrap data for new symbol, then restart WS."""
    from app.market_data import bootstrap_historical_candles, _trigger_ws_restart
    bootstrap_historical_candles(sym)
    _trigger_ws_restart()

"""Hybrid Scoring + Candlestick Pattern Strategy — Dynamic & Self-Improving.

Combines:
1. Candlestick patterns (Hammer, Shooting Star, Engulfing) — high confidence
2. Momentum scoring (EMA cross, RSI, MACD, volume) — medium frequency
3. Regime detection (trending/ranging, volatility) — adapts parameters
4. Confidence scoring — weighs all signals, only trades ≥ threshold
5. Per-entry-type performance tracking — self-learning tunes weights

Self-improving: tunes threshold (entry_strictness), wick_ratio, rr_ratio,
entry_type_weights (per-type), and risk_pct after every N trades.
"""

import time
from datetime import datetime
from collections import defaultdict

from app.config import (HAMMER_MIN_WICK_RATIO, RR_RATIO, MAX_EVENT_LOG,
                         ENTRY_THRESHOLD, RSI_OVERSOLD, RSI_OVERBOUGHT,
                         HAMMER_WEIGHT, SHOOTING_STAR_WEIGHT, ENGULFING_WEIGHT,
                         EMA_CROSS_WEIGHT, EMA_POSITION_WEIGHT, RSI_WEIGHT,
                         MACD_WEIGHT, VOLUME_WEIGHT, SL_ATR_MULTIPLIER,
                         TP_ATR_MULTIPLIER, TREND_EMA_PERIOD,
                         TREND_STRENGTH_MIN, ENTRY_TYPES, CANDLE_LIMIT)
from app.state import (SYMBOLS, candles, signal_state, daily_atr,
                        daily_atr_threshold, manipulation_candle,
                        manipulation_active, reversal_pattern,
                        last_15m_time, open_trades, indicators,
                        state_lock, event_log)
from app.indicators import atr, rsi, macd, ema, sma
from app.indicators import detect_engulfing, compute_ema_slope, classify_volatility
from app.database import save_event as _save_event
from app.self_learning import get_effective_param


def _dp(price):
    if price <= 0:
        return 8
    return 8 if price < 1.0 else 4


def log_event(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = {"time": timestamp, "message": msg, "level": level}
    with state_lock:
        event_log.append(entry)
        if len(event_log) > MAX_EVENT_LOG:
            event_log.pop(0)
    _save_event(msg, level)


# ─── Candlestick Pattern Detection ────────────────────────────────────────

def detect_hammer(candle, min_wick_ratio):
    """Hammer: long lower wick >= min_wick_ratio × body, tiny upper wick."""
    body = abs(candle["close"] - candle["open"])
    total_range = candle["high"] - candle["low"]
    if total_range <= 0 or body <= 0:
        return False, 0
    upper_wick = candle["high"] - max(candle["open"], candle["close"])
    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
    if lower_wick >= min_wick_ratio * body and upper_wick <= body * 0.3:
        return True, 1
    return False, 0


def detect_shooting_star(candle, min_wick_ratio):
    """Shooting Star: long upper wick >= min_wick_ratio × body, tiny lower wick."""
    body = abs(candle["close"] - candle["open"])
    total_range = candle["high"] - candle["low"]
    if total_range <= 0 or body <= 0:
        return False, 0
    upper_wick = candle["high"] - max(candle["open"], candle["close"])
    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
    if upper_wick >= min_wick_ratio * body and lower_wick <= body * 0.3:
        return True, -1
    return False, 0


def detect_doji(candle):
    """Doji: body < 10% of total range."""
    body = abs(candle["close"] - candle["open"])
    total_range = candle["high"] - candle["low"]
    if total_range <= 0:
        return False
    return body / total_range < 0.1


# ─── Data Helpers ─────────────────────────────────────────────────────────

def get_closed_15m(sym, lookback=10):
    """Get closed 15m candles (excludes live forming candle)."""
    with state_lock:
        c15 = list(candles[sym]["15m"])
    if len(c15) < 3:
        return []
    start = max(0, len(c15) - lookback - 1)
    return c15[start:-1]


def compute_indicators(sym, closed):
    """Compute all indicators for the strategy from closed 15m candles.
    
    Returns dict with: a15, rsi_val, macd_line, macd_sig, macd_hist,
    ema9, ema21, ema20, ema50, trend, volatility, vol_label, ema_slope,
    vol_sma20, current_volume.
    """
    result = {"a15": 0.0, "rsi_val": 50.0, "trend": "ranging",
              "vol_label": "normal"}
    
    if len(closed) < 15:
        return result
    
    closes = [c["close"] for c in closed]
    highs = [c["high"] for c in closed]
    lows = [c["low"] for c in closed]
    volumes = [c["volume"] for c in closed]
    
    # ATR
    result["a15"] = atr(highs, lows, closes, 14)
    
    # RSI
    result["rsi_val"] = rsi(closes, 14)
    
    # MACD
    m_line, m_sig, m_hist = macd(closes, 12, 26, 9)
    result["macd_line"] = m_line
    result["macd_sig"] = m_sig
    result["macd_hist"] = m_hist
    
    # EMA
    ema9_vals = ema(closes, 9)
    ema21_vals = ema(closes, 21)
    ema20_vals = ema(closes, TREND_EMA_PERIOD)
    result["ema9"] = ema9_vals[-1] if ema9_vals and ema9_vals[-1] is not None else 0
    result["ema21"] = ema21_vals[-1] if ema21_vals and ema21_vals[-1] is not None else 0
    result["ema20"] = ema20_vals[-1] if ema20_vals and ema20_vals[-1] is not None else 0
    
    # Trend (from EMA slope)
    slope = compute_ema_slope(closes, TREND_EMA_PERIOD, lookback=5)
    result["ema_slope"] = slope
    price = closes[-1] if closes else 1
    slope_pct = abs(slope) / max(price, 0.0001)
    if slope_pct >= TREND_STRENGTH_MIN:
        result["trend"] = "uptrend" if slope > 0 else "downtrend"
    else:
        result["trend"] = "ranging"

    # Price-vs-EMA20 cross-check: if close is on the wrong side of EMA,
    # the EMA20 slope is lagging — downgrade to "ranging" so the regime
    # filter doesn't block the correct direction.
    if result.get("ema20", 0) > 0 and closes[-1] > 0:
        if result["trend"] == "uptrend" and closes[-1] < result["ema20"]:
            result["trend"] = "ranging"
        elif result["trend"] == "downtrend" and closes[-1] > result["ema20"]:
            result["trend"] = "ranging"

    # Volatility
    vol_label, cur_atr, vol_pct = classify_volatility(highs, lows, closes, 14, 50)
    result["vol_label"] = vol_label
    result["vol_pct"] = vol_pct
    
    # Volume SMA20
    if len(volumes) >= 20:
        result["vol_sma20"] = sum(volumes[-20:]) / 20
    else:
        result["vol_sma20"] = sum(volumes) / len(volumes) if volumes else 0
    result["current_volume"] = volumes[-1] if volumes else 0
    
    return result


# ─── Entry Signal Scoring ─────────────────────────────────────────────────

def score_entry(sym, closed, ind):
    """Score all entry signal types and return the best setup.
    
    Returns dict with: score, direction, entry_type, trigger_price,
    confidence_msg, atr_stop, tp_price, sl_price.
    
    Score range: -8.0 to +8.0 (positive = long bias, negative = short bias).
    Entry requires abs(score) >= adaptive_threshold.
    """
    if len(closed) < 3:
        return None
    
    closest = closed[-1]  # most recent closed candle
    closes = [c["close"] for c in closed]
    a15 = ind["a15"]
    rsi_val = ind["rsi_val"]
    trend = ind["trend"]
    vol_label = ind["vol_label"]
    
    # Adaptive parameters from self-learning
    wick_ratio = get_effective_param("wick_ratio")
    entry_threshold = get_effective_param("entry_threshold")
    
    # Track how many entry types fire and their contributions
    contributions = []

    # Check preceding trend context for reversal patterns.
    # HAMMER should only fire after a downtrend, SHOOTING_STAR after an uptrend.
    # Use a slope check over last 4 closes (not strict consecutiveness).
    def _preceding_downtrend(closes, lookback=4):
        """Returns True if the last `lookback` closes show a downward slope."""
        if len(closes) < lookback + 1:
            return False
        prev = closes[-(lookback+1):-1]
        # Simple check: first close > last close = downward movement
        return prev[0] > prev[-1]

    def _preceding_uptrend(closes, lookback=4):
        """Returns True if the last `lookback` closes show an upward slope."""
        if len(closes) < lookback + 1:
            return False
        prev = closes[-(lookback+1):-1]
        return prev[0] < prev[-1]

    # --- 1. Candlestick Patterns (highest confidence) ---
    for i in range(min(3, len(closed))):
        candle = closed[-(i + 1)]
        if detect_doji(candle):
            continue
        
        # Hammer → LONG (only if preceding downtrend — reversal pattern)
        is_ham, ham_dir = detect_hammer(candle, wick_ratio)
        if is_ham and _preceding_downtrend(closes, lookback=3):
            contributions.append({
                "type": "HAMMER", "score": HAMMER_WEIGHT,
                "direction": 1, "trigger": candle["high"],
                "candle_low": candle["low"], "candle_high": candle["high"],
            })
            break
        
        # Shooting Star → SHORT (only if preceding uptrend — reversal pattern)
        is_star, star_dir = detect_shooting_star(candle, wick_ratio)
        if is_star and _preceding_uptrend(closes, lookback=3):
            contributions.append({
                "type": "SHOOTING_STAR", "score": SHOOTING_STAR_WEIGHT,
                "direction": -1, "trigger": candle["low"],
                "candle_low": candle["low"], "candle_high": candle["high"],
            })
            break
    
    # --- 2. Engulfing Pattern ---
    is_eng, eng_dir = detect_engulfing(closed)
    if is_eng:
        weight = ENGULFING_WEIGHT * (1 if eng_dir == 1 else -1)
        contributions.append({
            "type": "ENGULFING", "score": weight,
            "direction": eng_dir,
            "trigger": closed[-1]["high"] if eng_dir == 1 else closed[-1]["low"],
            "candle_low": closed[-1]["low"], "candle_high": closed[-1]["high"],
        })
    
    # --- 3. EMA Cross (fresh cross on last 2 candles) ---
    ema9_now = ind.get("ema9", 0)
    ema21_now = ind.get("ema21", 0)
    if ema9_now > 0 and ema21_now > 0:
        # Check if EMA just crossed
        closes_slice = closes[-(min(4, len(closes))):]
        ema9_slice = ema(closes_slice, 9)
        ema21_slice = ema(closes_slice, 21)
        if len(ema9_slice) >= 3 and len(ema21_slice) >= 3:
            crossed_up = (ema9_slice[-2] is not None and ema21_slice[-2] is not None
                          and ema9_slice[-2] <= ema21_slice[-2]
                          and ema9_now > ema21_now)
            crossed_down = (ema9_slice[-2] is not None and ema21_slice[-2] is not None
                            and ema9_slice[-2] >= ema21_slice[-2]
                            and ema9_now < ema21_now)
            if crossed_up:
                contributions.append({
                    "type": "EMA_CROSS", "score": EMA_CROSS_WEIGHT,
                    "direction": 1, "trigger": closest["close"],
                    "candle_low": closest["low"], "candle_high": closest["high"],
                })
            elif crossed_down:
                contributions.append({
                    "type": "EMA_CROSS", "score": -EMA_CROSS_WEIGHT,
                    "direction": -1, "trigger": closest["close"],
                    "candle_low": closest["low"], "candle_high": closest["high"],
                })
            else:
                # No fresh cross, but position matters
                pos_score = EMA_POSITION_WEIGHT if ema9_now > ema21_now else -EMA_POSITION_WEIGHT
                contributions.append({
                    "type": "EMA_POSITION", "score": pos_score,
                    "direction": 1 if pos_score > 0 else -1,
                    "trigger": closest["close"],
                    "candle_low": closest["low"], "candle_high": closest["high"],
                })
    
    # --- 4. RSI Bounce/Rejection ---
    os_threshold = RSI_OVERSOLD
    ob_threshold = RSI_OVERBOUGHT
    if rsi_val < os_threshold:
        # Oversold → potential bounce LONG
        depth = (os_threshold - rsi_val) / os_threshold  # 0-1 how oversold
        contributions.append({
            "type": "RSI_BOUNCE", "score": RSI_WEIGHT * (1 + depth * 0.3),
            "direction": 1,
            "trigger": closest["low"],
            "candle_low": closest["low"], "candle_high": closest["high"],
        })
    elif rsi_val > ob_threshold:
        depth = (rsi_val - ob_threshold) / (100 - ob_threshold)
        contributions.append({
            "type": "RSI_BOUNCE", "score": -RSI_WEIGHT * (1 + depth * 0.3),
            "direction": -1,
            "trigger": closest["high"],
            "candle_low": closest["low"], "candle_high": closest["high"],
        })
    else:
        # RSI in neutral zone — trend alignment matters more
        if trend == "uptrend" and rsi_val > 50:
            contributions.append({
                "type": "RSI_BOUNCE", "score": RSI_WEIGHT * 0.5,
                "direction": 1,
                "trigger": closest["close"],
                "candle_low": closest["low"], "candle_high": closest["high"],
            })
        elif trend == "downtrend" and rsi_val < 50:
            contributions.append({
                "type": "RSI_BOUNCE", "score": -RSI_WEIGHT * 0.5,
                "direction": -1,
                "trigger": closest["close"],
                "candle_low": closest["low"], "candle_high": closest["high"],
            })
    
    # --- 5. MACD Histogram ---
    macd_hist = ind.get("macd_hist", 0)
    if abs(macd_hist) > 0:
        macd_score = MACD_WEIGHT * (1 if macd_hist > 0 else -1)
        # Scale by magnitude (weak = 0.5x, strong = 1.5x)
        macd_mag = min(abs(macd_hist) / max(abs(ind.get("macd_line", 0.0001)), 0.0001), 1.5)
        macd_score *= min(macd_mag, 1.5)
        contributions.append({
            "type": "MOMENTUM", "score": round(macd_score, 2),
            "direction": 1 if macd_score > 0 else -1,
            "trigger": closest["close"],
            "candle_low": closest["low"], "candle_high": closest["high"],
        })
    
    # --- 6. Volume Confirmation ---
    vol_sma20 = ind.get("vol_sma20", 0)
    current_vol = ind.get("current_volume", 0)
    if vol_sma20 > 0 and current_vol > 0:
        vol_ratio = current_vol / vol_sma20
        if vol_ratio > 1.2:
            vol_score = VOLUME_WEIGHT  # direction taken from total score later
            contributions.append({
                "type": "MOMENTUM", "score": vol_score,
                "direction": 0,  # directionless — amplifies existing bias
                "trigger": closest["close"],
                "candle_low": closest["low"], "candle_high": closest["high"],
            })
    
    # ─── Aggregate Score ──────────────────────────────────────────────────
    if not contributions:
        return None
    
    # Compute total raw score
    total_score = sum(c["score"] for c in contributions)
    
    # Apply volume direction: volume takes sign of total score
    for c in contributions:
        if c["direction"] == 0 and c["type"] == "MOMENTUM":
            c["direction"] = 1 if total_score > 0 else -1
            c["score"] = abs(c["score"]) if total_score > 0 else -abs(c["score"])
    
    # Recompute total after volume direction fix
    total_score = sum(c["score"] for c in contributions)
    
    # Determine direction and best entry type
    direction = 1 if total_score > 0 else -1
    abs_total = abs(total_score)

    # Signal direction validation: dominant pattern shouldn't contradict final direction
    # e.g. HAMMER (bullish) should not produce a SHORT trade
    abs_contribs = [(abs(c["score"]), c) for c in contributions]
    abs_contribs.sort(key=lambda x: x[0], reverse=True)
    best_contrib = abs_contribs[0][1]
    best_dir = 1 if best_contrib["score"] > 0 else -1
    is_candle_pattern = best_contrib["type"] in ("HAMMER", "SHOOTING_STAR", "ENGULFING")
    if is_candle_pattern and direction != best_dir:
        # Major conflict: a candle pattern says one direction but total score says opposite.
        # Likely means conflicting signals are muddling the score — penalize heavily.
        total_score *= 0.3
        direction = 1 if total_score > 0 else -1
        abs_total = abs(total_score)

    # Check if score meets threshold
    if abs_total < entry_threshold:
        return None

    # ─── Regime/Vol Direction Filter ────────────────────────────────────────
    # Downtrend + normal vol → only SHORT, ignore LONG signals
    # Uptrend + normal vol → only LONG, ignore SHORT signals
    # In high/low volatility, the short-term EMA20 trend is unreliable,
    # so don't filter — let the EMA50 anti-trend filter handle it.
    if (trend == "downtrend" and vol_label == "normal" and direction == 1) or \
       (trend == "uptrend" and vol_label == "normal" and direction == -1):
        return None

    # Find the highest-contributing pattern for display
    entry_type = best_contrib["type"]
    trigger = best_contrib["trigger"]
    
    # Get pattern candle range for SL calculation
    candle_low = min(c["candle_low"] for c in contributions if c.get("candle_low"))
    candle_high = max(c["candle_high"] for c in contributions if c.get("candle_high"))
    
    # Build confidence message
    contrib_strs = []
    for c in contributions:
        d = "LONG" if c["score"] > 0 else "SHORT"
        contrib_strs.append(f"{c['type']}({c['score']:+.1f})")
    
    if a15 <= 0:
        a15 = 0.0001  # fallback

    # Get effective RR ratio (hot-reloadable by self-learning)
    rr = RR_RATIO  # Fixed 1:2 — not tunable by self-learning

    # Calculate SL first from the actual entry level (close price).
    # TP is then derived from SL distance × RR_RATIO to guarantee 1:2 ratio.
    entry_base = closest["close"]
    if direction == 1:
        sl_price = entry_base - SL_ATR_MULTIPLIER * a15
    else:
        sl_price = entry_base + SL_ATR_MULTIPLIER * a15

    # Safety: ensure SL is on correct side of entry (LONG: SL below, SHORT: SL above)
    # and clamp to pattern candle range so SL doesn't get placed absurdly far.
    # After clamping, TP is recomputed from the final SL distance to preserve RR ratio.
    if direction == 1:
        if sl_price >= entry_base:
            sl_price = entry_base - a15 * 0.5  # force minimum 0.5 ATR below entry
        # Don't let SL go too far below the pattern candle low,
        # but NEVER let it rise above entry_base
        if candle_low > 0:
            sl_price = max(sl_price, min(candle_low * 0.99, entry_base * 0.95))
        else:
            sl_price = min(sl_price, entry_base * 0.95)
    elif direction == -1:
        if sl_price <= entry_base:
            sl_price = entry_base + a15 * 0.5
        if candle_high > 0:
            sl_price = min(sl_price, max(candle_high * 1.01, entry_base * 1.05))
        else:
            sl_price = max(sl_price, entry_base * 1.05)

    # Derive TP from final SL distance × RR_RATIO to guarantee 1:2 ratio
    sl_distance = abs(entry_base - sl_price)
    if direction == 1:
        tp_price = entry_base + sl_distance * rr
    else:
        tp_price = entry_base - sl_distance * rr
    
    # Longer-term trend filter (EMA50 slope over ~2h)
    # BLOCKS trades against the dominant trend entirely.
    # The old behaviour halved the score AFTER threshold check,
    # rendering the filter completely ineffective — trades passed
    # through with only a position-sizing penalty.
    if len(closes) >= 58:  # 50 EMA + 8 slope lookback
        ema50_vals = ema(closes, 50)
        if len(ema50_vals) >= 8 and all(v is not None for v in ema50_vals[-8:]):
            lt_price = closes[-1] if closes else 1
            lt_slope = (ema50_vals[-1] - ema50_vals[-8]) / max(lt_price, 0.0001)
            lt_trend = "bullish" if lt_slope > TREND_STRENGTH_MIN else \
                       ("bearish" if lt_slope < -TREND_STRENGTH_MIN else "neutral")
            if (direction == 1 and lt_trend == "bearish") or \
               (direction == -1 and lt_trend == "bullish"):
                # Anti-trend trade: BLOCK entirely
                return None
    result = {
        "score": round(total_score, 1),
        "direction": direction,
        "entry_type": entry_type,
        "trigger": round(trigger, 8),
        "tp": round(tp_price, 8),
        "sl": round(sl_price, 8),
        "a15": a15,
        "trend": trend,
        "vol_label": vol_label,
        "rsi": rsi_val,
        "num_signals": len(contributions),
        "contributions": contrib_strs,
        "confidence_msg": f"score={total_score:+.1f} threshold={entry_threshold:.1f} [{', '.join(contrib_strs)}]",
    }
    return result


# ─── Signal Evaluation ────────────────────────────────────────────────────

def evaluate_signal_for_symbol(sym):
    """Run full strategy evaluation for one symbol.
    
    Flow:
    1. Compute indicators (ATR, RSI, MACD, EMA, trend, volatility)
    2. Score all entry types → get best setup with confidence
    3. If confidence ≥ threshold → set signal with TP/SL
    4. Clear stale signals
    """
    if daily_atr.get(sym, 0) <= 0:
        # Fallback: use 15m ATR if daily ATR hasn't been computed yet
        closed = get_closed_15m(sym, lookback=30)
        if len(closed) >= 15:
            closes = [c["close"] for c in closed]
            highs = [c["high"] for c in closed]
            lows = [c["low"] for c in closed]
            fallback_atr = atr(highs, lows, closes, 14)
            if fallback_atr > 0:
                # Scale 15m ATR to approximate daily ATR
                daily_atr[sym] = fallback_atr * (96 ** 0.5)
                with state_lock:
                    indicators[sym]["daily_atr"] = round(daily_atr[sym], 8)
        if daily_atr.get(sym, 0) <= 0:
            return
    
    closed = get_closed_15m(sym, lookback=30)
    if len(closed) < 15:
        return
    
    # Compute indicators
    ind = compute_indicators(sym, closed)
    a15 = ind["a15"]
    
    # Store indicators for display
    with state_lock:
        indicators[sym]["15m_atr14"] = round(a15, 8)
        indicators[sym]["trend"] = ind["trend"]
        indicators[sym]["vol_label"] = ind["vol_label"]
        indicators[sym]["rsi"] = ind["rsi_val"]
    
    # Score entry signals
    entry_signal = score_entry(sym, closed, ind)
    
    # Prepare signal state update (log outside lock)
    log_msgs = []

    with state_lock:
        if entry_signal:
            direction = entry_signal["direction"]
            signal_label = "LONG" if direction == 1 else "SHORT"
            tp = entry_signal["tp"]
            sl = entry_signal["sl"]
            score = entry_signal["score"]
            entry_type = entry_signal["entry_type"]
            
            # Check for duplicate signal
            old = signal_state[sym]
            already_set = (old.get("signal") == signal_label
                           and abs(old.get("tp", 0) - tp) < 1e-8
                           and abs(old.get("sl", 0) - sl) < 1e-8)
            
            if not already_set:
                signal_state[sym]["signal"] = signal_label
                signal_state[sym]["direction"] = direction
                signal_state[sym]["tp"] = round(tp, 8)
                signal_state[sym]["sl"] = round(sl, 8)
                signal_state[sym]["pattern_type"] = entry_type
                signal_state[sym]["score"] = score
                signal_state[sym]["num_signals"] = entry_signal["num_signals"]
                signal_state[sym]["trend"] = entry_signal["trend"]
                signal_state[sym]["vol_label"] = entry_signal["vol_label"]
                
                log_msgs.append({
                    "msg": (
                        f"[{sym}] ENTRY: {entry_type} → {signal_label} "
                        f"(score={score:+.1f} | "
                        f"{entry_signal['num_signals']} signals | "
                        f"trend={entry_signal['trend']} "
                        f"vol={entry_signal['vol_label']}) | "
                        f"TP=${tp:.{_dp(tp)}f} SL=${sl:.{_dp(sl)}f} | "
                        f"{entry_signal['confidence_msg']}"),
                    "sym": sym,
                })
        else:
            # No valid entry — clear signal
            if signal_state[sym].get("signal") is not None:
                signal_state[sym]["signal"] = None
                signal_state[sym]["direction"] = 0
                signal_state[sym]["tp"] = 0.0
                signal_state[sym]["sl"] = 0.0
                signal_state[sym]["pattern_type"] = ""
                signal_state[sym]["score"] = 0.0
                signal_state[sym]["num_signals"] = 0
                # Reset entry guard so new signals can enter
                signal_state[sym]["last_entry_signal"] = None
    
    # Log outside state_lock
    for log_entry in log_msgs:
        log_event(log_entry["msg"], "SIGNAL")
        # Capture full conditions snapshot on every signal
        try:
            from app.conditions import capture_conditions
            capture_conditions("SIGNAL", log_entry["sym"], log_entry["msg"])
        except Exception:
            pass


# ─── Strategy Loop ────────────────────────────────────────────────────────

def strategy_loop():
    """Main strategy loop — runs every 5 seconds."""
    cycle_count = 0
    while True:
        try:
            syms = list(SYMBOLS)
            for sym in syms:
                evaluate_signal_for_symbol(sym)
            cycle_count += 1
        except Exception as e:
            log_event(f"Strategy error: {e}", "ERROR")
        # Dynamic sleep: a bit longer if no data yet
        time.sleep(1)


# ─── Symbol Addition Helper ───────────────────────────────────────────────

def _bootstrap_and_restart_ws(sym):
    """Bootstrap data for new symbol, then restart WS."""
    from app.market_data import bootstrap_historical_candles, _trigger_ws_restart
    bootstrap_historical_candles(sym)
    _trigger_ws_restart()

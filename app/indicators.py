"""Pure Python technical indicators — zero external dependencies.
Enhanced with engulfing pattern detection and trend analysis."""


from app.config import ENGULFING_MIN_BODY_RATIO


def sma(values, period):
    """Simple Moving Average."""
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1: i + 1]) / period)
    return result


def ema(values, period):
    """Exponential Moving Average."""
    if not values:
        return []
    multiplier = 2.0 / (period + 1)
    result = [None] * len(values)
    first_valid = period - 1
    if first_valid >= len(values):
        return result
    seed = sum(values[:period]) / period
    result[first_valid] = seed
    for i in range(first_valid + 1, len(values)):
        result[i] = (values[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def rsi(closes, period=14):
    """Relative Strength Index — returns the *latest* RSI value."""
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = closes[-(period + 1) + i] - closes[-(period + 1) + i - 1]
        if delta > 0:
            gains += delta
        else:
            losses += abs(delta)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi_val = 100.0 - (100.0 / (1.0 + rs))
    for i in range(2, len(closes) - period + 1):
        idx = -(period + 1) + i
        delta = closes[idx] - closes[idx - 1]
        gain = max(delta, 0)
        loss = abs(min(delta, 0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi_val = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi_val, 2)


def atr(highs, lows, closes, period=14):
    """Average True Range — returns with high precision."""
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    atr_val = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
    return round(atr_val, 8)


def macd(closes, fast=12, slow=26, signal=9):
    """MACD — returns (macd_line, signal_line, histogram)."""
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_vals = []
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_vals.append(ema_fast[i] - ema_slow[i])
        else:
            macd_vals.append(None)
    valid = [v for v in macd_vals if v is not None]
    if len(valid) < signal:
        return 0.0, 0.0, 0.0
    sig_ema = ema(valid, signal)
    return (round(valid[-1], 8), round(sig_ema[-1], 8),
            round(valid[-1] - sig_ema[-1], 8))


# ─── Engulfing Pattern Detection ───────────────────────────────────────────

def detect_engulfing(candles):
    """Detect bullish or bearish engulfing pattern on the last 2 candles.
    
    Bullish engulfing: red candle followed by larger green candle that
    completely engulfs the previous body.
    Bearish engulfing: green candle followed by larger red candle that
    completely engulfs the previous body.
    
    Returns (is_valid, direction) where direction=1 (long) or -1 (short).
    """
    if len(candles) < 2:
        return False, 0
    prev = candles[-2]
    curr = candles[-1]
    prev_body = prev["close"] - prev["open"]
    curr_body = curr["close"] - curr["open"]
    if prev_body == 0 or curr_body == 0:
        return False, 0
    
    prev_is_red = prev_body < 0
    curr_is_green = curr_body > 0
    # Bullish engulfing: red → green, curr body completely covers prev body
    if prev_is_red and curr_is_green:
        prev_body_size = abs(prev_body)
        curr_body_size = abs(curr_body)
        if (curr_body_size >= prev_body_size * ENGULFING_MIN_BODY_RATIO and
                curr["open"] <= prev["open"] and
                curr["close"] >= prev["close"]):
            return True, 1  # LONG
    
    # Bearish engulfing: green → red, curr body completely covers prev body
    prev_is_green = prev_body > 0
    curr_is_red = curr_body < 0
    if prev_is_green and curr_is_red:
        prev_body_size = abs(prev_body)
        curr_body_size = abs(curr_body)
        if (curr_body_size >= prev_body_size * ENGULFING_MIN_BODY_RATIO and
                curr["open"] >= prev["open"] and
                curr["close"] <= prev["close"]):
            return True, -1  # SHORT
    
    return False, 0


# ─── Trend Detection ───────────────────────────────────────────────────────

def compute_ema_slope(closes, ema_period=20, lookback=5):
    """Compute EMA slope to determine trend strength.
    
    Returns slope as price change per period (positive = uptrend).
    Uses EMA regression: (ema[-1] - ema[-lookback]) / lookback
    """
    if len(closes) < ema_period + lookback:
        return 0.0
    ema_vals = ema(closes, ema_period)
    valid = [v for v in ema_vals if v is not None]
    if len(valid) < lookback + 1:
        return 0.0
    slope = (valid[-1] - valid[-(lookback + 1)]) / lookback
    return round(slope, 8)


def classify_trend(closes, ema_period=20, min_slope=0.0003):
    """Classify market regime as uptrend, downtrend, or ranging.
    
    Returns "uptrend", "downtrend", or "ranging".
    """
    slope = compute_ema_slope(closes, ema_period)
    price = closes[-1] if closes else 1
    slope_pct = abs(slope) / max(price, 0.0001)
    if slope_pct < min_slope:
        return "ranging"
    return "uptrend" if slope > 0 else "downtrend"


def classify_volatility(highs, lows, closes, period=14, lookback=50):
    """Classify volatility as low, normal, or high.
    
    Compares current ATR to its percentile over the lookback period.
    Returns (vol_label, current_atr, percentile).
    """
    if len(closes) < period + 1 or len(closes) < lookback:
        return "normal", 0.0, 50.0
    
    # Compute ATR for each window in the lookback
    atr_values = []
    for i in range(lookback, len(closes)):
        h_slice = highs[i - period + 1:i + 1]
        l_slice = lows[i - period + 1:i + 1]
        c_slice = closes[i - period + 1:i + 1]
        if len(h_slice) >= period + 1:
            atr_values.append(atr(h_slice, l_slice, c_slice, period))
    
    if not atr_values:
        return "normal", 0.0, 50.0
    
    current_atr = atr_values[-1]
    if current_atr <= 0:
        return "normal", 0.0, 50.0
    
    # Count how many past ATR values are below current
    below = sum(1 for a in atr_values[:-1] if a < current_atr)
    pct = (below / max(len(atr_values) - 1, 1)) * 100
    
    if pct >= 80:
        return "high", current_atr, round(pct, 1)
    elif pct <= 20:
        return "low", current_atr, round(pct, 1)
    return "normal", current_atr, round(pct, 1)

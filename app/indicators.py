"""Pure Python technical indicators — zero external dependencies."""


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
        return 0.0
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
    """Average True Range."""
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
    return round(atr_val, 4)


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
    return (round(valid[-1], 4), round(sig_ema[-1], 4),
            round(valid[-1] - sig_ema[-1], 4))

"""Configuration constants — ATLANTIDE Crypto Lab v2 Hybrid Strategy."""

# ─── Capital & Risk ────────────────────────────────────────────────────────
INITIAL_CAPITAL_PER_SYMBOL = 500.0
RISK_PCT = 0.10               # Base 10% risk per trade (self-improving: risk_pct param)
LEVERAGE = 5
MAX_OPEN_TRADES_PER_SYMBOL = 1
TRADING_FEE = 0.0005          # 0.05% per side (0.10% round-trip)

# ─── Candles & Logging ─────────────────────────────────────────────────────
CANDLE_LIMIT = 100            # Enough for ATR(14) + EMA(50)
MAX_EVENT_LOG = 200
MAX_CLOSED_TRADES = 500

# ─── Entry Type Weights (Scoring System) ───────────────────────────────────
# Max score range: -8.0 to +8.0. Entry when abs(score) >= ENTRY_THRESHOLD
ENTRY_THRESHOLD = 3.5         # Adaptive via self-learning threshold param (was 4.0 — too high, blocked all signals in low-volatility range)

HAMMER_WEIGHT = 2.5           # Bullish hammer pattern (high confidence reversal)
SHOOTING_STAR_WEIGHT = -2.5   # Bearish shooting star (high confidence reversal)
ENGULFING_WEIGHT = 2.0        # Bullish engulfing; bearish = -2.0
EMA_CROSS_WEIGHT = 2.0        # EMA9/21 crossover (fresh cross = full weight)
EMA_POSITION_WEIGHT = 1.0     # EMA9 > EMA21 = +1.0
RSI_WEIGHT = 1.0              # Oversold/overbought with zone weighting (was 1.5 — too easy for pure-indicator entries to cross threshold)
MACD_WEIGHT = 0.5             # MACD histogram sign (was 1.0 — pure-indicator entries mixed with RSI crossed threshold too easily)
VOLUME_WEIGHT = 0.3           # Volume vs SMA20, takes sign of running score (was 0.5 — confirmatory at best, shouldn't push marginal entries over threshold)

# ─── Pattern Detection ─────────────────────────────────────────────────────
HAMMER_MIN_WICK_RATIO = 1.5   # Wick must be >= 1.5x body (tuned as wick_ratio)
ENGULFING_MIN_BODY_RATIO = 1.5 # Engulfing body must be >= 1.5x previous body (was 1.3 — too permissive, all engulfing LONG trades lost)
RSI_OVERSOLD = 25             # Base oversold threshold (was 30 — too loose, weak signals entered)
RSI_OVERBOUGHT = 75           # Base overbought threshold (was 70 — too loose, weak signals entered)

# ─── TP / SL ───────────────────────────────────────────────────────────────
RR_RATIO = 1.5                # 1:1.5 risk-to-reward (was 2.0 — TP too far, only 5% hit rate)
SL_ATR_MULTIPLIER = 2.0       # Stop-loss = ATR × this multiplier (was 1.5 — too tight, 67% of trades hit SL)
TP_ATR_MULTIPLIER = 2.0       # Take-profit = ATR × this multiplier

# ─── Trade Cooldown ────────────────────────────────────────────────────────
TRADE_COOLDOWN_MINUTES = 15   # Min minutes between trades on same symbol (prevents rapid re-entry)

# ─── Consecutive Loss Pause ────────────────────────────────────────────────
CONSECUTIVE_SL_LIMIT = 5      # N consecutive SLs triggers pause
SL_PAUSE_SECONDS = 3600       # Pause duration in seconds (1h)

# ─── Trailing Stop ─────────────────────────────────────────────────────────
TRAILING_ACTIVATE_PCT = 0.35  # Activate trail when 35% of TP distance covered (was 50% — too late, missed profit locks)
TRAILING_DISTANCE = 0.35      # Trail is 35% of TP distance behind price extreme (was 50% — too loose, gave back profits)

# ─── Regime Detection ──────────────────────────────────────────────────────
TREND_EMA_PERIOD = 20         # EMA for trend direction
TREND_STRENGTH_MIN = 0.0003   # Min slope to classify as trending (vs ranging)
VOLATILITY_HIGH_PCT = 80      # ATR percentile for high volatility classification
VOLATILITY_LOW_PCT = 20       # ATR percentile for low volatility classification

# ─── Self-Learning Goals ───────────────────────────────────────────────────
TARGET_SHARPE = 1.5
TARGET_WINRATE = 40.0
TARGET_MONTHLY_RETURN = 10.0
MAX_DRAWDOWN_LIMIT = 20.0
SELF_LEARN_MIN_TRADES = 10
SELF_LEARN_REVIEW_INTERVAL = 10
PARAM_TUNE_AMOUNT = 0.05       # 5% change per cycle (was 10% — too aggressive, overshoots optimal)

# ─── Entry Types (for per-type tracking) ───────────────────────────────────
ENTRY_TYPES = [
    "HAMMER", "SHOOTING_STAR", "ENGULFING",
    "EMA_CROSS", "RSI_BOUNCE", "MOMENTUM",
]

# ─── Top 100 Symbols ───────────────────────────────────────────────────────
TOP100_USDT_SYMBOLS = [
    ("BTC", "BTC/USDT"), ("ETH", "ETH/USDT"), ("BNB", "BNB/USDT"),
    ("SOL", "SOL/USDT"), ("XRP", "XRP/USDT"), ("DOGE", "DOGE/USDT"),
    ("ADA", "ADA/USDT"), ("AVAX", "AVAX/USDT"), ("DOT", "DOT/USDT"),
    ("LINK", "LINK/USDT"), ("POL", "POL/USDT"), ("SHIB", "SHIB/USDT"),
    ("LTC", "LTC/USDT"), ("UNI", "UNI/USDT"), ("ATOM", "ATOM/USDT"),
    ("ETC", "ETC/USDT"), ("XLM", "XLM/USDT"), ("FIL", "FIL/USDT"),
    ("TRX", "TRX/USDT"), ("NEAR", "NEAR/USDT"), ("APT", "APT/USDT"),
    ("ARB", "ARB/USDT"), ("OP", "OP/USDT"), ("SUI", "SUI/USDT"),
    ("INJ", "INJ/USDT"), ("TIA", "TIA/USDT"), ("SEI", "SEI/USDT"),
    ("FTM", "FTM/USDT"), ("RUNE", "RUNE/USDT"), ("AAVE", "AAVE/USDT"),
    ("ALGO", "ALGO/USDT"), ("VET", "VET/USDT"), ("ICP", "ICP/USDT"),
    ("GRT", "GRT/USDT"), ("THETA", "THETA/USDT"), ("SAND", "SAND/USDT"),
    ("MANA", "MANA/USDT"), ("AXS", "AXS/USDT"), ("EGLD", "EGLD/USDT"),
    ("KLAY", "KLAY/USDT"), ("EOS", "EOS/USDT"), ("FLOW", "FLOW/USDT"),
    ("XTZ", "XTZ/USDT"), ("CRV", "CRV/USDT"), ("DYDX", "DYDX/USDT"),
    ("RNDR", "RNDR/USDT"), ("FET", "FET/USDT"), ("AGIX", "AGIX/USDT"),
    ("WLD", "WLD/USDT"), ("PEPE", "PEPE/USDT"), ("WIF", "WIF/USDT"),
    ("BONK", "BONK/USDT"), ("JUP", "JUP/USDT"), ("PYTH", "PYTH/USDT"),
    ("JTO", "JTO/USDT"), ("STRK", "STRK/USDT"), ("ENA", "ENA/USDT"),
    ("TAO", "TAO/USDT"), ("STX", "STX/USDT"), ("IMX", "IMX/USDT"),
    ("LDO", "LDO/USDT"), ("RAY", "RAY/USDT"), ("HNT", "HNT/USDT"),
    ("KAS", "KAS/USDT"), ("ONDO", "ONDO/USDT"), ("MKR", "MKR/USDT"),
    ("QNT", "QNT/USDT"), ("SNX", "SNX/USDT"), ("GALA", "GALA/USDT"),
    ("ORDI", "ORDI/USDT"), ("1000SATS", "1000SATS/USDT"),
    ("ZRO", "ZRO/USDT"), ("IO", "IO/USDT"), ("NOT", "NOT/USDT"),
    ("PEOPLE", "PEOPLE/USDT"), ("ENS", "ENS/USDT"), ("GMT", "GMT/USDT"),
    ("BLUR", "BLUR/USDT"), ("AEVO", "AEVO/USDT"), ("PORTAL", "PORTAL/USDT"),
    ("PENDLE", "PENDLE/USDT"), ("EIGEN", "EIGEN/USDT"),
    ("ZK", "ZK/USDT"), ("ZETA", "ZETA/USDT"), ("W", "W/USDT"),
    ("BOME", "BOME/USDT"), ("SLERF", "SLERF/USDT"),
    ("TURBO", "TURBO/USDT"), ("NEIRO", "NEIRO/USDT"),
    ("POPCAT", "POPCAT/USDT"), ("GOAT", "GOAT/USDT"),
    ("MEW", "MEW/USDT"), ("DOGS", "DOGS/USDT"),
    ("BRETT", "BRETT/USDT"), ("MOG", "MOG/USDT"),
    ("PNUT", "PNUT/USDT"), ("ACT", "ACT/USDT"),
    ("MOODENG", "MOODENG/USDT"), ("PENGU", "PENGU/USDT"),
]

SYMBOL_DISPLAY = {code + "USDT": display for code, display in TOP100_USDT_SYMBOLS}

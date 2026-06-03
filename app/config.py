"""Configuration constants — ATLANTIDE Crypto Lab v2 Hybrid Strategy."""

# ─── Capital & Risk ────────────────────────────────────────────────────────
INITIAL_CAPITAL_PER_SYMBOL = 500.0
RISK_PCT = 0.10               # Base 10% risk per trade (self-improving: risk_pct param)
LEVERAGE = 5
MAX_OPEN_TRADES_PER_SYMBOL = 2
TRADING_FEE = 0.0005          # 0.05% per side (0.10% round-trip)

# ─── Candles & Logging ─────────────────────────────────────────────────────
CANDLE_LIMIT = 100            # Enough for ATR(14) + EMA(50)
MAX_EVENT_LOG = 200
MAX_CLOSED_TRADES = 500

# ─── Entry Type Weights (Scoring System) ───────────────────────────────────
# Max score range: -8.0 to +8.0. Entry when abs(score) >= ENTRY_THRESHOLD
ENTRY_THRESHOLD = 3.5         # Adaptive via self-learning threshold param

HAMMER_WEIGHT = 2.5           # Bullish hammer pattern (high confidence reversal)
SHOOTING_STAR_WEIGHT = -2.5   # Bearish shooting star (high confidence reversal)
ENGULFING_WEIGHT = 2.0        # Bullish engulfing; bearish = -2.0
EMA_CROSS_WEIGHT = 2.0        # EMA9/21 crossover (fresh cross = full weight)
EMA_POSITION_WEIGHT = 1.0     # EMA9 > EMA21 = +1.0
RSI_WEIGHT = 1.5              # Oversold/overbought with zone weighting
MACD_WEIGHT = 1.0             # MACD histogram sign
VOLUME_WEIGHT = 0.5           # Volume vs SMA20, takes sign of running score

# ─── Pattern Detection ─────────────────────────────────────────────────────
HAMMER_MIN_WICK_RATIO = 1.5   # Wick must be >= 1.5x body (tuned as wick_ratio)
ENGULFING_MIN_BODY_RATIO = 1.3 # Engulfing body must be >= 1.3x previous body
RSI_OVERSOLD = 30             # Base oversold threshold (adjusted by volatility)
RSI_OVERBOUGHT = 70           # Base overbought threshold

# ─── TP / SL ───────────────────────────────────────────────────────────────
RR_RATIO = 2.0                # 1:2 risk-to-reward (self-improving: rr_ratio param)
SL_ATR_MULTIPLIER = 1.0       # Stop-loss = ATR × this multiplier
TP_ATR_MULTIPLIER = 2.0       # Take-profit = ATR × this multiplier

# ─── Trade Cooldown ────────────────────────────────────────────────────────
TRADE_COOLDOWN_MINUTES = 15   # Min minutes between trades on same symbol (prevents rapid re-entry)

# ─── Trailing Stop ─────────────────────────────────────────────────────────
TRAILING_ACTIVATE_PCT = 0.50  # Activate trail when 50% of TP distance covered
TRAILING_DISTANCE = 0.30      # Trail is 30% of TP distance behind price extreme

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
SELF_LEARN_REVIEW_INTERVAL = 20
PARAM_TUNE_AMOUNT = 0.10

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

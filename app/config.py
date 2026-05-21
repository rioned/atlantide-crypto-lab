"""Configuration constants and static data for ATLANTIDE Crypto Lab."""

INITIAL_CAPITAL_PER_SYMBOL = 500.0
RISK_PCT = 0.10          # 10% of symbol capital at risk per trade
LEVERAGE = 5
MAX_OPEN_TRADES_PER_SYMBOL = 5
MANIPULATION_THRESHOLD = 0.05    # 5% of Daily ATR (was 20% — too strict for 15m crypto candles)
JOHN_WICK_WICK_RATIO = 0.60      # >= 60% wick ratio
POWER_TOWER_RETRACE = 0.30       # 30% retrace for engulfing
RR_RATIO = 2.0                   # 1:2 risk-to-reward (risk 1 → make 2)
TRADING_FEE = 0.0005             # 0.05% per trade (each side: entry + exit = 0.10% round-trip)
CANDLE_LIMIT = 200
MAX_EVENT_LOG = 200
MAX_CLOSED_TRADES = 500

# ─── Market Sessions (UTC open/close times) ──────────────────────────────────
MARKET_SESSIONS = [
    {
        "id": "asian", "name": "Asian (Tokyo)",
        "open_utc_hour": 0, "open_utc_minute": 0,
        "close_utc_hour": 9, "close_utc_minute": 0,
        "emoji": "🇯🇵",
    },
    {
        "id": "european", "name": "European (London)",
        "open_utc_hour": 8, "open_utc_minute": 0,
        "close_utc_hour": 17, "close_utc_minute": 0,
        "emoji": "🇬🇧",
    },
    {
        "id": "us", "name": "US (New York)",
        "open_utc_hour": 13, "open_utc_minute": 30,
        "close_utc_hour": 21, "close_utc_minute": 0,
        "emoji": "🇺🇸",
    },
    {
        "id": "australian", "name": "Australian (Sydney)",
        "open_utc_hour": 0, "open_utc_minute": 0,
        "close_utc_hour": 6, "close_utc_minute": 0,
        "emoji": "🇦🇺",
    },
]

# ─── Top 100 Available Symbols ────────────────────────────────────────────────
TOP100_USDT_SYMBOLS = [
    ("BTC", "BTC/USDT"), ("ETH", "ETH/USDT"), ("BNB", "BNB/USDT"),
    ("SOL", "SOL/USDT"), ("XRP", "XRP/USDT"), ("DOGE", "DOGE/USDT"),
    ("ADA", "ADA/USDT"), ("AVAX", "AVAX/USDT"), ("DOT", "DOT/USDT"),
    ("LINK", "LINK/USDT"), ("MATIC", "MATIC/USDT"), ("SHIB", "SHIB/USDT"),
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

# Display mapping for all known symbols
SYMBOL_DISPLAY = {code + "USDT": display for code, display in TOP100_USDT_SYMBOLS}

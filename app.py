"""ATLANTIDE CRYPTO LAB — Multi-Symbol Pattern Scalp Simulator

Thin entry point. All logic lives in the `app/` package:
  app/config.py      — Constants, market sessions, symbol list
  app/state.py       — Global state, locks, init/teardown
  app/database.py    — SQLite persistence (save/load/reset)
  app/indicators.py  — Pure Python indicators (EMA, RSI, ATR, MACD)
  app/market_data.py — Binance REST bootstrap + WebSocket streams
  app/strategy.py    — Pattern Scalp strategy (manipulation → reversal)
  app/execution.py   — Trade execution, PnL tracking
  app/sessions.py    — Market session countdowns
  app/routes.py      — Flask API + SSE streaming
  app/main.py        — Startup sequence

Launch:  python app.py   or   systemctl start atlantide-crypto-lab
"""

from app.main import startup
from app.routes import app

if __name__ == "__main__":
    startup()
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

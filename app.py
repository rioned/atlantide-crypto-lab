"""CRYPTO LAB 2 — Multi-Symbol Pattern Scalp Simulator (15m, 1:2 RR)

Thin entry point. All logic lives in the `app/` package:
  app/config.py      — Constants, symbol list
  app/state.py       — Global state, locks, init/teardown
  app/database.py    — SQLite persistence (save/load/reset)
  app/indicators.py  — Pure Python indicators (EMA, RSI, ATR, MACD)
  app/market_data.py — Binance REST bootstrap + WebSocket streams
  app/strategy.py    — Pattern Scalp strategy (manipulation → reversal on 15m)
  app/execution.py   — Trade execution, PnL tracking
  app/routes.py      — Flask API + SSE streaming
  app/main.py        — Startup sequence

Launch:  python app.py   or   python app/main.py
"""

"""CRYPTO LAB 2 — direct launch (dev) or use gunicorn for production."""
from app.routes import app

if __name__ == "__main__":
    from app.main import startup
    startup()
    app.run(host="0.0.0.0", port=9090, debug=False, use_reloader=False)

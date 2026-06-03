# CRYPTO LAB 2

⚡ Self-learning pattern scalp paper trading simulator — 15m timeframe, 1:2 RR, John Wick / Power Tower entries.

**Stack:** Flask + Vanilla JS + Binance WebSocket (public market data only — no API keys)

## Features

- **4 simultaneous symbols** — independent $500 capital pools, 10% risk per trade
- **Pattern Scalp strategy** — 15m manipulation detection + 15m reversal patterns (John Wick, Power Tower)
- **5× leverage paper trading** — position sized dynamically for 10% risk
- **1:2 risk-reward** — adaptive RR ratio tuned by self-learning engine
- **Self-Improving AI Agent** — scores every trade, reviews every 20 trades, changes ONE parameter at a time (scientific method)
- **Dark terminal GUI** — self-learning sidebar with goal progress bars (Sharpe, win rate, return, drawdown)
- **SSE real-time streaming** — instant price updates via Server-Sent Events
- **Persistent SQLite state** — balance, trades, logs survive restart
- **100+ available USDT pairs** — add/remove symbols dynamically via web UI

## Quick Start

```bash
git clone https://github.com/rionedanny/crypto-lab2.git
cd crypto-lab2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open **http://localhost:8080**

## Architecture

```
app/
├── config.py       Constants, sessions, 100+ symbols
├── state.py        Global state + SSE broadcast
├── database.py     SQLite persistence
├── indicators.py   Pure Python indicators (SMA, EMA, RSI, ATR, MACD)
├── market_data.py  Binance REST bootstrap + WebSocket streams
├── strategy.py     Pattern Scalp: manipulation → reversal → entry
├── execution.py    Trade lifecycle, PnL, position sizing
├── sessions.py     Market session computation
├── routes.py       Flask API + SSE /api/stream
└── main.py         Startup orchestrator
```

## Pattern Scalp Strategy

| Phase | Timeframe | Detection |
|-------|-----------|-----------|
| **1. Manipulation** | 15m | Candle range ≥ 5% of Daily ATR → liquidity sweep |
| **2. Reversal** | 5m | John Wick (≥60% wick) or Power Tower (engulfing) |
| **3. Entry** | Next 5m bar | Break of reversal candle's extreme |
| **4. SL** | — | Manipulation candle's extreme |
| **5. TP** | — | 50% of manipulation range (1:2 RR) |

Risk: 10% of per-symbol capital per trade. 5× leverage. Max 5 concurrent per symbol.

## systemd (auto-start on boot)

```bash
sudo tee /etc/systemd/system/atlantide-crypto-lab.service << 'EOF'
[Unit]
Description=ATLANTIDE CRYPTO LAB - Paper Trading
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=openclaw
WorkingDirectory=/home/openclaw/workspace/atlantide-crypto-lab
ExecStart=/home/openclaw/workspace/atlantide-crypto-lab/venv/bin/python app.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload && sudo systemctl enable --now atlantide-crypto-lab
```

# ATLANTIDE CRYPTO LAB

⚡ Dark cyberpunk leveraged paper trading simulator with dynamic symbol support.

**Stack:** Flask + Vanilla JS Canvas + Binance WebSocket (public, no API keys)

## Features

- **100+ USDT pairs** — switch symbols dynamically via web GUI dropdown
- **3-agent architecture** — WebSocket streamer, pure-Python strategy engine, simulated execution
- **MTF confluence signals** — 1H EMA200 bias + 15M MACD trend + 5M EMA9/21 cross with RSI + volume spike
- **5x leverage paper trading** — $50 margin per trade, $250 notional
- **Auto TP/SL** — based on 2×/1× ATR(14)
- **Persistent SQLite state** — balance, trade history, event log survive restarts
- **Canvas candlestick chart** — 60 candles with EMA9/EMA21 overlays, TP/SL dashed lines
- **RSI gauge, MACD panel, ATR, Volume indicators**
- **Real-time WebSocket** — direct Binance stream, no polling, no API keys

## Quick Start

```bash
cd atlantide-crypto-lab
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open **http://localhost:8080**

## systemd — Auto-start on boot

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

sudo systemctl daemon-reload
sudo systemctl enable atlantide-crypto-lab
sudo systemctl start atlantide-crypto-lab

# Check status
sudo systemctl status atlantide-crypto-lab

# View logs
sudo journalctl -u atlantide-crypto-lab -f
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard |
| GET | `/api/state` | Full state JSON (ticker, account, signals, trades, candles, indicators, logs) |
| GET | `/api/symbols` | Available USDT pairs (top 100+) |
| POST | `/api/symbol/set` | Switch symbol `{"symbol": "btc"}` |
| POST | `/api/reset` | Reset account to $500 |

## Signal Logic

| Timeframe | Indicator | Condition |
|-----------|-----------|-----------|
| 1H | EMA200 | Price > EMA200 = BULLISH |
| 15M | MACD(12,26,9) | Line > Signal = BULLISH |
| 5M | EMA9/21 cross + RSI(14) + Vol | Cross up + RSI>50 + Vol>1.5×SMA = BULLISH |

All three must align for LONG/SHORT signal. Otherwise WAITING.

## Bot Health

Used by the Atlantide AI Lab. No API keys required — public Binance WebSocket only.

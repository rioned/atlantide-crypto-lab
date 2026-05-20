"""Flask routes and SSE streaming for ATLANTIDE Crypto Lab."""

import json
import queue
import threading
import time
from datetime import datetime

from flask import (Flask, render_template, jsonify, request,
                   Response, stream_with_context)
from flask_cors import CORS

from app.config import (SYMBOL_DISPLAY, TOP100_USDT_SYMBOLS,
                         INITIAL_CAPITAL_PER_SYMBOL)
from app.state import (SYMBOLS, capital, capital_lock, state_lock,
                        candles, ticker, signal_state, indicators,
                        manipulation_candle, manipulation_active,
                        reversal_pattern, open_trades, closed_trades,
                        event_log, sse_clients, sse_clients_lock)
from app.sessions import compute_session_data
from app.strategy import (_bootstrap_and_restart_ws, log_event)
from app.market_data import _trigger_ws_restart
from app.database import get_db, save_state
from app.state import init_symbol_state, remove_symbol_state, ensure_capital

# ─── Flask App Factory ────────────────────────────────────────────────────────

import os
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "templates")
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "static")
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
CORS(app)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _aggregated_account():
    """Compute combined stats across all symbols."""
    total_bal = 0.0
    total_initial = 0.0
    total_peak = 0.0
    total_trades = 0
    total_wins = 0
    total_pnl = 0.0
    max_dd = 0.0
    with capital_lock:
        caps = dict(capital)
    for cap in caps.values():
        total_bal += cap["balance"]
        total_initial += cap["initial"]
        total_peak += cap["peak"]
        total_trades += cap["total_trades"]
        total_wins += cap["winning_trades"]
        total_pnl += cap["total_pnl"]
        max_dd = max(max_dd, cap["max_dd"])
    winrate = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
    return {
        "balance": round(total_bal, 2),
        "initial": round(total_initial, 2),
        "peak": round(total_peak, 2),
        "max_drawdown": round(max_dd, 2),
        "total_trades": total_trades,
        "winning_trades": total_wins,
        "winrate": round(winrate, 1),
        "total_pnl": round(total_pnl, 2),
    }


def _trade_for_js(t, is_open=True):
    """Convert trade dict to JS-friendly format with dual-key tolerance."""
    d = t["direction"]
    side = "LONG" if d == 1 else "SHORT"
    result = {
        "id": t["id"], "side": side, "direction": d,
        "entry_price": t.get("entry", t.get("entry_price", 0)),
        "tp": t.get("tp", 0), "sl": t.get("sl", 0),
        "notional": t.get("notional", 250.0),
        "margin": t.get("margin", 50.0),
    }
    if is_open:
        result["entry_time"] = t.get("entry_time", "")
        result["unrealized_pnl"] = t.get("unrealized_pnl", 0.0)
    else:
        result["close_price"] = t.get("exit_price", 0)
        result["pnl"] = t.get("pnl", 0.0)
        result["close_time"] = t.get("exit_time", "")
        result["close_reason"] = t.get("reason", "")
        result["symbol"] = t.get("symbol", "")
    return result


# ─── SSE Stream ───────────────────────────────────────────────────────────────

@app.route("/api/stream")
def api_stream():
    """Server-Sent Events endpoint — pushes real-time price/signal updates."""
    client_queue = queue.Queue(maxsize=64)
    with sse_clients_lock:
        sse_clients.append(client_queue)

    def generate():
        # Send initial connection event
        yield "event: connected\ndata: {}\n\n"
        try:
            while True:
                try:
                    data = client_queue.get(timeout=30)
                    event_type = data.pop("event", "update")
                    yield f"event: {event_type}\n"
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    # Keepalive ping
                    yield "event: ping\ndata: {}\n\n"
        except GeneratorExit:
            pass
        finally:
            with sse_clients_lock:
                if client_queue in sse_clients:
                    sse_clients.remove(client_queue)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        })


# ─── Page Routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.route("/api/state")
def api_state():
    with state_lock:
        logs = list(event_log[-50:])
        all_closed = list(closed_trades[-50:])

    try:
        tz_offset = float(request.args.get("tz", "0"))
    except (ValueError, TypeError):
        tz_offset = 0.0
    utc_now = datetime.utcnow()
    sessions = compute_session_data(utc_now, tz_offset)

    symbols_data = {}
    for sym in SYMBOLS:
        with state_lock:
            sig = dict(signal_state[sym])
            ind = dict(indicators[sym])
            tick = dict(ticker[sym])
            open_t = list(open_trades[sym])
            c5 = list(candles[sym]["5m"][-60:])
            c15 = list(candles[sym]["15m"][-20:])
            mp = (dict(manipulation_candle[sym])
                  if manipulation_candle[sym] else None)
            rp = (dict(reversal_pattern[sym])
                  if reversal_pattern[sym] else None)

        open_pnl_sym = sum(
            (tick["price"] - t["entry"]) / t["entry"] *
            t.get("notional", 250.0) * t["direction"]
            for t in open_t) if tick["price"] > 0 else 0.0

        c5_js = [{"o": c["open"], "h": c["high"], "l": c["low"],
                  "c": c["close"], "v": c["volume"],
                  "time": c["time"]} for c in c5]
        c15_js = [{"o": c["open"], "h": c["high"], "l": c["low"],
                   "c": c["close"], "v": c["volume"],
                   "time": c["time"]} for c in c15]

        open_t_js = [_trade_for_js(t, is_open=True) for t in open_t]

        with capital_lock:
            sym_cap = dict(capital.get(sym, {
                "balance": INITIAL_CAPITAL_PER_SYMBOL,
                "initial": INITIAL_CAPITAL_PER_SYMBOL,
                "peak": INITIAL_CAPITAL_PER_SYMBOL,
                "max_dd": 0.0,
                "total_trades": 0, "winning_trades": 0, "total_pnl": 0.0,
            }))

        symbols_data[sym] = {
            "display": SYMBOL_DISPLAY.get(sym, sym),
            "ticker": tick,
            "signal_state": sig,
            "indicators": ind,
            "manipulation": mp,
            "reversal": rp,
            "open_trades": open_t_js,
            "candles_5m": c5_js,
            "candles_15m": c15_js,
            "open_pnl": round(open_pnl_sym, 2),
            "capital": sym_cap,
        }

    closed_t_js = [_trade_for_js(t, is_open=False) for t in all_closed]
    acct = _aggregated_account()

    logs_js = [{"time": e["time"], "tag": e.get("level", "INFO"),
                "msg": e.get("message", "")} for e in logs]

    return jsonify({
        "symbols": SYMBOLS,
        "data": symbols_data,
        "account": acct,
        "closed_trades": closed_t_js,
        "event_log": logs_js,
        "market_sessions": sessions,
        "utc_time": utc_now.strftime("%H:%M:%S UTC"),
        "server_tz_offset": tz_offset,
    })


@app.route("/api/symbols")
def api_symbols():
    """List all 100 available symbols + which are active."""
    result = []
    for code, display in TOP100_USDT_SYMBOLS:
        sym = code + "USDT"
        result.append({
            "code": sym,
            "display": display,
            "active": sym in SYMBOLS,
        })
    return jsonify(result)


@app.route("/api/symbol/add", methods=["POST"])
def api_symbol_add():
    """Add a symbol to active trading."""
    data = request.get_json(silent=True) or {}
    symbol_input = (data.get("symbol") or "").strip().upper()

    if not symbol_input.endswith("USDT"):
        symbol_input += "USDT"

    if symbol_input in SYMBOLS:
        return jsonify({"status": "error",
                        "message": f"{symbol_input} already active"}), 400

    known = {code + "USDT" for code, _ in TOP100_USDT_SYMBOLS}
    if symbol_input not in known:
        return jsonify({"status": "error",
                        "message": f"Unknown symbol: {symbol_input}"}), 400

    with state_lock:
        SYMBOLS.append(symbol_input)
        init_symbol_state(symbol_input)
    ensure_capital(symbol_input)
    save_state()
    log_event(f"[SYSTEM] Added {symbol_input} to active symbols", "SYSTEM")

    threading.Thread(target=_bootstrap_and_restart_ws,
                     args=(symbol_input,), daemon=True).start()

    return jsonify({"status": "ok", "message": f"Added {symbol_input}",
                    "symbols": SYMBOLS})


@app.route("/api/symbol/remove", methods=["POST"])
def api_symbol_remove():
    """Remove a symbol from active trading."""
    data = request.get_json(silent=True) or {}
    symbol_input = (data.get("symbol") or "").strip().upper()

    if not symbol_input.endswith("USDT"):
        symbol_input += "USDT"

    if symbol_input not in SYMBOLS:
        return jsonify({"status": "error",
                        "message": f"{symbol_input} not active"}), 400

    if len(SYMBOLS) <= 1:
        return jsonify({"status": "error",
                        "message": "Cannot remove last symbol"}), 400

    with state_lock:
        SYMBOLS.remove(symbol_input)
        remove_symbol_state(symbol_input)

    save_state()
    log_event(f"[SYSTEM] Removed {symbol_input} from active symbols", "SYSTEM")
    _trigger_ws_restart()

    return jsonify({"status": "ok", "message": f"Removed {symbol_input}",
                    "symbols": SYMBOLS})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset all accounts to initial capital."""
    sym = request.args.get("symbol", "").upper()
    if sym:
        if not sym.endswith("USDT"):
            sym += "USDT"
        with capital_lock:
            if sym in capital:
                capital[sym] = {
                    "balance": INITIAL_CAPITAL_PER_SYMBOL,
                    "initial": INITIAL_CAPITAL_PER_SYMBOL,
                    "peak": INITIAL_CAPITAL_PER_SYMBOL,
                    "max_dd": 0.0,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "total_pnl": 0.0,
                }
        with state_lock:
            signal_state[sym]["last_entry_signal"] = None
            open_trades[sym].clear()
        msg = f"{sym} capital reset to ${INITIAL_CAPITAL_PER_SYMBOL:.0f}"
    else:
        with capital_lock:
            for s in capital:
                capital[s] = {
                    "balance": INITIAL_CAPITAL_PER_SYMBOL,
                    "initial": INITIAL_CAPITAL_PER_SYMBOL,
                    "peak": INITIAL_CAPITAL_PER_SYMBOL,
                    "max_dd": 0.0,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "total_pnl": 0.0,
                }
        with state_lock:
            for s in SYMBOLS:
                signal_state[s]["last_entry_signal"] = None
                open_trades[s].clear()
            closed_trades.clear()
            event_log.clear()
        msg = f"All accounts reset to ${INITIAL_CAPITAL_PER_SYMBOL:.0f} each"

    conn = get_db()
    if not sym:
        conn.execute("DELETE FROM closed_trades")
        conn.execute("DELETE FROM event_log")
    conn.execute("DELETE FROM open_trades")
    conn.execute("DELETE FROM symbol_capital")
    conn.commit()
    conn.close()
    save_state()
    log_event(msg, "SYSTEM")
    return jsonify({"status": "ok", "message": msg})


@app.route("/api/capital/set", methods=["POST"])
def api_capital_set():
    """Set capital for a specific symbol."""
    data = request.get_json(silent=True) or {}
    sym = (data.get("symbol") or "").strip().upper()
    amount = float(data.get("amount", 0))
    if not sym.endswith("USDT"):
        sym += "USDT"
    if sym not in SYMBOLS:
        return jsonify({"status": "error",
                        "message": f"{sym} not active"}), 400
    if amount <= 0:
        return jsonify({"status": "error",
                        "message": "Amount must be > 0"}), 400
    with capital_lock:
        capital[sym] = {
            "balance": amount, "initial": amount, "peak": amount,
            "max_dd": 0.0, "total_trades": 0,
            "winning_trades": 0, "total_pnl": 0.0,
        }
    with state_lock:
        signal_state[sym]["last_entry_signal"] = None
        open_trades[sym].clear()
    save_state()
    log_event(f"[{sym}] Capital set to ${amount:.2f}", "SYSTEM")
    return jsonify({"status": "ok",
                    "message": f"{sym} capital set to ${amount:.2f}"})

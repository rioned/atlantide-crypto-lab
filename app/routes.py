"""Flask routes and SSE streaming for CRYPTO LAB 2."""

import json
import queue
import threading
import time
import csv
import io
from datetime import datetime

from flask import (Flask, render_template, jsonify, request,
                   Response, stream_with_context)
from flask_cors import CORS

from app.config import (SYMBOL_DISPLAY, TOP100_USDT_SYMBOLS,
                         INITIAL_CAPITAL_PER_SYMBOL, MAX_CLOSED_TRADES, TRADING_FEE)
from app.state import (SYMBOLS, capital, capital_lock, state_lock,
                        candles, ticker, signal_state, indicators,
                        manipulation_candle, manipulation_active,
                        reversal_pattern, open_trades, closed_trades,
                        event_log, sse_clients, sse_clients_lock,
                        param_history, param_version, param_lock,
                        active_params,
                        perf_metrics, perf_lock,
                        last_hypothesis, last_hypothesis_lock,
                        self_learning_active)
from app.strategy import (_bootstrap_and_restart_ws, log_event)
from app.market_data import _trigger_ws_restart
from app.database import get_db, save_state, save_closed_trade
from app.state import init_symbol_state, remove_symbol_state, ensure_capital
from app.state import last_trade_close_time
from app.market_sessions import get_all_market_statuses, check_alerts

# ─── Flask App Factory ────────────────────────────────────────────────────────

import os
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "templates")
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "static")
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
CORS(app)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _dp(price):
    if price <= 0:
        return 8
    return 8 if price < 1.0 else 4


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
        result["trailing_active"] = t.get("trailing_active", False)
        result["entry_fee"] = t.get("entry_fee", 0.0)
    else:
        result["close_price"] = t.get("exit_price", 0)
        result["pnl"] = t.get("pnl", 0.0)
        result["close_time"] = t.get("exit_time", "")
        result["close_reason"] = t.get("reason", "")
        result["symbol"] = t.get("symbol", "")
        result["pattern_type"] = t.get("pattern_type", "")
        result["total_fees"] = t.get("total_fees", t.get("entry_fee", 0))
        result["risk_amount"] = t.get("risk_amount", 0)
    return result


# ─── SSE Stream ───────────────────────────────────────────────────────────────

@app.route("/api/stream")
def api_stream():
    """Server-Sent Events endpoint — pushes real-time price/signal/self-learn updates."""
    client_queue = queue.Queue(maxsize=64)
    with sse_clients_lock:
        sse_clients.append(client_queue)

    def generate():
        yield "event: connected\ndata: {}\n\n"
        try:
            while True:
                try:
                    data = client_queue.get(timeout=30)
                    event_type = data.pop("event", "update")
                    yield f"event: {event_type}\n"
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
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

@app.route("/api/trades.csv")
def api_trades_csv():
    """Download all closed trades as CSV."""
    with state_lock:
        trades = list(closed_trades)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Symbol", "Side", "Entry Time", "Exit Time",
                     "Entry Price", "Exit Price", "Pattern", "PnL",
                     "Reason", "Fees", "Notional", "Risk Amount"])
    for t in trades:
        side = "LONG" if t.get("direction") == 1 else "SHORT"
        writer.writerow([
            t.get("id", ""),
            t.get("symbol", ""),
            side,
            t.get("entry_time", ""),
            t.get("exit_time", ""),
            t.get("entry", 0),
            t.get("exit_price", 0),
            t.get("pattern_type", ""),
            t.get("pnl", 0),
            t.get("reason", ""),
            t.get("total_fees", 0),
            t.get("notional", 250),
            t.get("risk_amount", 0),
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=trade_history.csv"}
    )


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

    # ── Self-learning data ──────────────────────────────────────────────
    with perf_lock:
        learn = dict(perf_metrics)
    with last_hypothesis_lock:
        hypothesis = last_hypothesis
    with param_lock:
        params = {
            "wick_ratio": active_params.get("wick_ratio"),
            "atr_threshold": active_params.get("atr_threshold"),
            "rr_ratio": active_params.get("rr_ratio"),
            "risk_pct": active_params.get("risk_pct"),
        }
        pv = param_version[0]
        ph = list(param_history[-10:]) if param_history else []

    return jsonify({
        "symbols": SYMBOLS,
        "data": symbols_data,
        "account": acct,
        "closed_trades": closed_t_js,
        "event_log": logs_js,
        "server_tz_offset": tz_offset,
        # Self-learning payload
        "self_learning": {
            "metrics": learn,
            "hypothesis": hypothesis,
            "param_version": pv,
            "param_history": ph,
            "active_params": params,
            "enabled": self_learning_active.is_set(),
        },
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
        conn.execute("DELETE FROM condition_snapshots")
    conn.execute("DELETE FROM open_trades")
    conn.execute("DELETE FROM symbol_capital")
    conn.commit()
    save_state()
    log_event(msg, "SYSTEM")
    return jsonify({"status": "ok", "message": msg})


@app.route("/api/trade/close", methods=["POST"])
def api_trade_close():
    """Manually close an open trade by trade_id and symbol."""
    data = request.get_json(silent=True) or {}
    trade_id = data.get("trade_id")
    sym = (data.get("symbol") or "").strip().upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    if not trade_id or sym not in SYMBOLS:
        return jsonify({"status": "error",
                        "message": "Missing trade_id or invalid symbol"}), 400

    try:
        trade_id = int(trade_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error",
                        "message": f"Invalid trade_id: {trade_id}"}), 400

    with state_lock:
        trades = open_trades.get(sym, [])
        trade = next((t for t in trades if t["id"] == trade_id), None)

    if not trade:
        return jsonify({"status": "error",
                        "message": f"Trade #{trade_id} not found in {sym}"}), 404

    current_price = 0.0
    with state_lock:
        current_price = ticker[sym]["price"]
    if current_price <= 0:
        return jsonify({"status": "error",
                        "message": "No price data available"}), 400

    notional = trade.get("notional", 250.0)
    pnl_pct = ((current_price - trade["entry"]) /
               trade["entry"] * trade["direction"])
    gross_pnl = round(pnl_pct * notional, 2)

    # Fees
    entry_fee = trade.get("entry_fee",
                          round(notional * TRADING_FEE, 2))
    exit_fee = round(abs(notional + gross_pnl) * TRADING_FEE, 2)
    total_fees = round(entry_fee + exit_fee, 2)
    net_pnl = round(gross_pnl - total_fees, 2)

    trade["exit_price"] = current_price
    trade["exit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    trade["pnl"] = net_pnl
    trade["entry_fee"] = entry_fee
    trade["exit_fee"] = exit_fee
    trade["total_fees"] = total_fees
    trade["reason"] = "MANUAL"

    # Update capital
    with capital_lock:
        cap = capital.get(sym)
        if cap:
            cap["balance"] += net_pnl
            cap["total_pnl"] += net_pnl
            cap["total_trades"] += 1
            if net_pnl > 0:
                cap["winning_trades"] += 1
            if cap["balance"] > cap["peak"]:
                cap["peak"] = cap["balance"]
            if cap["peak"] > 0:
                dd = ((cap["peak"] - cap["balance"]) / cap["peak"] * 100)
                cap["max_dd"] = max(cap["max_dd"], dd)

    # Move to closed trades
    with state_lock:
        trade["pattern_type"] = signal_state[sym].get("pattern_type", "")
        trade["manip_range"] = signal_state[sym].get("score", 0)
        closed_trades.append(dict(trade))
        closed_trades[-1]["symbol"] = sym
        if len(closed_trades) > MAX_CLOSED_TRADES:
            closed_trades.pop(0)
        # Don't clear last_entry_signal — cooldown is the gatekeeper
        if trade in open_trades[sym]:
            open_trades[sym].remove(trade)
        last_trade_close_time[sym] = time.time()

    save_closed_trade(trade, sym)
    save_state()

    log_event(
        f"[{sym}] TRADE #{trade_id} CLOSED (MANUAL): "
        f"{'LONG' if trade['direction'] == 1 else 'SHORT'} "
        f"Entry=${trade['entry']:.{_dp(trade['entry'])}f} "
        f"Exit=${current_price:.{_dp(current_price)}f} "
        f"PnL=${net_pnl:.2f} "
        f"Fees=${total_fees:.2f} | "
        f"Cap=${cap.get('balance', 0):.2f}",
        "TRADE")

    # Fire-and-forget trade scoring for self-learning
    try:
        from app.self_learning import _save_trade_score_for_trade
        _save_trade_score_for_trade(trade, sym)
    except Exception:
        pass

    # Capture conditions snapshot on manual close
    try:
        from app.conditions import capture_conditions
        capture_conditions("TRADE_CLOSE", sym,
            f"Trade #{trade_id} CLOSED (MANUAL) PnL=${net_pnl:.2f}")
    except Exception:
        pass

    return jsonify({
        "status": "ok",
        "message": f"Trade #{trade_id} closed with PnL=${net_pnl:.2f}",
        "pnl": net_pnl,
        "reason": "MANUAL",
    })


@app.route("/api/trim-logs", methods=["POST"])
def api_trim_logs():
    """Trim old ERROR events from event log (in-memory + DB)."""
    conn = get_db()
    conn.execute("DELETE FROM event_log WHERE level=\"ERROR\"")
    conn.commit()
    with state_lock:
        event_log[:] = [e for e in event_log if e.get("level") != "ERROR"]
    msg = f"Trimmed {len(event_log)} remaining entries"
    log_event("[SYSTEM] Removed stale ERROR entries from log", "SYSTEM")
    return jsonify({"status": "ok", "message": msg, "remaining": len(event_log)})


@app.route("/api/trim-conditions", methods=["POST"])
def api_trim_conditions():
    """Clear all condition snapshots."""
    conn = get_db()
    conn.execute("DELETE FROM condition_snapshots")
    conn.commit()
    msg = "Cleared all condition snapshots"
    log_event(f"[SYSTEM] {msg}", "SYSTEM")
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


# ─── Self-Learning API ───────────────────────────────────────────────────────

@app.route("/api/self-learn/toggle", methods=["POST"])
def api_self_learn_toggle():
    """Enable or disable the self-learning agent."""
    data = request.get_json(silent=True) or {}
    enable = data.get("enable", None)
    if enable is True:
        self_learning_active.set()
        log_event("[SELF-LEARN] Self-learning agent enabled", "SYSTEM")
    elif enable is False:
        self_learning_active.clear()
        log_event("[SELF-LEARN] Self-learning agent disabled", "SYSTEM")
    return jsonify({"enabled": self_learning_active.is_set()})


@app.route("/api/self-learn/history")
def api_self_learn_history():
    """Return full param history."""
    with param_lock:
        return jsonify(param_history)


# ─── Market Sessions API ──────────────────────────────────────────────────

@app.route("/api/market-sessions")
def api_market_sessions():
    """Return current status of all 7 major stock exchanges."""
    return jsonify(get_all_market_statuses())


# ─── Conditions API ───────────────────────────────────────────────────────

@app.route("/api/conditions")
def api_conditions():
    """Return condition snapshots (full system state at each event)."""
    from app.conditions import get_conditions
    limit = request.args.get("limit", 200, type=int)
    conds = get_conditions(limit)
    return jsonify(conds)

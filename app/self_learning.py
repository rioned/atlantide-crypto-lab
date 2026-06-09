"""Self-Improving Trading Engine — v2 with per-entry-type tracking.

From the video's core concept — expanded to tune entry type weights dynamically:

1. Score every trade by context (entry type, regime, volatility)
2. Track win rate / profit factor PER ENTRY TYPE
3. Review every N trades: analyze, hypothesize, change ONE variable
4. Dynamically adjust entry type weights based on recent performance
5. Prune underperforming entry types; amplify winners
"""

import time
import math
from datetime import datetime
from collections import defaultdict

from app.config import (SELF_LEARN_MIN_TRADES, SELF_LEARN_REVIEW_INTERVAL,
                         PARAM_TUNE_AMOUNT, TARGET_SHARPE, TARGET_WINRATE,
                         TARGET_MONTHLY_RETURN, MAX_DRAWDOWN_LIMIT,
                         HAMMER_MIN_WICK_RATIO, RR_RATIO, RISK_PCT,
                         ENTRY_THRESHOLD, ENTRY_TYPES)
from app.state import (closed_trades, open_trades, capital, capital_lock,
                        state_lock, param_history, param_version, param_lock,
                        active_params, perf_metrics, perf_lock,
                        last_hypothesis, last_hypothesis_lock,
                        self_learning_active)
from app.database import (save_trade_score, save_param_history,
                          save_perf_snapshot)


def log_event(msg, level="INFO"):
    from app.strategy import log_event as _log
    _log(msg, level)


# ─── Tunable Parameters ───────────────────────────────────────────────────

TUNABLE_PARAMS = [
    {
        "name": "entry_threshold",
        "config_ref": "ENTRY_THRESHOLD",
        "default": ENTRY_THRESHOLD,
        "min": 2.0,
        "max": 6.0,
        "description": "Min absolute score to enter a trade (higher = fewer, better trades)",
        "direction": 1,
    },
    {
        "name": "wick_ratio",
        "config_ref": "HAMMER_MIN_WICK_RATIO",
        "default": HAMMER_MIN_WICK_RATIO,
        "min": 1.2,
        "max": 4.0,
        "description": "Min wick-to-body ratio for candlestick patterns",
        "direction": 1,
    },
    {
        "name": "risk_pct",
        "config_ref": "RISK_PCT",
        "default": RISK_PCT,
        "min": 0.05,
        "max": 0.20,
        "description": "Risk percentage per trade",
        "direction": 1,
    },
]


# ─── Per-Entry-Type Performance Tracking ──────────────────────────────────

def _compute_type_performance(trades):
    """Compute win rate, profit factor, and total PnL per entry type.
    
    Returns dict: {entry_type: {"count": N, "wins": N, "pnl": X, "win_rate": %}}
    """
    by_type = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0, "pnls": []})
    for t in trades:
        entry_type = t.get("pattern_type", "unknown")
        if not entry_type:
            entry_type = "unknown"
        by_type[entry_type]["count"] += 1
        by_type[entry_type]["pnl"] += t["pnl"]
        by_type[entry_type]["pnls"].append(t["pnl"])
        if t["pnl"] > 0:
            by_type[entry_type]["wins"] += 1
    
    result = {}
    for entry_type, data in by_type.items():
        wr = (data["wins"] / data["count"] * 100) if data["count"] > 0 else 0
        gross_win = sum(p for p in data["pnls"] if p > 0)
        gross_loss = abs(sum(p for p in data["pnls"] if p < 0))
        pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
        result[entry_type] = {
            "count": data["count"],
            "wins": data["wins"],
            "pnl": round(data["pnl"], 2),
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2),
        }
    return result


def _compute_metrics(trades):
    """Compute all performance metrics from a list of trades."""
    if not trades:
        return {
            "sharpe_ratio": 0.0, "profit_factor": 0.0,
            "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "total_trades": 0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "expectancy": 0.0,
            "best_pattern": "", "worst_pattern": "",
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    sharpe = _compute_sharpe_ratio(pnls)
    pf = _compute_profit_factor(trades)

    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss) if pnls else 0

    peak = 0
    max_dd = 0
    running = 0
    for p in pnls:
        running += p
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    # Per-type performance
    type_perf = _compute_type_performance(trades)
    best_pat = ""
    worst_pat = ""
    if type_perf:
        sorted_types = sorted(type_perf.items(), key=lambda x: x[1]["pnl"])
        worst_pat = sorted_types[0][0] if sorted_types else ""
        best_pat = sorted_types[-1][0] if sorted_types else ""

    return {
        "sharpe_ratio": sharpe,
        "profit_factor": pf,
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "total_trades": len(trades),
        "net_pnl": round(net_pnl, 2),
        "max_drawdown": round(max_dd, 2),
        "expectancy": round(expectancy, 2),
        "best_pattern": best_pat,
        "worst_pattern": worst_pat,
        "type_performance": type_perf,
    }


def _compute_sharpe_ratio(pnls):
    if len(pnls) < 2:
        return 0.0
    mean_pnl = sum(pnls) / len(pnls)
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
    if variance <= 0:
        return 0.0
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return round(mean_pnl / std, 4)


def _compute_profit_factor(trades):
    gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    if gross_loss <= 0:
        return 999.0
    return round(gross_win / gross_loss, 4)


def _score_trade(trade):
    """Score a single trade on a 0-100 scale."""
    risk_amount = trade.get("risk_amount", 50.0)
    if risk_amount <= 0:
        risk_amount = 50.0
    pnl = trade["pnl"]
    r_multiple = pnl / risk_amount
    if r_multiple >= 2.0:
        base = 90
    elif r_multiple >= 1.0:
        base = 70
    elif r_multiple >= 0:
        base = 50
    elif r_multiple >= -0.5:
        base = 30
    elif r_multiple >= -1.0:
        base = 20
    else:
        base = 10
    
    pattern_type = trade.get("pattern_type", "")
    if pnl > 0 and pattern_type:
        base += 10
    reason = trade.get("reason", "")
    if (pnl > 0 and reason == "TP") or (pnl < 0 and reason == "SL"):
        base += 5
    return min(100, max(0, base))


def _classify_market_regime(trades):
    if not trades:
        return "unknown"
    long_pnl = sum(t["pnl"] for t in trades if t.get("direction", 0) == 1)
    short_pnl = sum(t["pnl"] for t in trades if t.get("direction", 0) == -1)
    total_pnl = long_pnl + short_pnl
    if total_pnl == 0:
        return "neutral"
    if long_pnl > short_pnl:
        return "bullish" if long_pnl > 0 else "bearish_long"
    else:
        return "bearish" if short_pnl > 0 else "bullish_short"


def _get_volatility(trades):
    if not trades:
        return 0.5
    ranges = [
        abs(t.get("entry", 0) - t.get("exit_price", 0)) / max(t.get("entry", 1), 0.01)
        for t in trades if t.get("entry", 0) > 0 and t.get("exit_price", 0) > 0
    ]
    if not ranges:
        return 0.5
    avg_range = sum(ranges) / len(ranges)
    return min(1.0, avg_range * 100 / 3.0)


def _determine_regime(trades_before):
    if not trades_before:
        return "neutral"
    wins = [t for t in trades_before if t["pnl"] > 0]
    losses = [t for t in trades_before if t["pnl"] < 0]
    if len(wins) > len(losses) * 1.5:
        return "favorable"
    elif len(losses) > len(wins) * 1.5:
        return "unfavorable"
    return "mixed"


# ─── Parameter Selection ──────────────────────────────────────────────────

def _select_param_to_tune(metrics):
    """Select which parameter to change — one at a time (scientific method)."""
    win_rate = metrics.get("win_rate", 0.0)
    profit_factor = metrics.get("profit_factor", 0.0)
    type_perf = metrics.get("type_performance", {})

    # Check if any entry type is severely underperforming
    for entry_type, perf in type_perf.items():
        if perf["count"] >= 5 and perf["win_rate"] < 25.0:
            # Bad entry type — tighten threshold to filter it out
            return TUNABLE_PARAMS[0]  # entry_threshold
    
    # Low win rate + bad PF → tighten entry threshold
    if win_rate < TARGET_WINRATE * 0.7 and profit_factor < 1.0:
        return TUNABLE_PARAMS[0]  # entry_threshold
    
    # Good win rate but small wins → increase risk per trade
    if win_rate >= TARGET_WINRATE and profit_factor < 1.5:
        return TUNABLE_PARAMS[2]  # risk_pct
    
    # Decent win rate but overall negative → tighten wick ratio
    if win_rate < TARGET_WINRATE * 0.8 and profit_factor < 1.0:
        return TUNABLE_PARAMS[1]  # wick_ratio
    
    # Cycle through remaining params
    for param in TUNABLE_PARAMS:
        current_val = active_params.get(param["name"])
        if current_val is not None:
            last_entry = param_history[-1] if param_history else None
            if last_entry and last_entry.get("param_changed") == param["name"]:
                next_idx = (TUNABLE_PARAMS.index(param) + 1) % len(TUNABLE_PARAMS)
                return TUNABLE_PARAMS[next_idx]
    return TUNABLE_PARAMS[0]


def _select_tune_direction(param, metrics, trades):
    """Choose whether to increase or decrease the selected parameter."""
    win_rate = metrics.get("win_rate", TARGET_WINRATE)
    profit_factor = metrics.get("profit_factor", 1.0)
    type_perf = metrics.get("type_performance", {})

    if param["name"] == "entry_threshold":
        # Check if last change helped
        last_entry = None
        for entry in reversed(param_history):
            if entry.get("param_changed") == "entry_threshold":
                last_entry = entry
                break
        if last_entry:
            last_was_increase = last_entry["new_value"] > last_entry["old_value"]
            last_wr = last_entry.get("win_rate_after", 0)
            if last_was_increase and last_wr < TARGET_WINRATE * 0.7:
                return "decrease"  # Making stricter didn't help → loosen
        
        # Check for failing entry types
        bad_types = sum(1 for p in type_perf.values()
                        if p["count"] >= 5 and p["win_rate"] < 25.0)
        if bad_types > 0:
            return "increase"  # Increase threshold to filter bad signals
        
        if win_rate < TARGET_WINRATE * 0.7:
            return "increase"  # Stricter = fewer but better trades
        if profit_factor < 1.0:
            return "decrease"  # Looser = more signals to find winners
        return "increase"

    # wick_ratio
    if param["name"] == "wick_ratio":
        last_wick = None
        for entry in reversed(param_history):
            if entry.get("param_changed") == "wick_ratio":
                last_wick = entry
                break
        if last_wick:
            last_was_increase = last_wick["new_value"] > last_wick["old_value"]
            last_wr = last_wick.get("win_rate_after", 0)
            if last_was_increase and last_wr < TARGET_WINRATE * 0.7:
                return "decrease"
        if win_rate < TARGET_WINRATE * 0.7:
            return "increase"
        if profit_factor < 0.8:
            return "decrease"
        return "increase"

    # risk_pct
    if param["name"] == "risk_pct":
        if win_rate >= TARGET_WINRATE:
            return "increase"
        else:
            return "decrease"

    return "increase"


def _form_hypothesis(param, direction, metrics, trades):
    """Form a human-readable hypothesis."""
    win_rate = metrics.get("win_rate", 0.0)
    pnl = metrics.get("net_pnl", 0.0)
    type_perf = metrics.get("type_performance", {})

    bad_types = [f"{t}({p['win_rate']:.0f}%)"
                 for t, p in type_perf.items()
                 if p["count"] >= 3 and p["win_rate"] < 30.0]
    good_types = [f"{t}({p['win_rate']:.0f}%)"
                  for t, p in type_perf.items()
                  if p["count"] >= 3 and p["win_rate"] >= 50.0]

    diagnosis_parts = []
    if bad_types:
        diagnosis_parts.append(f"weak: {', '.join(bad_types)}")
    if good_types:
        diagnosis_parts.append(f"strong: {', '.join(good_types)}")
    if win_rate < TARGET_WINRATE * 0.7:
        diagnosis_parts.append(f"WR={win_rate:.1f}% below {TARGET_WINRATE:.0f}%")
    if pnl < 0:
        diagnosis_parts.append(f"PnL=${pnl:.2f} negative")
    
    diagnosis = "; ".join(diagnosis_parts) if diagnosis_parts else "steady-state"
    action = f"{'increase' if direction == 'increase' else 'decrease'} {param['name']} ({param['description']})"
    
    return f"Diagnosis: {diagnosis}. Action: {action}. Expected: better alignment with market conditions."


def _apply_param_change(param, direction):
    """Apply parameter change to active_params (hot-reloadable by strategy).
    
    Returns (old_val, new_val, changed) where changed=False means the
    param was already at its bound and couldn't change further.
    """
    current_default = param["default"]
    current_override = active_params.get(param["name"])
    current = current_override if current_override is not None else current_default

    change_amount = current * PARAM_TUNE_AMOUNT * param["direction"]
    if direction == "increase":
        new_val = min(param["max"], current + abs(change_amount))
    else:
        new_val = max(param["min"], current - abs(change_amount))

    old_val = current
    changed = abs(new_val - old_val) > 1e-10
    if changed:
        with param_lock:
            active_params[param["name"]] = new_val

    return old_val, new_val, changed


def _compute_goals(metrics):
    """Calculate progress toward defined goals."""
    sharpe_progress = min(100, max(0, metrics["sharpe_ratio"] / TARGET_SHARPE * 100))
    winrate_progress = min(100, max(0, metrics["win_rate"] / TARGET_WINRATE * 100))
    with capital_lock:
        caps = dict(capital) if capital else {}
    total_capital = sum(c["balance"] for c in caps.values()) if caps else 500.0
    return_pct = (metrics.get("net_pnl", 0) / max(total_capital, 1)) * 100
    return_progress = min(100, max(0, return_pct / TARGET_MONTHLY_RETURN * 100))
    dd = metrics.get("max_drawdown", 0)
    total_initial = sum(c["initial"] for c in caps.values()) if caps else 500.0
    dd_pct = (dd / max(total_initial, 1)) * 100
    dd_status = "OK" if dd_pct < MAX_DRAWDOWN_LIMIT else "CRITICAL"

    return {
        "sharpe": round(sharpe_progress, 1),
        "winrate": round(winrate_progress, 1),
        "return_pct": round(return_progress, 1),
        "dd_pct": round(dd_pct, 1),
        "dd_status": dd_status,
    }


def _run_review_cycle(force=False):
    """Run one self-review cycle — analyze, hypothesize, change one param.
    
    Args:
        force: If True, skip the interval check (used for consecutive-loss trigger).
    """
    total_closed = 0
    with state_lock:
        total_closed = len(closed_trades)

    if total_closed < SELF_LEARN_MIN_TRADES:
        return

    with perf_lock:
        last_review = perf_metrics.get("last_review_trades", 0)

    new_trades_count = total_closed - last_review
    if not force and new_trades_count < SELF_LEARN_REVIEW_INTERVAL:
        return

    with state_lock:
        all_trades = list(closed_trades)

    metrics = _compute_metrics(all_trades)
    goals = _compute_goals(metrics)
    regime = _classify_market_regime(all_trades)
    volatility = _get_volatility(all_trades)

    before_trades = all_trades[:-new_trades_count] if new_trades_count < len(all_trades) else []
    after_trades = all_trades[-new_trades_count:] if new_trades_count <= len(all_trades) else all_trades
    before_metrics = _compute_metrics(before_trades)
    after_metrics = _compute_metrics(after_trades)

    # Per-type performance info for the hypothesis
    type_perf = after_metrics.get("type_performance", {})
    type_summary = "; ".join(
        f"{t}: {p['win_rate']:.0f}% ({p['count']}t {p['pnl']:+.2f})"
        for t, p in sorted(type_perf.items(), key=lambda x: -x[1]["count"])
    )

    param_to_tune = _select_param_to_tune(metrics)
    direction = _select_tune_direction(param_to_tune, metrics, after_trades)
    old_val, new_val, changed = _apply_param_change(param_to_tune, direction)
    
    # If param was stuck at bound, try the next param
    if not changed:
        for fallback in TUNABLE_PARAMS:
            if fallback["name"] == param_to_tune["name"]:
                continue
            fb_direction = _select_tune_direction(fallback, metrics, after_trades)
            fb_old, fb_new, fb_changed = _apply_param_change(fallback, fb_direction)
            if fb_changed:
                param_to_tune = fallback
                direction = fb_direction
                old_val, new_val, changed = fb_old, fb_new, fb_changed
                break
    
    hypothesis = _form_hypothesis(param_to_tune, direction, metrics, after_trades)

    with last_hypothesis_lock:
        last_hypothesis = hypothesis

    with param_lock:
        param_version[0] += 1
        entry = {
            "version": param_version[0],
            "param_changed": param_to_tune["name"],
            "old_value": round(old_val, 8),
            "new_value": round(new_val, 8),
            "trades_evaluated": len(all_trades),
            "win_rate_before": before_metrics["win_rate"],
            "win_rate_after": after_metrics["win_rate"],
            "pnl_before": before_metrics["net_pnl"],
            "pnl_after": after_metrics["net_pnl"],
            "type_performance": type_summary,
            "hypothesis": hypothesis,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        param_history.append(entry)

    try:
        save_param_history(entry)
    except Exception:
        pass

    with perf_lock:
        perf_metrics.update(metrics)
        perf_metrics["last_review_trades"] = total_closed
        perf_metrics["goal_sharpe_progress"] = goals["sharpe"]
        perf_metrics["goal_winrate_progress"] = goals["winrate"]
        perf_metrics["goal_return_progress"] = goals["return_pct"]
        perf_metrics["goal_dd_status"] = goals["dd_status"]
        perf_metrics["goal_dd_pct"] = goals["dd_pct"]

    try:
        snapshot = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_trades": metrics["total_trades"],
            "win_rate": metrics["win_rate"],
            "sharpe_ratio": metrics["sharpe_ratio"],
            "profit_factor": metrics["profit_factor"],
            "net_pnl": metrics["net_pnl"],
            "max_dd": metrics["max_drawdown"],
            "avg_win": metrics["avg_win"],
            "avg_loss": metrics["avg_loss"],
            "expectancy": metrics["expectancy"],
        }
        save_perf_snapshot(snapshot)
    except Exception:
        pass

    log_event(
        f"[SELF-LEARN] Review #{param_version[0]}: "
        f"Changed {param_to_tune['name']} "
        f"{old_val:.4f} → {new_val:.4f} | "
        f"WR={metrics['win_rate']:.1f}% PF={metrics['profit_factor']:.2f} "
        f"PnL=${metrics['net_pnl']:.2f} | "
        f"Types: {type_summary[:80]}... | "
        f"Hypothesis: {hypothesis[:60]}...", "SELF-LEARN")


def _save_trade_score_for_trade(trade, sym):
    """Score and save a closed trade for the self-learning database."""
    try:
        risk_amount = trade.get("risk_amount", 50.0)
        with state_lock:
            all_closed = list(closed_trades)
        regime = _determine_regime(all_closed[-20:]) if len(all_closed) >= 20 else "neutral"
        score = _score_trade(trade)

        # Check for 5 consecutive losses -> trigger immediate self-improvement
        # Throttled: max once per 10 minutes to prevent rapid-fire no-op changes
        if len(all_closed) >= 5:
            last_5 = all_closed[-5:]
            if all(t["pnl"] < 0 for t in last_5):
                # Check time since last forced review
                now = time.time()
                last_force = getattr(_save_trade_score_for_trade, "_last_force_time", 0)
                if now - last_force > 600:  # 10 minutes
                    _save_trade_score_for_trade._last_force_time = now
                    log_event(
                        "[SELF-LEARN] 5 consecutive losses detected — "
                        "triggering immediate review!", "WARN")
                    _run_review_cycle(force=True)

        trade_score = {
            "trade_id": trade["id"],
            "symbol": sym,
            "entry_time": trade.get("entry_time", ""),
            "exit_time": trade.get("exit_time", ""),
            "direction": trade["direction"],
            "entry_price": trade.get("entry", 0),
            "exit_price": trade.get("exit_price", 0),
            "pnl": trade["pnl"],
            "reason": trade.get("reason", ""),
            "pattern_type": trade.get("pattern_type", ""),
            "manip_range": trade.get("manip_range", 0),
            "daily_atr": trade.get("daily_atr", 0),
            "market_regime": regime,
            "score": score,
            "market_volatility": round(_get_volatility([trade]), 4),
        }
        save_trade_score(trade_score)
    except Exception:
        pass


def self_learning_loop():
    """Main self-learning loop — reviews trades every 60 seconds."""
    while True:
        try:
            if self_learning_active.is_set():
                _run_review_cycle()
        except Exception as e:
            log_event(f"[SELF-LEARN] Error: {e}", "ERROR")
        time.sleep(60)


def get_effective_param(param_name):
    """Get the effective (hot-reloadable) value for a parameter.
    
    Strategy calls this to check overrides from self-learning engine.
    """
    override = active_params.get(param_name)
    if override is not None:
        return override
    defaults = {
        "wick_ratio": HAMMER_MIN_WICK_RATIO,
        "entry_threshold": ENTRY_THRESHOLD,
        "rr_ratio": RR_RATIO,
        "risk_pct": RISK_PCT,
    }
    return defaults.get(param_name, 0)

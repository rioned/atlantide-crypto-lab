/* CRYPTO LAB 2 v3 — Self-Learning Trading Dashboard */
(function() {
  'use strict';

  let state = null;
  let eventSource = null;
  let clockInterval = null;
  let lastPing = Date.now();

  // ── Init ────────────────────────────────────────────────────────────────
  function init() {
    fetchState();
    fetchMarketSessions();
    fetchAllSymbols();
    clockInterval = setInterval(updateClock, 1000);
    setInterval(fetchMarketSessions, 30000);  // refresh market sessions every 30s
  }

  function fetchState() {
    const tz = -new Date().getTimezoneOffset() / 60;
    fetch(`/api/state?tz=${tz}`)
      .then(r => r.json())
      .then(s => {
        state = s;
        render();
        document.getElementById('loadingOverlay').classList.add('hidden');
        connectSSE();
      })
      .catch(err => {
        document.getElementById('loadingText').textContent = 'Connecting...';
        setTimeout(fetchState, 2000);
      });
  }

  // ── SSE ─────────────────────────────────────────────────────────────────
  function connectSSE() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource('/api/stream');
    eventSource.onmessage = function() {};
    eventSource.addEventListener('ticker', e => handleSSE('ticker', e.data));
    eventSource.addEventListener('kline', e => handleSSE('kline', e.data));
    eventSource.addEventListener('update', e => handleSSE('update', e.data));
    eventSource.addEventListener('ping', () => { lastPing = Date.now(); });
    eventSource.addEventListener('market_alert', e => handleMarketAlert(e.data));
    eventSource.onerror = function() {
      eventSource.close();
      setTimeout(connectSSE, 3000);
    };
  }

  function handleSSE(type, raw) {
    try { var data = JSON.parse(raw); } catch(e) { return; }
    if (type === 'ticker' || type === 'kline' || type === 'update') {
      refreshState();
    }
  }

  function handleMarketAlert(raw) {
    try {
      var data = JSON.parse(raw);
    } catch(e) { return; }
    var eventLabel = data.event_type === 'open' ? 'opens' : 'closes';
    var mins = Math.floor((data.seconds_until || 600) / 60);
    var secs = (data.seconds_until || 600) % 60;
    var timeStr = mins > 0 ? mins + 'm ' + secs + 's' : secs + 's';
    toast(data.flag + ' ' + data.market + ' ' + eventLabel + ' in ' + timeStr, 'info');
    // Refresh market sessions so the UI updates immediately
    fetchMarketSessions();
  }

  let refreshTimer = null;
  let marketSessionsData = [];  // cached market session data

  function refreshState() {
    if (refreshTimer) return;
    refreshTimer = setTimeout(() => {
      refreshTimer = null;
      const tz = -new Date().getTimezoneOffset() / 60;
      fetch(`/api/state?tz=${tz}`)
        .then(r => r.json())
        .then(s => { state = s; renderQuick(); })
        .catch(() => {});
    }, 1000);
  }

  // ── Clock ───────────────────────────────────────────────────────────────
  function updateClock() {
    const now = new Date();
    document.getElementById('clock').textContent = now.toLocaleTimeString('en-US', {hour12: false});
    renderMarketSessionCountdowns();
  }

  // ── Market Sessions ──────────────────────────────────────────────────────
  function fetchMarketSessions() {
    fetch('/api/market-sessions')
      .then(r => r.json())
      .then(data => {
        marketSessionsData = data;
        renderMarketSessions();
      })
      .catch(() => {});
  }

  function renderMarketSessions() {
    if (!marketSessionsData || marketSessionsData.length === 0) return;
    const grid = document.getElementById('marketSessionsGrid');
    if (!grid) return;

    document.getElementById('marketSessionTime').textContent =
      'Local · ' + (marketSessionsData[0] ? marketSessionsData[0].local_time : '');

    grid.innerHTML = marketSessionsData.map(m => {
      const secs = m.seconds_until_next;
      let countdownStr = formatCountdown(secs);
      let statusHTML;
      let statusClass;

      if (m.is_open) {
        statusClass = 'session-open';
        statusHTML = `<span class="session-dot session-dot-open"></span> Open <span class="session-closes-text">· closes ${countdownStr}</span>`;
      } else if (m.next_event_type === 'open') {
        statusClass = 'session-closed';
        if (secs <= 600) {
          statusClass = 'session-opening';
          statusHTML = `<span class="session-dot session-dot-opening"></span> Opens <span class="session-opens-text">${countdownStr}</span>`;
        } else {
          statusHTML = `<span class="session-dot session-dot-closed"></span> Closed · opens ${countdownStr}`;
        }
      } else {
        statusClass = 'session-closed';
        statusHTML = `<span class="session-dot session-dot-closed"></span> Closed · ${countdownStr}`;
      }

      return `<div class="session-card ${statusClass}">
        <div class="session-header">
          <span class="session-flag">${m.flag || ''}</span>
          <span class="session-name">${m.name}</span>
          <span class="session-tz">${m.tz.split('/').pop()}</span>
        </div>
        <div class="session-body">
          <div class="session-status">${statusHTML}</div>
          <div class="session-schedule">${m.session_name || (m.sessions ? m.sessions.map(s => s[0]+'–'+s[1]).join(' / ') : '')}</div>
        </div>
      </div>`;
    }).join('');
  }

  function renderMarketSessionCountdowns() {
    if (!marketSessionsData || marketSessionsData.length === 0) return;
    const cards = document.querySelectorAll('.session-card');
    marketSessionsData.forEach((m, i) => {
      const card = cards[i];
      if (!card) return;
      const secs = m.seconds_until_next;
      const statusEl = card.querySelector('.session-status');
      if (!statusEl) return;

      if (m.is_open) {
        const closeText = statusEl.querySelector('.session-closes-text');
        if (closeText) closeText.textContent = '· closes ' + formatCountdown(secs);
      } else if (m.next_event_type === 'open') {
        const opensText = statusEl.querySelector('.session-opens-text');
        if (opensText) opensText.textContent = formatCountdown(secs);
        // Re-dot if within 10 min
        const dot = statusEl.querySelector('.session-dot');
        if (dot && secs <= 600) {
          dot.className = 'session-dot session-dot-opening';
          card.className = 'session-card session-opening';
        }
      }
    });
  }

  function formatCountdown(secs) {
    if (secs <= 0) return 'now';
    if (secs < 60) return secs + 's';
    const mins = Math.floor(secs / 60);
    if (mins < 60) {
      const s = secs % 60;
      return `${mins}m${s > 0 ? ' ' + s + 's' : ''}`;
    }
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return `${h}h ${m}m`;
  }

  // ── Render ──────────────────────────────────────────────────────────────
  function render() {
    if (!state) return;
    renderTopbar();
    renderSelector();
    renderSymbols();
    renderOpenPositions();
    renderClosedTrades();
    renderLog();
    renderLearnPanel();
  }

  function renderQuick() {
    if (!state) return;
    renderTopbar();
    renderSymbolsQuick();
    renderOpenPositions();
    renderLearnPanel();
  }

  // ── Topbar ──────────────────────────────────────────────────────────────
  function renderTopbar() {
    const a = state.account || {};
    const el = document.getElementById('topbarStats');
    el.innerHTML = `
      <span class="stat-pill">Bal <span class="num">$${a.balance||0}</span></span>
      <span class="stat-pill">PnL <span class="num" style="color:${(a.total_pnl||0)>=0?'var(--green)':'var(--red)'}">$${a.total_pnl||0}</span></span>
      <span class="stat-pill">WR <span class="num">${a.winrate||0}%</span></span>
      <span class="stat-pill">DD <span class="num">${a.max_drawdown||0}%</span></span>
      <span class="stat-pill">Trades <span class="num">${a.total_trades||0}</span></span>
    `;
    document.getElementById('symCount').textContent = `· ${(state.symbols||[]).length} sym`;
  }

  // ── Selector ────────────────────────────────────────────────────────────
  function renderSelector() {
    const chips = document.getElementById('activeChips');
    const select = document.getElementById('symbolSelect');
    chips.innerHTML = (state.symbols||[]).map(sym => `
      <span class="chip active" onclick="removeSymbol('${sym}')" title="Click to remove ${sym}">${sym.replace('USDT','')} ✕</span>
    `).join('');
    select.innerHTML = '<option value="">+ Add Symbol</option>';
    // Populate from /api/symbols data (all 100 symbols) for adding new ones
    if (window.allSymbols && window.allSymbols.length > 0) {
      window.allSymbols.forEach(item => {
        if (!item.active) {
          select.innerHTML += `<option value="${item.code}">${item.display}</option>`;
        }
      });
    }
  }

  function fetchAllSymbols() {
    fetch('/api/symbols')
      .then(r => r.json())
      .then(data => {
        window.allSymbols = data;
        renderSelector();
      })
      .catch(() => {});
  }

  // ── Symbols ─────────────────────────────────────────────────────────────
  function symbolPanelHTML(sym, d) {
    const t = d.ticker || {};
    const sig = d.signal_state || {};
    const cap = d.capital || {};
    const ind = d.indicators || {};
    const mp = d.manipulation;
    const openTrades = d.open_trades || [];
    const price = t.price || 0;
    const chg = t.change_pct || 0;
    const chgClass = chg >= 0 ? 'positive' : 'negative';
    const chgSign = chg >= 0 ? '+' : '';

    const capPct = cap.initial > 0 ? ((cap.balance - cap.initial) / cap.initial * 100).toFixed(1) : '0.0';
    const capClass = cap.balance >= cap.initial ? 'green' : 'red';

    const trend = ind.trend || '';
    const vol = ind.vol_label || '';
    const rsi = ind.rsi || '';
    const atr = ind['15m_atr14'] || 0;

    return `
      <div class="sym-panel" id="panel-${sym}">
        <div class="sym-header">
          <span class="sym-name">${d.display||sym}</span>
          <span>
            <span class="sym-price">$${price.toFixed(price < 1 ? 6 : 2)}</span>
            <span class="sym-change ${chgClass}">${chgSign}${chg.toFixed(2)}%</span>
          </span>
        </div>
        <div class="sym-body">
          <div class="sym-row"><span class="sym-label">Signal</span><span class="sym-val ${sig.signal ? (sig.signal === 'LONG' ? 'green' : 'red') : ''}">${sig.signal || '—'} ${sig.pattern_type ? '('+sig.pattern_type+')' : ''}</span></div>
          <div class="sym-row"><span class="sym-label">Score / TP / SL</span><span class="sym-val">${sig.score ? 's='+sig.score : ''} ${sig.tp ? '$'+sig.tp.toFixed(sig.tp < 1 ? 6 : 2) : '—'} / ${sig.sl ? '$'+sig.sl.toFixed(sig.sl < 1 ? 6 : 2) : '—'}</span></div>
          <div class="sym-row"><span class="sym-label">Regime / Vol</span><span class="sym-val">${trend||'—'} ${vol||''}</span></div>
          <div class="sym-row"><span class="sym-label">ATR / RSI</span><span class="sym-val">$${atr.toFixed(atr < 1 ? 6 : 2)} / ${rsi}</span></div>
          <div class="sym-row"><span class="sym-label">Capital</span><span class="sym-val ${capClass}">$${cap.balance.toFixed(2)} (${capPct}%)</span></div>
          <div class="sym-row"><span class="sym-label">Open trades</span><span class="sym-val">${openTrades.length} (Unr. PnL: $${d.open_pnl||0})</span></div>
          ${openTrades.length > 0 ? openTrades.map(t => `
            <div class="sym-row" style="padding-left:12px;font-size:9px;">
              <span class="sym-label">#${t.id} ${t.side} <span style="color:${t.unrealized_pnl >= 0 ? 'var(--green)' : 'var(--red)'}">${t.unrealized_pnl >= 0 ? '+' : ''}$${t.unrealized_pnl.toFixed(2)}</span></span>
              <span class="sym-val">Entry $${t.entry_price.toFixed(t.entry_price < 1 ? 6 : 2)}</span>
            </div>
          `).join('') : ''}
        </div>
      </div>
    `;
  }

  function renderSymbols() {
    const grid = document.getElementById('symbolGrid');
    if (!state.data) return;
    grid.innerHTML = (state.symbols||[]).map(sym => {
      const d = state.data[sym];
      return d ? symbolPanelHTML(sym, d) : '';
    }).join('');
  }

  function renderSymbolsQuick() {
    if (!state.data) return;
    (state.symbols||[]).forEach(sym => {
      const d = state.data[sym];
      if (!d) return;
      const panel = document.getElementById(`panel-${sym}`);
      if (!panel) { renderSymbols(); return; }
      const price = d.ticker.price || 0;
      const pr = panel.querySelector('.sym-price');
      if (pr) pr.textContent = `$${price.toFixed(price < 1 ? 6 : 2)}`;
      const chg = d.ticker.change_pct || 0;
      const c = panel.querySelector('.sym-change');
      if (c) {
        c.textContent = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
        c.className = `sym-change ${chg >= 0 ? 'positive' : 'negative'}`;
      }
    });
  }

  // ── Open Positions ─────────────────────────────────────────────────────
  function renderOpenPositions() {
    const tbody = document.getElementById('openTbody');
    const count = document.getElementById('openCount');
    const empty = document.getElementById('openEmpty');
    const table = document.getElementById('openPosTable');
    const totalPnl = document.getElementById('openPnlTotal');

    // Collect all open trades from all symbols
    let allOpen = [];
    let totalUnrealized = 0;
    if (state.data) {
      (state.symbols||[]).forEach(sym => {
        const d = state.data[sym];
        if (d && d.open_trades) {
          d.open_trades.forEach(t => {
            t._symbol = sym;
            totalUnrealized += t.unrealized_pnl || 0;
            allOpen.push(t);
          });
        }
      });
    }

    count.textContent = `(${allOpen.length})`;
    totalPnl.textContent = allOpen.length > 0
      ? `Total: <span style="color:${totalUnrealized >= 0 ? 'var(--green)' : 'var(--red)'}">${totalUnrealized >= 0 ? '+' : ''}$${totalUnrealized.toFixed(2)}</span>`
      : '';
    totalPnl.innerHTML = totalPnl.textContent;

    empty.style.display = 'none';
    table.style.display = '';

    const now = new Date();
    if (allOpen.length === 0) {
      tbody.innerHTML = '<tr><td colspan="13" style="text-align:center;color:var(--text-muted);padding:16px;font-style:italic;">No open positions</td></tr>';
      return;
    }
    tbody.innerHTML = allOpen.map(t => {
      const side = t.side || (t.direction === 1 ? 'LONG' : t.direction === -1 ? 'SHORT' : '—');
      const entry = t.entry_price || 0;
      const tp = t.tp || 0;
      const sl = t.sl || 0;
      const notional = t.notional || 0;
      const upnl = t.unrealized_pnl || 0;
      const pnlClass = upnl > 0 ? 'green' : upnl < 0 ? 'red' : '';
      const pnlStr = (upnl >= 0 ? '+' : '') + upnl.toFixed(2);

      // R:R achieved
      const riskAmount = Math.abs(entry - sl);
      const rr = riskAmount > 0 ? (upnl / (notional * (riskAmount / entry))).toFixed(2) : '—';

      // Duration
      let duration = '—';
      if (t.entry_time) {
        const entryTime = new Date(t.entry_time);
        const diffMs = now - entryTime;
        const mins = Math.floor(diffMs / 60000);
        if (mins < 60) duration = `${mins}m`;
        else duration = `${Math.floor(mins/60)}h ${mins%60}m`;
      }

      const trail = t.trailing_active ? '🔒' : '—';
      const entryFee = t.entry_fee || 0;
      const notionalVal = t.notional || 0;
      const feePct = notionalVal > 0 ? (entryFee / notionalVal * 100).toFixed(2) + '%' : '—';

      return `<tr>
        <td>${t._symbol || ''}</td>
        <td>#${t.id || ''}</td>
        <td class="${side === 'LONG' ? 'green' : 'red'}">${side}</td>
        <td>$${entry.toFixed(entry < 1 ? 6 : 2)}</td>
        <td>$${tp.toFixed(tp < 1 ? 6 : 2)}</td>
        <td style="color:var(--red-dim)">$${sl.toFixed(sl < 1 ? 6 : 2)}</td>
        <td>$${notional.toFixed(0)}</td>
        <td class="${pnlClass}">$${pnlStr}</td>
        <td style="color:var(--text-muted);font-size:9px;">$${entryFee.toFixed(2)} · ${feePct}</td>
        <td style="color:var(--text-muted)">${rr}x</td>
        <td style="color:var(--text-muted);font-size:9px;">${duration}</td>
        <td style="text-align:center">${trail}</td>
        <td><button class="btn-close-trade" onclick="closeTrade(${t.id},'${t._symbol}')" title="Close trade">✕</button></td>
      </tr>`;
    }).join('');
  }

  // ── Closed Trades ──────────────────────────────────────────────────────
  function renderClosedTrades() {
    const tbody = document.getElementById('closedTbody');
    const count = document.getElementById('closedCount');
    const summary = document.getElementById('tradeSummary');
    if (!state.closed_trades) return;
    const trades = state.closed_trades;
    count.textContent = `(${trades.length})`;

    // Win/Loss summary
    const wins = trades.filter(t => t.pnl > 0);
    const losses = trades.filter(t => t.pnl < 0);
    const totalPnl = trades.reduce((s, t) => s + (t.pnl || 0), 0);
    const avgWin = wins.length > 0 ? (wins.reduce((s, t) => s + t.pnl, 0) / wins.length) : 0;
    const avgLoss = losses.length > 0 ? (losses.reduce((s, t) => s + t.pnl, 0) / losses.length) : 0;
    const bigWin = wins.length > 0 ? Math.max(...wins.map(t => t.pnl)) : 0;
    const bigLoss = losses.length > 0 ? Math.min(...losses.map(t => t.pnl)) : 0;

    summary.innerHTML = trades.length > 0
      ? `<span class="green">${wins.length}W</span> <span class="red">${losses.length}L</span>`
        + ` · AvgWin <span class="green">$${avgWin.toFixed(2)}</span>`
        + ` · AvgLoss <span class="red">$${avgLoss.toFixed(2)}</span>`
        + ` · Best <span class="green">+$${bigWin.toFixed(2)}</span>`
        + ` · Worst <span class="red">$${bigLoss.toFixed(2)}</span>`
        + ` · Total <span style="color:${totalPnl >= 0 ? 'var(--green)' : 'var(--red)'}">$${totalPnl.toFixed(2)}</span>`
      : 'No trades yet';

    const rows = trades.slice(-50).reverse().map(t => {
      const pnl = t.pnl || 0;
      const pnlClass = pnl > 0 ? 'pnl-positive' : pnl < 0 ? 'pnl-negative' : 'pnl-neutral';
      const reason = t.close_reason || t.reason || '';
      const reasonClass = reason === 'TP' ? 'close-tp' : reason === 'SL' ? 'close-sl' : reason === 'TRAILING' ? 'close-trailing' : reason === 'MANUAL' ? 'close-manual' : '';
      const pnlStr = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
      const fees = t.total_fees || t.entry_fee || 0;
      const risk = t.risk_amount || (t.notional ? t.notional * 0.02 : 0);
      const rr = risk > 0 ? (pnl / risk).toFixed(2) : '—';
      const side = t.side || (t.direction === 1 ? 'LONG' : t.direction === -1 ? 'SHORT' : '—');

      return `<tr>
        <td>${t.symbol||''}</td>
        <td>#${t.id||''}</td>
        <td class="${side === 'LONG' ? 'green' : 'red'}">${side}</td>
        <td>$${t.entry_price ? t.entry_price.toFixed(t.entry_price < 1 ? 6 : 2) : '—'}</td>
        <td>$${t.close_price ? t.close_price.toFixed(t.close_price < 1 ? 6 : 2) : '—'}</td>
        <td style="font-size:9px;color:var(--text-muted)">${t.pattern_type||'—'}</td>
        <td class="${pnlClass}">$${pnlStr}</td>
        <td style="color:var(--text-muted);font-size:9px;">${rr}x</td>
        <td class="${reasonClass}">${reason}</td>
        <td style="color:var(--text-muted);font-size:9px;">$${fees.toFixed(2)}</td>
        <td style="font-size:9px;color:var(--text-muted)">${t.close_time ? t.close_time.split(' ')[1] : ''}</td>
      </tr>`;
    }).join('');
    tbody.innerHTML = rows;
  }

  // ── Log ─────────────────────────────────────────────────────────────────
  function renderLog() {
    const el = document.getElementById('eventLog');
    if (!state.event_log) return;
    const lines = state.event_log.slice(-30).map(e =>
      `<div class="event-line"><span class="event-time">${e.time||''}</span><span class="event-tag ${e.tag||'INFO'}">${e.tag||'INFO'}</span><span class="event-msg">${escapeHtml(e.msg||'')}</span></div>`
    ).join('');
    el.innerHTML = lines;
    const container = document.getElementById('eventLogContainer');
    container.scrollTop = container.scrollHeight;
  }

  // ── Self-Learning Panel ─────────────────────────────────────────────────
  function renderLearnPanel() {
    if (!state.self_learning) return;
    const sl = state.self_learning;
    const m = sl.metrics || {};

    document.getElementById('learnSharpe').textContent = m.sharpe_ratio ? m.sharpe_ratio.toFixed(2) : '0.00';
    document.getElementById('sharpeFill').style.width = (m.goal_sharpe_progress||0) + '%';
    document.getElementById('learnWinrate').textContent = (m.win_rate||0) + '%';
    document.getElementById('winrateFill').style.width = (m.goal_winrate_progress||0) + '%';
    document.getElementById('learnReturn').textContent = (m.goal_return_progress||0) + '%';
    document.getElementById('returnFill').style.width = (m.goal_return_progress||0) + '%';
    document.getElementById('learnDD').textContent = (m.goal_dd_pct||0) + '%';
    document.getElementById('ddFill').style.width = Math.min(100, (m.goal_dd_pct||0) * 5) + '%';

    document.getElementById('statPF').textContent = m.profit_factor ? m.profit_factor.toFixed(2) : '0.00';
    document.getElementById('statExp').textContent = '$' + ((m.expectancy||0)).toFixed(2);
    document.getElementById('statAvgWin').textContent = '$' + (m.avg_win||0).toFixed(2);
    document.getElementById('statAvgLoss').textContent = '$' + (m.avg_loss||0).toFixed(2);
    document.getElementById('statBestPat').textContent = m.best_pattern || '—';
    document.getElementById('statWorstPat').textContent = m.worst_pattern || '—';
    document.getElementById('statTotalTrades').textContent = m.total_trades||0;

    const p = sl.active_params || {};
    document.getElementById('paramWick').textContent = p.wick_ratio !== null && p.wick_ratio !== undefined ? p.wick_ratio.toFixed(2) : 'default';
    document.getElementById('paramATR').textContent = p.entry_threshold !== null && p.entry_threshold !== undefined ? p.entry_threshold.toFixed(2) : 'default';
    document.getElementById('paramRR').textContent = p.rr_ratio !== null && p.rr_ratio !== undefined ? p.rr_ratio.toFixed(2) : 'default';
    document.getElementById('paramRisk').textContent = p.risk_pct !== null && p.risk_pct !== undefined ? (p.risk_pct*100).toFixed(1)+'%' : 'default';

    document.getElementById('hypoText').textContent = sl.hypothesis || 'Waiting for enough trades...';
    document.getElementById('learnVersion').textContent = `v${sl.param_version||0} — ${(sl.param_history||[]).length} tunings`;

    const badge = document.getElementById('learnStatusBadge');
    if (badge) {
      badge.textContent = sl.enabled ? 'ACTIVE' : 'PAUSED';
      badge.style.background = sl.enabled ? 'rgba(0,200,83,0.15)' : 'rgba(255,82,82,0.1)';
      badge.style.color = sl.enabled ? 'var(--green)' : 'var(--red)';
    }

    const btn = document.getElementById('learnBtn');
    if (btn) btn.style.opacity = sl.enabled ? '1' : '0.4';
  }

  // ── Conditions History ─────────────────────────────────────────────────
  let conditionsCollapsed = false;

  function fetchConditions() {
    fetch('/api/conditions?limit=100')
      .then(r => r.json())
      .then(conds => renderConditions(conds))
      .catch(() => {});
  }

  function renderConditions(conds) {
    const tbody = document.getElementById('conditionsTbody');
    const count = document.getElementById('conditionsCount');
    if (!tbody || !conds) return;
    count.textContent = `(${conds.length})`;

    tbody.innerHTML = conds.slice(-80).reverse().map(c => {
      const sig = c.signal || '—';
      const sigClass = sig === 'LONG' ? 'green' : sig === 'SHORT' ? 'red' : '';
      const score = c.score || 0;
      const tp = c.tp || 0;
      const sl = c.sl || 0;
      const evType = c.event_type || '';
      const evClass = evType === 'SIGNAL' ? 'yellow' : evType === 'TRADE_OPEN' ? 'green' : evType === 'TRADE_CLOSE' ? '' : '';

      return `<tr style="font-size:9px;">
        <td style="color:var(--text-muted)">${(c.timestamp||'').split(' ')[1]||''}</td>
        <td class="${evClass}" style="font-weight:600">${evType}</td>
        <td>${c.symbol||''}</td>
        <td class="${sigClass}">${sig}</td>
        <td>${score}</td>
        <td>${tp ? (tp < 1 ? tp.toFixed(6) : tp.toFixed(2)) : '—'}</td>
        <td>${sl ? (sl < 1 ? sl.toFixed(6) : sl.toFixed(2)) : '—'}</td>
        <td>${c.regime||'—'}</td>
        <td>${c.vol_label||'—'}</td>
        <td>${c.atr ? (c.atr < 1 ? c.atr.toFixed(6) : c.atr.toFixed(2)) : '—'}</td>
        <td>${c.rsi||'—'}</td>
        <td>${c.capital ? '$'+c.capital.toFixed(0) : '—'}</td>
        <td>${c.open_trades_count||0}</td>
        <td>${c.total_trades||0}</td>
        <td>${c.win_rate ? c.win_rate+'%' : '—'}</td>
        <td>${c.profit_factor||'—'}</td>
        <td style="color:var(--accent);font-size:8px;">${(c.best_pattern||'—').substring(0,8)}</td>
        <td style="color:var(--red-dim);font-size:8px;">${(c.worst_pattern||'—').substring(0,8)}</td>
        <td>${c.wick_ratio||'—'}</td>
        <td>${c.entry_threshold||'—'}</td>
        <td>${c.rr_ratio||'—'}</td>
        <td>${c.risk_pct ? (c.risk_pct*100).toFixed(1) : '—'}</td>
      </tr>`;
    }).join('');
  }

  window.toggleConditions = function() {
    conditionsCollapsed = !conditionsCollapsed;
    const body = document.getElementById('conditionsBody');
    const toggle = document.getElementById('conditionsToggle');
    if (body) body.style.display = conditionsCollapsed ? 'none' : '';
    if (toggle) toggle.textContent = conditionsCollapsed ? '▶' : '▼';
    if (!conditionsCollapsed) fetchConditions();
  };

  // ── Actions ─────────────────────────────────────────────────────────────
  window.addSymbol = function() {
    const sel = document.getElementById('symbolSelect');
    const sym = sel.value;
    if (!sym) return toast('Select a symbol first', 'info');
    fetch('/api/symbol/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol: sym})
    }).then(r => r.json()).then(d => {
      if (d.status === 'ok') { toast(`Added ${sym}`, 'success'); refreshState(); }
      else toast(d.message || 'Failed', 'error');
    }).catch(() => toast('Request failed', 'error'));
  };

  window.removeSymbol = function(sym) {
    fetch('/api/symbol/remove', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol: sym})
    }).then(r => r.json()).then(d => {
      if (d.status === 'ok') { toast(`Removed ${sym}`, 'info'); refreshState(); }
      else toast(d.message || 'Failed', 'error');
    }).catch(() => toast('Request failed', 'error'));
  };

  window.resetAll = function() {
    if (!confirm('Reset all accounts to initial capital?')) return;
    fetch('/api/reset', {method: 'POST'})
      .then(r => r.json()).then(d => {
        toast(d.message || 'Reset complete', 'success');
        refreshState();
      }).catch(() => toast('Reset failed', 'error'));
  };

  window.toggleLearn = function() {
    const current = state && state.self_learning ? state.self_learning.enabled : true;
    fetch('/api/self-learn/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enable: !current})
    }).then(r => r.json()).then(d => {
      toast(d.enabled ? 'Self-learning enabled' : 'Self-learning disabled', 'info');
      refreshState();
    }).catch(() => toast('Failed', 'error'));
  };

  // ── Close Trade ──────────────────────────────────────────────────────────
  window.closeTrade = function(tradeId, symbol) {
    if (!confirm(`Close trade #${tradeId} (${symbol})?`)) return;
    fetch('/api/trade/close', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({trade_id: tradeId, symbol: symbol})
    }).then(r => r.json()).then(d => {
      if (d.status === 'ok') {
        toast(d.message || `Trade #${tradeId} closed`, 'success');
        refreshState();
      } else {
        toast(d.message || 'Failed to close trade', 'error');
      }
    }).catch(() => toast('Close request failed', 'error'));
  };

  // ── Helpers ─────────────────────────────────────────────────────────────
  function toast(msg, type) {
    const container = document.getElementById('toastContainer');
    const el = document.createElement('div');
    el.className = `toast ${type||'info'}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.3s'; setTimeout(() => el.remove(), 300); }, 3000);
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Boot ────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', init);

})();

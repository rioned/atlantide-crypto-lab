/* ════════════════════════════════════════════════════════════════════════════
   ATLANTIDE CRYPTO LAB — Professional Dashboard JS
   SSE streaming | Price flash | TradingView-inspired | Fluid grid
   ════════════════════════════════════════════════════════════════════════════ */

let symOrder = [];
let allSymbols = [];
let userTzOffset = 0;
let notifiedSessions = {};
let sessionTimers = {};
let eventSource = null;
let lastPrices = {};       // sym → price (for flash detection)
let lastState = null;      // cached /api/state for fallback
let stateTimer = null;

// ─── Init ────────────────────────────────────────────────────────

async function init() {
  userTzOffset = -(new Date().getTimezoneOffset() / 60);
  await requestNotificationPermission();
  await loadSymbolList();

  // Initial full state load
  const resp = await fetch('/api/state?tz=' + userTzOffset);
  if (resp.ok) {
    lastState = await resp.json();
    renderFull(lastState);
  }

  document.getElementById('loadingOverlay').classList.remove('active');

  // Open SSE stream for real-time updates
  connectSSE();

  // Fallback: poll every 5s for non-SSE data (sessions, trades, logs)
  stateTimer = setInterval(pollState, 5000);

  // Clock ticker
  setInterval(() => {
    const now = new Date();
    document.getElementById('clock').textContent = now.toTimeString().split(' ')[0];
  }, 1000);
}

// ─── SSE Streaming ──────────────────────────────────────────────

function connectSSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource('/api/stream');

  eventSource.addEventListener('connected', () => {
    console.log('SSE connected');
    document.getElementById('livePill').textContent = '● LIVE';
    document.getElementById('livePill').style.background = 'var(--green-bg)';
    document.getElementById('livePill').style.color = 'var(--green)';
  });

  eventSource.addEventListener('ticker', (e) => {
    const data = JSON.parse(e.data);
    handleTickerUpdate(data);
  });

  eventSource.addEventListener('kline', (e) => {
    const data = JSON.parse(e.data);
    handleKlineUpdate(data);
  });

  eventSource.addEventListener('ping', () => {
    // Keep connection alive — heartbeat received
  });

  eventSource.onerror = () => {
    console.log('SSE disconnected, reconnecting in 3s...');
    document.getElementById('livePill').textContent = '○ RECONNECTING';
    document.getElementById('livePill').style.background = 'var(--yellow-bg)';
    document.getElementById('livePill').style.color = 'var(--yellow)';
    setTimeout(connectSSE, 3000);
  };
}

// ─── Real-time Handlers ─────────────────────────────────────────

function handleTickerUpdate(data) {
  const sym = data.symbol;
  const price = data.price;
  const prevPrice = lastPrices[sym] || price;

  // Update price display with flash
  const priceEl = document.getElementById('price-' + sym);
  if (priceEl) {
    const priceDec = price > 1000 ? 2 : price > 1 ? 4 : 6;
    priceEl.textContent = '$' + price.toFixed(priceDec);

    // Flash animation
    if (price > prevPrice) {
      priceEl.classList.remove('flash-down');
      priceEl.classList.add('flash-up');
      setTimeout(() => priceEl.classList.remove('flash-up'), 400);
      priceEl.style.color = 'var(--green)';
    } else if (price < prevPrice) {
      priceEl.classList.remove('flash-up');
      priceEl.classList.add('flash-down');
      setTimeout(() => priceEl.classList.remove('flash-down'), 400);
      priceEl.style.color = 'var(--red)';
    }
  }

  // Update change %
  const chgEl = document.getElementById('change-' + sym);
  if (chgEl && data.change_pct !== undefined) {
    const chg = data.change_pct;
    const chgClass = chg >= 0 ? 'var(--green)' : 'var(--red)';
    chgEl.textContent = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';
    chgEl.style.color = chgClass;
  }

  lastPrices[sym] = price;
}

function handleKlineUpdate(data) {
  const sym = data.symbol;
  // Only redraw chart on closed candles to avoid flicker
  if (data.is_closed && lastState) {
    // Update cached candle data
    const symData = lastState.data[sym];
    if (symData) {
      const cList = symData.candles_5m || [];
      cList.push(data.candle);
      if (cList.length > 60) cList.shift();
      drawMiniChart(sym, cList, symData.manipulation, symData.signal_state);
    }
  }
}

// ─── Poll state for non-SSE data ────────────────────────────────

async function pollState() {
  try {
    const resp = await fetch('/api/state?tz=' + userTzOffset);
    if (!resp.ok) return;
    lastState = await resp.json();
    updateSessions(lastState.market_sessions);
    updateClosedTrades(lastState.closed_trades);
    updateEventLog(lastState.event_log);
  } catch (e) { /* silent */ }
}

// ─── Full Render ─────────────────────────────────────────────────

function renderFull(state) {
  updateTopbarStats(state.account);
  updateSessions(state.market_sessions);
  updateSelector(state);
  buildSymbolPanels(state);
  updateClosedTrades(state.closed_trades);
  updateEventLog(state.event_log);
  document.getElementById('symCount').textContent =
    (state.symbols || []).length + ' symbols';

  // Track initial prices for flash detection
  for (const sym of (state.symbols || [])) {
    const d = (state.data || {})[sym] || {};
    const tick = d.ticker || {};
    if (tick.price) lastPrices[sym] = tick.price;
  }
}

// ─── Top Bar Stats ───────────────────────────────────────────────

function updateTopbarStats(acct) {
  if (!acct) return;
  const pnl = acct.total_pnl || 0;
  const pnlClass = pnl >= 0 ? 'var(--green)' : 'var(--red)';
  const pnlSign = pnl >= 0 ? '+' : '';

  document.getElementById('topbarStats').innerHTML =
    '<div class="topbar-stat">' +
    '<div class="label">Balance</div>' +
    '<div class="value" style="color:' + pnlClass + '">$' + (acct.balance || 0).toFixed(2) + '</div>' +
    '</div>' +
    '<div class="topbar-stat">' +
    '<div class="label">PnL</div>' +
    '<div class="value" style="color:' + pnlClass + '">' + pnlSign + '$' + pnl.toFixed(2) + '</div>' +
    '</div>' +
    '<div class="topbar-stat">' +
    '<div class="label">Win Rate</div>' +
    '<div class="value" style="color:var(--text-primary)">' + (acct.winrate || 0).toFixed(1) + '%</div>' +
    '</div>' +
    '<div class="topbar-stat">' +
    '<div class="label">Trades</div>' +
    '<div class="value" style="color:var(--text-primary)">' + (acct.total_trades || 0) + '</div>' +
    '</div>' +
    '<div class="topbar-stat">' +
    '<div class="label">Max DD</div>' +
    '<div class="value" style="color:var(--red)">-' + (acct.max_drawdown || 0).toFixed(2) + '%</div>' +
    '</div>';
}

// ─── Sessions ───────────────────────────────────────────────────

function updateSessions(sessions) {
  if (!sessions || sessions.length === 0) return;

  for (const id of Object.keys(sessionTimers)) {
    clearInterval(sessionTimers[id]);
    delete sessionTimers[id];
  }

  let html = '';
  for (const s of sessions) {
    let countdownSec = s.is_open ? s.time_until_close : s.time_until_open;
    const display = formatCountdown(Math.max(0, countdownSec));
    const isAlert = !s.is_open && countdownSec <= 600 && countdownSec > 0;

    let cardClass = '', countClass = 'IDLE', badgeClass = 'CLOSED', badgeText = 'CLOSED';

    if (s.is_open) {
      cardClass = 'OPEN'; countClass = 'OPEN';
      badgeClass = 'OPEN'; badgeText = '● OPEN';
      notifiedSessions[s.id] = false;
    } else if (isAlert) {
      cardClass = 'ALERT'; countClass = 'ALERT';
      badgeClass = 'UPCOMING'; badgeText = '⏰ SOON';
    } else {
      badgeClass = 'UPCOMING'; badgeText = 'UPCOMING';
    }

    html += '<div class="session-card ' + cardClass + '" id="sess-' + s.id + '">';
    html += '<div class="sess-emoji">' + s.emoji + '</div>';
    html += '<div class="sess-name">' + s.name + '</div>';
    html += '<div class="sess-countdown ' + countClass + '" id="cd-' + s.id + '">' + display + '</div>';
    html += '<div class="sess-times">' + s.open_local + ' – ' + s.close_local + '</div>';
    html += '<span class="sess-badge ' + badgeClass + '">' + badgeText + '</span>';
    html += '</div>';

    if (isAlert && !notifiedSessions[s.id]) {
      notifiedSessions[s.id] = true;
      showToast(s.name, s.open_local);
      sendBrowserNotification(s);
    }

    startCountdownTimer(s);
  }
  document.getElementById('sessionsBar').innerHTML = html;
}

function startCountdownTimer(session) {
  let remaining = session.is_open ? session.time_until_close : session.time_until_open;
  if (remaining <= 0 && !session.is_open) remaining = 86400;

  sessionTimers[session.id] = setInterval(() => {
    remaining--;
    if (remaining < 0) {
      clearInterval(sessionTimers[session.id]);
      delete sessionTimers[session.id];
      return;
    }
    const el = document.getElementById('cd-' + session.id);
    if (el) el.textContent = formatCountdown(Math.max(0, remaining));
  }, 1000);
}

function formatCountdown(totalSec) {
  if (totalSec <= 0) return '00:00:00';
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  return String(h).padStart(2, '0') + ':' +
    String(m).padStart(2, '0') + ':' +
    String(s).padStart(2, '0');
}

function showToast(sessionName, openTime) {
  const container = document.getElementById('toastContainer');
  const toastId = 'toast-' + Date.now();
  const toast = document.createElement('div');
  toast.className = 'toast'; toast.id = toastId;
  toast.innerHTML = '<div class="toast-title">🔔 MARKET OPENING</div>' +
    '<div class="toast-body"><b>' + sessionName + '</b> opens in <10 min at ' + openTime + '</div>' +
    '<span class="toast-dismiss" onclick="dismissToast(\'' + toastId + '\')">×</span>';
  container.appendChild(toast);
  setTimeout(() => dismissToast(toastId), 10000);
}

function dismissToast(toastId) {
  const el = document.getElementById(toastId);
  if (el) { el.style.opacity = '0'; el.style.transition = 'opacity 0.3s';
    setTimeout(() => el.remove(), 300); }
}

function sendBrowserNotification(session) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    const n = new Notification('🔔 ' + session.name + ' Opening Soon', {
      body: 'Opens in less than 10 minutes at ' + session.open_local,
      tag: 'session-' + session.id, requireInteraction: false,
    });
    setTimeout(() => n.close(), 8000);
  } catch (e) {}
}

// ─── Notifications permission ───────────────────────────────────

async function requestNotificationPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted' || Notification.permission === 'denied') return;
  try { await Notification.requestPermission(); } catch (e) {}
}

// ─── Symbol List ────────────────────────────────────────────────

async function loadSymbolList() {
  try {
    const resp = await fetch('/api/symbols');
    if (resp.ok) allSymbols = await resp.json();
  } catch (e) {}
}

// ─── Selector ───────────────────────────────────────────────────

function updateSelector(state) {
  const syms = state.symbols || [];
  symOrder = syms;

  let chipsHtml = '';
  for (const sym of syms) {
    const display = (state.data[sym] || {}).display || sym;
    chipsHtml += '<span class="active-chip">' + display +
      ' <span class="remove-chip" onclick="removeSymbol(\'' + sym + '\')" title="Remove">×</span></span>';
  }
  document.getElementById('activeChips').innerHTML = chipsHtml;

  const select = document.getElementById('symbolSelect');
  const currentVal = select.value;
  select.innerHTML = '<option value="">+ Add Symbol</option>';
  const activeSet = new Set(syms);
  for (const s of allSymbols) {
    if (!activeSet.has(s.code)) {
      select.innerHTML += '<option value="' + s.code + '">' + s.display + '</option>';
    }
  }
  if (currentVal && activeSet.has(currentVal)) select.value = '';
  else if (currentVal) select.value = currentVal;
}

// ─── Symbol Panels ──────────────────────────────────────────────

function buildSymbolPanels(state) {
  const syms = state.symbols || [];
  const data = state.data || {};

  const grid = document.getElementById('symbolGrid');
  if (syms.length === 0) {
    grid.innerHTML = '<div class="empty-state">No active symbols. Add one above.</div>';
    return;
  }

  grid.innerHTML = '';

  for (const sym of syms) {
    const d = data[sym] || {};
    const tick = d.ticker || {};
    const sig = d.signal_state || {};
    const ind = d.indicators || {};
    const manip = d.manipulation;
    const openT = d.open_trades || [];
    const c5 = d.candles_5m || [];
    const cap = d.capital || {};
    const openPnl = d.open_pnl || 0;

    const price = tick.price || 0;
    const chg = tick.change_pct || 0;
    const priceDec = price > 1000 ? 2 : price > 1 ? 4 : 6;
    const chgColor = chg >= 0 ? 'var(--green)' : 'var(--red)';

    // Signal status
    let sigClass = 'IDLE', sigText = '⏳ WAITING';
    if (sig.signal && sig.signal !== 'None') {
      sigClass = sig.signal;
      sigText = '🔥 ' + sig.signal + ' — ' + (sig.pattern_type || '') +
        ' | TP=$' + (sig.tp || 0).toFixed(priceDec) +
        ' SL=$' + (sig.sl || 0).toFixed(priceDec);
    } else if (manip) {
      sigClass = 'MANIPULATION';
      sigText = '⚠ MANIP: ' + manip.direction +
        ' Range=$' + (manip.range || 0).toFixed(priceDec);
    }

    const pnlClass = openPnl >= 0 ? 'var(--green)' : 'var(--red)';
    const pnlSign = openPnl >= 0 ? '+' : '';
    const capBal = cap.balance || 500;
    const capPnl = cap.total_pnl || 0;
    const capClass = capPnl >= 0 ? 'var(--green)' : 'var(--red)';
    const capSign = capPnl >= 0 ? '+' : '';
    const display = d.display || sym;

    const html =
      '<div class="sym-panel" id="panel-' + sym + '">' +
        '<div class="sym-header">' +
          '<div class="sym-name-section">' +
            '<div class="sym-pair" style="color:' + chgColor + '">' + display + '</div>' +
            '<div class="sym-change" style="color:' + chgColor + '" id="change-' + sym + '">' +
              (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</div>' +
            '<div class="sym-capital">' +
              '<span>Cap: <span class="cap-val" style="color:' + capClass + '">$' + capBal.toFixed(2) + '</span></span>' +
              '<span style="color:' + capClass + ';font-size:9px;">(' + capSign + '$' + capPnl.toFixed(2) + ')</span>' +
              '<span style="color:var(--accent);font-size:9px;">10%=$' + (capBal * 0.10).toFixed(2) + '</span>' +
              '<span style="color:var(--text-muted);font-size:9px;">' +
                (cap.total_trades || 0) + 'T/' + (cap.winning_trades || 0) + 'W</span>' +
            '</div>' +
          '</div>' +
          '<div class="sym-price-section">' +
            '<div class="sym-price" id="price-' + sym + '" style="color:' + chgColor + '">$' +
              price.toFixed(priceDec) + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="sym-signal-bar ' + sigClass + '">' + sigText + '</div>' +
        '<div class="sym-stats">' +
          '<div class="sym-stat"><div class="stat-label">Daily ATR</div>' +
            '<div class="stat-value" style="color:var(--accent)">$' + (ind.daily_atr || 0).toFixed(priceDec) + '</div></div>' +
          '<div class="sym-stat"><div class="stat-label">Threshold</div>' +
            '<div class="stat-value" style="color:var(--yellow)">$' + (ind.daily_atr_threshold || 0).toFixed(priceDec) + '</div></div>' +
          '<div class="sym-stat"><div class="stat-label">5m ATR</div>' +
            '<div class="stat-value" style="color:var(--text-secondary)">$' + (ind['5m_atr14'] || 0).toFixed(priceDec) + '</div></div>' +
          '<div class="sym-stat"><div class="stat-label">Vol/24h</div>' +
            '<div class="stat-value" style="color:var(--text-secondary)">$' + fmtVolume(tick.volume || 0) + '</div></div>' +
          '<div class="sym-stat"><div class="stat-label">Open PnL</div>' +
            '<div class="stat-value" style="color:' + pnlClass + '">' + openT.length + 'pos / ' + pnlSign + '$' + openPnl.toFixed(2) + '</div></div>' +
        '</div>' +
        '<div class="sym-chart"><canvas class="sym-canvas" id="chart-' + sym + '"></canvas></div>' +
        (openT.length > 0 ? renderOpenPositions(openT, priceDec) : '') +
        '<div class="sym-footer">' +
          '<button class="btn btn-danger btn-sm" onclick="resetSymbol(\'' + sym + '\')">Reset ' + display + '</button>' +
        '</div>' +
      '</div>';
    grid.innerHTML += html;
  }

  // Draw charts
  for (const sym of syms) {
    const d = data[sym] || {};
    drawMiniChart(sym, d.candles_5m || [], d.manipulation, d.signal_state || {});
  }
}

function renderOpenPositions(trades, priceDec) {
  let html = '<div class="sym-positions">';
  for (const t of trades) {
    const pnlS = (t.unrealized_pnl || 0) >= 0 ? '+' : '';
    const pnlC = (t.unrealized_pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)';
    html += '<div class="sym-pos-item">' +
      '<span>' + t.side + ' #' + t.id + ' @$' + (t.entry_price || 0).toFixed(priceDec) +
      ' N=$' + (t.notional || 250).toFixed(0) + '</span>' +
      '<span style="color:' + pnlC + '">' + pnlS + '$' + (t.unrealized_pnl || 0).toFixed(2) + '</span>' +
      '</div>';
  }
  html += '</div>';
  return html;
}

function fmtVolume(v) {
  if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(0);
}

// ─── Mini Candle Chart ──────────────────────────────────────────

function drawMiniChart(sym, c5, manip, sig) {
  const canvas = document.getElementById('chart-' + sym);
  if (!canvas || c5.length < 2) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width = canvas.offsetWidth;
  const H = canvas.height = 110;
  ctx.clearRect(0, 0, W, H);

  const ml = 35, mr = 8, mt = 6, mb = 14;
  const pw = W - ml - mr, ph = H - mt - mb;

  const prices = [];
  for (const c of c5) { prices.push(c.h, c.l); }
  let pmin = Math.min(...prices), pmax = Math.max(...prices);
  const pad = (pmax - pmin) * 0.06 || pmax * 0.01 || 1;
  pmin -= pad; pmax += pad;

  const xScale = (i) => ml + (i / (c5.length - 1)) * pw;
  const yScale = (p) => mt + (1 - (p - pmin) / (pmax - pmin)) * ph;
  const barW = Math.max(1, (pw / c5.length) * 0.65);

  // Grid lines
  ctx.strokeStyle = '#2a2e39'; ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {
    const y = mt + (i / 4) * ph;
    ctx.beginPath(); ctx.moveTo(ml, y); ctx.lineTo(W - mr, y); ctx.stroke();
  }

  // Candles
  for (let i = 0; i < c5.length; i++) {
    const c = c5[i]; const x = xScale(i);
    const oy = yScale(c.o), cy = yScale(c.c);
    const hy = yScale(c.h), ly = yScale(c.l);

    const isGreen = c.c >= c.o;
    ctx.strokeStyle = isGreen ? '#089981' : '#f23645'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, hy); ctx.lineTo(x, ly); ctx.stroke();

    ctx.fillStyle = isGreen ? '#089981' : '#f23645';
    const bodyTop = Math.min(oy, cy);
    const bodyH = Math.max(1, Math.abs(cy - oy));
    ctx.fillRect(x - barW / 2, bodyTop, barW, bodyH);
  }

  // Manipulation lines
  if (manip) {
    ctx.setLineDash([3, 3]); ctx.strokeStyle = '#ff9800'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(ml, yScale(manip.high)); ctx.lineTo(W - mr, yScale(manip.high)); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(ml, yScale(manip.low)); ctx.lineTo(W - mr, yScale(manip.low)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#ff9800'; ctx.font = '8px monospace'; ctx.textAlign = 'left';
    ctx.fillText('M:' + manip.direction, W - mr - 45, yScale(manip.high) - 2);
  }

  // TP/SL lines
  if (sig.signal && sig.tp > 0) {
    ctx.setLineDash([2, 4]);
    ctx.strokeStyle = '#089981'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(ml, yScale(sig.tp)); ctx.lineTo(W - mr, yScale(sig.tp)); ctx.stroke();
    ctx.fillStyle = '#089981'; ctx.font = '8px monospace'; ctx.textAlign = 'left';
    ctx.fillText('TP', W - mr - 18, yScale(sig.tp) - 2);

    ctx.strokeStyle = '#f23645';
    ctx.beginPath(); ctx.moveTo(ml, yScale(sig.sl)); ctx.lineTo(W - mr, yScale(sig.sl)); ctx.stroke();
    ctx.fillStyle = '#f23645';
    ctx.fillText('SL', W - mr - 18, yScale(sig.sl) - 2);
    ctx.setLineDash([]);
  }

  // Price labels
  ctx.fillStyle = '#5d606b'; ctx.font = '8px monospace'; ctx.textAlign = 'right';
  const dec = c5[0].o < 1 ? 5 : 2;
  ctx.fillText('$' + pmax.toFixed(dec), ml - 3, mt + 8);
  ctx.fillText('$' + pmin.toFixed(dec), ml - 3, mt + ph);
}

// ─── Closed Trades ──────────────────────────────────────────────

function updateClosedTrades(trades) {
  const tbody = document.getElementById('closedTbody');
  document.getElementById('closedCount').textContent = '(' + (trades || []).length + ' shown)';

  if (!trades || trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:16px;">No closed trades</td></tr>';
    return;
  }

  let html = '';
  for (const t of trades) {
    const pnlClass = (t.pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)';
    const pnlSign = (t.pnl || 0) >= 0 ? '+' : '';
    const resultBadge = (t.pnl || 0) >= 0 ? 'badge badge-win' : 'badge badge-loss';
    const resultText = (t.pnl || 0) >= 0 ? 'WIN' : 'LOSS';
    const sideBadge = t.side === 'LONG' ? 'badge badge-long' : 'badge badge-short';
    const priceDec = (t.close_price || t.entry_price || 0) > 1000 ? 2 : 4;

    html += '<tr>' +
      '<td style="color:var(--accent)">' + (t.symbol || '') + '</td>' +
      '<td>#' + t.id + '</td>' +
      '<td><span class="' + sideBadge + '">' + t.side + '</span></td>' +
      '<td>$' + (t.entry_price || 0).toFixed(priceDec) + '</td>' +
      '<td>$' + (t.close_price || 0).toFixed(priceDec) + '</td>' +
      '<td>$' + (t.notional || 250).toFixed(0) + '</td>' +
      '<td style="color:' + pnlClass + '">' + pnlSign + '$' + (t.pnl || 0).toFixed(2) + '</td>' +
      '<td><span class="' + resultBadge + '">' + resultText + '</span> ' + (t.close_reason || '') + '</td>' +
      '<td style="font-size:9px;color:var(--text-muted)">' + (t.close_time || '') + '</td>' +
      '</tr>';
  }
  tbody.innerHTML = html;
}

// ─── Event Log ──────────────────────────────────────────────────

function updateEventLog(events) {
  const container = document.getElementById('eventLog');
  let html = '';
  for (const e of (events || [])) {
    html += '<div class="log-entry">' +
      '<span class="log-time">' + e.time + '</span>' +
      '<span class="log-tag-' + e.tag + '">[' + e.tag + ']</span> ' +
      (e.msg || '') + '</div>';
  }
  container.innerHTML = html;
  const scrollC = document.getElementById('eventLogContainer');
  if (scrollC) scrollC.scrollTop = scrollC.scrollHeight;
}

// ─── Symbol Add / Remove ────────────────────────────────────────

async function addSymbol() {
  const select = document.getElementById('symbolSelect');
  const sym = select.value;
  if (!sym) return;
  showLoading('Adding ' + sym + '...');
  try {
    const resp = await fetch('/api/symbol/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sym }),
    });
    const data = await resp.json();
    if (data.status === 'ok') {
      select.value = '';
      await loadSymbolList();
    } else { alert(data.message); }
  } catch (e) { alert('Add failed: ' + e); }
  hideLoading();
}

async function removeSymbol(sym) {
  if (!confirm('Remove ' + sym + ' from active trading?')) return;
  showLoading('Removing ' + sym + '...');
  try {
    const resp = await fetch('/api/symbol/remove', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sym }),
    });
    const data = await resp.json();
    if (data.status === 'ok') { await loadSymbolList(); }
    else { alert(data.message); }
  } catch (e) { alert('Remove failed: ' + e); }
  hideLoading();
}

// ─── Reset ──────────────────────────────────────────────────────

async function resetAll() {
  if (!confirm('Reset ALL accounts to $500 each? This clears ALL trade history.')) return;
  showLoading('Resetting all...');
  try { await fetch('/api/reset', { method: 'POST' }); } catch (e) {}
  hideLoading();
}

async function resetSymbol(sym) {
  if (!confirm('Reset ' + sym + ' capital to $500?')) return;
  showLoading('Resetting ' + sym + '...');
  try { await fetch('/api/reset?symbol=' + sym, { method: 'POST' }); } catch (e) {}
  hideLoading();
}

// ─── Loading ────────────────────────────────────────────────────

function showLoading(msg) {
  document.getElementById('loadingOverlay').classList.add('active');
  document.getElementById('loadingText').textContent = msg;
}
function hideLoading() {
  document.getElementById('loadingOverlay').classList.remove('active');
}

// ─── Start ──────────────────────────────────────────────────────
init();

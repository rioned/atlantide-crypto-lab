// ATLANTIDE — Multi-Symbol Dashboard JS
// Dynamic symbol management, per-symbol capital, 10% risk/trade
// Market session countdowns + 10-minute browser notifications
let refreshInterval = null;
let symOrder = [];
let allSymbols = [];  // full list from /api/symbols
let userTzOffset = 0;       // hours from UTC (auto-detected)
let notifiedSessions = {};  // session_id -> true (prevent re-notify)
let sessionTimers = {};     // session_id -> setInterval handle

// ─── Init ────────────────────────────────────────────────────────
async function init() {
  userTzOffset = -(new Date().getTimezoneOffset() / 60);  // hours from UTC
  await requestNotificationPermission();
  await loadSymbolList();
  await refresh();
  refreshInterval = setInterval(refresh, 2000);
}

// ─── Notifications ──────────────────────────────────────────────
async function requestNotificationPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') return;
  if (Notification.permission === 'denied') return;
  try {
    const result = await Notification.requestPermission();
    console.log('Notification permission:', result);
  } catch (e) { console.log('Notification not supported'); }
}

// ─── Market Sessions ───────────────────────────────────────────
function updateMarketSessions(sessions, utcTime) {
  const bar = document.getElementById('sessionsBar');
  if (!sessions || sessions.length === 0) return;

  // Kill old countdown timers
  for (const id of Object.keys(sessionTimers)) {
    clearInterval(sessionTimers[id]);
    delete sessionTimers[id];
  }

  const now = new Date();

  let html = '';
  for (const s of sessions) {
    let countdownSec = s.is_open ? s.time_until_close : s.time_until_open;
    const display = formatCountdown(Math.max(0, countdownSec));
    const isAlert = !s.is_open && countdownSec <= 600 && countdownSec > 0;

    let cardClass = '';
    let countClass = 'IDLE';
    let badgeClass = 'CLOSED';
    let badgeText = 'CLOSED';

    if (s.is_open) {
      cardClass = 'OPEN';
      countClass = 'OPEN';
      badgeClass = 'OPEN';
      badgeText = '● OPEN';
      // Reset notification flag when session opens
      notifiedSessions[s.id] = false;
    } else if (isAlert) {
      cardClass = 'ALERT';
      countClass = 'ALERT';
      badgeClass = 'UPCOMING';
      badgeText = '⏰ OPENING SOON';
    } else {
      countClass = 'IDLE';
      badgeClass = 'UPCOMING';
      badgeText = 'UPCOMING';
    }

    const localLabel = userTzOffset === 0 ? 'UTC' :
      'UTC' + (userTzOffset >= 0 ? '+' : '') + userTzOffset.toFixed(1);

    html += '<div class="session-card ' + cardClass + '" id="sess-' + s.id + '">';
    html += '<div class="sess-emoji">' + s.emoji + '</div>';
    html += '<div class="sess-name">' + s.name + '</div>';
    html += '<div class="sess-countdown ' + countClass + '" id="cd-' + s.id + '">' + display + '</div>';
    html += '<div class="sess-times">' + s.open_local + ' – ' + s.close_local +
      ' <span style="color:#445566;">(' + localLabel + ')</span></div>';
    html += '<div class="sess-times" style="margin-top:2px;">' + s.open_utc + ' – ' + s.close_utc + '</div>';
    html += '<span class="sess-badge ' + badgeClass + '">' + badgeText + '</span>';
    html += '</div>';

    // Fire 10-min notification
    if (isAlert && !notifiedSessions[s.id]) {
      notifiedSessions[s.id] = true;
      showToast(s.name, s.open_local + ' (' + localLabel + ')');
      sendBrowserNotification(s);
    }

    // Start 1-second countdown timer for this card
    startCountdownTimer(s);
  }

  bar.innerHTML = html;
}

function startCountdownTimer(session) {
  let remaining = session.is_open ? session.time_until_close : session.time_until_open;
  if (remaining <= 0 && !session.is_open) remaining = 86400; // next day

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
  toast.className = 'toast';
  toast.id = toastId;
  toast.innerHTML = '<div class="toast-title">🔔 MARKET OPENING</div>' +
    '<div class="toast-body"><b>' + sessionName + '</b> opens in <10 min at ' + openTime + '</div>' +
    '<span class="toast-dismiss" onclick="dismissToast(\'' + toastId + '\')">×</span>';
  container.appendChild(toast);

  // Auto-dismiss after 10 seconds
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
      body: 'Opens in less than 10 minutes at ' + session.open_local +
        ' (' + session.open_utc + ')',
      icon: '/static/favicon.ico',
      tag: 'session-' + session.id,
      requireInteraction: false,
    });
    setTimeout(() => n.close(), 8000);
  } catch (e) { console.log('Notification error:', e); }
}

// ─── Load full symbol list for selector ─────────────────────────
async function loadSymbolList() {
  try {
    const resp = await fetch('/api/symbols');
    if (resp.ok) allSymbols = await resp.json();
  } catch (e) { console.error('Symbol list error:', e); }
}

// ─── Main Refresh ──────────────────────────────────────────────
async function refresh() {
  try {
    const resp = await fetch('/api/state?tz=' + userTzOffset);
    if (!resp.ok) return;
    const state = await resp.json();
    updateHeader(state);
    updateMarketSessions(state.market_sessions, state.utc_time);
    updateAccount(state.account);
    updateSelector(state);
    buildSymbolPanels(state);
    updateClosedTrades(state.closed_trades);
    updateEventLog(state.event_log);
    document.getElementById('loadingOverlay').classList.remove('active');
  } catch (e) {
    console.error('Refresh error:', e);
  }
}

// ─── Header ────────────────────────────────────────────────────
function updateHeader(state) {
  const now = new Date();
  document.getElementById('clock').textContent = now.toTimeString().split(' ')[0];
  document.getElementById('livePill').textContent = '● LIVE';
  document.getElementById('livePill').style.background = '#00ff88';
  const count = (state.symbols || []).length;
  document.getElementById('symCount').textContent = count + ' symbols';
}

// ─── Account ───────────────────────────────────────────────────
function updateAccount(acct) {
  document.getElementById('valBalance').textContent = '$' + (acct.balance || 0).toFixed(2);
  const pnl = acct.total_pnl || 0;
  const pnlEl = document.getElementById('valTotalPnl');
  pnlEl.textContent = '$' + (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
  pnlEl.className = 'value ' + (pnl >= 0 ? 'green' : 'red');

  document.getElementById('valWinrate').textContent = (acct.winrate || 0).toFixed(1) + '%';
  document.getElementById('valPeak').textContent = '$' + (acct.peak || 0).toFixed(2);
  document.getElementById('valDD').textContent = '-' + (acct.max_drawdown || 0).toFixed(2) + '%';
  document.getElementById('valTradeCount').textContent = (acct.total_trades || 0);

  const balEl = document.getElementById('valBalance');
  balEl.className = 'value ' + ((acct.total_pnl || 0) >= 0 ? 'green' : 'red');
}

// ─── Symbol Selector ──────────────────────────────────────────
function updateSelector(state) {
  const syms = state.symbols || [];
  symOrder = syms;

  // Active chips
  const chipsDiv = document.getElementById('activeChips');
  let chipsHtml = '';
  for (const sym of syms) {
    const display = (state.data[sym] || {}).display || sym;
    chipsHtml += '<span class="active-chip">' + display +
      ' <span class="remove-chip" onclick="removeSymbol(\'' + sym + '\')" title="Remove">×</span></span>';
  }
  chipsDiv.innerHTML = chipsHtml;

  // Dropdown: show only inactive symbols
  const select = document.getElementById('symbolSelect');
  // Preserve selected value
  const currentVal = select.value;
  select.innerHTML = '<option value="">+ Add Symbol</option>';
  const activeSet = new Set(syms);
  for (const s of allSymbols) {
    if (!activeSet.has(s.code)) {
      select.innerHTML += '<option value="' + s.code + '">' + s.display + '</option>';
    }
  }
  if (currentVal && activeSet.has(currentVal)) {
    select.value = '';  // was active, clear
  } else if (currentVal) {
    select.value = currentVal;
  }
}

// ─── Add Symbol ────────────────────────────────────────────────
async function addSymbol() {
  const select = document.getElementById('symbolSelect');
  const sym = select.value;
  if (!sym) return;
  showLoading('Adding ' + sym + '...');
  try {
    const resp = await fetch('/api/symbol/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sym })
    });
    const data = await resp.json();
    if (data.status === 'ok') {
      select.value = '';
      await loadSymbolList();
    } else {
      alert(data.message);
    }
  } catch (e) { alert('Add failed: ' + e); }
  hideLoading();
}

// ─── Remove Symbol ──────────────────────────────────────────────
async function removeSymbol(sym) {
  if (!confirm('Remove ' + sym + ' from active trading?')) return;
  showLoading('Removing ' + sym + '...');
  try {
    const resp = await fetch('/api/symbol/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sym })
    });
    const data = await resp.json();
    if (data.status === 'ok') {
      await loadSymbolList();
    } else {
      alert(data.message);
    }
  } catch (e) { alert('Remove failed: ' + e); }
  hideLoading();
}

// ─── Symbol Panels ────────────────────────────────────────────
function buildSymbolPanels(state) {
  const syms = state.symbols || [];
  const data = state.data || {};
  symOrder = syms;

  const grid = document.getElementById('symbolGrid');
  if (syms.length === 0) {
    grid.innerHTML = '<div class="no-data">No active symbols. Use the selector above to add some.</div>';
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

    // Determine status
    let statusClass = 'WAITING';
    let statusText = '⏳ WAITING';
    if (sig.signal && sig.signal !== 'None') {
      statusClass = sig.signal;
      statusText = '🔥 ' + sig.signal + ' — ' + (sig.pattern_type || '') +
        ' | TP=$' + (sig.tp || 0).toFixed(priceDec) + ' SL=$' + (sig.sl || 0).toFixed(priceDec);
    } else if (manip) {
      statusClass = 'MANIPULATION';
      statusText = '⚠ MANIP: ' + manip.direction + ' Range=$' + (manip.range || 0).toFixed(priceDec);
    }

    const pnlClass = openPnl >= 0 ? 'green' : 'red';
    const pnlSign = openPnl >= 0 ? '+' : '';
    const posCount = openT.length;

    // Capital PnL coloring
    const capBal = cap.balance || 500;
    const capPnl = cap.total_pnl || 0;
    const capClass = capPnl >= 0 ? 'green' : 'red';
    const capSign = capPnl >= 0 ? '+' : '';

    const display = d.display || sym;

    const html = `
      <div class="sym-panel" id="panel-${sym}">
        <div class="sym-header">
          <div>
            <div class="sym-name" style="color:${chg >= 0 ? '#00ff88' : '#ff3366'}">${display}</div>
            <div class="sym-change" style="color:${chg >= 0 ? '#00ff88' : '#ff3366'}">${(chg >= 0 ? '+' : '') + chg.toFixed(2)}%</div>
            <div class="sym-capital">
              Cap: <span class="cap-value" style="color:${capPnl >= 0 ? '#00ff88' : '#ff3366'}">$${capBal.toFixed(2)}</span>
              <span style="font-size:9px;color:${capPnl >= 0 ? '#00ff88' : '#ff3366'}">(${capSign}$${capPnl.toFixed(2)})</span>
              <span style="margin-left:8px;font-size:9px;color:#00d4ff;">10% risk=$${(capBal * 0.10).toFixed(2)}</span>
            </div>
          </div>
          <div>
            <div class="sym-price" style="color:${chg >= 0 ? '#00ff88' : '#ff3366'}">$${price.toFixed(priceDec)}</div>
            <div style="text-align:right;font-size:9px;color:#556677;">Risk/cap: ${((capBal * 0.10) / capBal * 100).toFixed(0)}%</div>
          </div>
        </div>
        <div class="sym-status ${statusClass}">${statusText}</div>
        <div class="sym-stats">
          <div class="sym-stat">
            <div class="slabel">Daily ATR</div>
            <div class="svalue" style="color:#00d4ff">$${(ind.daily_atr || 0).toFixed(priceDec)}</div>
          </div>
          <div class="sym-stat">
            <div class="slabel">Threshold</div>
            <div class="svalue" style="color:#ffcc00">$${(ind.daily_atr_threshold || 0).toFixed(priceDec)}</div>
          </div>
          <div class="sym-stat">
            <div class="slabel">5m ATR</div>
            <div class="svalue" style="color:#8899aa">$${(ind['5m_atr14'] || 0).toFixed(priceDec)}</div>
          </div>
          <div class="sym-stat">
            <div class="slabel">Cap/Wins</div>
            <div class="svalue" style="color:#8899aa">${cap.total_trades || 0}T / ${cap.winning_trades || 0}W</div>
          </div>
          <div class="sym-stat">
            <div class="slabel">Open PnL</div>
            <div class="svalue ${pnlClass}">${posCount}pos / ${pnlSign}$${openPnl.toFixed(2)}</div>
          </div>
        </div>
        <div class="sym-chart">
          <canvas class="sym-canvas" id="chart-${sym}" width="380" height="130"></canvas>
        </div>
        ${posCount > 0 ? renderOpenPositions(openT, priceDec) : ''}
        <div style="padding:4px 14px;border-top:1px solid #1e2a3a;display:flex;gap:6px;">
          <button class="btn btn-danger btn-sm" onclick="resetSymbol('${sym}')" style="margin-left:auto;">Reset ${display}</button>
        </div>
      </div>
    `;
    grid.innerHTML += html;
  }

  // Draw charts after DOM update
  for (const sym of syms) {
    const c5 = (data[sym] || {}).candles_5m || [];
    const manip = (data[sym] || {}).manipulation;
    const sig = (data[sym] || {}).signal_state || {};
    drawMiniChart(sym, c5, manip, sig);
  }
}

function renderOpenPositions(trades, priceDec) {
  let html = '<div class="sym-positions">';
  for (const t of trades) {
    const pnlSign = (t.unrealized_pnl || 0) >= 0 ? '+' : '';
    const pnlClass = (t.unrealized_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
    const notional = t.notional || 250;
    html += '<div class="sym-pos-item">';
    html += '<span>' + t.side + ' #' + t.id + ' @$' + (t.entry_price || 0).toFixed(priceDec) +
      ' N=$' + notional.toFixed(0) + '</span>';
    html += '<span class="' + pnlClass + '">' + pnlSign + '$' + (t.unrealized_pnl || 0).toFixed(2) + '</span>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

// ─── Mini Candle Chart ────────────────────────────────────────
function drawMiniChart(sym, c5, manip, sig) {
  const canvas = document.getElementById('chart-' + sym);
  if (!canvas || c5.length < 2) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width; const H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const ml = 35, mr = 8, mt = 8, mb = 16;
  const pw = W - ml - mr; const ph = H - mt - mb;

  const prices = [];
  for (const c of c5) { prices.push(c.h, c.l); }
  let pmin = Math.min(...prices); let pmax = Math.max(...prices);
  const pad = (pmax - pmin) * 0.06 || pmax * 0.01 || 1;
  pmin -= pad; pmax += pad;

  const xScale = (i) => ml + (i / (c5.length - 1)) * pw;
  const yScale = (p) => mt + (1 - (p - pmin) / (pmax - pmin)) * ph;
  const barW = Math.max(1, (pw / c5.length) * 0.65);

  // Grid
  ctx.strokeStyle = '#141a24'; ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {
    const y = mt + (i / 4) * ph;
    ctx.beginPath(); ctx.moveTo(ml, y); ctx.lineTo(W - mr, y); ctx.stroke();
  }

  // Candles
  for (let i = 0; i < c5.length; i++) {
    const c = c5[i]; const x = xScale(i);
    const oy = yScale(c.o), cy = yScale(c.c);
    const hy = yScale(c.h), ly = yScale(c.l);

    ctx.strokeStyle = c.c >= c.o ? '#00ff88' : '#ff3366'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, hy); ctx.lineTo(x, ly); ctx.stroke();

    ctx.fillStyle = c.c >= c.o ? '#00ff88' : '#ff3366';
    const bodyTop = Math.min(oy, cy);
    const bodyH = Math.max(1, Math.abs(cy - oy));
    ctx.fillRect(x - barW / 2, bodyTop, barW, bodyH);
  }

  // Manipulation lines
  if (manip) {
    ctx.setLineDash([3, 3]); ctx.strokeStyle = '#ffcc00'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(ml, yScale(manip.high)); ctx.lineTo(W - mr, yScale(manip.high)); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(ml, yScale(manip.low)); ctx.lineTo(W - mr, yScale(manip.low)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#ffcc00'; ctx.font = '8px Courier New'; ctx.textAlign = 'left';
    ctx.fillText('M:' + manip.direction, W - mr - 50, yScale(manip.high) - 2);
  }

  // TP/SL
  if (sig.signal && sig.tp > 0) {
    ctx.setLineDash([2, 4]);
    ctx.strokeStyle = '#00ff88'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(ml, yScale(sig.tp)); ctx.lineTo(W - mr, yScale(sig.tp)); ctx.stroke();
    ctx.fillStyle = '#00ff88'; ctx.font = '8px Courier New'; ctx.textAlign = 'left';
    ctx.fillText('TP', W - mr - 20, yScale(sig.tp) - 2);

    ctx.strokeStyle = '#ff3366';
    ctx.beginPath(); ctx.moveTo(ml, yScale(sig.sl)); ctx.lineTo(W - mr, yScale(sig.sl)); ctx.stroke();
    ctx.fillStyle = '#ff3366';
    ctx.fillText('SL', W - mr - 20, yScale(sig.sl) - 2);
    ctx.setLineDash([]);
  }

  // Price labels
  ctx.fillStyle = '#556677'; ctx.font = '8px Courier New'; ctx.textAlign = 'right';
  ctx.fillText('$' + pmax.toFixed(c5[0].o < 1 ? 5 : 2), ml - 3, mt + 8);
  ctx.fillText('$' + pmin.toFixed(c5[0].o < 1 ? 5 : 2), ml - 3, mt + ph);
}

// ─── Closed Trades ──────────────────────────────────────────────
function updateClosedTrades(trades) {
  const tbody = document.getElementById('closedTbody');
  document.getElementById('closedCount').textContent = '(' + trades.length + ' shown)';

  if (trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#556677;">No closed trades</td></tr>';
    return;
  }

  let html = '';
  for (const t of trades) {
    const pnlClass = (t.pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
    const pnlSign = (t.pnl || 0) >= 0 ? '+' : '';
    const resultBadge = (t.pnl || 0) >= 0 ? 'badge badge-win' : 'badge badge-loss';
    const resultText = (t.pnl || 0) >= 0 ? 'WIN' : 'LOSS';
    const sideBadge = t.side === 'LONG' ? 'badge badge-long' : 'badge badge-short';
    const sym = t.symbol || '';
    const priceDec = (t.close_price || t.entry_price || 0) > 1000 ? 2 : 4;
    const notional = t.notional || 250;

    html += '<tr>';
    html += '<td style="color:#00d4ff;">' + sym + '</td>';
    html += '<td>#' + t.id + '</td>';
    html += '<td><span class="' + sideBadge + '">' + t.side + '</span></td>';
    html += '<td>$' + (t.entry_price || 0).toFixed(priceDec) + '</td>';
    html += '<td>$' + (t.close_price || 0).toFixed(priceDec) + '</td>';
    html += '<td>$' + notional.toFixed(0) + '</td>';
    html += '<td class="' + pnlClass + '">' + pnlSign + '$' + (t.pnl || 0).toFixed(2) + '</td>';
    html += '<td><span class="' + resultBadge + '">' + resultText + '</span> ' + (t.close_reason || '') + '</td>';
    html += '<td style="font-size:9px;color:#667788;">' + (t.close_time || '') + '</td>';
    html += '</tr>';
  }
  tbody.innerHTML = html;
}

// ─── Event Log ──────────────────────────────────────────────────
function updateEventLog(events) {
  const container = document.getElementById('eventLog');
  let html = '';
  for (const e of events) {
    html += '<div class="log-entry">';
    html += '<span class="log-time">' + e.time + '</span>';
    html += '<span class="log-tag-' + e.tag + '">[' + e.tag + ']</span> ';
    html += e.msg;
    html += '</div>';
  }
  container.innerHTML = html;
  const scrollContainer = document.getElementById('eventLogContainer');
  if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight;
}

// ─── Reset ──────────────────────────────────────────────────────
async function resetAll() {
  if (!confirm('Reset ALL accounts to $500 each? This clears ALL trade history.')) return;
  showLoading('Resetting all...');
  try {
    await fetch('/api/reset', { method: 'POST' });
  } catch (e) { console.error('Reset error:', e); }
  hideLoading();
}

async function resetSymbol(sym) {
  if (!confirm('Reset ' + sym + ' capital to $500? This clears trades for this symbol.')) return;
  showLoading('Resetting ' + sym + '...');
  try {
    await fetch('/api/reset?symbol=' + sym, { method: 'POST' });
  } catch (e) { console.error('Reset error:', e); }
  hideLoading();
}

// ─── Loading UX ─────────────────────────────────────────────────
function showLoading(msg) {
  document.getElementById('loadingOverlay').classList.add('active');
  document.getElementById('loadingText').textContent = msg;
}
function hideLoading() {
  document.getElementById('loadingOverlay').classList.remove('active');
}

// ─── Start ──────────────────────────────────────────────────────
init();

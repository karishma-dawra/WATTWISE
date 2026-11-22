/* WATTWISE shared helpers */
const WW = (() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  async function api(path, opts = {}) {
    const o = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers['Content-Type'] = 'application/json'; }
    let res;
    try { res = await fetch(path, o); }
    catch (e) { throw new Error('Cannot reach the WATTWISE server. Is it running?'); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-json */ }
    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }

  function toast(msg, type = '') {
    const t = document.createElement('div');
    t.className = 'toast ' + type; t.textContent = msg;
    $('#toasts').appendChild(t);
    setTimeout(() => { t.style.opacity = 0; t.style.transition = '.3s'; setTimeout(() => t.remove(), 300); }, type === 'err' ? 6000 : 3200);
  }

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const n = (v, d = 1) => (v === null || v === undefined || isNaN(v)) ? '–' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
  const money = v => (window.WW_CUR || '') + n(v, 0);
  const dayName = (iso, long) => new Date(iso + 'T12:00:00').toLocaleDateString('en-IN', long ? { weekday: 'long', day: 'numeric', month: 'short' } : { weekday: 'short', day: 'numeric' });
  const shortDate = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  const dur = h => { if (h <= 0) return 'Off'; const m = Math.round(h * 60); if (m < 60) return m + ' min'; const a = Math.floor(m / 60), b = m % 60; return b ? `${a} h ${b} min` : `${a} h`; };

  function riskBadge(level) {
    const c = { 'Safe': 'b-safe', 'Moderate': 'b-moderate', 'High Risk': 'b-high', 'Budget Exceeded': 'b-exceeded' }[level] || 'b-info';
    return `<span class="badge ${c}">${esc(level)}</span>`;
  }
  const riskColor = l => ({ 'Safe': '#22c55e', 'Moderate': '#f59e0b', 'High Risk': '#f97316', 'Budget Exceeded': '#ef4444' }[l] || '#38bdf8');
  const sevIcon = { critical: '🔴', warning: '🟠', info: '🔵' };
  const wxIcon = c => c === 0 || c === 1 ? '☀️' : c === 2 ? '⛅' : c === 3 ? '☁️' : (c >= 45 && c <= 48) ? '🌫️' : (c >= 51 && c <= 67) || (c >= 80 && c <= 82) ? '🌧️' : (c >= 71 && c <= 77) || c === 85 || c === 86 ? '❄️' : c >= 95 ? '⛈️' : '🌡️';

  function alertHTML(a, dismissable = true) {
    return `<div class="alert ${a.severity}" data-id="${a.id ?? ''}"><span class="ico">${sevIcon[a.severity] || '🔵'}</span>
      <div><div class="t">${esc(a.title)} <span class="badge ${a.severity === 'critical' ? 'b-exceeded' : a.severity === 'warning' ? 'b-moderate' : 'b-info'}" style="margin-left:6px">${a.severity}</span></div>
      <div class="m">${esc(a.message)}</div></div>
      ${dismissable && a.id ? `<button class="x" title="Dismiss" data-dismiss="${a.id}">✕</button>` : ''}</div>`;
  }

  function loading(el, text = 'Loading…') { el.innerHTML = `<div class="loading-block"><span class="spinner"></span>${esc(text)}</div>`; }
  function errorBox(el, msg, retry) {
    el.innerHTML = `<div class="notice err">⚠ ${esc(msg)} ${retry ? '<button class="btn btn-sm" style="margin-left:10px" id="retryBtn">Retry</button>' : ''}</div>`;
    if (retry) $('#retryBtn', el).onclick = retry;
  }
  async function busy(btn, fn) {
    const t = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
    try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = t; }
  }

  /* Run times on a 12-hour clock. Windows come from the server as ["13:00–14:00", ...]; [] = off, null = the user decides */
  const t12 = hhmm => { const [h, m] = hhmm.split(':').map(Number); const hh = h % 24; return `${hh % 12 || 12}:${String(m).padStart(2, '0')} ${hh < 12 ? 'AM' : 'PM'}`; };
  const fmtWindows = w => w == null ? 'Your choice' : !w.length ? 'Off' : w.map(x => x === '00:00–24:00' ? 'All day' : x.split('–').map(t12).join(' – ')).join(', ');
  // Start and End as two columns; several windows are stacked one per line
  function winCells(w) {
    if (w == null) return ['Your choice', '—'];
    if (!w.length) return ['Off', '—'];
    if (w.length === 1 && w[0] === '00:00–24:00') return ['All day', '—'];
    const parts = w.map(x => x.split('–'));
    return [parts.map(p => `<div>${t12(p[0])}</div>`).join(''), parts.map(p => `<div>${t12(p[1])}</div>`).join('')];
  }
  /* The schedule table with its Recommended / Comfort-first / Eco saver tabs (advisor page and dashboard) */
  function scenarioView(a, idx) {
    const s = a.scenarios[idx];
    return `<div class="row" style="margin-bottom:12px">
      <div class="pill-tabs">${a.scenarios.map((x, i) => `<button class="pill ${i === idx ? 'active' : ''}" data-sc="${i}">${esc(x.label)}</button>`).join('')}</div></div>
      <p class="small text2" style="margin-bottom:10px">${esc(s.description)}</p>
      <div class="table-wrap"><table><thead><tr><th>Appliance</th><th>Start time</th><th>End time</th><th class="num">Duration</th></tr></thead><tbody>
      ${s.plan.map(p => { const [st, en] = winCells(p.windows); return `<tr><td>${esc(p.name)}</td><td>${st}</td><td>${en}</td><td class="num">${esc(p.duration)}</td></tr>`; }).join('')}</tbody></table></div>
      <div class="row between small" style="margin-top:12px"><span>Tomorrow: <b>${n(s.units_tomorrow)}</b> units</span>
      <span>Month-end if repeated daily: <b style="color:${s.within_budget ? 'var(--ok)' : 'var(--bad)'}">${n(s.month_end_if_repeated, 0)} units</b> ${s.within_budget ? '✓ within budget' : '✗ over budget'}</span></div>`;
  }

  /* Auto-detect location via the browser, then let the server resolve region, currency and pricing */
  function detectLocation(btn, onDone) {
    if (!navigator.geolocation) return toast('This browser does not support location detection. Search your city in Setup instead.', 'err');
    const label = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Detecting…'; }
    const done = () => { if (btn) { btn.disabled = false; btn.innerHTML = label; } };
    navigator.geolocation.getCurrentPosition(async pos => {
      try {
        const r = await api('/api/location/detect', { method: 'POST', body: { latitude: pos.coords.latitude, longitude: pos.coords.longitude } });
        if (r.warning) toast(r.warning, 'err');
        else {
          toast('Location detected: ' + r.geo.label, 'ok');
          if (r.tariff_was_manual && r.profile && r.profile.level !== 'none' &&
              confirm(`Apply the ${r.geo.state || r.geo.country} pricing profile (${r.profile.currency_symbol})? This replaces the tariff you entered. Values are indicative — verify with your bill.`)) {
            await api('/api/region/apply', { method: 'POST' });
          }
        }
        onDone ? onDone(r) : setTimeout(() => location.reload(), 600);
      } catch (e) { toast(e.message, 'err'); }
      finally { done(); }
    }, err => {
      done();
      toast(err.code === 1 ? 'Location permission was denied. Allow it in your browser, or search your city in Setup.' :
        err.code === 3 ? 'Location request timed out. Try again or search your city in Setup.' : 'Could not determine your location. Search your city in Setup.', 'err');
    }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 });
  }

  /* Notification bell: unread badge, polling every 30 s, and a toast when a new critical alert arrives */
  let notifSeen = null;  // highest notification id already known on this page (null until the first poll)
  async function pollNotifications() {
    try {
      const r = await api('/api/notifications?unread=true');
      const badge = $('#bellBadge'), bell = $('#bellBtn'), count = r.unread_count;
      if (badge) { badge.textContent = count > 99 ? '99+' : count; badge.hidden = !count; }
      if (bell) bell.setAttribute('aria-label', count ? `Notifications, ${count} unread` : 'Notifications, none unread');
      const maxId = r.notifications.reduce((m, x) => Math.max(m, x.id), 0);
      if (notifSeen !== null) r.notifications.filter(x => x.id > notifSeen && x.severity === 'critical').forEach(x => toast(`🔴 ${x.title}: ${x.message}`, 'err'));
      notifSeen = Math.max(notifSeen ?? 0, maxId);
      document.dispatchEvent(new CustomEvent('ww:notifications', { detail: r }));
    } catch (e) { /* polling is best-effort: the next tick tries again */ }
  }
  function startNotifications() {
    if (!window.WW_LOGGED_IN || !$('#bellBtn')) return;
    pollNotifications();
    setInterval(() => { if (!document.hidden) pollNotifications(); }, 30000);
  }

  async function initShell() {
    initTheme();
    startNotifications();
    const navBtn = $('#navToggle');
    if (navBtn) navBtn.onclick = () => $('#navLinks').classList.toggle('open');
    try {
      const h = await api('/api/household');
      if (h.latitude == null && !location.pathname.startsWith('/setup')) {
        const bar = document.createElement('div'); bar.className = 'info-banner';
        bar.style.cssText = 'background:rgba(56,189,248,.10);border-bottom-color:rgba(56,189,248,.3);color:var(--info)';
        bar.innerHTML = `<div class="in"><span>📍 <b>Set your location</b> to unlock live weather optimisation and your region's currency and tariff.</span>
          <span class="row" style="gap:8px"><button class="btn btn-sm btn-primary" id="detectLoc">Detect my location</button><a class="btn btn-sm" href="/setup">Enter city manually</a></span></div>`;
        $('#topBanner').appendChild(bar);
        $('#detectLoc').onclick = e => detectLocation(e.currentTarget);
      }
    } catch (e) { /* page handles its own errors */ }
    try {
      const a = await api('/api/alerts');
      const c = a.filter(x => x.severity === 'critical' || x.severity === 'warning').length;
      const dot = $('#navAlertDot'); if (c && dot) { dot.textContent = c; dot.hidden = false; }
    } catch (e) { }
  }
  document.addEventListener('DOMContentLoaded', initShell);

  /* Dark / light theme: the sun / moon button in the navbar. The choice is remembered in localStorage. */
  const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const currentTheme = () => document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  function themeCharts() {   // chart colours come from the theme's CSS variables
    if (!window.Chart) return;
    Chart.defaults.color = cssVar('--text2');
    Chart.defaults.borderColor = cssVar('--grid');
    const tip = Chart.defaults.plugins.tooltip;
    tip.backgroundColor = cssVar('--tooltip-bg'); tip.titleColor = tip.bodyColor = cssVar('--tooltip-text'); tip.borderColor = cssVar('--line2');
    Object.values(Chart.instances || {}).forEach(c => {   // charts already on the page
      if (c.config.type === 'doughnut') c.data.datasets.forEach(d => { d.borderColor = cssVar('--surface'); });
      c.update('none');
    });
  }
  function syncThemeButton() {
    const b = $('#themeToggle'); if (!b) return;
    const light = currentTheme() === 'light', label = light ? 'Switch to dark theme' : 'Switch to light theme';
    b.setAttribute('aria-label', label); b.title = label; b.setAttribute('aria-pressed', String(light));
  }
  function setTheme(theme, remember = true) {
    document.documentElement.setAttribute('data-theme', theme);
    if (remember) { try { localStorage.setItem('ww_theme', theme); } catch (e) { /* private mode: the choice just is not remembered */ } }
    syncThemeButton(); themeCharts();
    document.dispatchEvent(new CustomEvent('ww:theme', { detail: theme }));
  }
  function initTheme() {
    syncThemeButton();
    const b = $('#themeToggle'); if (b) b.onclick = () => setTheme(currentTheme() === 'light' ? 'dark' : 'light');
    window.addEventListener('storage', e => { if (e.key === 'ww_theme' && e.newValue) setTheme(e.newValue === 'light' ? 'light' : 'dark', false); });  // another tab changed it
  }

  const chartDefaults = () => {
    if (!window.Chart) return;
    Chart.defaults.color = '#a4b2d0'; Chart.defaults.font.family = "Inter, system-ui, sans-serif"; Chart.defaults.font.size = 12;
    Chart.defaults.borderColor = 'rgba(148,170,210,.10)';
    Chart.defaults.plugins.legend.display = false;
    Chart.defaults.plugins.tooltip.backgroundColor = '#0b1326'; Chart.defaults.plugins.tooltip.borderColor = 'rgba(148,170,210,.3)';
    Chart.defaults.plugins.tooltip.borderWidth = 1; Chart.defaults.plugins.tooltip.padding = 10; Chart.defaults.plugins.tooltip.cornerRadius = 8;
    Chart.defaults.maintainAspectRatio = false;
  };
  chartDefaults();
  themeCharts();  // replace the dark defaults above with the saved theme's colours
  const COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#a78bfa', '#38bdf8', '#f472b6', '#94a3b8', '#fb923c'];

  return { detectLocation, $, $$, api, toast, esc, n, money, dayName, shortDate, dur, riskBadge, riskColor, alertHTML, wxIcon, loading, errorBox, busy, t12, fmtWindows, scenarioView, refreshNotifications: pollNotifications, cssVar, setTheme, currentTheme, COLORS };
})();

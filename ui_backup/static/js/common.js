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
  const hm = h => {
    if (h === null || h === undefined || isNaN(h)) return '–';
    if (h <= 0) return '0 minutes';
    if (h < 1) return (h * 60).toFixed(1) + ' minutes';
    const whole = Math.floor(h), mins = Math.round((h - whole) * 60);
    if (mins === 60) return (whole + 1) + ' h';
    return mins ? `${whole} h ${mins} min` : `${whole} h`;
  };
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

  const q = (extra = '') => (extra ? '?' + extra : '');

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

  async function initShell() {
    const navBtn = $('#navToggle');
    if (navBtn) navBtn.onclick = () => $('#navLinks').classList.toggle('open');
    try {
      const h = await api('/api/household');
      if (h.is_demo) {
        $('#demoBanner').innerHTML = `<div class="demo-banner"><div class="in"><span>🧪 <b>Demo mode</b> — Sharma family, Ludhiana. Appliances and consumption readings are <b>simulated sample data</b>; weather is live from Open-Meteo.</span>
        <span class="row" style="gap:8px"><button class="btn btn-sm" id="resetDemo">Reset demo</button><button class="btn btn-sm btn-ghost" id="startFresh">Start with my own data</button></span></div></div>`;
        $('#resetDemo').onclick = async e => { await busy(e.target, async () => { await api('/api/demo/reset', { method: 'POST' }); toast('Demo household reset', 'ok'); setTimeout(() => location.reload(), 400); }); };
        $('#startFresh').onclick = async () => {
          if (!confirm('This clears the demo household, appliances and readings so you can enter your own. Continue?')) return;
          await api('/api/household/clear', { method: 'POST' }); location.href = '/setup';
        };
      }
      if (h.latitude == null && !location.pathname.startsWith('/setup')) {
        const bar = document.createElement('div'); bar.className = 'demo-banner';
        bar.style.cssText = 'background:rgba(56,189,248,.10);border-bottom-color:rgba(56,189,248,.3);color:#bae6fd';
        bar.innerHTML = `<div class="in"><span>📍 <b>Set your location</b> to unlock live weather optimisation and your region's currency and tariff.</span>
          <span class="row" style="gap:8px"><button class="btn btn-sm btn-primary" id="detectLoc">Detect my location</button><a class="btn btn-sm" href="/setup">Enter city manually</a></span></div>`;
        $('#demoBanner').appendChild(bar);
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
  const COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#a78bfa', '#38bdf8', '#f472b6', '#94a3b8', '#fb923c'];

  return { detectLocation, $, $$, api, toast, esc, n, money, dayName, shortDate, dur, hm, riskBadge, riskColor, alertHTML, wxIcon, loading, errorBox, busy, q, COLORS };
})();

/* Sweep every page in jsdom, running the real HTML and static JS, and report script errors, failed API calls and
 * page elements that never rendered.
 *
 * Usage (server must be running, any database):
 *   cd tests && npm install jsdom@24
 *   node ui_all_pages.js http://127.0.0.1:5051
 *
 * Chart.js needs a real <canvas>, which jsdom does not have, so "not implemented: canvas" noise is ignored.
 */
const { JSDOM, VirtualConsole } = require('jsdom');

const BASE = process.argv[2] || 'http://127.0.0.1:5051';
const PAGES = ['/dashboard', '/advisor', '/planner', '/weather', '/appliances', '/setup', '/alerts', '/notifications', '/profile'];
const sleep = ms => new Promise(r => setTimeout(r, ms));
let COOKIE = '';

async function login() {
  const r = await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mobile: '9999999999', password: 'demo1234' }) });
  if (r.status !== 200) throw new Error('demo login failed: ' + r.status);
  COOKIE = (r.headers.get('set-cookie') || '').split(';')[0];
}

async function sweep(path) {
  const html = await (await fetch(BASE + path, { headers: { Cookie: COOKIE } })).text();
  const problems = [], calls = [];
  // Chart.js under jsdom: no real canvas, and its MutationObserver set-up rejects jsdom's fake nodes (one error per chart)
  const noise = m => /not implemented|getContext|HTMLCanvasElement|navigation|MutationObserver/i.test(String(m));
  const vc = new VirtualConsole();
  vc.on('error', e => { if (!noise(e)) problems.push('console.error: ' + e); });
  vc.on('jsdomError', e => { const m = (e.detail && e.detail.message) || e.message; if (!noise(m)) problems.push('script error: ' + m); });
  const dom = new JSDOM(html, {
    url: BASE + path, runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = async (u, opts = {}) => {   // jsdom has no fetch: forward to Node's, with the session cookie
        const url = new URL(u, BASE).href;
        const res = await fetch(url, { ...opts, headers: { ...(opts.headers || {}), Cookie: COOKIE } });
        calls.push(`${res.status} ${(opts.method || 'GET')} ${url.replace(BASE, '')}`);
        return res;
      };
      // Chart.js needs a 2D context: hand it a do-nothing one so chart code runs and only real errors surface
      w.HTMLCanvasElement.prototype.getContext = function () {
        const canvas = this, state = {};
        const fn = () => new Proxy(function () {}, { get: () => fn(), apply: () => fn() });
        return new Proxy(state, {
          get(t, k) {
            if (k === 'canvas') return canvas;
            if (k === 'measureText') return () => ({ width: 10 });
            if (k === 'createLinearGradient' || k === 'createRadialGradient') return () => ({ addColorStop() {} });
            if (k in t) return t[k];
            return () => {};
          },
          set(t, k, v) { t[k] = v; return true; },
        });
      };
      w.addEventListener('unhandledrejection', e => { if (!noise(e.reason)) problems.push('unhandled rejection: ' + (e.reason && e.reason.message || e.reason)); });
      w.scrollTo = () => {};
      w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };   // missing in jsdom, used by Chart.js
    },
  });
  await sleep(5000);
  const doc = dom.window.document;
  // page-specific content checks: the schedule table has Start/End columns and 12-hour times
  const table = { '/dashboard': '#schedBody', '/advisor': '#scen' }[path];
  if (table) {
    const root = doc.querySelector(table);
    const heads = root ? [...root.querySelectorAll('th')].map(x => x.textContent.trim()) : [];
    const cells = root ? [...root.querySelectorAll('td')].map(x => x.textContent.trim()).join(' | ') : '';
    if (!['Appliance', 'Start time', 'End time', 'Duration'].every(h => heads.includes(h))) problems.push('schedule table columns wrong: ' + heads.join(', '));
    if (/(1[3-9]|2[0-3]):\d\d/.test(cells)) problems.push('24-hour time still shown: ' + cells.slice(0, 120));
    if (!/\d{1,2}:\d\d (AM|PM)/.test(cells)) problems.push('no 12-hour AM/PM time in the table: ' + cells.slice(0, 120));
    console.log(`     ${path} table: [${heads.join(' | ')}]  e.g. ${cells.slice(0, 110)}`);
  }
  const failed = calls.filter(c => !c.startsWith('2'));
  const stuck = [...doc.querySelectorAll('.loading-block, .spinner')].filter(e => e.offsetParent !== null || true).length;
  const errBox = [...doc.querySelectorAll('.error-box:not([hidden]), .notice.err')].map(e => e.textContent.trim().slice(0, 120));
  dom.window.close();
  return { path, problems, failed, stuck, errBox, calls: calls.length };
}

(async () => {
  await login();
  let bad = 0;
  for (const p of PAGES) {
    const r = await sweep(p);
    const ok = !r.problems.length && !r.failed.length && !r.errBox.length;
    if (!ok) bad++;
    console.log(`${ok ? 'OK  ' : 'FAIL'} ${p}  (${r.calls} api calls, ${r.stuck} loading/spinner left)`);
    r.problems.forEach(x => console.log('     ' + x));
    r.failed.forEach(x => console.log('     failed call: ' + x));
    r.errBox.forEach(x => console.log('     error shown on page: ' + x));
  }
  process.exit(bad ? 1 : 0);
})();

/* Browser-level test (jsdom) for the dark / light theme toggle (sun and moon).
 *
 * Usage (server running, any database):  cd tests && node ui_theme.js http://127.0.0.1:5052
 * Colours themselves are checked by looking at screenshots; this checks the behaviour: which icon shows, switching,
 * remembering the choice, applying it before the page paints, and keeping several tabs in step.
 */
const { JSDOM, VirtualConsole } = require('jsdom');
const BASE = process.argv[2] || 'http://127.0.0.1:5052';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let COOKIE = '', failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? '  — ' + detail : ''}`); if (!ok) failures++; };

async function page(path, saved) {
  const html = await (await fetch(BASE + path, { headers: { Cookie: COOKIE } })).text();
  const errors = [], seenAtParse = {};
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => { const m = (e.detail && e.detail.message) || e.message; if (!/not implemented|navigation|MutationObserver/i.test(m)) errors.push(m); });
  const dom = new JSDOM(html, {
    url: BASE + path, runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      if (saved) w.localStorage.setItem('ww_theme', saved);
      w.fetch = async (u, o = {}) => fetch(new URL(u, BASE).href, { ...o, headers: { ...(o.headers || {}), Cookie: COOKIE } });
      w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get: (t, k) => k === 'canvas' ? undefined : () => ({ width: 10 }), set: () => true });
      w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    },
  });
  // the theme must already be set when the stylesheet is applied, i.e. before DOMContentLoaded
  dom.window.document.addEventListener('DOMContentLoaded', () => { seenAtParse.theme = dom.window.document.documentElement.getAttribute('data-theme'); });
  return { w: dom.window, d: dom.window.document, errors, seenAtParse };
}
const shows = (p, sel) => p.w.getComputedStyle(p.d.querySelector(sel)).display !== 'none';

(async () => {
  const login = await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mobile: '9999999999', password: 'demo1234' }) });
  COOKIE = (login.headers.get('set-cookie') || '').split(';')[0];

  // ---- first visit: dark, with a sun
  let p = await page('/dashboard'); await sleep(2500);
  const btn = () => p.d.querySelector('#themeToggle');
  check('the navbar has a theme button with a sun and a moon icon', !!btn() && !!btn().querySelector('.ico-sun') && !!btn().querySelector('.ico-moon'));
  check('the first visit is dark', p.d.documentElement.getAttribute('data-theme') === 'dark' && p.seenAtParse.theme === 'dark');
  check('dark theme shows the sun and hides the moon', shows(p, '.ico-sun') && !shows(p, '.ico-moon'));
  check('the button says what it will do', btn().getAttribute('aria-label') === 'Switch to light theme' && btn().title === 'Switch to light theme' && btn().getAttribute('aria-pressed') === 'false');
  check('the icons are hidden from screen readers (the button carries the label)', btn().querySelector('.ico-sun').getAttribute('aria-hidden') === 'true' && btn().querySelector('.ico-moon').getAttribute('aria-hidden') === 'true');

  // ---- switch to light
  let heard = null;
  p.d.addEventListener('ww:theme', e => { heard = e.detail; });
  btn().click();
  check('clicking switches to the light theme', p.d.documentElement.getAttribute('data-theme') === 'light');
  check('light theme shows the moon and hides the sun', shows(p, '.ico-moon') && !shows(p, '.ico-sun'));
  check('the button now offers the dark theme', btn().getAttribute('aria-label') === 'Switch to dark theme' && btn().getAttribute('aria-pressed') === 'true');
  check('the choice is remembered', p.w.localStorage.getItem('ww_theme') === 'light');
  check('the page announces the change with a ww:theme event', heard === 'light');

  // ---- another tab changes it
  p.w.dispatchEvent(new p.w.StorageEvent('storage', { key: 'ww_theme', newValue: 'dark' }));
  check('a change made in another tab is followed here, without being saved again', p.d.documentElement.getAttribute('data-theme') === 'dark' && shows(p, '.ico-sun'));
  check('no script errors while switching', p.errors.length === 0, p.errors.join(' | '));
  p.w.close();

  // ---- a saved choice is applied before the page paints
  p = await page('/dashboard', 'light'); await sleep(2000);
  check('a saved light choice is applied before DOMContentLoaded (no flash of dark)', p.seenAtParse.theme === 'light' && p.d.documentElement.getAttribute('data-theme') === 'light');
  check('and the button already shows the moon', shows(p, '.ico-moon') && p.d.querySelector('#themeToggle').getAttribute('aria-label') === 'Switch to dark theme');
  p.d.querySelector('#themeToggle').click();
  check('switching back to dark works and is remembered', p.d.documentElement.getAttribute('data-theme') === 'dark' && p.w.localStorage.getItem('ww_theme') === 'dark');
  p.w.close();

  // ---- a nonsense saved value falls back to dark
  p = await page('/login', 'purple'); await sleep(1500);
  check('an unknown saved value falls back to dark', p.d.documentElement.getAttribute('data-theme') === 'dark');
  p.w.close();

  // ---- logged-out pages have the button too
  const anon = await (await fetch(BASE + '/login')).text();
  check('the login page has the theme button', anon.includes('id="themeToggle"'));
  check('the early script and colour-scheme hint are in the page head', /<meta name="color-scheme"/.test(anon) && /ww_theme/.test(anon.slice(0, anon.indexOf('</head>'))));

  console.log(failures ? `\n${failures} CHECK(S) FAILED` : '\nALL THEME CHECKS PASSED');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('test crashed:', e); process.exit(1); });

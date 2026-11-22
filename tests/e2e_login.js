/* End-to-end test of the login page in a REAL browser (Microsoft Edge or Chrome, headless), driven over the DevTools
 * protocol. Needs Node 22+ (built-in WebSocket) and a server running on a THROWAWAY database:
 *   set WATTWISE_DB=%TEMP%\ww_e2e.db && set PORT=5052 && python app.py
 *   node tests\e2e_login.js http://127.0.0.1:5052 [screenshot-folder]
 * Set BROWSER to the browser's path if it is not in one of the usual places.
 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const BASE = process.argv[2] || 'http://127.0.0.1:5052';
const SHOTS = process.argv[3];
const CANDIDATES = [process.env.BROWSER, 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe'].filter(Boolean);
const EXE = CANDIDATES.find(p => fs.existsSync(p));
if (!EXE) { console.error('No Edge or Chrome found. Set BROWSER.'); process.exit(2); }

const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? '  — ' + detail : ''}`); if (!ok) failures++; };

async function launch() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ww-e2e-'));
  const proc = spawn(EXE, ['--headless=new', '--disable-gpu', '--remote-debugging-port=9333', `--user-data-dir=${dir}`, '--no-first-run', ...(process.env.BROWSER_ARGS ? process.env.BROWSER_ARGS.split(' ') : []), 'about:blank'], { stdio: 'ignore' });
  for (let i = 0; i < 50; i++) {
    try { const t = await (await fetch('http://127.0.0.1:9333/json')).json(); const pg = t.find(x => x.type === 'page'); if (pg) return { proc, ws: pg.webSocketDebuggerUrl }; } catch (e) { /* not up yet */ }
    await sleep(200);
  }
  throw new Error('browser did not start');
}

function connect(url) {
  const ws = new WebSocket(url); let id = 0; const waiting = new Map();
  ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); } };
  const opened = new Promise(r => { ws.onopen = r; });
  const send = async (method, params = {}) => { await opened; return new Promise(res => { const i = ++id; waiting.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); }); };
  return { send, close: () => ws.close() };
}

(async () => {
  const { proc, ws } = await launch();
  const b = connect(ws);
  const ev = async expr => { const r = await b.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }); if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails)); return r.result.result.value; };
  const go = async (url, viewport) => {
    await b.send('Emulation.setDeviceMetricsOverride', { width: viewport ? viewport[0] : 1280, height: viewport ? viewport[1] : 800, deviceScaleFactor: 1, mobile: !!(viewport && viewport[0] < 600) });
    await b.send('Page.navigate', { url }); await sleep(1500);
  };
  const shot = async name => { if (!SHOTS) return; fs.mkdirSync(SHOTS, { recursive: true }); const r = await b.send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(SHOTS, name + '.png'), Buffer.from(r.result.data, 'base64')); };
  const fill = (id, v) => ev(`(() => { const e = document.getElementById('${id}'); e.focus(); e.value = ${JSON.stringify(v)}; e.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  const click = sel => ev(`document.querySelector(${JSON.stringify(sel)}).click()`);
  const text = sel => ev(`(document.querySelector(${JSON.stringify(sel)}) || {}).textContent`);
  const where = () => ev('location.pathname + location.search');
  await b.send('Page.enable'); await b.send('Runtime.enable');

  // ---- the page itself
  await go(BASE + '/login');
  await ev("localStorage.removeItem('ww_theme')");
  await go(BASE + '/login'); await shot('login-dark');
  const body = await ev('document.body.innerText');
  check('the page shows no demo credentials or the word "demo"', !/demo/i.test(body) && !/9999999999/.test(body) && !/demo1234/.test(await ev('document.documentElement.outerHTML')), body.slice(0, 80));
  check('welcome heading, benefits and a create-account button are there', /Welcome back/.test(body) && /weather/i.test(body) && /Create an account/.test(body));
  check('the mobile field has the keyboard focus', await ev("document.activeElement && document.activeElement.id") === 'l_mobile');
  check('labels are tied to their fields', await ev("document.querySelector('label[for=l_mobile]') && document.querySelector('label[for=l_password]') ? true : false"));

  // ---- validation
  await click('#l_submit'); await sleep(300);
  check('an empty form shows an error on both fields and stays on the page', (await text('#l_mobile_err')).length > 0 && (await text('#l_password_err')).length > 0 && (await where()).startsWith('/login'));
  check('the invalid field is marked for screen readers', await ev("document.getElementById('l_mobile').getAttribute('aria-invalid')") === 'true');
  await fill('l_mobile', '12345'); await fill('l_password', 'x'); await click('#l_submit'); await sleep(300);
  check('a short or non-Indian number is rejected before asking the server', /10-digit/.test(await text('#l_mobile_err')) && (await where()).startsWith('/login'));
  await fill('l_mobile', '9999999999');
  check('typing clears the field error', (await text('#l_mobile_err')) === '');

  // ---- show / hide password
  await fill('l_password', 'secret1');
  check('the password is hidden by default', await ev("document.getElementById('l_password').type") === 'password');
  await click('#l_toggle');
  check('Show reveals it and the button becomes Hide', await ev("document.getElementById('l_password').type") === 'text' && /Hide/.test(await text('#l_toggle')) && await ev("document.getElementById('l_toggle').getAttribute('aria-pressed')") === 'true');
  await click('#l_toggle');
  check('and hides it again', await ev("document.getElementById('l_password').type") === 'password');

  // ---- wrong password
  await fill('l_mobile', '9999999999'); await fill('l_password', 'definitely-wrong'); await click('#l_submit'); await sleep(1500);
  check('a wrong password shows one clear error and stays on the login page', /incorrect/i.test(await text('#l_error')) && await ev("!document.getElementById('l_error').hidden") && (await where()).startsWith('/login'));
  check('the button works again after the error', await ev("!document.getElementById('l_submit').disabled"));
  await shot('login-error');

  // ---- good login, with the number typed in other formats
  await fill('l_mobile', '+91 99999-99999'); await fill('l_password', 'demo1234'); await click('#l_submit'); await sleep(2500);
  check('"+91 99999-99999" with the right password logs in and goes to the dashboard', (await where()) === '/dashboard', await where());

  // ---- next= is honoured, but only for paths on this site
  await go(BASE + '/api/auth/logout'); // GET is not allowed, just make sure we are logged out below
  await ev("fetch('/api/auth/logout', { method: 'POST' }).then(() => 1)");
  await go(BASE + '/login?next=/planner');
  await fill('l_mobile', '9999999999'); await fill('l_password', 'demo1234'); await click('#l_submit'); await sleep(2500);
  check('after login you go to the page you asked for (?next=/planner)', (await where()) === '/planner', await where());
  await ev("fetch('/api/auth/logout', { method: 'POST' }).then(() => 1)");
  await go(BASE + '/login?next=//evil.example');
  await fill('l_mobile', '9999999999'); await fill('l_password', 'demo1234'); await click('#l_submit'); await sleep(2500);
  check('a ?next= pointing to another site is ignored', (await where()) === '/dashboard' && (await ev('location.hostname')) === new URL(BASE).hostname, await where());
  await ev("fetch('/api/auth/logout', { method: 'POST' }).then(() => 1)");

  // ---- the profile page saves and says so (it used to fail with "t is not a function" after saving)
  await go(BASE + '/login'); await fill('l_mobile', '9999999999'); await fill('l_password', 'demo1234'); await click('#l_submit'); await sleep(2500);
  await go(BASE + '/profile'); await sleep(1500);
  await click('#p_save'); await sleep(1500);
  const toast = await text('.toast'), fieldErr = await ev("[...document.querySelectorAll('#fPersonal .field-err')].map(e => e.textContent).join('')");
  check('saving personal details shows the saved message and no script error', /saved|सेव/i.test(toast || '') && !/not a function/.test(fieldErr + toast), `toast=${toast} err=${fieldErr}`);
  await ev("fetch('/api/auth/logout', { method: 'POST' }).then(() => 1)");

  // ---- looks: light theme, narrow screen
  await go(BASE + '/login'); await ev("localStorage.setItem('ww_theme', 'light')"); await go(BASE + '/login'); await shot('login-light');
  check('the light theme applies to the login page', await ev("document.documentElement.getAttribute('data-theme')") === 'light');
  console.log('      diag: attr=' + await ev("document.documentElement.getAttribute('data-theme')") + ' --bg=' + await ev("getComputedStyle(document.documentElement).getPropertyValue('--bg')") + ' sheets=' + await ev("[...document.styleSheets].map(x => x.href || 'inline').join(',')"));
  const css = await ev("JSON.stringify({ bg: getComputedStyle(document.body).backgroundColor, text: getComputedStyle(document.body).color, scheme: getComputedStyle(document.documentElement).colorScheme, osDark: matchMedia('(prefers-color-scheme: dark)').matches })");
  console.log('      light theme computed styles:', css);
  check('light theme: a light page background and dark text are computed', (() => { const c = JSON.parse(css); const n = x => x.match(/\d+/g).map(Number); return n(c.bg).every(v => v > 200) && n(c.text).every(v => v < 80); })(), css);
  await go(BASE + '/login', [390, 800]); await shot('login-mobile');
  check('on a phone-sized screen nothing scrolls sideways', await ev('document.documentElement.scrollWidth <= window.innerWidth + 1'), String(await ev('document.documentElement.scrollWidth')));
  check('and the form is still fully usable (fields wider than 250px)', await ev("document.getElementById('l_mobile').getBoundingClientRect().width > 250"));
  const navFits = () => ev("(() => { const r = document.querySelector('.nav-right').getBoundingClientRect(); return r.right <= window.innerWidth + 0.5 && r.left >= 0; })()");
  check('on a phone the whole navbar (theme, language, Login, Register) is on screen', await navFits());
  await go(BASE + '/login'); await fill('l_mobile', '9999999999'); await fill('l_password', 'demo1234'); await click('#l_submit'); await sleep(2500);
  await go(BASE + '/dashboard', [390, 800]);
  check('and so is the logged-in navbar (bell, theme, language, logout)', await navFits() && await ev('document.documentElement.scrollWidth <= window.innerWidth + 1'));
  await ev("fetch('/api/auth/logout', { method: 'POST' }).then(() => 1)");
  await ev("localStorage.removeItem('ww_theme')");

  b.close(); proc.kill();
  console.log(failures ? `\n${failures} CHECK(S) FAILED` : '\nALL LOGIN E2E CHECKS PASSED');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('test crashed:', e); process.exit(1); });

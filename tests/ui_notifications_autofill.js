/* Browser-level test (jsdom) for the bell, the Personal Notifications page and the model-number autofill.
 *
 * Usage: start a server on a THROWAWAY database, then
 *   set WATTWISE_DB=%TEMP%\ww_ui.db && set PORT=5052 && python app.py
 *   cd tests && node ui_notifications_autofill.js http://127.0.0.1:5052 %TEMP%\ww_ui.db
 * The second argument is the same database file: the test adds one notification to it behind the page's back to
 * check the live update and the critical-alert toast. Camera and OCR need a real browser and are not covered here.
 */
const { JSDOM, VirtualConsole } = require('jsdom');
const { execFileSync } = require('child_process');

const BASE = process.argv[2] || 'http://127.0.0.1:5052';
const DB = process.argv[3];
const sleep = ms => new Promise(r => setTimeout(r, ms));
let COOKIE = '', failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? '  — ' + detail : ''}`); if (!ok) failures++; };

async function api(path, opts = {}) {
  const r = await fetch(BASE + path, { ...opts, headers: { 'Content-Type': 'application/json', Cookie: COOKIE, ...(opts.headers || {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null), headers: r.headers };
}

async function page(path) {
  const html = await (await fetch(BASE + path, { headers: { Cookie: COOKIE } })).text();
  const errors = [], calls = [];
  const noise = m => /not implemented|navigation/i.test(String(m));
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => { const m = (e.detail && e.detail.message) || e.message; if (!noise(m)) errors.push(m); });
  const dom = new JSDOM(html, {
    url: BASE + path, runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = async (u, o = {}) => { const url = new URL(u, BASE).href; calls.push((o.method || 'GET') + ' ' + url.replace(BASE, ''));
        return fetch(url, { ...o, headers: { ...(o.headers || {}), Cookie: COOKIE } }); };
      w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get: (t, k) => k === 'canvas' ? undefined : () => ({ width: 10 }), set: () => true });
      w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
      w.confirm = () => true;
    },
  });
  return { dom, w: dom.window, d: dom.window.document, errors, calls };
}

(async () => {
  const login = await api('/api/auth/login', { method: 'POST', body: { mobile: '9999999999', password: 'demo1234' } });
  COOKIE = (login.headers.get('set-cookie') || '').split(';')[0];
  check('demo login', login.status === 200);

  // raise a real critical alert: a reading above the budget
  const h = (await api('/api/household')).json;
  await api('/api/readings', { method: 'POST', body: { reading_date: new Date().toISOString().slice(0, 10), units_consumed: h.monthly_budget + 25 } });

  // ---------------- notifications page + bell
  let p = await page('/notifications');
  await sleep(2500);
  const cards = [...p.d.querySelectorAll('.notif')];
  check('notifications page lists the alert cards', cards.length >= 1, `${cards.length} cards, errors: ${p.errors.join(' | ')}`);
  check('a critical card has the critical class, a time and both actions',
    !!p.d.querySelector('.notif.critical time') && !!p.d.querySelector('.notif.critical [data-read]') && !!p.d.querySelector('.notif.critical [data-del]'));
  check('the time reads like "just now" or "N minutes ago"', /(just now|ago)$/.test(p.d.querySelector('.notif time').textContent.trim()), p.d.querySelector('.notif time').textContent);
  const badge = p.d.querySelector('#bellBadge'), bell = p.d.querySelector('#bellBtn');
  check('the bell shows the unread count with an accessible label', !badge.hidden && Number(badge.textContent) >= 1 && /unread/.test(bell.getAttribute('aria-label')) && bell.getAttribute('aria-label').includes(badge.textContent),
    `${badge.textContent} / ${bell.getAttribute('aria-label')}`);
  check('Mark all as read is enabled while something is unread', !p.d.querySelector('#readAllBtn').disabled);

  const tab = f => p.d.querySelector(`#filters [data-f="${f}"]`).click();
  tab('critical'); await sleep(800);
  check('Critical tab shows only critical cards', [...p.d.querySelectorAll('.notif')].every(c => c.classList.contains('critical')) && p.d.querySelectorAll('.notif').length >= 1);
  tab('warning'); await sleep(800);
  const warn = p.d.querySelectorAll('.notif').length;
  check('Warning tab shows only warning cards (or the empty state)', [...p.d.querySelectorAll('.notif')].every(c => c.classList.contains('warning')));
  check('a tab is marked selected for screen readers', p.d.querySelector('#filters [data-f="warning"]').getAttribute('aria-selected') === 'true');

  // live update: a new critical alert appears while the page is open -> list reloads and a toast pops up
  tab('all'); await sleep(800);
  const before = p.d.querySelectorAll('.notif').length;
  if (DB) {
    execFileSync('python', ['-c', "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute(\"INSERT INTO notifications (user_id,type,severity,title,message) VALUES (1,'overuse','critical','Live test alert','Added while the page was open')\");c.commit()", DB]);
    await p.w.eval('WW.refreshNotifications()'); await sleep(1500);
    check('a new critical alert shows a toast', [...p.d.querySelectorAll('.toast')].some(t => t.textContent.includes('Live test alert')), [...p.d.querySelectorAll('.toast')].map(t => t.textContent).join(' | '));
    check('and the list reloads by itself', p.d.querySelectorAll('.notif').length === before + 1, `${before} -> ${p.d.querySelectorAll('.notif').length}`);
  }

  p.d.querySelector('.notif.unread [data-read]').click(); await sleep(1200);
  const unreadAfter = p.d.querySelectorAll('.notif.unread').length;
  p.d.querySelector('#readAllBtn').click(); await sleep(1200);
  check('Mark read and Mark all as read clear the unread cards', p.d.querySelectorAll('.notif.unread').length === 0 && p.d.querySelector('#bellBadge').hidden, `after one: ${unreadAfter}`);
  check('Mark all as read is disabled again', p.d.querySelector('#readAllBtn').disabled);
  const n0 = p.d.querySelectorAll('.notif').length;
  p.d.querySelector('[data-del]').click(); await sleep(1200);
  check('Delete removes a card', p.d.querySelectorAll('.notif').length === n0 - 1);
  while (p.d.querySelector('[data-del]')) { p.d.querySelector('[data-del]').click(); await sleep(700); }
  check('with none left the empty state shows', /No alerts, you're on track!/.test(p.d.querySelector('#notifs').textContent), p.d.querySelector('#notifs').textContent.slice(0, 80));
  check('no script errors on the notifications page', p.errors.length === 0, p.errors.join(' | '));
  p.w.close();

  // ---------------- model-number autofill
  p = await page('/appliances');
  await sleep(1500);
  p.d.querySelector('#addBtn').click();
  const type = async v => { const el = p.d.querySelector('#fModel'); el.value = v; el.dispatchEvent(new p.w.Event('input', { bubbles: true })); };
  const searches = () => p.calls.filter(c => c.includes('/api/catalog/search')).length;

  await type('PS-Q'); await sleep(100); await type('PS-Q19'); await sleep(100); await type('PS-Q19YNZE');
  await sleep(300);
  check('nothing is searched while typing (500 ms debounce)', searches() === 0, `${searches()} searches after 500 ms of typing`);
  await sleep(1200);
  check('one search runs once typing pauses', searches() === 1, String(searches()));
  const v = id => p.d.querySelector('#' + id).value;
  check('a known model fills name, wattage, category, priority and weather sensitivity',
    v('fName') === 'LG Air Conditioner' && v('fWatt') === '1300' && v('fCategory') === 'Cooling' && v('fPriority') === 'flexible' && v('fWeather') === 'cooling',
    [v('fName'), v('fWatt'), v('fCategory'), v('fPriority'), v('fWeather')].join(' / '));
  check('filled fields are highlighted, editable and flagged "please verify"',
    p.d.querySelector('#fWatt').classList.contains('auto-filled') && !p.d.querySelector('#fWatt').disabled && !p.d.querySelector('#fAutoNote').hidden && /verify/.test(p.d.querySelector('#fModelMsg').textContent));
  const wat = p.d.querySelector('#fWatt'); wat.value = '1250'; wat.dispatchEvent(new p.w.Event('input', { bubbles: true }));
  check('editing a value removes its highlight', !wat.classList.contains('auto-filled') && wat.value === '1250');

  await type('ZZ-NOT-A-MODEL'); await sleep(1500);
  check('an unknown model says so and leaves the form for manual entry',
    /not found/i.test(p.d.querySelector('#fModelMsg').textContent) && p.d.querySelector('#fModelMsg').classList.contains('err') && p.d.querySelector('#fSuggest').hidden, p.d.querySelector('#fModelMsg').textContent);
  await type('STEALTH'); await sleep(1500);
  check('a partial model offers suggestions to pick', !p.d.querySelector('#fSuggest').hidden && p.d.querySelectorAll('#fSuggest button').length >= 1);
  p.d.querySelector('#fSuggest button').click(); await sleep(1500);
  check('picking a suggestion autofills the form', v('fName') === 'Havells Ceiling Fan' && v('fWatt') === '75', `${v('fName')} / ${v('fWatt')}`);

  // ---- scan flow: the camera and the OCR engine need a real browser, so their output is simulated here;
  // everything after them (parse on the server, fill the form, errors, retry) is the real code
  const scanWith = async (ocrText, ocrError) => {
    p.w.WWScan.barcode = async () => '';
    p.w.WWScan.ocr = async () => { if (ocrError) throw new Error(ocrError); return ocrText; };
    p.d.querySelector('#scanBtn').click();
    const input = p.d.querySelector('#scanFile');
    Object.defineProperty(input, 'files', { value: [new p.w.File(['x'], 'sticker.jpg', { type: 'image/jpeg' })], configurable: true });
    input.dispatchEvent(new p.w.Event('change', { bubbles: true }));
    await sleep(1500);
  };
  const reset = () => { for (const id of ['fName', 'fWatt']) p.d.querySelector('#' + id).value = ''; p.d.querySelector('#fModel').value = ''; };
  reset();
  await scanWith('LG Electronics\nModel: PS-Q19YNZE\nRated Power 1300 W 220-240 V 50 Hz 5 Star');
  check('scan: a sticker with a known model fills the form and closes the dialog',
    v('fName') === 'LG Air Conditioner' && v('fWatt') === '1300' && v('fModel') === 'PS-Q19YNZE' && !p.d.querySelector('#scanBg').classList.contains('open'),
    `${v('fName')} / ${v('fWatt')} / ${v('fModel')}`);
  check('scan: the result is flagged "please verify"', !p.d.querySelector('#fAutoNote').hidden && /verify/.test(p.d.querySelector('#fModelMsg').textContent));
  reset();
  await scanWith('XYZ Appliances\nModel No: ZX-900-Q\nPower: 750W 230V 50Hz');
  check('scan: an unknown model fills only what the sticker shows (watts) and says so',
    v('fWatt') === '750' && v('fName') === '' && /not in the catalog/i.test(p.d.querySelector('#fModelMsg').textContent), `${v('fWatt')} / "${v('fName')}" / ${p.d.querySelector('#fModelMsg').textContent}`);
  reset();
  await scanWith('', 'Could not load https://cdn.jsdelivr.net/npm/tesseract.js');
  check('scan: a failed scan shows a clear error with Retry and Enter manually',
    p.d.querySelector('#scanBg').classList.contains('open') && /internet connection/.test(p.d.querySelector('#scanStatus').textContent)
    && !!p.d.querySelector('#scanRetry') && !!p.d.querySelector('#scanManual'), p.d.querySelector('#scanStatus').textContent);
  p.d.querySelector('#scanRetry').click();
  check('scan: Retry brings the photo choices back', !p.d.querySelector('#scanChoices').hidden && p.d.querySelector('#scanStatus').textContent === '');
  await scanWith('', null);
  check('scan: text that cannot be read is reported', /No text could be read/.test(p.d.querySelector('#scanStatus').textContent), p.d.querySelector('#scanStatus').textContent);
  p.d.querySelector('#scanManual').click();
  check('scan: Enter manually closes the dialog and leaves the form editable', !p.d.querySelector('#scanBg').classList.contains('open') && !p.d.querySelector('#fName').disabled);

  p.d.querySelector('#scanBtn').click();
  check('Scan sticker opens the dialog with a photo/upload choice and a camera option',
    p.d.querySelector('#scanBg').classList.contains('open') && !!p.d.querySelector('#scanFile') && !!p.d.querySelector('#scanCam'));
  p.d.querySelector('#scanClose').click();
  check('the dialog closes', !p.d.querySelector('#scanBg').classList.contains('open'));
  check('no script errors on the appliances page', p.errors.length === 0, p.errors.join(' | '));
  p.w.close();

  console.log(failures ? `\n${failures} CHECK(S) FAILED` : '\nALL UI CHECKS PASSED');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('test crashed:', e); process.exit(1); });

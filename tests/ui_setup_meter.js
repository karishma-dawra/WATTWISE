/* Browser-level test for the Setup page meter section (runs the real page HTML + static JS in jsdom).
 *
 * Usage:
 *   cd tests && npm install jsdom@24
 *   node ui_setup_meter.js http://127.0.0.1:5001
 *
 * Point it at a server started with a THROWAWAY database, e.g.
 *   set WATTWISE_DB=%TEMP%\ww_test.db && set PORT=5001 && python app.py
 * Exits with code 1 if any check fails. Checks that no page script or console error occurs.
 */
const { JSDOM, VirtualConsole } = require('jsdom');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2] || 'http://127.0.0.1:5001';
const COMMON = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'common.js'), 'utf8')
  .replace('const WW = (', 'window.WW = (');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
  if (!ok) failures++;
};

let COOKIE = '';
async function login() {  // every page needs an account: log in as the demo account and keep its session cookie
  const r = await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mobile: '9999999999', password: 'demo1234' }) });
  if (r.status !== 200) throw new Error('demo login failed: ' + r.status);
  COOKIE = (r.headers.get('set-cookie') || '').split(';')[0];
}

async function openPage(p) {
  const html = await (await fetch(BASE + p, { headers: { Cookie: COOKIE } })).text();
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('error', e => errors.push('console.error: ' + e));
  vc.on('jsdomError', e => errors.push('jsdom: ' + (e.detail && e.detail.message || e.message)));
  const dom = new JSDOM(html, { url: BASE + p, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc });
  const w = dom.window;
  w.addEventListener('error', e => errors.push('uncaught: ' + e.message));
  w.fetch = (u, o = {}) => fetch(new URL(u, BASE), { ...o, headers: { ...(o.headers || {}), Cookie: COOKIE } });
  w.confirm = () => true;
  w.eval(COMMON);
  for (const m of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) {
    if (/\{\{|\{%/.test(m[1])) continue;
    try { w.eval(m[1]); } catch (e) { errors.push('script: ' + e.message); }
  }
  await sleep(900);
  return { dom, w, errors };
}
const $ = (w, id) => w.document.getElementById(id);
const val = (w, id) => $(w, id).value;
const toasts = w => [...w.document.querySelectorAll('#toasts .toast')].map(t => t.textContent.trim());
function setVal(w, id, v) { $(w, id).value = v; $(w, id).dispatchEvent(new w.Event('input')); }
async function saveMeter(w, o) {
  w.document.querySelectorAll('#toasts .toast').forEach(x => x.remove());
  w.document.querySelectorAll('#meterCard .field-err').forEach(x => x.textContent = '');
  setVal(w, 'm_prev', o.prev ?? ''); setVal(w, 'm_today', o.today ?? ''); setVal(w, 'm_budget', o.budget ?? '');
  $(w, 'm_reset').checked = !!o.reset; $(w, 'm_reset').dispatchEvent(new w.Event('change'));
  if (o.before !== undefined) setVal(w, 'm_before', o.before);
  $(w, 'm_save').click();
  await sleep(1000);
}
const noErrors = p => p.errors.length === 0;

(async () => {
  await login();
  // 1. first load of a fresh household: no errors, empty meter fields
  let p = await openPage('/setup');
  check('1 first load has no page or console errors', noErrors(p), p.errors.join(' | '));
  check('1 meter previous reading starts empty for a new household', val(p.w, 'm_prev') === '', `prev="${val(p.w, 'm_prev')}"`);

  // 2. first-time save
  await saveMeter(p.w, { prev: 1250, today: 1370, budget: 300 });
  check('2 first save shows exactly one success notice', JSON.stringify(toasts(p.w)) === '["Meter details saved"]', JSON.stringify(toasts(p.w)));
  check('2 first save shows units 120 / remaining 180 / 40%',
    $(p.w, 'c_used').textContent.startsWith('120') && $(p.w, 'c_rem').textContent.startsWith('180') && $(p.w, 'c_util').textContent.startsWith('40'),
    `${$(p.w, 'c_used').textContent} / ${$(p.w, 'c_rem').textContent} / ${$(p.w, 'c_util').textContent}`);
  check('2 no errors after first save', noErrors(p), p.errors.join(' | '));
  p.dom.window.close();

  // 3. refresh the page: values persist from the database
  p = await openPage('/setup');
  check('3 after reload: previous reading persisted', val(p.w, 'm_prev') === '1250', val(p.w, 'm_prev'));
  check('3 after reload: today reading persisted', val(p.w, 'm_today') === '1370', val(p.w, 'm_today'));
  check('3 after reload: budget persisted', val(p.w, 'm_budget') === '300', val(p.w, 'm_budget'));
  check('3 no errors on reload', noErrors(p), p.errors.join(' | '));

  // 4. edit and save existing details
  await saveMeter(p.w, { prev: 1250, today: 1400, budget: 350 });
  check('4 edit shows one success notice', JSON.stringify(toasts(p.w)).includes('Meter details saved') && !toasts(p.w).some(t => /lower|error|Cannot/.test(t)), JSON.stringify(toasts(p.w)));
  check('4 edit recalculates units (150) and utilisation (42.9%)',
    $(p.w, 'c_used').textContent.startsWith('150') && $(p.w, 'c_util').textContent.startsWith('42.9'),
    `${$(p.w, 'c_used').textContent} / ${$(p.w, 'c_util').textContent}`);
  check('4 no errors after edit', noErrors(p), p.errors.join(' | '));

  // 5. optional fields empty: today's reading left blank is accepted (no new consumption yet)
  await saveMeter(p.w, { prev: 1250, today: '', budget: 350 });
  check('5 blank today reading saves without error', JSON.stringify(toasts(p.w)) === '["Meter details saved"]', JSON.stringify(toasts(p.w)));
  check('5 no errors with optional fields empty', noErrors(p), p.errors.join(' | '));

  // 6. invalid input shows an error and NEVER a success message
  await saveMeter(p.w, { prev: 1250, today: 1200, budget: 350 });
  const fieldErr = id => p.w.document.querySelector(`#meterCard .field-err[data-for="${id}"]`).textContent;
  check('6 lower reading shows the error beside the field, not success',
    !toasts(p.w).includes('Meter details saved') && /lower than the previous/.test(fieldErr('today')), `toasts=${JSON.stringify(toasts(p.w))} field="${fieldErr('today').slice(0, 60)}"`);
  await saveMeter(p.w, { prev: 1250, today: 1300, budget: 0 });
  check('6 zero budget shows the error beside the field, not success',
    !toasts(p.w).includes('Meter details saved') && /greater than zero/.test(fieldErr('budget')), `toasts=${JSON.stringify(toasts(p.w))} field="${fieldErr('budget')}"`);
  check('6 no page errors from invalid input', noErrors(p), p.errors.join(' | '));

  // 7. meter reset flow
  p.w.document.querySelectorAll('#toasts .toast').forEach(x => x.remove());
  await saveMeter(p.w, { prev: 1250, today: 40, budget: 350, reset: true, before: 1400 });
  check('7 reset saves and carries units across the reset (150 + 40 = 190)',
    toasts(p.w).includes('Meter details saved') && $(p.w, 'c_used').textContent.startsWith('190'), `${JSON.stringify(toasts(p.w))} ${$(p.w, 'c_used').textContent}`);
  check('7 no errors on reset', noErrors(p), p.errors.join(' | '));
  p.dom.window.close();

  // 8. navigate away and back
  p = await openPage('/dashboard');
  check('8 dashboard loads without errors', noErrors(p), p.errors.join(' | '));
  p.dom.window.close();
  p = await openPage('/setup');
  check('8 back on Setup: meter values still shown', val(p.w, 'm_prev') === '1250' && val(p.w, 'm_budget') === '350', `${val(p.w, 'm_prev')} / ${val(p.w, 'm_budget')}`);
  check('8 no errors after navigating back', noErrors(p), p.errors.join(' | '));

  // 9. the rest of the Setup page still works (household save + readings)
  p.w.document.querySelectorAll('#toasts .toast').forEach(x => x.remove());
  setVal(p.w, 'name', 'Test House');
  $(p.w, 'hhForm').dispatchEvent(new p.w.Event('submit', { cancelable: true }));
  await sleep(1000);
  check('9 household save works and shows success', toasts(p.w).includes('Household saved'), JSON.stringify(toasts(p.w)));
  check('9 no errors from household save', noErrors(p), p.errors.join(' | '));
  p.dom.window.close();

  console.log(failures === 0 ? '\nALL CHECKS PASSED' : `\n${failures} CHECK(S) FAILED`);
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });

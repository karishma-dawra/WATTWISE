/* Browser-level test (jsdom) for the state tariff card on Setup and the unverified-charges note on the dashboard.
 *
 * Usage: start a server on a THROWAWAY database, then
 *   set WATTWISE_DB=%TEMP%\ww_ui.db && set PORT=5052 && python app.py
 *   cd tests && node ui_tariff.js http://127.0.0.1:5052 %TEMP%\ww_ui.db
 * The second argument is the same database file: the test sets the household's state in it between checks.
 */
const { JSDOM, VirtualConsole } = require('jsdom');
const { execFileSync } = require('child_process');

const BASE = process.argv[2] || 'http://127.0.0.1:5052';
const DB = process.argv[3];
const sleep = ms => new Promise(r => setTimeout(r, ms));
let COOKIE = '', failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? '  — ' + detail : ''}`); if (!ok) failures++; };
const setState = state => execFileSync('python', ['-c', "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute(\"UPDATE household SET country_code='IN',region_name=?,latitude=30.9,longitude=75.85 WHERE id=1\",(sys.argv[2],));c.commit()", DB, state]);

async function page(path) {
  const html = await (await fetch(BASE + path, { headers: { Cookie: COOKIE } })).text();
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => { const m = (e.detail && e.detail.message) || e.message; if (!/not implemented|navigation|MutationObserver/i.test(m)) errors.push(m); });
  const dom = new JSDOM(html, {
    url: BASE + path, runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = async (u, o = {}) => fetch(new URL(u, BASE).href, { ...o, headers: { ...(o.headers || {}), Cookie: COOKIE } });
      w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get: (t, k) => k === 'canvas' ? undefined : () => ({ width: 10 }), set: () => true });
      w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
      w.confirm = () => true;
    },
  });
  return { w: dom.window, d: dom.window.document, errors };
}

(async () => {
  const login = await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mobile: '9999999999', password: 'demo1234' }) });
  COOKIE = (login.headers.get('set-cookie') || '').split(';')[0];

  // ---- Haryana: one slab rate unknown -> nothing is applied, every unknown says so
  setState('Haryana');
  let p = await page('/setup'); await sleep(2500);
  let card = p.d.querySelector('#regionCard');
  check('Haryana card names the state, its status and regulator', /Haryana/.test(card.textContent) && /Partially verified/.test(card.textContent) && /HERC/.test(card.textContent), card.textContent.slice(0, 120));
  check('known slab rates are shown', /₹2.2 per unit/.test(card.textContent) && /₹2.95 per unit/.test(card.textContent));
  check('the unknown slab says "Rates not verified yet"', [...card.querySelectorAll('td .nv')].some(e => e.textContent === 'Rates not verified yet'));
  check('unknown fixed charge, fuel surcharge and duty say "Rates not verified yet"', (card.textContent.match(/Rates not verified yet/g) || []).length >= 4, String((card.textContent.match(/Rates not verified yet/g) || []).length));
  check('unknown values are highlighted', card.querySelectorAll('.nv').length >= 4);
  check('Apply is disabled for a tariff that is not complete', p.d.querySelector('#applyRegion').disabled && /not verified yet/i.test(p.d.querySelector('#applyRegion').title));
  check('the card says the tariff cannot be applied', /cannot be applied/.test(card.textContent));
  check('the source link opens safely', (() => { const a = card.querySelector('a[href^="https://herc.gov.in"]'); return a && a.target === '_blank' && /noopener/.test(a.rel); })());
  check('no script errors on Setup', p.errors.length === 0, p.errors.join(' | '));
  p.w.close();

  // ---- Punjab: complete slabs -> can be applied; fixed/fuel/duty still unknown
  setState('Punjab');
  p = await page('/setup'); await sleep(2500);
  card = p.d.querySelector('#regionCard');
  check('Punjab card shows both slab rates', /₹3.85 per unit/.test(card.textContent) && /₹7.05 per unit/.test(card.textContent) && /0–300 units/.test(card.textContent) && /301\+ units/.test(card.textContent));
  check('Punjab fixed charge, fuel surcharge and duty are marked not verified', ['Fixed charge', 'Fuel surcharge', 'Electricity duty'].every(l => new RegExp(l + ': Rates not verified yet').test(card.textContent)));
  check('the free-units scheme is listed and flagged "to verify"', /300 free units for domestic consumers/.test(card.textContent) && /to verify/.test(card.textContent));
  check('the central scheme is listed with its link', /PM Surya Ghar/.test(card.textContent) && !!card.querySelector('a[href="https://pmsuryaghar.gov.in"]'));
  check('Apply is enabled for Punjab', !p.d.querySelector('#applyRegion').disabled);
  p.d.querySelector('#applyRegion').click(); await sleep(1500);
  const h = await (await fetch(BASE + '/api/household', { headers: { Cookie: COOKIE } })).json();
  check('applying stores the slabs and the unverified list', JSON.stringify(h.tariff_slabs) === '[[300,3.85],[null,7.05]]' && h.tariff_unverified.length === 3, JSON.stringify(h.tariff_unverified));

  // the 0-300 unit subsidy: button, warning, applied state, remove
  card = p.d.querySelector('#regionCard');
  const subBtn = card.querySelector('[data-subsidy]');
  check('the free-units scheme has an Apply button with its allowance', !!subBtn && /first 300 units free/.test(subBtn.textContent), card.textContent.slice(-200));
  check('the card explains the rule and that it is not verified', /Units 1–300 are covered; units above 300 pay the normal slab rate/.test(card.textContent) && !![...card.querySelectorAll('.nv')].some(e => e.textContent === 'not verified'));
  check('the central scheme without an allowance has no Apply button', card.querySelectorAll('[data-subsidy]').length === 1);
  subBtn.click(); await sleep(2500);
  card = p.d.querySelector('#regionCard');
  check('after applying, the card shows "applied" and a Remove button', /applied/.test(card.textContent) && !!card.querySelector('[data-subsidy-remove]') && !card.querySelector('[data-subsidy]'));
  let hs = await (await fetch(BASE + '/api/household', { headers: { Cookie: COOKIE } })).json();
  check('the household has 300 free units in cover mode', hs.free_units === 300 && hs.free_units_mode === 'cover' && hs.subsidy_scheme === '300 free units for domestic consumers', JSON.stringify([hs.free_units, hs.free_units_mode]));
  check('the Setup form shows the 300 free units', p.d.querySelector('#free_units').value === '300', p.d.querySelector('#free_units').value);
  p.w.close();

  // ---- dashboard: the bill estimate says what it leaves out
  p = await page('/dashboard'); await sleep(3500);
  const note = p.d.querySelector('#bNote').textContent;
  check('the dashboard bill note says which rates are not verified and that they are left out',
    /Rates not verified yet for fixed charge, fuel surcharge, electricity duty/.test(note) && /left out of this estimate/.test(note), note);
  check('the dashboard also says the free-units rule is not verified', /free-units rule is not verified/.test(note) && /first 300 units as free/.test(note), note);
  check('no script errors on the dashboard', p.errors.length === 0, p.errors.join(' | '));
  p.w.close();

  // removing it from Setup brings the Apply button back
  p = await page('/setup'); await sleep(2500);
  p.d.querySelector('[data-subsidy-remove]').click(); await sleep(2500);
  check('Remove subsidy brings the Apply button back and clears the free units', !!p.d.querySelector('#regionCard [data-subsidy]') && !p.d.querySelector('#regionCard [data-subsidy-remove]') && p.d.querySelector('#free_units').value === '0', p.d.querySelector('#free_units').value);
  check('no script errors on Setup after applying and removing', p.errors.length === 0, p.errors.join(' | '));
  p.w.close();

  // ---- a state the file does not cover keeps the old country-average card
  setState('Delhi');
  p = await page('/setup'); await sleep(2500);
  check('a state outside the file shows the country average and can be applied', /country average/.test(p.d.querySelector('#regionCard').textContent) && !p.d.querySelector('#applyRegion').disabled);
  p.w.close();

  console.log(failures ? `\n${failures} CHECK(S) FAILED` : '\nALL TARIFF UI CHECKS PASSED');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('test crashed:', e); process.exit(1); });

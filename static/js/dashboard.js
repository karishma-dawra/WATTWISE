/* Dashboard: status line, KPIs, action row, next-8-days strip, tomorrow's schedule, weather, "why" */
(() => {
  const { $, api, esc, n, money, riskBadge, riskColor, alertHTML, wxIcon, dayName, shortDate, dur } = WW;
  // i18n.js attaches t() to window.WW (common.js's WW const is a separate object)
  const t = (k) => window.WW.t(k);
  const tf = (k, vars) => Object.entries(vars).reduce((s, [v, x]) => s.replace(`{${v}}`, x), t(k));
  let built = false;
  let showTable = false;
  let sched = 0, lastAdvice = null;  // which schedule tab is open, and the advice it shows

  const SKELETON = `
  <div class="grid g4" style="margin-bottom:16px">
    <div class="card kpi" id="cardRemain"><div class="label">${t('dash.remaining')}</div><div class="value mono" id="kRemain">–</div><div class="foot" id="kRemainFoot"></div></div>
    <div class="card kpi" id="cardBudget"><div class="row" style="gap:18px;flex-wrap:nowrap"><div class="ring" id="ring"><div><div><b id="ringPct">–</b><span class="xs muted">${t('dash.budget_used')}</span></div></div></div>
      <div><div class="label">${t('dash.budget_used')}</div><div class="foot" id="kBudgetFoot"></div></div></div></div>
    <div class="card kpi" id="cardProj"><div class="label">${t('dash.projected_month_end')}</div><div class="value mono" id="kProj">–</div><div class="foot" id="kProjFoot"></div></div>
    <div class="card kpi" id="cardBill"><div class="label">${t('dash.bill')}</div><div class="value mono" id="kBill">–</div><div class="foot" id="kBillFoot"></div></div>
  </div>

  <details class="card" open style="margin-bottom:16px">
    <summary style="font-weight:600;font-size:1rem;color:var(--text)">${t('dash.schedule')} <span class="sub" id="schedSub" style="font-weight:400;color:var(--muted);font-size:.8rem"></span></summary>
    <div id="schedBody" style="margin-top:12px"></div>
  </details>

  <div class="grid g3" style="margin-bottom:16px">
    <div class="card plan-card"><div class="eyebrow">${t('dash.recommendation')}</div><div id="recoWidget" style="margin-top:10px"></div></div>
    <div class="card"><h3>${t('dash.alerts')} <a class="small" href="/alerts">${t('dash.view_all')} →</a></h3><div id="alertsWidget"></div></div>
    <div class="card"><h3>${t('dash.bill_est')} <span class="sub">${t('dash.editable')}</span></h3>
      <form id="billForm" class="stack" novalidate>
        <div class="form-grid"><div><label>${t('common.units')}</label><input id="bUnits" type="number" step="0.1" min="0"></div><div><label>Tariff ${WW_CUR}/unit</label><input id="bTariff" type="number" step="0.01" min="0"></div>
        <div><label>Fixed ${WW_CUR}</label><input id="bFixed" type="number" step="1" min="0"></div><div><label>Other ${WW_CUR}</label><input id="bOther" type="number" step="1" min="0"></div></div>
        <div class="row between"><button class="btn btn-sm" type="submit">${t('dash.calculate')}</button><b class="mono" id="bResult" style="font-size:1.3rem">–</b></div>
        <div class="hint" id="bNote">${t('dash.bill_note')}</div>
      </form></div>
  </div>

  <div class="card" style="margin-bottom:16px">
    <div class="row between" style="align-items:flex-start;margin-bottom:12px">
      <div><h3 style="margin:0">${t('dash.next8')}</h3><div class="xs muted" style="margin-top:4px">${t('dash.next8_sub')}</div></div>
      <button class="btn btn-sm" id="dayToggle" type="button" aria-expanded="false" aria-controls="allocCard">${t('dash.view_table')}</button>
    </div>
    <div class="day-strip" id="dayStrip"><div class="loading-block"><span class="spinner"></span></div></div>
    <div id="allocCard" hidden style="margin-top:14px"></div>
  </div>

  <div style="margin-bottom:16px">
    <div class="card"><h3>${t('dash.weather')} <span class="sub" id="wxSub"></span></h3><div id="wxWidget"></div></div>
  </div>

  <details class="card">
    <summary style="font-weight:600;font-size:1rem;color:var(--text)">${t('dash.why')} <span class="sub" id="methodSub" style="font-weight:400;color:var(--muted);font-size:.8rem"></span></summary>
    <ul class="why" id="whyList" style="list-style:disc;margin-top:10px"></ul>
  </details>`;

  function renderStatus(p, cyc) {
    const used = p.budget ? 100 * p.consumed / p.budget : 0;
    let s = tf('dash.status', { pct: n(used, 0), left: n(Math.max(0, p.remaining_units), 0), days: cyc.remaining_days });
    if (p.projected > p.budget) s += ' ' + tf('dash.status_over', { over: n(p.projected - p.budget, 0) });
    $('#statusLine').textContent = s;
  }

  function render(d) {
    const p = d.prediction, h = d.household, cyc = p.cycle;
    $('#subtitle').innerHTML = `${esc(h.name)} · ${esc(h.location_name || t('dash.no_location'))} · ${t('dash.cycle')} ${shortDate(cyc.start)} – ${shortDate(cyc.end)} (${t('dash.day')} ${cyc.elapsed_days} ${t('dash.of')} ${cyc.total_days})`;
    renderStatus(p, cyc);

    const used = p.budget ? 100 * p.consumed / p.budget : 0;
    const color = riskColor(p.risk.level);
    ['cardRemain', 'cardBudget', 'cardProj', 'cardBill'].forEach(id => ($('#' + id).style.borderTop = '3px solid ' + color));
    $('#ring').style.setProperty('--p', Math.min(100, used)); $('#ring').style.setProperty('--ringc', color);
    $('#ringPct').textContent = Math.round(used) + '%';
    $('#kBudgetFoot').textContent = tf('dash.used_of', { used: n(p.consumed), budget: n(p.budget, 0) });
    $('#kRemain').innerHTML = `${n(p.remaining_units)}<small>units</small>`;
    $('#kRemain').style.color = p.remaining_units <= 0 ? 'var(--bad)' : '';
    const limit = cyc.remaining_days ? Math.max(0, p.remaining_units) / cyc.remaining_days : 0;
    $('#kRemainFoot').textContent = tf('dash.days_left_limit', { days: cyc.remaining_days, limit: n(limit, 1) });
    $('#kProj').innerHTML = `${n(p.projected, 0)}<small>units</small>`;
    $('#kProjFoot').innerHTML = `${riskBadge(p.risk.level)} <span class="xs">${Math.round(p.risk.probability * 100)}% ${t('dash.exceed_risk')} · ${n(p.projected_pct_of_budget, 0)}% ${t('dash.of_budget')}</span>`;
    $('#kBill').textContent = money(p.bill.total);
    $('#kBillFoot').textContent = `${t('dash.estimate')} · ${n(p.projected, 0)} units · ${p.bill.slab_tariff ? t('dash.slab') : t('dash.flat') + ' ' + WW_CUR + n(p.bill.tariff, 2) + '/unit'}${p.bill.duty_percent ? ' + ' + p.bill.duty_percent + '% duty' : ''}`;
    $('#methodSub').textContent = p.method;
    $('#whyList').innerHTML = p.why.map(w => `<li>${esc(w)}</li>`).join('');
    if (document.activeElement.closest && !document.activeElement.closest('#billForm')) {
      $('#bUnits').value = p.projected; $('#bTariff').value = h.tariff; $('#bFixed').value = h.fixed_charges; $('#bOther').value = h.other_charges;
      $('#bResult').textContent = money(p.bill.total);
      // charges your state's tariff does not list yet are not in this estimate: say so instead of looking exact
      const names = { fixed_charge: 'fixed charge', fuel_surcharge_pct: 'fuel surcharge', electricity_duty_pct: 'electricity duty' };
      const left = (h.tariff_unverified || []).map(k => names[k]).filter(Boolean);
      const freeRule = (h.tariff_unverified || []).includes('free_units_rule');
      $('#bNote').textContent = t('dash.bill_note') + (left.length ? ` Rates not verified yet for ${left.join(', ')}: they are left out of this estimate, so your real bill can be higher.` : '')
        + (freeRule ? ` The free-units rule is not verified: this estimate treats the first ${h.free_units} units as free and charges units above that at the normal slab rate. Check your bill.` : '');
    }


    // weather widget
    const w = d.weather;
    if (!w.available) {
      $('#wxSub').textContent = '';
      $('#wxWidget').innerHTML = `<div class="notice warn">${esc(w.reason)} <a href="/setup">${t('dash.set_location')}</a></div>`;
    } else {
      $('#wxSub').innerHTML = 'Open-Meteo · live';
      const cur = w.current ? `<div class="row between"><div><div style="font-size:2.1rem;font-weight:700">${wxIcon(0).replace('☀️', '')}${n(w.current.temp, 0)}°C</div><div class="small text2">${esc(w.current.condition || '')} now</div></div><div class="small muted" style="text-align:right">${esc(w.location || '')}</div></div>` : '';
      $('#wxWidget').innerHTML = (w.stale_warning ? `<div class="notice warn small" style="margin-bottom:10px">${esc(w.stale_warning)}</div>` : '') + cur +
        `<div style="margin-top:12px">${w.forecast.slice(0, 5).map(f => `<div class="row between" style="padding:7px 0;border-top:1px solid var(--line)"><span class="small">${wxIcon(f.code)} ${dayName(f.day)}</span><span class="small text2">${esc(f.condition)}</span><b class="mono small">${n(f.temp_max, 0)}° <span class="muted">/ ${n(f.temp_min, 0)}°</span></b></div>`).join('')}</div>
         <a class="small" href="/weather" style="display:inline-block;margin-top:10px">${t('dash.full_forecast')} →</a>`;
    }

    // recommendation widget
    const a = d.advice;
    if (!a.ready) $('#recoWidget').innerHTML = `<div class="empty"><b>${t('dash.no_plan')}</b>${esc(a.message)}</div>`;
    else {
      const flex = a.plan.filter(x => x.priority === 'flexible');
      $('#recoWidget').innerHTML = `<p class="small" style="color:var(--text)">${esc(a.headline)}</p>
        <div style="margin-top:10px">${flex.map(x => `<div class="row between" style="padding:6px 0;border-top:1px solid var(--line)"><span class="small">${esc(x.name)}</span><b>${esc(x.duration)}</b></div>`).join('')}</div>
        <div class="row between small text2" style="margin-top:10px"><span>≈ ${n(a.estimated_units)} units tomorrow (sum of appliances) · ${a.expected_savings_units < 0 ? 'above normal by ' + n(-a.expected_savings_units) : 'saves ' + n(a.expected_savings_units)} vs normal use</span></div>
        <a class="btn btn-primary btn-sm" href="/advisor" style="margin-top:12px">${t('dash.full_plan')} →</a>`;
      renderSchedule(a);
    }
    $('#alertsWidget').innerHTML = d.alerts.length ? d.alerts.slice(0, 3).map(x => alertHTML(x, false)).join('') : `<div class="empty">${t('dash.no_alerts')} 🎉</div>`;
  }

  // Tomorrow's appliance schedule (from the advisor plan)
  function renderSchedule(a) {
    lastAdvice = a;
    $('#schedSub').textContent = a.target_date ? `· ${dayName(a.target_date, true)}` : '';
    if (!a.plan.length) { $('#schedBody').innerHTML = `<div class="empty">${t('dash.no_apps_sched')}</div>`; return; }
    $('#schedBody').innerHTML = WW.scenarioView(a, sched);
  }

  // Next 8 days strip, built from the weather-based allocation
  const condIcon = (c = '') => /storm|thunder/i.test(c) ? '⛈️' : /rain|drizzle|shower/i.test(c) ? '🌧️' : /snow/i.test(c) ? '❄️' : /fog|mist|haze/i.test(c) ? '🌫️' : /overcast|cloud/i.test(c) ? '☁️' : /partly|mainly/i.test(c) ? '⛅' : /clear|sun/i.test(c) ? '☀️' : '🌡️';

  function renderDayStrip(a) {
    const days = a.days.slice(0, 8);
    const peak = a.peak_day ? a.peak_day.date : null;
    $('#dayStrip').innerHTML = days.length ? days.map(d => `
      <div class="day${d.date === peak ? ' peak' : ''}${d.over_allowance ? ' over' : ''}">
        <div class="dn">${esc(dayName(d.date))}</div>
        <div class="ic" title="${esc(d.condition || '')}">${condIcon(d.condition)}</div>
        <div class="small"><b class="mono">${d.temp_max != null ? n(d.temp_max, 0) + '°' : '–'}</b> <span class="muted">/ ${d.temp_min != null ? n(d.temp_min, 0) + '°' : '–'}</span></div>
        <div class="pred mono">${n(d.predicted_units, 2)} <small class="muted" style="font-size:.7rem">units</small></div>
        <div class="xs muted">${t('dash.allowed')} ${n(d.allowance, 2)}</div>
        ${d.over_allowance ? `<span class="warn-ico" title="${t('dash.over_allow')}" aria-label="${t('dash.over_allow')}">▲</span>` : ''}
        ${d.date === peak ? `<div class="xs" style="color:var(--amber);font-weight:700;margin-top:4px">${t('dash.highest_tag')}</div>` : ''}
      </div>`).join('') : `<div class="empty">${t('dash.no_days')}</div>`;
  }

  /* ---- Weather-based unit allocation (detailed table, shown on toggle) ---- */
  const sevIcon = { critical: '🔴', warning: '🟠', info: '🔵' };
  function allocHTML(a) {
    const maxScore = Math.max(1, ...a.days.map(d => d.weather_score));
    const peak = a.peak_day;
    return `
    <div>
      <div class="row between" style="align-items:flex-start;margin-bottom:12px"><div><h3 style="margin:0">${t('dash.allocation')} <span class="sub">${a.remaining_days} billing days · ${a.weather_available ? 'live forecast + climate outlook' : 'typical conditions'}</span></h3>
        <div class="xs muted" style="margin-top:4px">${esc(a.basis)}</div></div></div>
      <div class="grid g4 keep2" style="margin-bottom:14px">
        <div class="tile"><div class="l">${t('dash.predicted_month')}</div><div class="v mono">${n(a.predicted_month_units, 1)} <small class="muted" style="font-size:.7rem">units</small></div><div class="xs muted">${n(a.consumed, 1)} used + ${n(a.predicted_units_remaining, 1)} predicted</div></div>
        <div class="tile"><div class="l">${t('dash.remaining')}</div><div class="v mono" style="color:${a.remaining_now < 0 ? 'var(--bad)' : ''}">${n(a.remaining_now, 1)} <small class="muted" style="font-size:.7rem">units now</small></div><div class="xs" style="${a.over_budget ? 'color:var(--bad)' : 'color:var(--muted)'}">${a.over_budget ? 'Predicted ' + n(a.units_over_budget, 1) + ' over budget' : n(a.headroom_after_prediction, 1) + ' units ' + t('dash.headroom')}</div></div>
        <div class="tile"><div class="l">${t('dash.highest')}</div><div class="v mono" style="font-size:1.2rem">${peak ? dayName(peak.date, true) : '–'}</div><div class="xs muted">${peak ? n(peak.predicted_units, 2) + ' units' + (peak.feels_like != null ? ' · feels like ' + n(peak.feels_like, 0) + '°C' : '') + ' · ' + esc(peak.source_label) : ''}</div></div>
        <div class="tile"><div class="l">${t('dash.tomorrow')}</div><div class="v mono">${a.days.length ? n(a.days[0].predicted_units, 2) : '–'}</div><div class="xs muted">${a.days.length ? t('dash.allowed') + ' ' + n(a.days[0].allowance, 2) : ''}</div></div>
      </div>
      <div class="table-wrap"><table><thead><tr><th>${t('dash.day')}</th><th>${t('dash.weather')}</th><th style="min-width:110px">Demand score</th><th class="num">AC</th><th class="num">Fan &amp; cooler</th><th class="num">Heater &amp; geyser</th><th class="num">Other</th><th class="num">${t('dash.predicted_col')}</th><th class="num">${t('dash.allowance')}</th></tr></thead><tbody>
      ${a.days.map(d => `<tr class="${d.over_allowance ? 'over' : ''}">
        <td>${dayName(d.date)}<div class="xs muted">${esc(d.source_label)}</div></td>
        <td><div class="small">${d.temp_max != null ? n(d.temp_max, 0) + '° / ' + n(d.temp_min, 0) + '°' : '–'}${d.feels_like != null ? ' <span class="muted">feels ' + n(d.feels_like, 0) + '°</span>' : ''}</div>
          <div class="xs muted">${esc(d.condition || '')}${d.humidity != null ? ' · 💧' + n(d.humidity, 0) + '%' : ''}${d.rain_mm != null && d.rain_mm >= 0.1 ? ' · ☔' + n(d.rain_mm, 1) + ' mm' : ''}</div></td>
        <td><div class="bar thin"><span style="width:${Math.min(100, 100 * d.weather_score / maxScore)}%"></span></div><div class="xs muted">${n(d.weather_score, 2)}×</div></td>
        <td class="num">${n(d.groups.ac, 2)}</td><td class="num">${n(d.groups.cooling, 2)}</td><td class="num">${n(d.groups.heating, 2)}</td><td class="num">${n(d.groups.other, 2)}</td>
        <td class="num"><b>${n(d.predicted_units, 2)}</b></td>
        <td class="num" style="${d.over_allowance ? 'color:var(--bad)' : ''}">${n(d.allowance, 2)}${d.over_allowance ? ' ▲' : ''}</td></tr>`).join('')}
      </tbody></table></div>
      <div class="xs muted" style="margin-top:6px">Allowance is each day's share of the units still available; ▲ marks days whose predicted use exceeds it. Totals are the sum of every appliance's kWh.</div>
      <h3 style="margin:18px 0 10px">Weather-based energy-saving recommendations</h3>
      ${a.recommendations.map(r => `<div class="alert ${r.severity === 'critical' ? 'critical' : r.severity === 'warning' ? 'warning' : 'info'}" style="margin-top:8px"><span class="ico">${sevIcon[r.severity] || '🔵'}</span><div class="m" style="color:var(--text)">${esc(r.text)}</div></div>`).join('')}
    </div>`;
  }

  async function loadAlloc(refresh) {
    const strip = $('#dayStrip'), el = $('#allocCard');
    if (!strip || !el) return;
    try {
      const a = await api('/api/allocation' + (refresh ? '?refresh=1' : ''));
      renderDayStrip(a);
      el.innerHTML = allocHTML(a);
    } catch (e) { WW.errorBox(strip, 'Allocation unavailable: ' + e.message, () => loadAlloc(refresh)); }
  }

  async function load(refresh) {
    try {
      const d = await api('/api/dashboard' + (refresh ? '?refresh=1' : ''));
      if (!built) { $('#dash').innerHTML = SKELETON; bindBill(); bindToggles(); built = true;
        $('#schedBody').onclick = e => { const b = e.target.closest('[data-sc]'); if (b && lastAdvice) { sched = +b.dataset.sc; renderSchedule(lastAdvice); } }; }
      render(d);
      loadAlloc(refresh);
      if (refresh) WW.toast(d.weather.available ? 'Weather refreshed' : 'Weather unavailable – ' + d.weather.reason, d.weather.available ? 'ok' : 'err');
    } catch (e) { WW.errorBox($('#dash'), e.message, () => load()); built = false; }
  }

  function bindToggles() {
    const btn = $('#dayToggle');
    const sync = () => {
      $('#allocCard').hidden = !showTable;
      btn.setAttribute('aria-expanded', String(showTable));
      btn.textContent = t(showTable ? 'dash.hide_table' : 'dash.view_table');
    };
    btn.onclick = () => { showTable = !showTable; sync(); };
    sync();
  }

  function bindBill() {
    $('#billForm').onsubmit = async ev => {
      ev.preventDefault();
      try {
        const r = await api('/api/bill', { method: 'POST', body: { units: $('#bUnits').value, tariff: $('#bTariff').value, fixed_charges: $('#bFixed').value || 0, other_charges: $('#bOther').value || 0 } });
        $('#bResult').textContent = money(r.total);
        $('#bNote').textContent = `Energy ${money(r.energy_charge)} + fixed ${money(r.fixed_charges)}${r.duty ? ' + duty ' + money(r.duty) : ''} + other ${money(r.other_charges)}. ${r.disclaimer}`;
      } catch (e) { WW.toast(e.message, 'err'); }
    };
  }

  $('#refreshBtn').onclick = e => WW.busy(e.currentTarget, () => load(true));
  // Language toggle: i18n.js updates static text; rebuild this page's JS-built text in the new language.
  document.addEventListener('click', e => {
    if (!e.target.closest('[data-lang-set]')) return;
    setTimeout(() => { built = false; load(); }, 0);
  });
  load();
})();

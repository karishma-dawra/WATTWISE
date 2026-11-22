/* Dashboard: KPIs, Chart.js charts, widgets */
(() => {
  const { $, api, esc, n, money, riskBadge, riskColor, alertHTML, wxIcon, dayName, shortDate, dur } = WW;
  let charts = {};
  let built = false;

  const SKELETON = `
  <div class="grid g4" style="margin-bottom:16px">
    <div class="card kpi"><div class="row" style="gap:18px;flex-wrap:nowrap"><div class="ring" id="ring"><div><div><b id="ringPct">–</b><span class="xs muted">of budget</span></div></div></div>
      <div><div class="label">Monthly budget</div><div class="value mono" id="kBudget">–</div><div class="foot" id="kBudgetFoot"></div></div></div></div>
    <div class="card kpi"><div class="label">Remaining units</div><div class="value mono" id="kRemain">–</div><div class="foot" id="kRemainFoot"></div></div>
    <div class="card kpi"><div class="label">Predicted month-end</div><div class="value mono" id="kProj">–</div><div class="foot" id="kProjFoot"></div></div>
    <div class="card kpi"><div class="label">Estimated bill</div><div class="value mono" id="kBill">–</div><div class="foot" id="kBillFoot"></div></div>
  </div>
  <div class="grid g-main" style="margin-bottom:16px">
    <div class="card"><h3>Monthly consumption <span class="sub" id="monthSub"></span></h3><div class="chart-box"><canvas id="cMonth"></canvas></div>
      <div class="legend"><span><i style="background:#3b82f6"></i>Actual (cumulative)</span><span><i style="background:#f59e0b"></i>Projected</span><span><i style="background:#94a3b8"></i>Even budget pace</span></div></div>
    <div class="card"><h3>Appliance share <span class="sub">daily kWh</span></h3><div class="chart-box sm"><canvas id="cDonut"></canvas></div><div class="legend" id="donutLegend"></div></div>
  </div>
  <div class="grid g-main" style="margin-bottom:16px">
    <div class="card"><h3>Daily consumption <span class="sub" id="dailySub"></span></h3><div class="chart-box"><canvas id="cDaily"></canvas></div>
      <div class="legend"><span><i style="background:#3b82f6"></i>Actual</span><span><i style="background:rgba(59,130,246,.35)"></i>Projected</span><span><i style="background:#22c55e"></i>Weather-weighted allowance</span></div></div>
    <div class="card"><h3>Weather <span class="sub" id="wxSub"></span></h3><div id="wxWidget"></div></div>
  </div>
  <div class="grid g3" style="margin-bottom:16px">
    <div class="card plan-card" style="grid-column:span 1"><div class="eyebrow">Smart recommendation</div><div id="recoWidget" style="margin-top:10px"></div></div>
    <div class="card"><h3>Recent alerts <a class="small" href="/alerts">View all →</a></h3><div id="alertsWidget"></div></div>
    <div class="card"><h3>Bill estimator <span class="sub">editable</span></h3>
      <form id="billForm" class="stack" novalidate>
        <div class="form-grid"><div><label>Units</label><input id="bUnits" type="number" step="0.1" min="0"></div><div><label>Tariff ${WW_CUR}/unit</label><input id="bTariff" type="number" step="0.01" min="0"></div>
        <div><label>Fixed ${WW_CUR}</label><input id="bFixed" type="number" step="1" min="0"></div><div><label>Other ${WW_CUR}</label><input id="bOther" type="number" step="1" min="0"></div></div>
        <div class="row between"><button class="btn btn-sm" type="submit">Calculate</button><b class="mono" id="bResult" style="font-size:1.3rem">–</b></div>
        <div class="hint" id="bNote">Estimate = units × tariff + fixed + other charges. Actual bills may include slabs, taxes and subsidies.</div>
      </form></div>
  </div>
  <div id="allocCard" style="margin-bottom:16px"></div>
  <div class="card"><h3>Why this prediction? <span class="sub" id="methodSub"></span></h3><ul class="why" id="whyList" style="list-style:disc"></ul></div>`;

  function mkCharts() {
    const grid = { color: 'rgba(148,170,210,.08)' };
    charts.month = new Chart($('#cMonth'), { type: 'line', data: { labels: [], datasets: [
      { label: 'Actual (cumulative)', data: [], borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,.15)', fill: true, borderWidth: 2.5, tension: .25, pointRadius: 3, pointHoverRadius: 6, spanGaps: false },
      { label: 'Projected', data: [], borderColor: '#f59e0b', borderDash: [6, 5], borderWidth: 2.5, tension: .25, pointRadius: 0, pointHoverRadius: 5 },
      { label: 'Even budget pace', data: [], borderColor: '#94a3b8', borderDash: [2, 4], borderWidth: 1.5, pointRadius: 0 },
    ] }, options: { interaction: { mode: 'index', intersect: false }, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } }, y: { grid, beginAtZero: true, title: { display: true, text: 'Units (kWh)' } } },
      plugins: { tooltip: { callbacks: { label: c => c.raw == null ? null : ` ${c.dataset.label}: ${n(c.raw)} units` } } } } });
    charts.daily = new Chart($('#cDaily'), { data: { labels: [], datasets: [
      { type: 'bar', label: 'Actual', data: [], backgroundColor: '#3b82f6', borderRadius: 4, borderSkipped: 'bottom', maxBarThickness: 18, order: 2 },
      { type: 'bar', label: 'Projected', data: [], backgroundColor: 'rgba(59,130,246,.35)', borderRadius: 4, borderSkipped: 'bottom', maxBarThickness: 18, order: 2 },
      { type: 'line', label: 'Allowance', data: [], borderColor: '#22c55e', borderWidth: 2.5, pointRadius: 3, tension: .25, order: 1 },
    ] }, options: { interaction: { mode: 'index', intersect: false }, scales: { x: { stacked: true, grid: { display: false }, ticks: { maxTicksLimit: 8 } }, y: { stacked: true, grid, beginAtZero: true, title: { display: true, text: 'Units / day' } } },
      plugins: { tooltip: { callbacks: { label: c => c.raw == null ? null : ` ${c.dataset.label}: ${n(c.raw, 2)} units` } } } } });
    charts.donut = new Chart($('#cDonut'), { type: 'doughnut', data: { labels: [], datasets: [{ data: [], backgroundColor: WW.COLORS, borderColor: '#111a30', borderWidth: 3, hoverOffset: 6 }] },
      options: { cutout: '66%', plugins: { tooltip: { callbacks: { label: c => ` ${c.label}: ${n(c.raw, 2)} kWh/day` } } } } });
  }

  function render(d) {
    const p = d.prediction, h = d.household, c = d.charts, cyc = p.cycle;
    $('#subtitle').innerHTML = `${esc(h.name)} · ${esc(h.location_name || 'No location set')} · Cycle ${shortDate(cyc.start)} – ${shortDate(cyc.end)} (day ${cyc.elapsed_days} of ${cyc.total_days})`;
    const used = p.budget ? 100 * p.consumed / p.budget : 0;
    const color = riskColor(p.risk.level);
    $('#ring').style.setProperty('--p', Math.min(100, used)); $('#ring').style.setProperty('--ringc', color);
    $('#ringPct').textContent = Math.round(used) + '%';
    $('#kBudget').innerHTML = `${n(p.budget, 0)}<small>units</small>`;
    $('#kBudgetFoot').textContent = `${n(p.consumed)} used so far`;
    $('#kRemain').innerHTML = `${n(p.remaining_units)}<small>units</small>`;
    $('#kRemain').style.color = p.remaining_units <= 0 ? '#f87171' : '';
    const limit = cyc.remaining_days ? Math.max(0, p.remaining_units) / cyc.remaining_days : 0;
    $('#kRemainFoot').textContent = `${cyc.remaining_days} days left · limit ${n(limit, 1)} units/day`;
    $('#kProj').innerHTML = `${n(p.projected, 0)}<small>units</small>`;
    $('#kProjFoot').innerHTML = `${riskBadge(p.risk.level)} <span class="xs">${Math.round(p.risk.probability * 100)}% exceed-risk · ${n(p.projected_pct_of_budget, 0)}% of budget</span>`;
    $('#kBill').textContent = money(p.bill.total);
    $('#kBillFoot').textContent = `Estimate · ${n(p.projected, 0)} units · ${p.bill.slab_tariff ? 'slab tariff' : 'flat ' + WW_CUR + n(p.bill.tariff, 2) + '/unit'}${p.bill.duty_percent ? ' + ' + p.bill.duty_percent + '% duty' : ''}`;
    $('#methodSub').textContent = p.method;
    $('#whyList').innerHTML = p.why.map(w => `<li>${esc(w)}</li>`).join('');
    if (document.activeElement.closest && !document.activeElement.closest('#billForm')) {
      $('#bUnits').value = p.projected; $('#bTariff').value = h.tariff; $('#bFixed').value = h.fixed_charges; $('#bOther').value = h.other_charges;
      $('#bResult').textContent = money(p.bill.total);
    }

    // charts
    charts.month.data.labels = c.labels.map(shortDate);
    charts.month.data.datasets[0].data = c.cumulative_actual;
    charts.month.data.datasets[1].data = c.cumulative_projected;
    charts.month.data.datasets[2].data = c.budget_line;
    $('#monthSub').innerHTML = c.has_actual ? (c.actual_is_demo ? '<span class="badge b-demo">Demo sample data</span>' : 'Your logged readings') : '<span class="badge b-info">No readings logged yet</span>';
    charts.month.update();
    charts.daily.data.labels = c.labels.map(shortDate);
    charts.daily.data.datasets[0].data = c.actual_daily;
    charts.daily.data.datasets[1].data = c.projected_daily;
    charts.daily.data.datasets[2].data = c.allowance;
    $('#dailySub').innerHTML = c.has_actual ? (c.actual_is_demo ? '<span class="badge b-demo">Demo sample data</span>' : 'Derived from readings (spread evenly between them)') : '';
    charts.daily.update();
    const apps = d.appliance_summary.appliances.filter(a => a.daily_kwh > 0).sort((a, b) => b.daily_kwh - a.daily_kwh);
    charts.donut.data.labels = apps.map(a => a.name);
    charts.donut.data.datasets[0].data = apps.map(a => a.daily_kwh);
    charts.donut.update();
    $('#donutLegend').innerHTML = apps.length ? apps.map((a, i) => `<span><i style="background:${WW.COLORS[i % 8]}"></i>${esc(a.name)} ${n(a.percent, 0)}%</span>`).join('')
      : '<span>No appliances yet — <a href="/appliances">add some</a></span>';

    // weather widget
    const w = d.weather;
    if (!w.available) {
      $('#wxSub').textContent = '';
      $('#wxWidget').innerHTML = `<div class="notice warn">${esc(w.reason)} <a href="/setup">Set location</a></div>`;
    } else {
      const cur = w.current ? `<div class="row between"><div><div style="font-size:2.1rem;font-weight:700">${wxIcon(0).replace('☀️', '')}${n(w.current.temp, 0)}°C</div><div class="small text2">${esc(w.current.condition || '')} now</div></div><div class="small muted" style="text-align:right">${esc(w.location || '')}</div></div>` : '';
      $('#wxWidget').innerHTML = (w.stale_warning ? `<div class="notice warn small" style="margin-bottom:10px">${esc(w.stale_warning)}</div>` : '') + cur +
        `<div style="margin-top:12px">${w.forecast.slice(0, 5).map(f => `<div class="row between" style="padding:7px 0;border-top:1px solid var(--line)"><span class="small">${wxIcon(f.code)} ${dayName(f.day)}</span><span class="small text2">${esc(f.condition)}</span><b class="mono small">${n(f.temp_max, 0)}° <span class="muted">/ ${n(f.temp_min, 0)}°</span></b></div>`).join('')}</div>
         <a class="small" href="/weather" style="display:inline-block;margin-top:10px">Full forecast & impact →</a>`;
    }

    // reco widget
    const a = d.advice;
    if (!a.ready) $('#recoWidget').innerHTML = `<div class="empty"><b>No plan yet</b>${esc(a.message)}</div>`;
    else {
      const flex = a.plan.filter(x => x.priority === 'flexible');
      $('#recoWidget').innerHTML = `<p class="small" style="color:var(--text)">${esc(a.headline)}</p>
        <div style="margin-top:10px">${flex.map(x => `<div class="row between" style="padding:6px 0;border-top:1px solid var(--line)"><span class="small">${esc(x.name)}</span><b>${esc(x.duration)}</b></div>`).join('')}</div>
        <div class="row between small text2" style="margin-top:10px"><span>≈ ${n(a.estimated_units)} units tomorrow (sum of appliances) · ${a.expected_savings_units < 0 ? 'above normal by ' + n(-a.expected_savings_units) : 'saves ' + n(a.expected_savings_units)} vs normal use</span></div>
        <a class="btn btn-primary btn-sm" href="/advisor" style="margin-top:12px">Full plan & reasoning →</a>`;
    }
    $('#alertsWidget').innerHTML = d.alerts.length ? d.alerts.slice(0, 4).map(x => alertHTML(x, false)).join('') : '<div class="empty">No active alerts 🎉</div>';
  }

  /* ---- Weather-based unit allocation (daily + monthly, appliance-wise) ---- */
  const sevIcon = { critical: '🔴', warning: '🟠', info: '🔵' };
  function allocHTML(a) {
    const maxScore = Math.max(1, ...a.days.map(d => d.weather_score));
    const peak = a.peak_day;
    return `
    <div class="card">
      <div class="row between" style="align-items:flex-start;margin-bottom:12px"><div><h3 style="margin:0">Weather-based unit allocation <span class="sub">${a.remaining_days} billing days · ${a.weather_available ? 'live forecast + climate outlook' : 'typical conditions'}</span></h3>
        <div class="xs muted" style="margin-top:4px">${esc(a.basis)}</div></div></div>
      <div class="grid g4 keep2" style="margin-bottom:14px">
        <div class="tile"><div class="l">Predicted this month</div><div class="v mono">${n(a.predicted_month_units, 1)} <small class="muted" style="font-size:.7rem">units</small></div><div class="xs muted">${n(a.consumed, 1)} used + ${n(a.predicted_units_remaining, 1)} predicted</div></div>
        <div class="tile"><div class="l">Remaining budget</div><div class="v mono" style="color:${a.remaining_now < 0 ? '#f87171' : ''}">${n(a.remaining_now, 1)} <small class="muted" style="font-size:.7rem">units now</small></div><div class="xs" style="${a.over_budget ? 'color:#f87171' : 'color:var(--muted)'}">${a.over_budget ? 'Predicted ' + n(a.units_over_budget, 1) + ' over budget' : n(a.headroom_after_prediction, 1) + ' units headroom after prediction'}</div></div>
        <div class="tile"><div class="l">Highest-consumption day</div><div class="v mono" style="font-size:1.2rem">${peak ? dayName(peak.date, true) : '–'}</div><div class="xs muted">${peak ? n(peak.predicted_units, 2) + ' units' + (peak.feels_like != null ? ' · feels like ' + n(peak.feels_like, 0) + '°C' : '') + ' · ' + esc(peak.source_label) : ''}</div></div>
        <div class="tile"><div class="l">Tomorrow's predicted units</div><div class="v mono">${a.days.length ? n(a.days[0].predicted_units, 2) : '–'}</div><div class="xs muted">${a.days.length ? 'allowance ' + n(a.days[0].allowance, 2) : ''}</div></div>
      </div>
      <div class="table-wrap"><table><thead><tr><th>Day</th><th>Weather</th><th style="min-width:110px">Demand score</th><th class="num">AC</th><th class="num">Fan &amp; cooler</th><th class="num">Heater &amp; geyser</th><th class="num">Other</th><th class="num">Predicted</th><th class="num">Allowance</th></tr></thead><tbody>
      ${a.days.map(d => `<tr class="${d.over_allowance ? 'over' : ''}">
        <td>${dayName(d.date)}<div class="xs muted">${esc(d.source_label)}</div></td>
        <td><div class="small">${d.temp_max != null ? n(d.temp_max, 0) + '° / ' + n(d.temp_min, 0) + '°' : '–'}${d.feels_like != null ? ' <span class="muted">feels ' + n(d.feels_like, 0) + '°</span>' : ''}</div>
          <div class="xs muted">${esc(d.condition || '')}${d.humidity != null ? ' · 💧' + n(d.humidity, 0) + '%' : ''}${d.rain_mm != null && d.rain_mm >= 0.1 ? ' · ☔' + n(d.rain_mm, 1) + ' mm' : ''}</div></td>
        <td><div class="bar thin"><span style="width:${Math.min(100, 100 * d.weather_score / maxScore)}%"></span></div><div class="xs muted">${n(d.weather_score, 2)}×</div></td>
        <td class="num">${n(d.groups.ac, 2)}</td><td class="num">${n(d.groups.cooling, 2)}</td><td class="num">${n(d.groups.heating, 2)}</td><td class="num">${n(d.groups.other, 2)}</td>
        <td class="num"><b>${n(d.predicted_units, 2)}</b></td>
        <td class="num" style="${d.over_allowance ? 'color:#f87171' : ''}">${n(d.allowance, 2)}${d.over_allowance ? ' ▲' : ''}</td></tr>`).join('')}
      </tbody></table></div>
      <div class="xs muted" style="margin-top:6px">Allowance is each day's share of the units still available; ▲ marks days whose predicted use exceeds it. Totals are the sum of every appliance's kWh.</div>
      <h3 style="margin:18px 0 10px">When to run each appliance (tomorrow)</h3>
      <div class="table-wrap"><table><thead><tr><th>Appliance</th><th class="num">Hours</th><th class="num">kWh</th><th>Run time</th></tr></thead><tbody>
      ${(a.days[0] ? a.days[0].appliances : []).map(x => `<tr><td>${esc(x.name)}</td><td class="num">${WW.hm(x.hours)}</td><td class="num">${n(x.kwh, 2)}</td><td>${x.windows && x.windows.length ? esc(x.windows.join(', ')) : (x.hours > 0 ? 'short use, see hours' : 'Off today')}</td></tr>`).join('')}
      </tbody></table></div>
      <h3 style="margin:18px 0 10px">Weather-based energy-saving recommendations</h3>
      ${a.recommendations.map(r => `<div class="alert ${r.severity === 'critical' ? 'critical' : r.severity === 'warning' ? 'warning' : 'info'}" style="margin-top:8px"><span class="ico">${sevIcon[r.severity] || '🔵'}</span><div class="m" style="color:var(--text)">${esc(r.text)}</div></div>`).join('')}
    </div>`;
  }

  async function loadAlloc(refresh) {
    const el = $('#allocCard');
    if (!el) return;
    try {
      const a = await api('/api/allocation' + WW.q(refresh ? 'refresh=1' : ''));
      el.innerHTML = allocHTML(a);
    } catch (e) { WW.errorBox(el, 'Allocation unavailable: ' + e.message, () => loadAlloc(refresh)); }
  }

  async function load(refresh) {
    try {
      const d = await api('/api/dashboard' + WW.q(refresh ? 'refresh=1' : ''));
      if (!built) { $('#dash').innerHTML = SKELETON; mkCharts(); bindBill(); built = true; }
      render(d);
      loadAlloc(refresh);
      if (refresh) WW.toast(d.weather.available ? 'Weather refreshed' : 'Weather unavailable – ' + d.weather.reason, d.weather.available ? 'ok' : 'err');
    } catch (e) { WW.errorBox($('#dash'), e.message, () => load()); built = false; }
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
  load();
})();

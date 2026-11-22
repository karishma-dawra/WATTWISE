/* Smart Appliance Usage Advisor */
(() => {
  const { $, api, esc, n, money, dur, dayName } = WW;
  let scenario = 0, last = null;

  const barPct = (h, max) => Math.min(100, max > 0 ? 100 * h / max : 0);

  function planRows(a) {
    const max = Math.max(...a.plan.map(p => Math.max(p.hours, p.usual_hours, p.desired_hours || 0)), 1);
    return a.plan.map(p => {
      const sub = p.priority === 'essential' ? 'Essential · normal use' : p.weather_type !== 'none' ? `Weather-driven · usually ${p.usual_duration}` : `Flexible · usually ${p.usual_duration}`;
      return `<div class="plan-item" title="${esc(p.reason)}">
        <div><div class="nm">${esc(p.name)}</div><div class="tag">${sub}</div></div>
        <div class="hours-bar"><span class="ghost" style="width:${barPct(Math.max(p.usual_hours, p.desired_hours || 0), max)}%"></span><span style="width:${barPct(p.hours, max)}%"></span></div>
        <div class="dur">${esc(p.duration)}<div class="tag" style="font-weight:400">${n(p.kwh, 2)} kWh</div></div></div>`;
    }).join('');
  }

  function scenarioView(a) {
    const s = a.scenarios[scenario];
    return `<div class="row" style="margin-bottom:12px">
      <div class="pill-tabs">${a.scenarios.map((x, i) => `<button class="pill ${i === scenario ? 'active' : ''}" data-sc="${i}">${esc(x.label)}</button>`).join('')}</div></div>
      <p class="small text2" style="margin-bottom:10px">${esc(s.description)}</p>
      <div class="table-wrap"><table><thead><tr><th>Appliance</th><th class="num">Duration</th></tr></thead><tbody>
      ${s.plan.map(p => `<tr><td>${esc(p.name)}</td><td class="num">${esc(p.duration)}</td></tr>`).join('')}</tbody></table></div>
      <div class="row between small" style="margin-top:12px"><span>Tomorrow: <b>${n(s.units_tomorrow)}</b> units</span>
      <span>Month-end if repeated daily: <b style="color:${s.within_budget ? '#4ade80' : '#f87171'}">${n(s.month_end_if_repeated, 0)} units</b> ${s.within_budget ? '✓ within budget' : '✗ over budget'}</span></div>`;
  }

  function render(a) {
    last = a;
    const el = $('#advisor');
    if (!a.ready) { el.innerHTML = `<div class="card empty"><b>${esc(a.message)}</b><a class="btn btn-primary" href="/appliances" style="margin-top:12px">Add appliances</a></div>`; return; }
    const f = a.forecast;
    const wx = f.forecasted ? `${n(f.temp_max, 0)}° / ${n(f.temp_min, 0)}° · ${esc(f.condition)}` : 'Forecast unavailable';
    el.innerHTML = `
    <div class="grid g-main">
      <div class="card plan-card">
        <div class="eyebrow">Your smart energy plan</div>
        <h2 style="margin:8px 0 4px;font-size:1.5rem">${dayName(a.target_date, true)} <span class="small muted" style="font-weight:400">· ${wx}</span></h2>
        <p class="text2" style="margin-top:8px">${esc(a.headline)}</p>
        <div class="stat-tiles">
          <div class="tile" title="Sum of every appliance's predicted kWh for tomorrow"><div class="v mono">${n(a.estimated_units)}</div><div class="l">Units tomorrow · sum of ${a.plan.length} appliances</div></div>
          <div class="tile" title="Normal use (typical hours, no weather change) minus this plan"><div class="v mono" style="color:${a.expected_savings_units < 0 ? '#fbbf24' : '#4ade80'}">${a.expected_savings_units < 0 ? '+' : ''}${n(-a.expected_savings_units)}</div><div class="l">${a.expected_savings_units < 0 ? 'Units above normal' : 'Units saved'} vs normal use · ${money(Math.abs(a.expected_savings_amount))}</div></div>
          <div class="tile" title="Tomorrow's share of the units left in this billing cycle"><div class="v mono">${n(a.allowance_tomorrow ?? a.weighted_allowance)}</div><div class="l">Tomorrow's allowance · of ${n(a.remaining_units)} remaining</div></div>
        </div>
        ${a.budget_exceeded ? '<div class="notice err" style="margin-bottom:12px">Monthly budget is already used. Plan shows essentials plus minimum comfort only.</div>' : ''}
        ${a.shortfall ? '<div class="notice warn" style="margin-bottom:12px">Your remaining budget cannot fully cover even minimum comfort hours for weather-driven appliances.</div>' : ''}
        <div>${planRows(a)}</div>
        <div class="legend"><span><i style="background:rgba(255,255,255,.18)"></i>Usual / weather-desired hours</span><span><i style="background:#22c55e"></i>Recommended</span></div>
      </div>
      <div class="stack">
        <div class="card"><h3>Why this recommendation?</h3><ul class="why" style="list-style:disc;margin-top:-6px">${a.why.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>
        <div class="card"><h3>Budget breakdown</h3>
          <div class="row between small"><span class="text2">Remaining units</span><b class="mono">${n(a.remaining_units)}</b></div>
          <div class="row between small" style="margin-top:6px"><span class="text2">Remaining days</span><b class="mono">${a.remaining_days}</b></div>
          <div class="row between small" style="margin-top:6px"><span class="text2">Essential reserve</span><b class="mono">${n(a.essential_kwh, 2)}</b></div>
          <div class="row between small" style="margin-top:6px"><span class="text2">Flexible units</span><b class="mono">${n(a.flexible_units, 2)}</b></div>
          <div class="row between small" style="margin-top:6px"><span class="text2">Comfort-first would use</span><b class="mono">${n(a.unconstrained_units, 2)}</b></div></div>
      </div>
    </div>
    <div class="grid g-main2" style="margin-top:16px">
      <div class="card"><h3>Alternative usage schedules</h3><div id="scen">${scenarioView(a)}</div></div>
      <div class="card"><h3>Appliance-by-appliance reasoning</h3>
        ${a.plan.map(p => `<div style="padding:10px 0;border-top:1px solid var(--line)"><div class="row between"><b>${esc(p.name)}</b><span class="badge ${p.priority === 'essential' ? 'b-safe' : 'b-info'}">${esc(p.duration)}</span></div>
          <div class="small text2" style="margin-top:4px">${esc(p.reason)}</div>${p.window ? `<div class="xs muted" style="margin-top:4px">💡 ${esc(p.window)}</div>` : ''}</div>`).join('')}
      </div>
    </div>`;
    el.onclick = e => { const b = e.target.closest('[data-sc]'); if (b) { scenario = +b.dataset.sc; $('#scen').innerHTML = scenarioView(last); } };
  }

  async function load() {
    const el = $('#advisor');
    try {
      const a = await api('/api/recommendations');
      render(a);
    } catch (e) { WW.errorBox(el, e.message, load); }
  }

  load();
})();

/* Budget planner + automatic Smart Appliance Usage Plan (no user-entered hours) */
(() => {
  const { $, api, esc, n, money, riskBadge, riskColor, dayName, shortDate } = WW;
  let chart = null, built = false, advice = null;

  const TEMPLATE = `
  <div class="grid g4 keep2" style="margin-bottom:16px">
    <div class="card kpi"><div class="label">Total monthly units</div><div class="value mono" id="pTotal">–</div><div class="foot" id="pConsumed"></div></div>
    <div class="card kpi"><div class="label">Remaining units</div><div class="value mono" id="pRemain">–</div><div class="foot" id="pDays"></div></div>
    <div class="card kpi"><div class="label">Flat average (reference)</div><div class="value mono" id="pLimit">–</div><div class="foot">remaining ÷ days, before weather</div></div>
    <div class="card kpi"><div class="label">Projected month-end</div><div class="value mono" id="pProj">–</div><div class="foot" id="pRisk"></div></div>
  </div>

  <div class="card plan-card" id="usagePlan" style="margin-bottom:16px">
    <div class="row between" style="align-items:flex-start">
      <div><div class="eyebrow">Your Smart Appliance Usage Plan</div>
        <h2 style="margin-top:6px;font-size:1.4rem" id="planDate">–</h2>
        <div class="xs muted" style="margin-top:4px">Calculated automatically from your budget, the weather forecast and your appliances. <b>Estimates</b> — not meter readings.</div></div>
      <button class="btn btn-primary" id="applyPlan" disabled>Apply Recommended Schedule</button>
    </div>
    <div class="notice" style="margin-top:14px"><b>Why are we recommending this?</b><div class="small text2" id="planWhy" style="margin-top:4px"></div></div>
    <div id="planWarn"></div>
    <div class="grid" id="planCards" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr));margin-top:16px"></div>
    <div class="row" style="margin-top:16px;justify-content:space-between">
      <div class="small text2" id="planTotals"></div>
      <div class="xs muted">Applying changes your appliance schedule only; meter readings are untouched.</div>
    </div>
  </div>

  <div class="grid g-main" style="margin-bottom:16px">
    <div class="card"><h3>Daily allowance vs projected usage <span class="sub">units per day</span></h3><div class="chart-box tall"><canvas id="cPlan"></canvas></div>
      <div class="legend"><span><i style="background:#22c55e"></i>Weather-weighted allowance</span><span><i style="background:#3b82f6"></i>Projected usage (within allowance)</span><span><i style="background:#ef4444"></i>Projected usage (over allowance)</span></div></div>
    <div class="card"><h3>Budget outlook <span class="sub" id="planMethod"></span></h3><div id="outlook"></div></div>
  </div>
  <div class="card"><h3>Day-by-day plan</h3><div class="table-wrap"><table><thead><tr><th>Date</th><th>Weather</th><th class="num">High / Low</th><th class="num">Allowance</th><th class="num">Projected</th><th class="num">AC / cooling</th><th class="num">Heater</th><th class="num">Essentials</th><th class="num">Cumulative</th></tr></thead><tbody id="planBody"></tbody></table></div>
  <div class="hint">Allowance = that day's essential load, plus the remaining units shared in proportion to each day's weather-driven demand. Days beyond the 14-day forecast use average conditions.</div></div>`;

  function mkChart() {
    chart = new Chart($('#cPlan'), { data: { labels: [], datasets: [
      { type: 'bar', label: 'Projected usage', data: [], backgroundColor: [], borderRadius: 4, borderSkipped: 'bottom', maxBarThickness: 22, order: 2 },
      { type: 'line', label: 'Allowance', data: [], borderColor: '#22c55e', borderWidth: 2.5, pointRadius: 3, tension: .25, order: 1 },
    ] }, options: { interaction: { mode: 'index', intersect: false }, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } }, y: { beginAtZero: true, grid: { color: 'rgba(148,170,210,.08)' }, title: { display: true, text: 'Units / day' } } },
      plugins: { tooltip: { callbacks: { label: c => ` ${c.dataset.label}: ${n(c.raw, 2)} units` } } } } });
  }

  function renderPlan(a) {
    advice = a;
    const btn = $('#applyPlan');
    if (!a.ready) {
      $('#planDate').textContent = 'No plan yet';
      $('#planWhy').textContent = a.message;
      $('#planCards').innerHTML = '';
      $('#planWarn').innerHTML = '';
      $('#planTotals').textContent = '';
      btn.disabled = true;
      return;
    }
    const f = a.forecast;
    const wx = f.forecasted ? `High ${n(f.temp_max, 0)}°C · feels like ${n(f.effective_temp, 0)}°C · low ${n(f.temp_min, 0)}°C` : 'Forecast unavailable (estimate)';
    $('#planDate').textContent = `Recommended usage for ${dayName(a.target_date, true)}`;
    $('#planWhy').innerHTML = `${esc(a.explanation)}<div class="xs muted" style="margin-top:6px">${esc(wx)}</div>`;
    $('#planWarn').innerHTML = a.warning ? `<div class="notice ${a.budget_exceeded || !a.within_budget ? 'err' : 'warn'}" style="margin-top:12px">⚠ ${esc(a.warning)}</div>` : '';
    $('#planCards').innerHTML = a.plan.map(p => {
      const kind = p.priority === 'essential' ? 'Essential' : (p.weather_type !== 'none' ? 'Weather-driven' : 'Flexible');
      const bar = Math.min(100, a.plan.length && Math.max(...a.plan.map(x => x.hours)) > 0 ? 100 * p.hours / Math.max(...a.plan.map(x => x.hours)) : 0);
      return `<div class="card" style="padding:16px">
        <div class="row between"><b>${esc(p.name)}</b><span class="badge ${p.priority === 'essential' ? 'b-safe' : p.weather_type !== 'none' ? 'b-info' : 'b-demo'}">${kind}</span></div>
        <div class="xs muted" style="margin-top:2px">${esc(kind)} · ${n(p.wattage, 0)} W × ${p.quantity} · normal ${esc(p.usual_duration)}</div>
        <div class="xs" style="margin-top:2px;color:${Math.abs(p.hours - p.usual_hours) < 0.05 ? 'var(--text2)' : p.hours > p.usual_hours ? '#fbbf24' : '#7dd3fc'}">${Math.abs(p.hours - p.usual_hours) < 0.05 ? '● at normal use' : p.hours > p.usual_hours ? '▲ above normal use' : '▼ below normal use'}</div>
        <div style="font-size:1.9rem;font-weight:700;margin:10px 0 2px" class="mono">${esc(p.duration)}</div>
        <div class="xs muted">recommended operating hours</div>
        <div class="bar thin" style="margin:12px 0"><span style="width:${bar}%"></span></div>
        <div class="small" style="display:grid;grid-template-columns:1fr auto;gap:4px 10px">
          <span class="text2">Expected daily units</span><b class="mono">${n(p.kwh, 2)} kWh</b>
          <span class="text2">Estimated monthly impact</span><b class="mono">${n(p.monthly_kwh, 1)} kWh · ${n(p.monthly_share_pct, 0)}%</b>
        </div>
        <div class="xs" style="margin-top:10px;color:var(--text2)">${esc(p.reason)}</div>
        ${p.window ? `<div class="xs muted" style="margin-top:6px">💡 ${esc(p.window)}</div>` : ''}
      </div>`;
    }).join('');
    const total = a.estimated_units;
    const saved = a.expected_savings_units;
    $('#planTotals').innerHTML = `Tomorrow's total (sum of ${a.plan.length} appliances): <b>${n(total, 2)} units</b> · allowance ${n(a.allowance_tomorrow ?? a.weighted_allowance, 2)} · ${saved < 0 ? 'using <b>' + n(-saved, 2) + ' units more</b> than normal use' : 'saving <b>' + n(saved, 2) + ' units</b> vs normal use'} (${money(Math.abs(a.expected_savings_amount))})`;
    btn.disabled = false;
  }

  function renderBudget(p) {
    $('#pTotal').innerHTML = `${n(p.total_units, 0)}<small>units</small>`;
    $('#pConsumed').textContent = `${n(p.consumed)} already consumed`;
    $('#pRemain').innerHTML = `${n(p.remaining_units)}<small>units</small>`;
    $('#pRemain').style.color = p.remaining_units <= 0 ? '#f87171' : '';
    $('#pDays').textContent = `${p.remaining_days} days remaining`;
    $('#pLimit').innerHTML = `${n(p.recommended_daily_limit, 2)}<small>units/day</small>`;
    $('#pProj').innerHTML = `${n(p.projected, 0)}<small>units</small>`;
    $('#pProj').style.color = riskColor(p.risk.level);
    $('#pRisk').innerHTML = `${riskBadge(p.risk.level)} <span class="xs">${p.units_over_budget > 0 ? n(p.units_over_budget, 0) + ' units over' : 'within budget'}</span>`;
    $('#planMethod').textContent = p.method;
    $('#outlook').innerHTML = `<div class="small text2">Projected month-end: <b class="mono">${n(p.projected, 1)} units</b> of ${n(p.total_units, 0)}.</div>
      <div class="small text2" style="margin-top:6px">Estimated bill: <b class="mono">${money(p.bill.total)}</b> <span class="xs muted">(estimate)</span></div>
      <div class="bar" style="margin-top:12px"><span style="width:${Math.min(100, 100 * p.projected / (p.total_units || 1))}%;${p.units_over_budget > 0 ? 'background:linear-gradient(90deg,#f59e0b,#ef4444)' : ''}"></span></div>
      <div class="xs muted" style="margin-top:8px">Weather effect on the month-end projection: ${n(p.weather_effect, 1)} units vs a straight-line estimate.</div>`;
    const d = p.daily_plan;
    chart.data.labels = d.map(x => shortDate(x.date));
    chart.data.datasets[0].data = d.map(x => x.projected_usage);
    chart.data.datasets[0].backgroundColor = d.map(x => x.over_allowance ? '#ef4444' : '#3b82f6');
    chart.data.datasets[1].data = d.map(x => x.allowance);
    chart.update();
    $('#planBody').innerHTML = d.length ? d.map(x => `<tr class="${x.over_allowance ? 'over' : ''}"><td>${dayName(x.date)}</td>
      <td>${x.forecasted ? esc(x.condition) : '<span class="muted">avg. assumed</span>'}</td>
      <td class="num">${x.temp_max != null ? n(x.temp_max, 0) + '° / ' + n(x.temp_min, 0) + '°' : '–'}</td>
      <td class="num">${n(x.allowance, 2)}</td><td class="num" style="${x.over_allowance ? 'color:#f87171;font-weight:600' : ''}">${n(x.projected_usage, 2)}</td>
      <td class="num">${n(x.ac_demand, 2)}</td><td class="num">${n(x.heater_demand, 2)}</td><td class="num">${n(x.essential_demand, 2)}</td><td class="num">${n(x.cumulative_projected, 1)}</td></tr>`).join('')
      : '<tr><td colspan="9"><div class="empty"><b>No remaining days in this billing cycle</b>Update your cycle in Setup.</div></td></tr>';
  }

  async function load() {
    try {
      if (!built) { $('#planner').innerHTML = TEMPLATE; mkChart(); built = true; bind(); }
      const [budget, plan, appl] = await Promise.all([
        api('/api/budget/plan' + WW.q()),
        api('/api/recommendations' + WW.q()),
        api('/api/appliances'),
      ]);
      renderBudget(budget);
      renderPlan(plan);
      window.__appliances = appl;
    } catch (e) { built = false; WW.errorBox($('#planner'), e.message, load); }
  }

  function bind() {
    $('#applyPlan').onclick = async e => {
      if (!advice || !advice.ready) return;
      const lines = advice.plan.map(p => `• ${p.name}: ${p.duration}`).join('\n');
      if (!confirm(`Apply this recommended schedule to your appliances?\n\n${lines}\n\nThis changes your appliance schedule (not your meter readings). You can change it later in Appliances.`)) return;
      try {
        const r = await WW.busy(e.currentTarget, () => api('/api/recommendations/apply', { method: 'POST', body: { confirmed: true } }));
        WW.toast(`Schedule applied: ${r.changes.length} appliance(s) updated`, 'ok');
        await load();
      } catch (er) { WW.toast(er.message, 'err'); }
    };
  }

  load();
})();

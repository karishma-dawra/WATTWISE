const $ = id => document.getElementById(id);
const charts = {};
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function api(path, method = 'GET', body) {
  const r = await fetch(path, {method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json();
  if (!r.ok) { alert(j.error || 'Request failed'); throw new Error(j.error); }
  return j;
}

function chart(id, cfg) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart($(id), cfg);
}

async function load() {
  const s = await api('/api/state');
  const m = s.summary;
  $('kBudget').textContent = m.budget;
  $('kRemaining').textContent = m.remaining;
  $('kDays').textContent = `units · ${m.days_left} days left`;
  $('kLimit').textContent = m.daily_limit;
  $('kBill').textContent = '₹' + m.predicted_bill.toLocaleString();
  $('kPred').textContent = `${m.predicted_units} units predicted`;
  $('barFill').style.width = Math.min(m.percent_used, 100) + '%';
  $('barFill').style.background = m.percent_used > 90 ? 'var(--bad)' : m.percent_used > 70 ? 'var(--warn)' : 'var(--ok)';
  $('barText').textContent = `${m.used} of ${m.budget} units used (${m.percent_used}%)` +
    (m.today_used != null ? ` · today so far: ${m.today_used}` : '');

  if (document.activeElement.id !== 'budget') $('budget').value = s.settings.budget;
  if (document.activeElement.id !== 'city') $('city').value = s.settings.city;
  if (!$('aCat').options.length) $('aCat').innerHTML = s.categories.map(c => `<option value="${c}">${c.replace('_', ' ')}</option>`).join('');
  if (!$('uDay').value) $('uDay').value = s.today;

  $('alerts').innerHTML = s.alerts.map(a => `<div class="alert ${a.level}">${esc(a.text)}</div>`).join('');

  $('appTable').tBodies[0].innerHTML = s.appliances.length ? s.appliances.map(a => `
    <tr><td>${esc(a.name)}</td><td>${esc(a.category.replace('_', ' '))}</td><td>${a.watts} W × ${a.quantity}</td>
    <td><input type="number" min="0" max="24" step="0.5" value="${a.hours}" onchange="setHours(${a.id}, this.value)"></td>
    <td>${(a.watts * a.quantity * a.hours / 1000).toFixed(2)}</td>
    <td><button class="del" onclick="delApp(${a.id})">Remove</button></td></tr>`).join('')
    : '<tr><td colspan="6" class="muted">No appliances yet – add some or load the demo home.</td></tr>';
  $('topApp').textContent = s.top_appliances.length
    ? `Highest consumer: ${s.top_appliances[0].name} (${s.top_appliances[0].units_per_day} units/day, ~${s.top_appliances[0].units_per_month} units/month at comfort hours).` : '';

  $('advice').innerHTML = s.advice.length && s.appliances.length ? s.advice.map((d, i) => `
    <div class="day ${i === 0 ? 'first' : ''}">
      <h4>${i === 0 ? 'Today' : d.date} · ${d.tmax.toFixed(0)}°C / ${d.tmin.toFixed(0)}°C <span class="tag ${d.label.split(' ')[0]}">${d.label}</span></h4>
      <p>${esc(d.message)}</p>
      <p class="muted">Limit ${d.limit} units · planned ${d.total_units} units · comfort demand ${d.comfort_demand}</p>
      <div class="chips">${d.appliances.filter(a => a.flexible).map(a =>
        `<span class="chip">${esc(a.name)}: ${a.recommended_hours} h${a.recommended_hours < a.comfort_hours ? ` (wanted ${a.comfort_hours})` : ''}</span>`).join('')}</div>
    </div>`).join('') : '<p class="muted">Add appliances to get recommendations.</p>';

  $('wsrc').textContent = 'Source: ' + s.weather.source;
  $('planTable').tBodies[0].innerHTML = s.plan.slice(0, 16).map(p => `
    <tr><td>${p.date}${p.forecast ? '' : ' (est.)'}</td><td>${p.tmax.toFixed(0)} / ${p.tmin.toFixed(0)}</td>
    <td><span class="tag ${p.label.split(' ')[0]}">${p.label}</span></td><td>${p.demand}</td><td>${p.limit}</td></tr>`).join('');

  const c = s.charts;
  const labels = c.month_days.map(d => d.slice(8));
  chart('cMonth', {type: 'line', data: {labels, datasets: [
    {label: 'Actual (cumulative)', data: c.cumulative, borderColor: '#2563eb', backgroundColor: '#2563eb33', fill: true, tension: .2},
    {label: 'Predicted', data: c.predicted_cumulative, borderColor: '#d97706', borderDash: [6, 4], tension: .2},
    {label: 'Budget', data: labels.map(() => c.budget), borderColor: '#b91c1c', pointRadius: 0}]},
    options: {maintainAspectRatio: false, scales: {x: {title: {display: true, text: 'Day of month'}}, y: {title: {display: true, text: 'Units'}}}}});
  chart('cDaily', {data: {labels, datasets: [
    {type: 'bar', label: 'Actual units', data: c.daily_actual, backgroundColor: '#2563eb'},
    {type: 'line', label: 'Recommended limit', data: c.daily_limit, borderColor: '#15803d', tension: .2}]},
    options: {maintainAspectRatio: false, scales: {x: {title: {display: true, text: 'Day of month'}}, y: {beginAtZero: true, title: {display: true, text: 'Units'}}}}});
  chart('cApp', {type: 'doughnut', data: {labels: s.top_appliances.map(a => a.name),
    datasets: [{data: s.top_appliances.map(a => a.units_per_month)}]}, options: {maintainAspectRatio: false, plugins: {legend: {position: 'right'}}}});
}

window.setHours = async (id, v) => { await api('/api/appliances/' + id, 'PUT', {hours: v}); load(); };
window.delApp = async id => { await api('/api/appliances/' + id, 'DELETE'); load(); };

$('setupForm').onsubmit = async e => { e.preventDefault(); await api('/api/settings', 'POST', {budget: $('budget').value, city: $('city').value}); load(); };
$('usageForm').onsubmit = async e => { e.preventDefault(); await api('/api/usage', 'POST', {day: $('uDay').value, units: $('uUnits').value}); $('uUnits').value = ''; load(); };
$('appForm').onsubmit = async e => {
  e.preventDefault();
  await api('/api/appliances', 'POST', {name: $('aName').value, category: $('aCat').value, watts: $('aWatts').value, quantity: $('aQty').value, hours: $('aHours').value});
  e.target.reset(); $('aQty').value = 1; $('aHours').value = 4; load();
};
$('demoBtn').onclick = async () => { await api('/api/demo', 'POST'); load(); };
$('resetBtn').onclick = async () => { if (confirm('Delete all appliances and usage logs?')) { await api('/api/reset', 'POST'); load(); } };

load();

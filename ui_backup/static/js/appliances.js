/* Appliance management: CRUD, live calculations, share chart */
(() => {
  const { $, api, esc, n, dur } = WW;
  let presets = [], chart = null, editing = null, days = 30;

  async function load() {
    try {
      const s = await api('/api/appliances/summary');
      days = s.billing_days; render(s);
    } catch (e) { WW.errorBox($('#list'), e.message, load); }
  }

  function render(s) {
    const a = s.appliances;
    $('#cnt').textContent = a.length + ' item' + (a.length === 1 ? '' : 's');
    $('#stats').innerHTML = `
      <div class="card kpi"><div class="label">Total daily</div><div class="value mono">${n(s.total_daily_kwh, 2)}<small>kWh</small></div></div>
      <div class="card kpi"><div class="label">Est. monthly (${s.billing_days} d)</div><div class="value mono">${n(s.total_monthly_kwh, 1)}<small>kWh</small></div></div>
      <div class="card kpi"><div class="label">Highest consumer</div><div class="value" style="font-size:1.3rem">${s.highest ? esc(s.highest.name) : '–'}</div><div class="foot">${s.highest ? n(s.highest.daily_kwh, 2) + ' kWh/day · ' + n(s.highest.percent, 0) + '%' : ''}</div></div>
      <div class="card kpi"><div class="label">Lowest consumer</div><div class="value" style="font-size:1.3rem">${s.lowest ? esc(s.lowest.name) : '–'}</div><div class="foot">${s.lowest ? n(s.lowest.daily_kwh, 3) + ' kWh/day · ' + n(s.lowest.percent, 1) + '%' : ''}</div></div>`;
    if (!a.length) {
      $('#list').innerHTML = '<div class="empty"><b>No appliances yet</b>Add your AC, fans, fridge… so WATTWISE can plan your usage.</div>';
    } else {
      $('#list').innerHTML = `<div class="table-wrap"><table><thead><tr><th>Appliance</th><th class="num">Qty</th><th class="num">Watts</th><th class="num" title="Your entry, or an estimate from the appliance type">Hours/day</th><th class="num">kWh/day</th><th class="num">kWh/mo</th><th style="min-width:110px">Share</th><th></th></tr></thead><tbody>
      ${a.map(x => `<tr><td><b>${esc(x.name)}</b><div class="xs muted">${esc(x.category)} · ${x.priority}${x.weather_type !== 'none' ? ' · ' + x.weather_type : ''}</div></td>
        <td class="num"><button class="btn btn-sm btn-ghost" data-q="${x.id}:-1" aria-label="Decrease quantity">−</button> ${x.quantity} <button class="btn btn-sm btn-ghost" data-q="${x.id}:1" aria-label="Increase quantity">+</button></td>
        <td class="num">${n(x.wattage, 0)}</td><td class="num">${WW.hm(x.daily_hours)} <span class="xs ${x.usage_hours_entered ? 'badge b-info' : 'muted'}">${x.usage_hours_entered ? 'your entry · minimum' : 'estimate'}</span></td><td class="num"><b>${n(x.daily_kwh, 3)}</b></td><td class="num">${n(x.monthly_kwh, 1)}</td>
        <td><div class="bar thin"><span style="width:${x.percent}%"></span></div><div class="xs muted">${n(x.percent, 1)}%</div></td>
        <td class="num"><button class="btn btn-sm" data-edit="${x.id}">Edit</button> <button class="btn btn-sm btn-danger" data-del="${x.id}">Delete</button></td></tr>`).join('')}</tbody></table></div>`;
    }
    const used = a.filter(x => x.daily_kwh > 0).sort((p, q) => q.daily_kwh - p.daily_kwh);
    if (!chart) chart = new Chart($('#cShare'), { type: 'doughnut', data: { labels: [], datasets: [{ data: [], backgroundColor: WW.COLORS, borderColor: '#111a30', borderWidth: 3, hoverOffset: 6 }] },
      options: { cutout: '66%', plugins: { tooltip: { callbacks: { label: c => ` ${c.label}: ${n(c.raw, 2)} kWh/day` } } } } });
    chart.data.labels = used.map(x => x.name); chart.data.datasets[0].data = used.map(x => x.daily_kwh); chart.update();
    $('#shareLegend').innerHTML = used.map((x, i) => `<span><i style="background:${WW.COLORS[i % 8]}"></i>${esc(x.name)} ${n(x.percent, 0)}%</span>`).join('');
  }

  /* ---- modal ---- */
  const open = () => $('#modalBg').classList.add('open');
  const close = () => { $('#modalBg').classList.remove('open'); editing = null; };
  function preview() {
    const kw = (+$('#fWatt').value || 0) * (+$('#fQty').value || 0) / 1000;
    $('#fPreview').textContent = `Draws ${n(kw, 3)} kWh per hour with this quantity. Daily use is estimated from the appliance type.`;
  }
  function fill(p) {
    $('#fName').value = p.name; $('#fCategory').value = p.category; $('#fQty').value = p.quantity ?? 1;
    $('#fUsage').value = p.user_hours ?? ''; $('#fWatt').value = p.wattage; $('#fPriority').value = p.priority; $('#fWeather').value = p.weather_type; preview();
  }
  function showErrors(msg, field) {
    WW.$$('.field-err').forEach(e => e.textContent = ''); WW.$$('#appForm input').forEach(i => i.classList.remove('invalid'));
    const m = { name: 'fName', quantity: 'fQty', wattage: 'fWatt', usage_hours: 'fUsage' };
    const key = Object.keys(m).find(k => msg.toLowerCase().includes(k.replace('_', ' ')) || msg.includes(`'${k}'`) || (k === 'name' && msg.toLowerCase().includes('name')));
    if (key) { $('#' + m[key]).classList.add('invalid'); $(`.field-err[data-for="${key}"]`).textContent = msg; } else WW.toast(msg, 'err');
  }

  async function init() {
    const pr = await api('/api/presets'); presets = pr.presets;
    $('#fPreset').innerHTML = '<option value="">Choose a preset…</option>' + presets.map((p, i) => `<option value="${i}">${esc(p.name)} (${p.wattage} W)</option>`).join('') + '<option value="custom">Custom appliance</option>';
    $('#fCategory').innerHTML = pr.categories.map(c => `<option>${c}</option>`).join('');
    $('#fPreset').onchange = e => { const v = e.target.value; if (v === '' ) return; if (v === 'custom') { fill({ name: '', category: 'Other', quantity: 1, wattage: '', daily_hours: '', priority: 'flexible', weather_type: 'none' }); $('#fName').focus(); } else fill({ ...presets[+v], quantity: 1 }); };
    ['fWatt', 'fQty'].forEach(id => $('#' + id).oninput = preview);
    $('#addBtn').onclick = () => { editing = null; $('#modalTitle').textContent = 'Add appliance'; $('#fPreset').parentElement.hidden = false; $('#fPreset').value = ''; fill({ name: '', category: 'Other', quantity: 1, wattage: '', daily_hours: '', priority: 'flexible', weather_type: 'none' }); showErrors(''); open(); $('#fName').focus(); };
    $('#cancelBtn').onclick = close;
    $('#modalBg').onclick = e => { if (e.target.id === 'modalBg') close(); };
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    $('#appForm').onsubmit = async ev => {
      ev.preventDefault(); showErrors('');
      const body = { name: $('#fName').value, category: $('#fCategory').value, quantity: $('#fQty').value, wattage: $('#fWatt').value, priority: $('#fPriority').value, weather_type: $('#fWeather').value,
                     usage_hours: $('#fUsage').value };
      try {
        await WW.busy($('#saveBtn'), () => editing ? api('/api/appliances/' + editing, { method: 'PUT', body }) : api('/api/appliances', { method: 'POST', body }));
        WW.toast(editing ? 'Appliance updated' : 'Appliance added', 'ok'); close(); load();
      } catch (e) { showErrors(e.message); }
    };
    $('#list').onclick = async e => {
      const ed = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]'), q = e.target.closest('[data-q]');
      try {
        if (ed) {
          const a = (await api('/api/appliances')).find(x => x.id == ed.dataset.edit); if (!a) return;
          editing = a.id; $('#modalTitle').textContent = 'Edit ' + a.name; $('#fPreset').parentElement.hidden = true; fill(a); showErrors(''); open();
        } else if (del) {
          if (!confirm('Remove this appliance?')) return;
          await api('/api/appliances/' + del.dataset.del, { method: 'DELETE' }); WW.toast('Appliance removed', 'ok'); load();
        } else if (q) {
          const [id, d] = q.dataset.q.split(':'); const a = (await api('/api/appliances')).find(x => x.id == id);
          if (a.quantity + +d < 1) return WW.toast('Quantity must be at least 1 — delete the appliance instead', 'err');
          await api('/api/appliances/' + id, { method: 'PUT', body: { quantity: a.quantity + +d } }); load();
        }
      } catch (er) { WW.toast(er.message, 'err'); }
    };
    load();
  }
  init().catch(e => WW.errorBox($('#list'), e.message));
})();

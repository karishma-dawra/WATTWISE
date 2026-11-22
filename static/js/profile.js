/* Profile page: five tabs, each saved to the logged-in account only. */
(() => {
  const { $, $$, api, esc } = WW;
  const t = k => window.WW.t(k);  // i18n.js puts t() on window.WW, which is not the same object as common.js's WW
  const ICONS = { 'Air Conditioner': '❄️', 'Cooler': '🌬️', 'Ceiling Fan': '🌀', 'Refrigerator': '🧊', 'Geyser': '🚿',
    'Room Heater': '🔥', 'LED Bulb': '💡', 'Television': '📺', 'Laptop': '💻', 'Washing Machine': '🧺', 'Iron': '👔',
    'Water Pump': '💧', 'Microwave': '🍲' };
  const iconFor = name => ICONS[name] || '🔌';

  function showTab(name) {
    $$('.tab').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
    $$('.tabpanel').forEach(p => p.hidden = p.dataset.panel !== name);
  }
  $$('.tab').forEach(b => b.onclick = () => showTab(b.dataset.tab));

  function fieldErrors(form, msg) {
    WW.$$(`#${form} .field-err`).forEach(x => x.textContent = '');
    const map = [['full_name', /name/i], ['mobile', /mobile/i], ['email', /email/i], ['city', /city/i], ['family_size', /family/i],
      ['budget', /budget/i], ['tariff', /tariff|rate/i]];
    const hit = map.find(([, rx]) => rx.test(msg));
    const fe = hit && WW.$(`#${form} .field-err[data-for="${hit[0]}"]`);
    if (fe) fe.textContent = msg; else WW.toast(msg, 'err');
  }

  function fillPersonal(p, fam) {
    $('#p_name').value = p.full_name || ''; $('#p_mobile').value = p.mobile || ''; $('#p_email').value = p.email || '';
    $('#p_city').value = p.city || ''; $('#p_family').value = fam ?? '';
  }
  function fillMeter(h) {
    $('#m_provider').value = h.provider || ''; $('#m_consumer').value = h.consumer_number || '';
    $('#m_metertype').value = h.meter_type || 'Smart'; $('#m_connection').value = h.connection_type || 'Domestic';
    $('#m_load').value = h.sanctioned_load ?? ''; $('#m_budget').value = h.monthly_budget ?? '';
    $('#m_tariff').value = h.tariff ?? ''; $('#m_billdate').value = h.bill_date || '';
  }
  function fillHousehold(h) {
    $('#h_hometype').value = h.home_type || 'Apartment'; $('#h_rooms').value = h.rooms ?? '';
    $('#h_floors').value = h.floors ?? ''; $('#h_members').value = h.family_size ?? '';
  }
  async function renderApps() {
    const apps = await api('/api/appliances');
    $('#pApps').innerHTML = apps.length ? apps.map(a => `<div class="app-chip">
      <span class="app-ico">${iconFor(a.name)}</span><div><b>${esc(a.name)}</b><div class="xs muted">${a.quantity} × ${Math.round(a.wattage)} W · ${esc(a.category)}</div></div></div>`).join('')
      : `<div class="empty">${esc(t('profile.no_apps'))}</div>`;
  }
  async function load() {
    const data = await api('/api/profile');
    fillPersonal(data.personal, data.household.family_size);
    fillMeter(data.household); fillHousehold(data.household);
    $('#x_lang').value = data.personal.language || 'en';
    await renderApps();
  }

  $('#fPersonal').onsubmit = async ev => {
    ev.preventDefault();
    try {
      const famVal = $('#p_family').value;
      await WW.busy($('#p_save'), async () => {
        await api('/api/profile/personal', { method: 'PUT', body: { full_name: $('#p_name').value, mobile: $('#p_mobile').value,
          email: $('#p_email').value, city: $('#p_city').value } });
        if (famVal) await api('/api/profile/household', { method: 'PUT', body: { family_size: famVal } });
      });
      WW.toast(t('profile.saved'), 'ok');
      await load();
    } catch (e) { fieldErrors('fPersonal', e.message); }
  };

  $('#fMeter').onsubmit = async ev => {
    ev.preventDefault();
    try {
      await WW.busy($('#m_save'), async () => {
        await api('/api/profile/household', { method: 'PUT', body: { provider: $('#m_provider').value, consumer_number: $('#m_consumer').value,
          meter_type: $('#m_metertype').value, connection_type: $('#m_connection').value, sanctioned_load: $('#m_load').value,
          bill_date: $('#m_billdate').value } });
        await api('/api/household', { method: 'PUT', body: { tariff: $('#m_tariff').value } });
        const meter = await api('/api/meter');
        await api('/api/meter', { method: 'PUT', body: { previous_reading: meter.previous_reading ?? '', today_reading: meter.today_reading ?? '',
          monthly_budget: $('#m_budget').value } });
      });
      WW.toast(t('profile.saved'), 'ok');
      await load();
    } catch (e) { fieldErrors('fMeter', e.message); }
  };

  $('#fHousehold').onsubmit = async ev => {
    ev.preventDefault();
    try {
      await WW.busy($('#h_save'), () => api('/api/profile/household', { method: 'PUT', body: { home_type: $('#h_hometype').value,
        rooms: $('#h_rooms').value, floors: $('#h_floors').value, family_size: $('#h_members').value } }));
      WW.toast(t('profile.saved'), 'ok');
      await load();
    } catch (e) { WW.toast(e.message, 'err'); }
  };

  $('#x_lang').onchange = async e => { await WW.setLanguage(e.target.value); };

  $('#fPassword').onsubmit = async ev => {
    ev.preventDefault();
    try {
      await WW.busy($('#x_pw_save'), () => api('/api/auth/change-password', { method: 'POST',
        body: { current: $('#x_cur').value, new: $('#x_new').value } }));
      $('#x_cur').value = ''; $('#x_new').value = '';
      WW.toast(t('profile.pwdone'), 'ok');
    } catch (e) { WW.toast(e.message, 'err'); }
  };

  load().catch(e => WW.toast(e.message, 'err'));
})();

/* WATTWISE translations: one dictionary for English and Hindi.
 * Static text is marked with data-i18n="key" (text is replaced, markup is kept).
 * Dynamic text built in JavaScript uses WW.t("key").
 * The language is saved per account (server) and remembered on this device (localStorage). */
(() => {
  const DICT = {
    en: {
      'nav.dashboard': 'Dashboard', 'nav.appliances': 'Appliances', 'nav.predictions': 'Predictions',
      'nav.usage': 'Electricity Usage', 'nav.weather': 'Weather', 'nav.alerts': 'Alerts', 'nav.profile': 'Profile',
      'nav.settings': 'Settings', 'nav.login': 'Login', 'nav.register': 'Register', 'nav.logout': 'Logout',
      'common.save': 'Save', 'common.cancel': 'Cancel', 'common.edit': 'Edit', 'common.delete': 'Delete',
      'common.add': 'Add', 'common.loading': 'Loading…', 'common.saved': 'Saved', 'common.retry': 'Retry',
      'common.units': 'units', 'common.yes': 'Yes', 'common.no': 'No',
      'auth.login.title': 'Log in to WATTWISE', 'auth.login.sub': 'Use your mobile number and password.',
      'auth.login.welcome': 'Welcome back', 'auth.login.eyebrow': 'Weather-aware energy budgeting',
      'auth.login.aside_title': 'Your electricity budget, planned around the weather.',
      'auth.login.b1': "A daily plan for every appliance, shaped by tomorrow's forecast", 'auth.login.b2': 'Track your units against your monthly budget',
      'auth.login.b3': 'Get alerts before you go over', 'auth.show': 'Show', 'auth.hide': 'Hide', 'auth.capslock': 'Caps Lock is on',
      'auth.new_here': 'New to WATTWISE?', 'auth.create': 'Create an account', 'auth.mobile_bad': 'Enter a valid 10-digit mobile number',
      'auth.mobile_ph': '10-digit mobile number',
      'auth.mobile': 'Mobile number', 'auth.password': 'Password', 'auth.login.btn': 'Log in',
      'auth.wrong': 'Mobile number or password is incorrect.', 'auth.noaccount': "Don't have an account? Register",
      'auth.forgot': 'Forgot password?', 'auth.register.title': 'Create your WATTWISE account',
      'auth.register.sub': 'Each account keeps its own meter details, household and appliances.',
      'auth.fullname': 'Full name', 'auth.email': 'Email (optional)', 'auth.city': 'City',
      'auth.language': 'Preferred language', 'auth.register.btn': 'Create account',
      'auth.haveaccount': 'Already registered? Log in', 'auth.recover.title': 'Recover your password',
      'auth.recover.sub': 'Confirm your mobile number and the city you registered with.',
      'auth.newpassword': 'New password', 'auth.recover.btn': 'Set new password', 'auth.recover.done': 'Password updated. You can log in now.',
      'auth.logged_out': 'You have been logged out.',
      'profile.title': 'Profile', 'profile.sub': 'Your details, meter and household. Everything here is saved to your account.',
      'profile.tab.personal': 'Personal Information', 'profile.tab.meter': 'Electricity Meter Details',
      'profile.tab.household': 'Household Setup', 'profile.tab.appliances': 'Appliance Configuration', 'profile.tab.prefs': 'Preferences',
      'profile.fullname': 'Full name', 'profile.mobile': 'Mobile number', 'profile.email': 'Email', 'profile.city': 'City',
      'profile.family': 'Family size', 'profile.provider': 'Electricity provider (DISCOM)', 'profile.consumer': 'Consumer / meter number',
      'profile.metertype': 'Meter type', 'profile.connection': 'Connection type', 'profile.load': 'Sanctioned load (kW)',
      'profile.budget': 'Monthly electricity budget (units)', 'profile.tariff': 'Tariff per unit (₹)',
      'profile.billdate': 'Billing cycle / bill date', 'profile.hometype': 'Home type', 'profile.rooms': 'Number of rooms',
      'profile.floors': 'Number of floors', 'profile.members': 'Number of family members', 'profile.language': 'Language',
      'profile.saved': 'Profile saved', 'profile.password': 'Change password', 'profile.current': 'Current password',
      'profile.newpw': 'New password', 'profile.pwdone': 'Password changed',
      'profile.apps_hint': 'Add, edit or remove appliances on the Appliances page. Appliances you do not own are not counted.',
      'profile.apps_open': 'Open Appliances',
      'dash.title': 'Dashboard', 'dash.budget': 'Monthly budget', 'dash.predicted': 'Predicted monthly consumption',
      'dash.remaining': 'Remaining units', 'dash.bill': 'Estimated electricity bill', 'dash.weather': 'Weather',
      'dash.daily': 'Daily consumption forecast', 'dash.appliances': 'Appliance-wise expected consumption',
      'dash.recs': 'Energy-saving recommendations', 'dash.alerts': 'Recent alerts', 'dash.allocation': 'Weather-based unit allocation',
      'dash.highest': 'Highest-consumption day', 'dash.tomorrow': "Tomorrow's predicted units",
      'dash.predicted_month': 'Predicted this month', 'dash.headroom': 'Headroom after prediction',
      'dash.usage_share': 'Share of total', 'dash.hours': 'Predicted hours/day', 'dash.units': 'Predicted units',
      'meter.title': 'Electricity Usage', 'meter.sub': 'Your meter readings and the units you have left.',
      'lang.label': 'Language', 'appl.usage': 'Usage hours per day (optional)', 'appl.usage_help': 'If you enter hours, WattWise will run this appliance for at least this many hours every day in its recommendations. Leave it blank and WattWise uses a labelled estimate for this appliance type.', 'common.required': 'This field is required', 'profile.no_apps': 'No appliances yet. Add them on the Appliances page.',
      'dash.refresh': '↻ Refresh weather', 'dash.open_advisor': 'Open Smart Advisor',
      'dash.budget_used': 'Budget used', 'dash.projected_month_end': 'Predicted month-end', 'dash.view_all': 'View all',
      'dash.bill_est': 'Bill estimator', 'dash.editable': 'editable', 'dash.calculate': 'Calculate',
      'dash.bill_note': 'Estimate = units × tariff + fixed + other charges. Actual bills may include slabs, taxes and subsidies.',
      'dash.recommendation': 'Smart Recommendation', 'dash.next8': 'Next 8 days',
      'dash.next8_sub': 'Predicted use against the daily allowance. ▲ = above allowance.',
      'dash.view_table': 'View detailed table', 'dash.hide_table': 'Hide detailed table',
      'dash.schedule': "Tomorrow's appliance schedule", 'dash.analytics': 'Analytics & Trends',
      'dash.monthly': 'Monthly consumption', 'dash.actual_cum': 'Actual (cumulative)', 'dash.projected': 'Projected',
      'dash.even_pace': 'Even budget pace', 'dash.appliance_share': 'Appliance share', 'dash.daily_kwh': 'daily kWh',
      'dash.actual': 'Actual', 'dash.allowance_w': 'Weather-weighted allowance', 'dash.why': 'Why this prediction?',
      'dash.status': "You've used {pct}% of your budget. {left} units are left for {days} days.",
      'dash.status_over': "At this pace you'll be over by about {over} units.",
      'dash.no_location': 'No location set', 'dash.cycle': 'Cycle', 'dash.day': 'Day', 'dash.of': 'of',
      'dash.used_of': '{used} used of {budget} units', 'dash.days_left_limit': '{days} days left · limit {limit} units/day',
      'dash.exceed_risk': 'exceed-risk', 'dash.of_budget': 'of budget', 'dash.estimate': 'Estimate',
      'dash.slab': 'slab tariff', 'dash.flat': 'flat',
      'dash.your_readings': 'Your logged readings', 'dash.no_readings': 'No readings logged yet',
      'dash.derived': 'Derived from readings (spread evenly between them)', 'dash.no_apps': 'No appliances yet',
      'dash.add_some': 'add some', 'dash.set_location': 'Set location', 'dash.full_forecast': 'Full forecast & impact',
      'dash.no_plan': 'No plan yet', 'dash.full_plan': 'Full plan & reasoning', 'dash.no_alerts': 'No active alerts',
      'dash.no_apps_sched': 'No appliances scheduled yet.', 'dash.appliance': 'Appliance', 'dash.type': 'Type',
      'dash.run_time': 'Run time', 'dash.usual': 'Usual (h)', 'dash.total': 'Total', 'dash.allowed': 'allowed',
      'dash.over_allow': 'Above allowance', 'dash.highest_tag': 'Highest use', 'dash.no_days': 'No forecast days yet.',
      'dash.predicted_col': 'Predicted', 'dash.allowance': 'Allowance',
    },
    hi: {
      'nav.dashboard': 'डैशबोर्ड', 'nav.appliances': 'उपकरण', 'nav.predictions': 'अनुमान',
      'nav.usage': 'बिजली उपयोग', 'nav.weather': 'मौसम', 'nav.alerts': 'अलर्ट', 'nav.profile': 'प्रोफ़ाइल',
      'nav.settings': 'सेटिंग्स', 'nav.login': 'लॉगिन', 'nav.register': 'पंजीकरण', 'nav.logout': 'लॉगआउट',
      'common.save': 'सेव करें', 'common.cancel': 'रद्द करें', 'common.edit': 'संपादित करें', 'common.delete': 'हटाएं',
      'common.add': 'जोड़ें', 'common.loading': 'लोड हो रहा है…', 'common.saved': 'सेव हो गया', 'common.retry': 'फिर से कोशिश करें',
      'common.units': 'यूनिट', 'common.yes': 'हाँ', 'common.no': 'नहीं',
      'auth.login.title': 'WATTWISE में लॉगिन करें', 'auth.login.sub': 'अपना मोबाइल नंबर और पासवर्ड लिखें।',
      'auth.login.welcome': 'वापसी पर स्वागत है', 'auth.login.eyebrow': 'मौसम के अनुसार बिजली बजट',
      'auth.login.aside_title': 'मौसम के हिसाब से बनी आपकी बिजली बजट योजना।',
      'auth.login.b1': 'कल के मौसम के अनुसार हर उपकरण की दैनिक योजना', 'auth.login.b2': 'अपने मासिक बजट के मुकाबले यूनिट ट्रैक करें',
      'auth.login.b3': 'बजट पार होने से पहले अलर्ट पाएं', 'auth.show': 'दिखाएं', 'auth.hide': 'छिपाएं', 'auth.capslock': 'Caps Lock चालू है',
      'auth.new_here': 'WATTWISE में नए हैं?', 'auth.create': 'खाता बनाएं', 'auth.mobile_bad': '10 अंकों का सही मोबाइल नंबर लिखें',
      'auth.mobile_ph': '10 अंकों का मोबाइल नंबर',
      'auth.mobile': 'मोबाइल नंबर', 'auth.password': 'पासवर्ड', 'auth.login.btn': 'लॉगिन करें',
      'auth.wrong': 'मोबाइल नंबर या पासवर्ड गलत है।', 'auth.noaccount': 'खाता नहीं है? पंजीकरण करें',
      'auth.forgot': 'पासवर्ड भूल गए?', 'auth.register.title': 'अपना WATTWISE खाता बनाएं',
      'auth.register.sub': 'हर खाते में उसका अपना मीटर विवरण, घर की जानकारी और उपकरण सुरक्षित रहते हैं।',
      'auth.fullname': 'पूरा नाम', 'auth.email': 'ईमेल (वैकल्पिक)', 'auth.city': 'शहर',
      'auth.language': 'पसंदीदा भाषा', 'auth.register.btn': 'खाता बनाएं',
      'auth.haveaccount': 'पहले से खाता है? लॉगिन करें', 'auth.recover.title': 'पासवर्ड पुनः प्राप्त करें',
      'auth.recover.sub': 'अपना मोबाइल नंबर और पंजीकरण के समय दिया शहर लिखें।',
      'auth.newpassword': 'नया पासवर्ड', 'auth.recover.btn': 'नया पासवर्ड सेट करें', 'auth.recover.done': 'पासवर्ड बदल गया है। अब आप लॉगिन कर सकते हैं।',
      'auth.logged_out': 'आप लॉगआउट हो गए हैं।',
      'profile.title': 'प्रोफ़ाइल', 'profile.sub': 'आपकी जानकारी, मीटर और घर का विवरण। यहाँ सब कुछ आपके खाते में सेव होता है।',
      'profile.tab.personal': 'व्यक्तिगत जानकारी', 'profile.tab.meter': 'बिजली मीटर विवरण',
      'profile.tab.household': 'घर का विवरण', 'profile.tab.appliances': 'उपकरण विन्यास', 'profile.tab.prefs': 'प्राथमिकताएँ',
      'profile.fullname': 'पूरा नाम', 'profile.mobile': 'मोबाइल नंबर', 'profile.email': 'ईमेल', 'profile.city': 'शहर',
      'profile.family': 'परिवार के सदस्य', 'profile.provider': 'बिजली कंपनी (DISCOM)', 'profile.consumer': 'उपभोक्ता / मीटर नंबर',
      'profile.metertype': 'मीटर का प्रकार', 'profile.connection': 'कनेक्शन का प्रकार', 'profile.load': 'स्वीकृत लोड (kW)',
      'profile.budget': 'मासिक बिजली बजट (यूनिट)', 'profile.tariff': 'प्रति यूनिट दर (₹)',
      'profile.billdate': 'बिलिंग चक्र / बिल की तारीख', 'profile.hometype': 'घर का प्रकार', 'profile.rooms': 'कमरों की संख्या',
      'profile.floors': 'मंज़िलों की संख्या', 'profile.members': 'परिवार के सदस्यों की संख्या', 'profile.language': 'भाषा',
      'profile.saved': 'प्रोफ़ाइल सेव हो गई', 'profile.password': 'पासवर्ड बदलें', 'profile.current': 'मौजूदा पासवर्ड',
      'profile.newpw': 'नया पासवर्ड', 'profile.pwdone': 'पासवर्ड बदल गया',
      'profile.apps_hint': 'उपकरण जोड़ें, बदलें या हटाएं — उपकरण पेज पर। जो उपकरण आपके पास नहीं हैं, वे गिने नहीं जाते।',
      'profile.apps_open': 'उपकरण खोलें',
      'dash.title': 'डैशबोर्ड', 'dash.budget': 'मासिक बजट', 'dash.predicted': 'अनुमानित मासिक खपत',
      'dash.remaining': 'शेष यूनिट', 'dash.bill': 'अनुमानित बिजली बिल', 'dash.weather': 'मौसम',
      'dash.daily': 'दैनिक खपत का अनुमान', 'dash.appliances': 'उपकरणवार अनुमानित खपत',
      'dash.recs': 'बिजली बचाने के सुझाव', 'dash.alerts': 'हाल के अलर्ट', 'dash.allocation': 'मौसम आधारित यूनिट आवंटन',
      'dash.highest': 'सबसे अधिक खपत वाला दिन', 'dash.tomorrow': 'कल का अनुमानित उपयोग',
      'dash.predicted_month': 'इस महीने का अनुमान', 'dash.headroom': 'अनुमान के बाद बची यूनिट',
      'dash.usage_share': 'कुल का हिस्सा', 'dash.hours': 'अनुमानित घंटे/दिन', 'dash.units': 'अनुमानित यूनिट',
      'meter.title': 'बिजली उपयोग', 'meter.sub': 'आपके मीटर रीडिंग और बची हुई यूनिट।',
      'lang.label': 'भाषा', 'appl.usage': 'प्रतिदिन उपयोग के घंटे (वैकल्पिक)', 'appl.usage_help': 'अगर आप घंटे लिखते हैं, तो WattWise इस उपकरण को हर दिन कम से कम उतने घंटे चलने की सिफ़ारिश करेगा। खाली छोड़ने पर इस प्रकार के उपकरण के लिए अनुमानित घंटे लिए जाते हैं।', 'common.required': 'यह फ़ील्ड ज़रूरी है', 'profile.no_apps': 'अभी कोई उपकरण नहीं। उपकरण पेज पर जोड़ें।',
      'dash.refresh': '↻ मौसम रीफ़्रेश करें', 'dash.open_advisor': 'स्मार्ट सलाहकार खोलें',
      'dash.budget_used': 'बजट उपयोग', 'dash.projected_month_end': 'महीने के अंत का अनुमान', 'dash.view_all': 'सभी देखें',
      'dash.bill_est': 'बिल अनुमानक', 'dash.editable': 'संपादन योग्य', 'dash.calculate': 'गणना करें',
      'dash.bill_note': 'अनुमान = यूनिट × दर + स्थिर + अन्य शुल्क। असली बिल में स्लैब, कर और सब्सिडी शामिल हो सकते हैं।',
      'dash.recommendation': 'स्मार्ट सुझाव', 'dash.next8': 'अगले 8 दिन',
      'dash.next8_sub': 'अनुमानित खपत बनाम रोज़ का आवंटन। ▲ = आवंटन से ऊपर।',
      'dash.view_table': 'विस्तृत तालिका देखें', 'dash.hide_table': 'विस्तृत तालिका छिपाएं',
      'dash.schedule': 'कल का उपकरण कार्यक्रम', 'dash.analytics': 'विश्लेषण और रुझान',
      'dash.monthly': 'मासिक खपत', 'dash.actual_cum': 'वास्तविक (संचयी)', 'dash.projected': 'अनुमानित',
      'dash.even_pace': 'समान बजट गति', 'dash.appliance_share': 'उपकरण हिस्सा', 'dash.daily_kwh': 'प्रतिदिन kWh',
      'dash.actual': 'वास्तविक', 'dash.allowance_w': 'मौसम-भारित आवंटन', 'dash.why': 'यह अनुमान क्यों?',
      'dash.status': 'आपने बजट का {pct}% उपयोग किया है। {left} यूनिट {days} दिनों के लिए बची हैं।',
      'dash.status_over': 'इस गति से लगभग {over} यूनिट अधिक हो जाएंगी।',
      'dash.no_location': 'स्थान सेट नहीं', 'dash.cycle': 'चक्र', 'dash.day': 'दिन', 'dash.of': 'का',
      'dash.used_of': '{budget} में से {used} यूनिट उपयोग', 'dash.days_left_limit': '{days} दिन बाकी · सीमा {limit} यूनिट/दिन',
      'dash.exceed_risk': 'सीमा पार जोखिम', 'dash.of_budget': 'बजट का', 'dash.estimate': 'अनुमान',
      'dash.slab': 'स्लैब दर', 'dash.flat': 'स्थिर दर',
      'dash.your_readings': 'आपकी दर्ज रीडिंग', 'dash.no_readings': 'अभी कोई रीडिंग दर्ज नहीं',
      'dash.derived': 'रीडिंग से निकाला गया (बीच में समान रूप से बाँटा गया)', 'dash.no_apps': 'अभी कोई उपकरण नहीं',
      'dash.add_some': 'कुछ जोड़ें', 'dash.set_location': 'स्थान सेट करें', 'dash.full_forecast': 'पूरा पूर्वानुमान और प्रभाव',
      'dash.no_plan': 'अभी कोई योजना नहीं', 'dash.full_plan': 'पूरी योजना और कारण', 'dash.no_alerts': 'कोई सक्रिय अलर्ट नहीं',
      'dash.no_apps_sched': 'अभी कोई उपकरण शेड्यूल नहीं।', 'dash.appliance': 'उपकरण', 'dash.type': 'प्रकार',
      'dash.run_time': 'चलने का समय', 'dash.usual': 'सामान्य (घंटे)', 'dash.total': 'कुल', 'dash.allowed': 'आवंटित',
      'dash.over_allow': 'आवंटन से अधिक', 'dash.highest_tag': 'सबसे अधिक उपयोग', 'dash.no_days': 'अभी पूर्वानुमान के दिन नहीं।',
      'dash.predicted_col': 'अनुमानित', 'dash.allowance': 'आवंटन',
    },
  };

  // Logged-in: the account's saved language. Logged-out: this device's choice first, then the server default.
  let current = window.WW_LOGGED_IN ? (window.WW_LANG || 'en') : (localStorage.getItem('ww_lang') || window.WW_LANG || 'en');
  if (!DICT[current]) current = 'en';

  function t(key) {
    return (DICT[current] && DICT[current][key]) || DICT.en[key] || key;
  }

  function apply() {
    document.documentElement.lang = current;
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const v = t(el.dataset.i18n);
      if (v !== el.textContent) el.textContent = v;
    });
    document.querySelectorAll('[data-lang-set]').forEach(b => b.classList.toggle('on', b.dataset.langSet === current));
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  }

  async function setLanguage(lang) {
    if (!DICT[lang]) return;
    current = lang;
    localStorage.setItem('ww_lang', lang);
    window.WW_LANG = lang;
    apply();
    try {  // save for the logged-in account (or this session when logged out)
      await fetch('/api/lang', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ language: lang }) });
    } catch (e) { /* offline: the device setting still applies */ }
  }

  window.WW = window.WW || {};
  window.WW.t = t;
  window.WW.lang = () => current;
  window.WW.setLanguage = setLanguage;
  window.WW.applyI18n = apply;
  window.WW.I18N = DICT;

  document.addEventListener('click', e => {
    const b = e.target.closest('[data-lang-set]');
    if (b) { setLanguage(b.dataset.langSet); return; }
    if (e.target.closest('#logoutBtn')) {
      fetch('/api/auth/logout', { method: 'POST' }).finally(() => { localStorage.removeItem('ww_lang_user'); location.href = '/login?msg=out'; });
    }
  });
  apply();
})();

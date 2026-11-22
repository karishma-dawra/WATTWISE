"use strict";

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n) => String(Math.round(Number(n) * 100) / 100);

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error((data && data.error) || res.statusText);
    err.status = res.status;
    throw err;
  }
  return data;
}

let toastTimer;
function toast(message) {
  const t = $("toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3500);
}

// ---------- state ----------
const state = { household: null, appliances: [], consumption: [], advice: null, adviceError: null };

const SOURCE_LABELS = {
  live: "Live data",
  sample_hot: "Sample data: hot",
  sample_cold: "Sample data: cold",
  sample_fallback: "Sample data: live weather unavailable",
};
const SEVERITY = {
  safe: { icon: "✔", label: "Safe" },
  warning: { icon: "▲", label: "Warning" },
  critical: { icon: "✖", label: "Critical" },
};

async function loadAll() {
  const scenario = $("scenario").value;
  const [household, appliances, consumption] = await Promise.all([
    api("GET", "/api/household").catch((e) => (e.status === 404 ? null : Promise.reject(e))),
    api("GET", "/api/appliances"),
    api("GET", "/api/consumption"),
  ]);
  Object.assign(state, { household, appliances, consumption, advice: null, adviceError: null });
  if (household) {
    try {
      state.advice = await api("GET", `/api/advice?scenario=${scenario}`);
    } catch (e) {
      state.adviceError = e.message;
    }
  }
  render();
}

// ---------- rendering ----------
function render() {
  const a = state.advice;
  const source = $("source");
  source.textContent = a ? SOURCE_LABELS[a.weather_source] || "" : "";
  source.hidden = !a;

  renderBanner();
  $("onboarding").hidden = !!state.household;
  $("dashboard-content").hidden = !a;
  if (a) {
    renderTiles(a);
    renderAlerts(a);
    usageChart(a);
    forecastChart(a);
    applianceChart(a);
  }
  renderPlan(a);
  renderApplianceTable();
  renderUsageTable();
  fillHouseholdForm();
}

function renderBanner() {
  const a = state.advice;
  const messages = [];
  let error = false;
  if (state.adviceError) { messages.push(state.adviceError); error = true; }
  if (a && a.weather_source === "sample_fallback") messages.push("Live weather is unavailable, so sample weather is being used.");
  if (sessionStorage.getItem("demo") === "1") messages.push("Showing demo data: generated numbers, not real usage.");
  const b = $("banner");
  b.hidden = messages.length === 0;
  b.className = "banner" + (error ? " error" : "");
  b.innerHTML = messages.map(esc).join("<br>");
}

function renderTiles(a) {
  const b = a.budget_status, o = a.optimized_plan;
  const sev = SEVERITY[b.severity] || SEVERITY.safe;
  const color = { safe: "var(--good)", warning: "var(--warning)", critical: "var(--critical)" }[b.severity];
  const todayEntry = state.consumption.find((c) => c.date === a.today_plan.date);
  const usedToday = todayEntry ? todayEntry.units : 0;
  $("tiles").innerHTML = `
    <div class="tile">
      <div class="label">Budget used this month</div>
      <div class="value">${num(b.percent_used)}%</div>
      <div class="meter"><div style="width:${Math.min(100, b.percent_used)}%;background:${color}"></div></div>
      <div class="note">${num(b.consumed_units)} of ${num(b.monthly_budget_units)} kWh</div>
      <span class="sev ${esc(b.severity)}"><i>${sev.icon}</i>${sev.label}</span>
    </div>
    <div class="tile">
      <div class="label">Projected month total</div>
      <div class="value">${num(b.predicted_total)} kWh</div>
      <div class="note">${num(b.predicted_percent)}% of budget${b.will_exceed ? ` · over by ${num(b.excess_units)} kWh` : ""}</div>
    </div>
    <div class="tile">
      <div class="label">Allowed today</div>
      <div class="value">${num(a.daily_allocation.today_units)} kWh</div>
      <div class="note">Used so far: ${num(usedToday)} kWh · ${num(b.remaining_units)} kWh left for ${b.days_left} days</div>
    </div>
    <div class="tile">
      <div class="label">Today's plan</div>
      <div class="value">${num(o.predicted_units)} → ${num(o.adjusted_units)} kWh</div>
      <div class="note">${o.unachievable ? "Can't meet the allowance even at minimum usage" : "Optimized to fit the allowance"}</div>
    </div>`;
}

function renderAlerts(a) {
  const icon = { critical: "✖", warning: "▲" };
  const rows = a.alerts.map((x) =>
    `<div class="alert ${esc(x.severity)}"><i>${icon[x.severity] || "ℹ"}</i><div>${esc(x.message)}<div class="type">${esc(x.type)}</div></div></div>`);
  [...a.warnings, ...a.weather_warnings].forEach((n) =>
    rows.push(`<div class="alert info"><i>ℹ</i><div>${esc(n)}</div></div>`));
  $("alerts").innerHTML = rows.join("") || '<div class="empty">No alerts. Everything looks fine.</div>';
}

function windowText(w) { return `${w.start_hour}:00–${w.end_hour}:00`; }

function renderPlan(a) {
  if (!a) {
    const msg = '<div class="empty">Set your budget in Settings to see your plan.</div>';
    $("plan-today").innerHTML = msg;
    $("plan-optimized").innerHTML = msg;
    return;
  }
  const t = a.today_plan, o = a.optimized_plan;
  $("plan-today").innerHTML = `
    <p class="sub">${esc(t.date)} · ${num(t.max_temp)}° / ${num(t.min_temp)}° · humidity ${num(t.humidity)}%</p>
    <div class="table-wrap"><table>
      <tr><th>Appliance</th><th>Type</th><th class="num">Hours</th><th class="num">Units (kWh)</th><th>Time slots</th></tr>
      ${t.appliances.map((x) => `<tr><td>${esc(x.name)}</td><td>${esc(x.classification)}</td>
        <td class="num">${num(x.recommended_hours)}</td><td class="num">${num(x.units)}</td>
        <td>${x.time_slots.filter((s) => s.hours > 0).map((s) => `${esc(s.slot)} ${num(s.hours)}h`).join(", ") || "-"}</td></tr>`).join("")}
      <tr><th colspan="3">Total</th><th class="num">${num(t.total_units)}</th><th></th></tr>
    </table></div>`;

  const weekly = Object.entries(o.weekly_flexible_runs).map(([name, r]) =>
    `<li><strong>${esc(name)}</strong>: at least ${r.min_runs_per_week} runs per week, on ${r.must_run_dates.map(esc).join(", ")}</li>`).join("");
  $("plan-optimized").innerHTML = `
    <pre class="explain">${esc(o.explanation)}</pre>
    <p class="sub">Allowance ${num(o.daily_allocation)} kWh · predicted ${num(o.predicted_units)} · adjusted ${num(o.adjusted_units)} · minimum possible ${num(o.minimum_units)}</p>
    <div class="table-wrap"><table>
      <tr><th>Appliance</th><th>Type</th><th class="num">Original h</th><th class="num">Adjusted h</th><th class="num">Units (kWh)</th><th>Run between</th></tr>
      ${o.appliances.map((x) => `<tr><td>${esc(x.name)}</td><td>${esc(x.classification)}</td>
        <td class="num">${num(x.original_hours)}</td><td class="num">${num(x.adjusted_hours)}</td><td class="num">${num(x.units)}</td>
        <td>${x.time_windows.map(windowText).join(", ") || "-"}</td></tr>`).join("")}
    </table></div>
    ${weekly ? `<h2 style="margin-top:14px">Flexible appliances this week</h2><ul>${weekly}</ul>` : ""}`;
}

function renderApplianceTable() {
  const types = {};
  if (state.advice) state.advice.classification.forEach((c) => (types[c.name] = c.classification));
  const rows = state.appliances.map((a) => `<tr>
      <td>${esc(a.name)}</td><td class="num">${num(a.wattage)}</td><td class="num">${a.quantity}</td>
      <td class="num">${num(a.daily_hours)}</td><td class="num">${num((a.wattage * a.quantity * a.daily_hours) / 1000)}</td>
      <td>${esc(types[a.name] || "-")}</td>
      <td><button class="btn small secondary" data-edit="${a.id}">Edit</button>
          <button class="btn small secondary" data-del="${a.id}">Delete</button></td></tr>`).join("");
  $("appliance-table").innerHTML = state.appliances.length
    ? `<tr><th>Name</th><th class="num">Watts</th><th class="num">Qty</th><th class="num">Hours/day</th><th class="num">kWh/day</th><th>Type</th><th></th></tr>${rows}`
    : '<tr><td class="empty">No appliances yet. Add one below.</td></tr>';
}

function renderUsageTable() {
  const rows = [...state.consumption].reverse().slice(0, 31)
    .map((c) => `<tr><td>${esc(c.date)}</td><td class="num">${num(c.units)}</td></tr>`).join("");
  $("usage-table").innerHTML = state.consumption.length
    ? `<tr><th>Date</th><th class="num">Units (kWh)</th></tr>${rows}`
    : '<tr><td class="empty">Nothing logged yet.</td></tr>';
}

function fillHouseholdForm() {
  const h = state.household;
  if (!h || document.activeElement.closest("#household-form")) return;
  $("h-name").value = h.name || "My Home";
  $("h-city").value = h.city || "Delhi";
  $("h-type").value = h.budget_type || "kwh";
  $("h-budget").value = h.budget_value;
  $("h-tariff").value = h.tariff_rate || 8;
}

// ---------- charts (plain SVG) ----------
const W = 620, H = 260, M = { l: 44, r: 84, t: 10, b: 28 };
const IW = W - M.l - M.r, IH = H - M.t - M.b;

function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function gridAndAxis(max, labels, step) {
  let s = "";
  for (let i = 0; i <= 4; i++) {
    const v = (max * i) / 4, y = M.t + IH - (IH * i) / 4;
    s += `<line class="grid-line" x1="${M.l}" x2="${M.l + IW}" y1="${y}" y2="${y}"/>
          <text x="${M.l - 6}" y="${y + 4}" text-anchor="end">${num(v)}</text>`;
  }
  const band = IW / labels.length;
  labels.forEach((l, i) => {
    if (i % step === 0) s += `<text x="${M.l + band * i + band / 2}" y="${H - 8}" text-anchor="middle">${esc(l)}</text>`;
  });
  return s;
}

function legend(items) {
  return `<div class="legend">${items.map(([c, t]) => `<span style="--c:${c}">${esc(t)}</span>`).join("")}</div>`;
}

function usageChart(a) {
  const today = a.today_plan.date, ym = today.slice(0, 7), day = Number(today.slice(8));
  const logged = {};
  state.consumption.filter((c) => c.date.startsWith(ym)).forEach((c) => (logged[Number(c.date.slice(8))] = c.units));
  const share = a.budget_status.monthly_budget_units / a.budget_status.days_in_month;
  const labels = [], vals = [];
  for (let d = 1; d <= day; d++) { labels.push(String(d)); vals.push(d in logged ? logged[d] : null); }
  const max = niceMax(Math.max(share, ...vals.filter((v) => v !== null)) * 1.05);
  const band = IW / labels.length, bw = Math.min(band * 0.7, 28);
  const y = (v) => M.t + IH - (IH * v) / max;
  const step = Math.ceil(labels.length / 16);

  let svg = gridAndAxis(max, labels, step);
  vals.forEach((v, i) => {
    const x = M.l + band * i + (band - bw) / 2;
    if (v !== null) svg += `<rect x="${x}" y="${y(v)}" width="${bw}" height="${Math.max(0, M.t + IH - y(v))}" rx="3" fill="var(--s1)"/>`;
    const tip = v === null ? `Day ${labels[i]}: not logged (counted as 0)` : `Day ${labels[i]}: ${num(v)} kWh`;
    svg += `<rect x="${M.l + band * i}" y="${M.t}" width="${band}" height="${IH}" fill="transparent" data-tip="${esc(tip)}"/>`;
  });
  svg += `<line x1="${M.l}" x2="${M.l + IW}" y1="${y(share)}" y2="${y(share)}" stroke="var(--s2)" stroke-width="2" stroke-dasharray="6 4"/>
          <text x="${M.l + IW + 6}" y="${y(share) + 4}">${num(share)}/day</text>`;
  $("chart-usage").innerHTML =
    legend([["var(--s1)", "Logged usage (kWh)"], ["var(--s2)", "Even daily share of budget"]]) +
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Daily usage this month">${svg}</svg>`;
}

function forecastChart(a) {
  const pred = a.monthly_prediction.days, alloc = a.daily_allocation.days;
  const labels = pred.map((d) => d.date.slice(5));
  const series = [
    { name: "Predicted", color: "var(--s1)", values: pred.map((d) => d.predicted_units) },
    { name: "Allowed", color: "var(--s2)", values: pred.map((_, i) => (alloc[i] ? alloc[i].units : null)) },
  ];
  $("chart-forecast").innerHTML = legend(series.map((s) => [s.color, s.name + " (kWh/day)"])) + lineChart(labels, series, "forecast");
  attachCrosshair($("chart-forecast"));
}

function lineChart(labels, series, id) {
  const all = series.flatMap((s) => s.values.filter((v) => v !== null));
  const max = niceMax(Math.max(...all, 0.1) * 1.1);
  const band = IW / labels.length;
  const x = (i) => M.l + band * i + band / 2;
  const y = (v) => M.t + IH - (IH * v) / max;
  let svg = gridAndAxis(max, labels, 1);
  series.forEach((s) => {
    const pts = s.values.map((v, i) => (v === null ? null : [x(i), y(v)])).filter(Boolean);
    svg += `<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${pts.map((p) => p.join(",")).join(" ")}"/>`;
    pts.forEach((p) => (svg += `<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="${s.color}" stroke="var(--surface)" stroke-width="2"/>`));
    const last = pts[pts.length - 1];
    if (last) svg += `<text x="${last[0] + 8}" y="${last[1] + 4}">${esc(s.name)}</text>`;
  });
  svg += `<line class="cross" x1="0" x2="0" y1="${M.t}" y2="${M.t + IH}" stroke="var(--muted)" stroke-width="1" visibility="hidden"/>`;
  labels.forEach((l, i) => {
    const tip = `${l}: ` + series.map((s) => `${s.name} ${s.values[i] === null ? "-" : num(s.values[i]) + " kWh"}`).join(" · ");
    svg += `<rect x="${M.l + band * i}" y="${M.t}" width="${band}" height="${IH}" fill="transparent" data-x="${x(i)}" data-tip="${esc(tip)}"/>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Predicted versus allowed usage">${svg}</svg>`;
}

function attachCrosshair(container) {
  const cross = container.querySelector(".cross");
  container.querySelectorAll("[data-x]").forEach((r) => {
    r.addEventListener("mouseenter", () => { cross.setAttribute("x1", r.dataset.x); cross.setAttribute("x2", r.dataset.x); cross.setAttribute("visibility", "visible"); });
    r.addEventListener("mouseleave", () => cross.setAttribute("visibility", "hidden"));
  });
}

function applianceChart(a) {
  const rows = [...a.today_plan.appliances].sort((p, q) => q.units - p.units);
  const rowH = 30, left = 150, right = 70, h = rows.length * rowH + 8, w = 620;
  const max = Math.max(...rows.map((r) => r.units), 0.1);
  let svg = "";
  rows.forEach((r, i) => {
    const yy = 4 + i * rowH, len = ((w - left - right) * r.units) / max;
    svg += `<text x="${left - 8}" y="${yy + 17}" text-anchor="end">${esc(r.name)}</text>
            <rect x="${left}" y="${yy + 4}" width="${Math.max(len, 1)}" height="18" rx="3" fill="var(--s1)"/>
            <text x="${left + len + 6}" y="${yy + 17}">${num(r.units)} kWh</text>
            <rect x="0" y="${yy}" width="${w}" height="${rowH}" fill="transparent" data-tip="${esc(`${r.name}: ${num(r.recommended_hours)} h, ${num(r.units)} kWh`)}"/>`;
  });
  $("chart-appliances").innerHTML = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Energy per appliance today">${svg}</svg>`;
}

// tooltip for every element with data-tip
const tip = $("tooltip");
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest && e.target.closest("[data-tip]");
  if (!t) { tip.hidden = true; return; }
  tip.textContent = t.dataset.tip;
  tip.hidden = false;
  tip.style.left = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = e.clientY + 14 + "px";
});

// ---------- forms & actions ----------
async function guarded(action, success) {
  try {
    await action();
    if (success) toast(success);
    await loadAll();
  } catch (e) {
    toast(e.message);
  }
}

function resetApplianceForm() {
  $("appliance-form").reset();
  $("a-id").value = "";
  $("a-qty").value = 1;
  $("appliance-form-title").textContent = "Add an appliance";
  $("a-cancel").hidden = true;
}

$("appliance-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const body = { name: $("a-name").value.trim(), wattage: Number($("a-watts").value),
    quantity: Number($("a-qty").value), daily_hours: Number($("a-hours").value) };
  const id = $("a-id").value;
  guarded(() => (id ? api("PUT", `/api/appliances/${id}`, body) : api("POST", "/api/appliances", body)),
    id ? "Appliance updated" : "Appliance added").then(resetApplianceForm);
});
$("a-cancel").addEventListener("click", resetApplianceForm);

$("appliance-table").addEventListener("click", (e) => {
  const edit = e.target.dataset.edit, del = e.target.dataset.del;
  if (edit) {
    const a = state.appliances.find((x) => String(x.id) === edit);
    $("a-id").value = a.id; $("a-name").value = a.name; $("a-watts").value = a.wattage;
    $("a-qty").value = a.quantity; $("a-hours").value = a.daily_hours;
    $("appliance-form-title").textContent = "Edit appliance";
    $("a-cancel").hidden = false;
    $("a-name").focus();
  } else if (del && confirm("Delete this appliance?")) {
    guarded(() => api("DELETE", `/api/appliances/${del}`), "Appliance deleted");
  }
});

$("household-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guarded(() => api("POST", "/api/household", {
    name: $("h-name").value.trim() || "My Home", city: $("h-city").value.trim() || "Delhi",
    budget_type: $("h-type").value, budget_value: Number($("h-budget").value), tariff_rate: Number($("h-tariff").value) || 8,
  }), "Saved").then(() => (location.hash = "#dashboard"));
});

$("log-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guarded(() => api("POST", "/api/consumption", { units: Number($("l-units").value) }), "Usage logged")
    .then(() => $("log-form").reset());
});

$("demo-btn").addEventListener("click", () => {
  const level = $("demo-level").value;
  if (!confirm(`This DELETES all usage you have logged and replaces it with generated "${level}" demo data. It is not real usage. Continue?`)) return;
  guarded(async () => {
    await api("POST", "/api/demo/seed", { level, scenario: $("scenario").value });
    sessionStorage.setItem("demo", "1");
  }, "Demo data loaded");
});

$("scenario").value = localStorage.getItem("scenario") || "live";
$("scenario").addEventListener("change", () => {
  localStorage.setItem("scenario", $("scenario").value);
  guarded(async () => {});
});

// ---------- routing ----------
function route() {
  const page = (location.hash || "#dashboard").slice(1);
  const known = ["dashboard", "plan", "appliances", "usage", "settings"];
  const current = known.includes(page) ? page : "dashboard";
  known.forEach((p) => ($("page-" + p).hidden = p !== current));
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === current));
}
window.addEventListener("hashchange", route);
route();
loadAll().catch((e) => { $("banner").hidden = false; $("banner").className = "banner error"; $("banner").textContent = "Cannot reach the backend: " + e.message; });

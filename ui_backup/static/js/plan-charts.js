// Chart.js helpers for the weather-scheduling plan. Needs Chart.js (static/js/vendor/chart.umd.js) loaded first.
// Not wired into a page yet: call these from any page that has a <canvas>.
//
//   drawHourlyChart(canvas, "2026-11-23")      -> hourly feels-like temperature (line) vs planned kWh (bars)
//   drawAllocationChart(canvas, actualByDay)   -> daily allocation (bars) vs actual usage (bars)
//
// actualByDay is an optional {"YYYY-MM-DD": units} map, e.g. built from /api/dashboard charts.actual_daily.

async function drawHourlyChart(canvas, date) {
  const res = await fetch(`/api/hourly-plan?date=${encodeURIComponent(date)}`);
  if (!res.ok) throw new Error((await res.json()).error || "Could not load the hourly plan");
  const plan = await res.json();
  const kwhByHour = Array(24).fill(0);
  const tempByHour = Array(24).fill(null);
  for (const r of plan.rows) {          // rows are one per (hour, appliance); add the appliances up per hour
    kwhByHour[r.hour] += r.kwh;
    tempByHour[r.hour] = r.temp;
  }
  return new Chart(canvas, {
    data: {
      labels: [...Array(24).keys()].map(h => `${String(h).padStart(2, "0")}:00`),
      datasets: [
        { type: "bar", label: "Planned kWh", data: kwhByHour.map(v => +v.toFixed(3)), yAxisID: "kwh", backgroundColor: "rgba(37, 99, 235, 0.55)" },
        { type: "line", label: "Feels-like °C", data: tempByHour, yAxisID: "temp", borderColor: "#dc2626", tension: 0.3, pointRadius: 2 },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        kwh: { position: "left", beginAtZero: true, title: { display: true, text: "kWh per hour" } },
        temp: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "°C" } },
      },
    },
  });
}

async function drawAllocationChart(canvas, actualByDay = {}) {
  const res = await fetch("/api/daily-allocation");
  if (!res.ok) throw new Error((await res.json()).error || "Could not load the daily allocation");
  const alloc = await res.json();
  return new Chart(canvas, {
    type: "bar",
    data: {
      labels: alloc.days.map(d => d.day.slice(5)),
      datasets: [
        { label: "Allocation (kWh)", data: alloc.days.map(d => d.allocation_kwh), backgroundColor: "rgba(37, 99, 235, 0.55)" },
        { label: "Actual (kWh)", data: alloc.days.map(d => actualByDay[d.day] ?? null), backgroundColor: "rgba(245, 158, 11, 0.75)" },
      ],
    },
    options: {
      responsive: true,
      scales: { y: { beginAtZero: true, title: { display: true, text: "kWh per day" } } },
    },
  });
}

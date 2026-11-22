# WATTWISE – Smart Electricity Management Platform

Weather + Appliances + Electricity Budget + Smart Prediction, in one explainable plan.
Stack: Flask · SQLite · Chart.js · Open-Meteo (no API key).

## Run it (Windows PowerShell)

```powershell
cd "f:\jagrit data\watt-wise hackathon project"
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 . On first launch the **demo household** (Sharma family, Ludhiana, 300-unit budget) is created in `instance/wattwise.db`.
Weather needs an internet connection; if it drops, the last saved forecast is shown with a warning.

## Demo script (≈3 min)

1. **Dashboard** – budget ring, consumption, predicted month-end, risk badge, estimated bill.
2. **Weather** – live Open-Meteo 7-day forecast for Ludhiana. (Real forecast may be mild.)
3. **Planner** – drag an appliance's hours: the projected month-end and the day-by-day plan update instantly.
4. **Smart Advisor** – "Your smart energy plan": hours **and clock times** for every appliance (from tomorrow's hour-by-hour forecast), reasoning, savings and alternative schedules.
5. **Budget Planner** – drag the AC slider (e.g. 6 h → 10 h): projected month-end and day-by-day plan update instantly; days over allowance turn red.
6. **Alerts** – critical "Projected to exceed budget" appears. **Reset demo** (banner) restores everything.

## How the numbers work

* `Daily kWh = W × qty × hours / 1000`; `Monthly = Daily × days`
* Remaining = Budget − Consumed; Avg daily = Consumed ÷ elapsed days; Baseline = Consumed + Avg × remaining days
* **Weather-adjusted projection**: each remaining day = observed average × (modelled demand that day ÷ modelled demand at reference conditions).
  Cooling appliances scale with forecast max temp above a reference (default 32 °C, +5 %/°C); heating with min temp below a reference (12 °C, +8 %/°C). Both are editable in Setup. Fewer than 3 days of data → appliance model only. The dashboard projection above stays rule-based: **no ML drives it**, because a single billing cycle is too little data to train on.
* **Advisor**: flat allowance = remaining ÷ days; tomorrow's allowance is weighted by that day's modelled demand; essentials are reserved; the rest ("flexible units") is water-filled across flexible appliances with protected comfort floors for weather-driven ones; hours = allocated kWh ÷ (kW × qty).
* Risk: *Safe* < 90 % of budget projected, *Moderate* 90–100 %, *High Risk* > 100 %, *Budget Exceeded* when already over. The % probability is a heuristic uncertainty estimate.
* Bill = units × tariff + fixed + other (flat tariff estimate; actual slabs/taxes not modelled).

## Weather scheduling modules (`/api/hourly-plan`, `/api/daily-allocation`, `/api/optimize`)

A second, spec-driven path that sits beside the dashboard model. It does **not** change the dashboard numbers.

1. **Weather cache** (`weather_service`): hourly temperature, feels-like, humidity, cloud cover, solar radiation, rain and daily sunrise/sunset are cached in SQLite (`weather_hourly`, `weather.sunrise/sunset`) and refreshed every 3 hours. Days past the 16-day forecast use the 3-year archive average.
2. **Comfort need** (`weather_energy_engine.hourly_comfort_table`): feels-like drives `cool_need = clamp((T-24)/12)` and `heat_need = clamp((18-T)/13)`. AC runs only at need >= 0.1 (never at 24 °C or below), fan at 22-28 °C, cooler above 30 °C when humidity < 50 %, heater at need >= 0.1, geyser in the 2 hours before an assumed 06:00 wake time, lights after sunset or under >80 % cloud (0.3). Assumptions: occupancy defaults to home all day, and where AC (>25 °C) and fan (22-28 °C) bands overlap both run.
3. **Daily load** (`prediction_service.predict_daily`): `base + k_cool x CDH + k_heat x HDH` (x1.05 on weekends), with k measured from the hourly model. With 15 or more logged days (14 training days after the previous-day feature) a scikit-learn `LinearRegression` is trained on mean/max temperature, CDH, HDH, humidity, weekday, weekend flag and previous-day kWh, and is retrained weekly. Without scikit-learn or enough history the formula is used and the response says why.
4. **Allocation** (`allocation_service.daily_allocation`): `alloc_d = R x p_d / sum(p)`, each day kept at or above its essential floor. If the floors alone exceed the remaining units the plan is flagged infeasible with the shortfall. Recomputed on every call.
5. **Optimizer** (`allocation_service.optimize_day`): AC setpoint +1 °C steps (6 % each, up to 3), then fan instead of AC when feels-like <= 30 °C or humidity < 50 % (fan assumed at 10 % of AC power). Moving flexible loads and pre-cooling are listed but save 0 units. Essentials are never cut.
6. **Alerts**: yellow at 80 % and red at 100 % of today's allocation (against today's logged units; there is no hourly meter data), and a warning when the projection passes a tariff slab boundary.

Tables: `weather_hourly`, `hourly_plan`, `daily_plan`. Chart helpers: `static/js/plan-charts.js` (not wired into a page yet).
Tests: `tests/test_spec_modules.py` (set `WATTWISE_DB` to a throwaway file first).

## Personal notifications and appliance autofill

**Notifications** (`/notifications`, the bell in the navbar). A persistent history of alerts, separate from the live `/alerts` page.
* Table `notifications` (id, user_id, type `budget_warning | overuse | high_consumption | appliance_fault | info`, severity `info | warning | critical`, title, message, appliance_id, is_read, created_at). Deleting is a soft delete, so a deleted alert is not recreated inside its 24 hours.
* API: `GET /api/notifications` (`?unread=true`, `?severity=`; also returns `unread_count`), `PATCH /api/notifications/<id>/read`, `PATCH /api/notifications/read-all`, `DELETE /api/notifications/<id>`.
* `notification_service.create_notification(user_id, type, severity, title, message, appliance_id)` skips the same alert (same type, title and appliance) created in the last 24 hours. It is called automatically when alert conditions are found: budget at 80 % / 100 %, projected overshoot, today's allocation used up, high consumption, a sudden spike (latest day at least 1.5x the previous 7-day average), and an appliance expected to use 25 % more than usual. Conditions are checked on the dashboard, on `/api/alerts`, `/api/notifications`, `/api/catalog/search?q=`, `/api/tariffs`, when a reading is saved, and at most every 15 minutes by the notification poll.
* The page polls every 30 s, shows a toast for a new critical alert, and has All / Unread / Critical / Warning tabs, loading, error and empty states.
* `appliance_fault` is accepted but never raised automatically: there is no per-appliance meter to detect a fault.

**Appliance autofill** (Add appliance form).
* Type a model number: after a 500 ms pause the catalog (`appliances_catalog`, 55 sample rows) is searched and name, category, wattage, priority and weather sensitivity are filled. Unknown models say so; close matches are offered as suggestions.
* **Scan sticker**: take or upload a photo (or use the live camera). The browser tries a barcode/QR first (`BarcodeDetector`, with html5-qrcode as fallback), then OCR (Tesseract.js). Both libraries load from a CDN only when a scan needs them, so scanning needs internet. Only the recognised text is sent to `POST /api/catalog/parse-sticker`, which extracts model, brand, watts, volts, Hz and BEE stars with regexes and looks the model up.
* Everything stays editable and is marked "Auto-detected, please verify". The catalog is **illustrative sample data**: wattages are typical values and model numbers are representative, not verified against manufacturer catalogs. Check the sticker on the appliance itself.

## State tariffs and subsidies (`tariff_data.json`)

Indian state electricity tariffs, DISCOM/regulator details and subsidies live in `tariff_data.json`. **`null` means "not verified yet"** and the app never turns a null into a number: it shows **"Rates not verified yet"** instead.

* Setup shows the household's state: regulator, DISCOMs, effective date, source link, a slab table, fixed charge / fuel surcharge / electricity duty, billing cycle, subsidies (marked "to verify") and central schemes. Unknown values are highlighted.
* **Apply regional pricing** only works when every slab rate is known (today: Punjab). A state with any unknown slab rate (Haryana, Rajasthan, Himachal Pradesh, Bihar) cannot be applied: the household keeps its own tariff and the app asks you to enter the rates from your bill.
* When a tariff is applied, an unknown fixed charge, fuel surcharge or duty is **not** replaced by a guess or by zero: the household keeps its own values, those charges are listed as unverified, and the dashboard's bill note says they are left out of the estimate, so the real bill can be higher.
* Free-unit schemes (such as Punjab's 300 free units) are listed under subsidies but never applied automatically: the exact rule is still to verify.
* The file is checked when it loads (numbers must be numbers or null, slabs must join up, only http(s) links are kept) and re-read when it changes. A broken file keeps the last good data; `GET /api/tariffs` shows the status and each state's verification level.
* States and countries not in the file use the flat country average as before. The hard-coded Punjab figures that used to be in `region_service.py` (₹4.49 / 6.34 / 7.30, ₹180 fixed, 13 % duty) were replaced by the file's.
* To add or verify a state, edit the file: fill the values from the official tariff order, set `last_verified_on` and change `verification_status`. Tests: `tests/test_tariff_data.py` (set `WATTWISE_DB` first) and `tests/ui_tariff.js`.

## Dark and light theme

A sun / moon button in the navbar (also on the login and register pages) switches between the dark and light theme: the **sun** shows in the dark theme (click for light), the **moon** shows in the light theme (click for dark). The choice is saved in the browser (`localStorage`, key `ww_theme`), applied before the page paints so there is no flash, and followed by other open tabs. Dark is the default. Colours come from CSS variables in `static/css/style.css` (`:root` for dark, `:root[data-theme="light"]` for light); charts read the same variables (`WW.setTheme`, `WW.cssVar` in `common.js`). Tests: `tests/ui_theme.js`.

## Data honesty

* Consumption history comes only from readings the user logs. Daily values are derived by spreading units evenly between readings.
* Demo readings are simulated and labelled **demo** everywhere. "Start with my own data" wipes them.

## Layout

```
app.py  database.py  models.py  requirements.txt
services/  weather_service  prediction_service  recommendation_engine  budget_service  bill_calculator  alert_service
templates/ index dashboard setup appliances weather advisor planner alerts base
static/    css/style.css  js/{common,dashboard,appliances,recommendations,planner}.js  js/vendor/chart.umd.js
instance/  wattwise.db (created at first run)
```

REST API: `/api/household`, `/api/appliances[/<id>]`, `/api/appliances/summary`, `/api/weather`, `/api/geocode?q=`, `/api/readings`,
`/api/consumption`, `/api/budget/plan`, `/api/recommendations`, `/api/hourly-plan?date=`, `/api/daily-allocation`, `/api/optimize?date=`, `/api/alerts`, `/api/dashboard`, `/api/bill`, `/api/demo/reset`, `/api/household/clear`.
Predictions use the real hourly forecast (feels-like temperature) for the 16 forecast days and are **not** capped at the budget: the month-end projection is the expected use, and the allowances are the plan.

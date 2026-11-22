"""Personal notifications: a persistent, readable history of the alerts the app raises.

The live `alerts` table (services/alert_service.py) always mirrors the current situation and drops alerts that stop
applying. Notifications are the history on top of it: each one is created once, can be marked read or deleted, and the
same alert is never created again within DEDUPE_HOURS.

create_notification() is the single entry point. sync_from_alerts() calls it for every alert condition the app finds:
budget at 80 % / 100 %, projected overshoot, today's allocation used up, high consumption, a sudden spike in the daily
readings, and an appliance expected to use much more than usual.

`appliance_fault` is a valid type for callers, but nothing here raises it automatically: the app has no per-appliance
meter, so a fault cannot be detected, only reported by a caller that knows about one.
"""
from datetime import datetime, timedelta, timezone

from database import get_db

TYPES = ("budget_warning", "overuse", "high_consumption", "appliance_fault", "info")
SEVERITIES = ("info", "warning", "critical")
DEDUPE_HOURS = 24
KEEP_DAYS = 90                         # old rows are purged so the table does not grow forever


def _utc_ago(**delta):
    """UTC timestamp text (same format as created_at) for `now - delta`; works on SQLite and PostgreSQL."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")


SPIKE_RATIO, SPIKE_MIN_UNITS = 1.5, 2.0
OVERUSE_RATIO, OVERUSE_MIN_KWH = 1.25, 0.5

# live-alert code -> notification type. Codes not listed become "info"; SKIP codes never become notifications.
ALERT_TYPE = {
    "budget_exceeded": "budget_warning", "budget_near": "budget_warning", "projected_over": "budget_warning",
    "projected_moderate": "budget_warning", "slab_crossing": "budget_warning",
    "daily_red": "overuse", "daily_yellow": "overuse", "high_consumption": "high_consumption",
}
SKIP = {"on_track", "adjust_appliance"}


def _public(r):
    d = dict(r)
    d["is_read"] = bool(d["is_read"])
    d.pop("is_deleted", None)
    d["created_at"] = d["created_at"].replace(" ", "T") + "Z"     # stored as UTC; the browser turns it into "2 hours ago"
    return d


def create_notification(user_id, type, severity, title, message, appliance_id=None, conn=None):
    """Create a notification unless the same alert (same type, title and appliance) was already created for this user in
    the last DEDUPE_HOURS hours. Deleted notifications still count, so deleting one does not make it come straight back.
    Returns the new notification as a dict, or None when it was a duplicate."""
    if type not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
    own = conn is None
    conn = conn or get_db()
    try:
        dup = conn.execute(
            "SELECT 1 FROM notifications WHERE user_id=? AND type=? AND title=? AND appliance_id IS ? "
            "AND created_at >= ? LIMIT 1",
            (user_id, type, title, appliance_id, _utc_ago(hours=DEDUPE_HOURS))).fetchone()
        if dup:
            return None
        cur = conn.execute(
            "INSERT INTO notifications (user_id, type, severity, title, message, appliance_id) VALUES (?,?,?,?,?,?)",
            (user_id, type, severity, title, message, appliance_id))
        conn.execute("DELETE FROM notifications WHERE user_id=? AND created_at < ?", (user_id, _utc_ago(days=KEEP_DAYS)))
        conn.commit()
        return _public(conn.execute("SELECT * FROM notifications WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        if own:
            conn.close()


def list_notifications(user_id, unread=False, severity=None, limit=200):
    """Newest first. Optional filters: unread only, one severity."""
    if severity is not None and severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
    sql, args = "SELECT * FROM notifications WHERE user_id=? AND is_deleted=0", [user_id]
    if unread:
        sql += " AND is_read=0"
    if severity:
        sql += " AND severity=?"
        args.append(severity)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(int(limit))
    conn = get_db()
    try:
        return [_public(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def unread_count(user_id):
    conn = get_db()
    try:
        return conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_deleted=0 AND is_read=0", (user_id,)).fetchone()[0]
    finally:
        conn.close()


def _update(sql, args):
    conn = get_db()
    try:
        n = conn.execute(sql, args).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def mark_read(user_id, nid):
    """True if the notification exists and belongs to the user."""
    return _update("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=? AND is_deleted=0", (nid, user_id)) > 0


def mark_all_read(user_id):
    """Marks every unread notification read; returns how many changed."""
    return _update("UPDATE notifications SET is_read=1 WHERE user_id=? AND is_deleted=0 AND is_read=0", (user_id,))


def delete(user_id, nid):
    """Soft delete (the row stays so the 24-hour duplicate check still applies). True if it existed."""
    return _update("UPDATE notifications SET is_deleted=1, is_read=1 WHERE id=? AND user_id=? AND is_deleted=0", (nid, user_id)) > 0


# ------------------------------------------------------------ alert conditions -> notifications
def detect_spike(daily):
    """A sudden jump in the daily readings: the latest day is well above the average of the 7 days before it.
    daily: rows with day and units, oldest first. Returns a (severity, title, message) tuple or None."""
    rows = sorted(daily, key=lambda d: d["day"])
    if len(rows) < 8:
        return None
    last, prev = rows[-1], rows[-8:-1]
    avg = sum(d["units"] for d in prev) / len(prev)
    if avg <= 0 or last["units"] < SPIKE_RATIO * avg or last["units"] - avg < SPIKE_MIN_UNITS:
        return None
    pct = 100 * (last["units"] / avg - 1)
    sev = "critical" if last["units"] >= 2 * avg else "warning"
    return (sev, "Sudden spike in electricity use",
            f"{last['day']}: {last['units']:.1f} units, {pct:.0f}% above your average of {avg:.1f} units/day over the 7 days before.")


def appliance_overuse(apps, advice):
    """Appliances the advisor expects to run well above their usual use tomorrow.
    Returns [(appliance_id, severity, title, message)]."""
    from services.consumption import predicted_kwh
    out = []
    if not (advice or {}).get("ready"):
        return out
    by_id = {a["id"]: a for a in apps}
    for p in advice["plan"]:
        a = by_id.get(p["id"])
        if not a or p.get("priority") == "essential":
            continue
        usual = predicted_kwh(a, a.get("need_hours") or a["daily_hours"])
        if usual > 0 and p["kwh"] > OVERUSE_RATIO * usual and p["kwh"] - usual >= OVERUSE_MIN_KWH:
            out.append((a["id"], "warning", f"{a['name']} will use more than usual",
                        f"{advice['target_date']}: {p['kwh']:.1f} kWh expected against {usual:.1f} kWh on a usual day "
                        f"({100 * (p['kwh'] / usual - 1):.0f}% more), mostly because of the weather."))
    return out


def sync_from_alerts(user_id, alerts, daily=None, apps=None, advice=None):
    """Turn the alert conditions found right now into notifications (duplicates inside 24 hours are skipped).
    alerts: dicts from alert_service (code, severity, title, message). Returns the notifications actually created."""
    created = []

    def add(*args, **kw):
        n = create_notification(user_id, *args, **kw)
        if n:
            created.append(n)

    for a in alerts or []:
        if a["code"] in SKIP:
            continue
        add(ALERT_TYPE.get(a["code"], "info"), a["severity"], a["title"], a["message"])
    spike = detect_spike(daily or [])
    if spike:
        add("high_consumption", spike[0], spike[1], spike[2])
    for aid, sev, title, msg in appliance_overuse(apps or [], advice):
        add("overuse", sev, title, msg, appliance_id=aid)
    return created


_last_run = {}


def due(user_id, minutes=15):
    """True at most once per `minutes` per user: lets the notification list refresh itself without recomputing the whole
    forecast on every 30-second poll."""
    now = datetime.now(timezone.utc)
    last = _last_run.get(user_id)
    if last and (now - last).total_seconds() < minutes * 60:
        return False
    _last_run[user_id] = now
    return True

"""Local multi-user accounts (SQLite + salted password hashes; no external auth service, no OTP).

Each user owns one household row with the same id as the user, so every existing table that is keyed by
household id is automatically isolated per account.
"""
import re

from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db
from models import ValidationError

LANGUAGES = ("en", "hi")
DEMO_MOBILE = "9999999999"
DEMO_PASSWORD = "demo1234"


def normalize_mobile(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise ValidationError("Enter a valid 10-digit Indian mobile number")
    return digits


def _check_password(pw):
    if not pw or len(pw) < 6:
        raise ValidationError("Password must be at least 6 characters")
    if len(pw) > 128:
        raise ValidationError("Password is too long")


def _user_row(row):
    if row is None:
        return None
    d = dict(row)
    d.pop("password_hash", None)
    return d


def get_user(uid):
    conn = get_db()
    r = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return _user_row(r)


def register(full_name, mobile, password, email="", city="", language="en"):
    name = str(full_name or "").strip()
    if len(name) < 2 or len(name) > 80:
        raise ValidationError("Full name must be between 2 and 80 characters")
    mob = normalize_mobile(mobile)
    _check_password(password)
    email = str(email or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValidationError("Enter a valid email address or leave it blank")
    city = str(city or "").strip()[:80]
    language = language if language in LANGUAGES else "en"
    conn = get_db()
    if conn.execute("SELECT 1 FROM users WHERE mobile=?", (mob,)).fetchone():
        conn.close()
        raise ValidationError("This mobile number is already registered. Log in instead.")
    cur = conn.execute("INSERT INTO users (full_name,mobile,email,city,language,password_hash) VALUES (?,?,?,?,?,?)",
                       (name, mob, email, city, language, generate_password_hash(password)))
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    from models import create_household_row
    create_household_row(uid, city)
    return get_user(uid)


def authenticate(mobile, password):
    """Return the user for a correct mobile + password, else None (same message for both cases)."""
    try:
        mob = normalize_mobile(mobile)
    except ValidationError:
        return None
    conn = get_db()
    r = conn.execute("SELECT * FROM users WHERE mobile=?", (mob,)).fetchone()
    conn.close()
    if r is None or not check_password_hash(r["password_hash"], password or ""):
        return None
    return _user_row(r)


def recover_password(mobile, city, new_password):
    """Demo-friendly recovery: the mobile number plus the city given at registration proves ownership.
    No SMS or email is sent (this is a local demo)."""
    mob = normalize_mobile(mobile)
    _check_password(new_password)
    conn = get_db()
    r = conn.execute("SELECT * FROM users WHERE mobile=?", (mob,)).fetchone()
    if r is None or str(city or "").strip().lower() != (r["city"] or "").strip().lower():
        conn.close()
        raise ValidationError("We could not verify these details. Check your mobile number and registered city.")
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), r["id"]))
    conn.commit()
    conn.close()
    return True


def change_password(uid, current, new):
    _check_password(new)
    conn = get_db()
    r = conn.execute("SELECT password_hash FROM users WHERE id=?", (uid,)).fetchone()
    if r is None or not check_password_hash(r["password_hash"], current or ""):
        conn.close()
        raise ValidationError("Current password is incorrect")
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), uid))
    conn.commit()
    conn.close()


def update_personal(uid, data):
    """Full name, mobile (must stay unique), email, city, and language."""
    cur = get_user(uid)
    name = str(data.get("full_name", cur["full_name"]) or "").strip()
    if len(name) < 2 or len(name) > 80:
        raise ValidationError("Full name must be between 2 and 80 characters")
    mob = normalize_mobile(data.get("mobile", cur["mobile"]))
    email = str(data.get("email", cur["email"]) or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValidationError("Enter a valid email address or leave it blank")
    city = str(data.get("city", cur["city"]) or "").strip()[:80]
    conn = get_db()
    if mob != cur["mobile"] and conn.execute("SELECT 1 FROM users WHERE mobile=? AND id<>?", (mob, uid)).fetchone():
        conn.close()
        raise ValidationError("This mobile number is already used by another account")
    conn.execute("UPDATE users SET full_name=?, mobile=?, email=?, city=? WHERE id=?", (name, mob, email, city, uid))
    conn.commit()
    conn.close()
    return get_user(uid)


def set_language(uid, lang):
    if lang not in LANGUAGES:
        raise ValidationError("Language must be English (en) or Hindi (hi)")
    conn = get_db()
    conn.execute("UPDATE users SET language=? WHERE id=?", (lang, uid))
    conn.commit()
    conn.close()
    return lang


def ensure_demo_account():
    """On first run, the demo account (id 1) owns the existing household so nothing is lost."""
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    if n:
        return
    conn = get_db()
    conn.execute("INSERT INTO users (id,full_name,mobile,email,city,language,password_hash) VALUES (1,?,?,?,?,?,?)",
                 ("My Account", DEMO_MOBILE, "", "Ludhiana", "en", generate_password_hash(DEMO_PASSWORD)))
    conn.commit()
    conn.close()

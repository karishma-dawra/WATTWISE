"""Appliance catalog (autofill) and rating-sticker parsing.

* SEED          ~35 common Indian household appliances. ILLUSTRATIVE sample data: brands and wattages are typical
                values, and the model numbers are representative codes, not verified against each manufacturer's
                catalog. Always check the wattage on the appliance's own sticker. Add real models by inserting rows
                into `appliances_catalog`.
* search()      find catalog rows by model number (exact, then prefix, then contains).
* parse_sticker()  pull model, brand, wattage, voltage, frequency and BEE star rating out of OCR / barcode text.
* form_fields() map a catalog row to the Add-Appliance form (name, category, priority, weather type, wattage).
"""
import re

# (brand, model_number, type, wattage W, voltage V, BEE stars or None, annual kWh)
SEED = [
    # fans
    ("Havells", "STEALTH-AIR-1200", "Ceiling Fan", 75, 230, None, 274),
    ("Orient Electric", "AEROQUIET-1200", "Ceiling Fan", 70, 230, None, 256),
    ("Crompton", "ENERGION-HS-1200", "Ceiling Fan", 35, 230, 5, 128),
    ("Atomberg", "RENESA-1200", "BLDC Fan", 28, 230, 5, 102),
    ("Usha", "MAXX-AIR-1200", "Ceiling Fan", 78, 230, None, 285),
    ("Bajaj", "MAXIMA-1200", "Ceiling Fan", 75, 230, None, 274),
    ("Havells", "SWING-LX-400", "Table Fan", 50, 230, None, 91),
    # air conditioners
    ("Voltas", "185V-VECTRA-ELITE", "Air Conditioner", 1500, 230, 5, 760),
    ("LG", "PS-Q19YNZE", "Air Conditioner", 1300, 230, 5, 700),
    ("Daikin", "FTKM50", "Air Conditioner", 1500, 230, 3, 920),
    ("Samsung", "AR18CYLZAWK", "Air Conditioner", 1550, 230, 3, 950),
    ("Blue Star", "IC318DNUS", "Air Conditioner", 1650, 230, 3, 1010),
    # refrigerators
    ("Samsung", "RT28C3733S8", "Refrigerator", 150, 230, 3, 285),
    ("LG", "GL-S292RDSX", "Refrigerator", 120, 230, 3, 260),
    ("Whirlpool", "WDE-205-CLS", "Refrigerator", 100, 230, 3, 195),
    ("Godrej", "RT-EONVALOR-236", "Refrigerator", 180, 230, 2, 330),
    ("Haier", "HRD-1953CIS", "Refrigerator", 105, 230, 3, 205),
    # water heaters
    ("Bajaj", "SHAKTI-15L", "Geyser", 2000, 230, 3, 730),
    ("Racold", "ETERNO-25L", "Geyser", 2000, 230, 5, 690),
    ("AO Smith", "HSE-SHS-015", "Geyser", 2000, 230, 4, 700),
    ("Havells", "INSTANIO-3L", "Geyser", 3000, 230, None, 540),
    ("Crompton", "ARNO-25L", "Geyser", 2000, 230, 3, 730),
    # televisions
    ("Sony", "KD-43X75L", "Television", 100, 230, None, 146),
    ("Samsung", "UA43CUE60", "Television", 90, 230, None, 131),
    ("LG", "32LQ576", "Television", 45, 230, None, 66),
    ("Xiaomi", "L43M7-5AIN", "Television", 80, 230, None, 117),
    # washing machines
    ("LG", "T7081NDDL1", "Washing Machine", 500, 230, 5, 90),
    ("IFB", "SENATOR-6510", "Washing Machine", 800, 230, 5, 140),
    ("Samsung", "WA70BG4441", "Washing Machine", 400, 230, 5, 85),
    ("Whirlpool", "STAINWASH-ULTRA-7.5", "Washing Machine", 480, 230, 5, 88),
    # lighting
    ("Philips", "ACE-SAVER-9W", "LED Bulb", 9, 230, None, 20),
    ("Syska", "SSK-PAG-12W", "LED Bulb", 12, 230, None, 26),
    ("Wipro", "GARNET-9W", "LED Bulb", 9, 230, None, 20),
    ("Syska", "LED-BATTEN-20W", "Tube Light", 20, 230, None, 44),
    # coolers and heaters
    ("Symphony", "DIET-22I", "Air Cooler", 170, 230, None, 250),
    ("Crompton", "OZONE-75", "Air Cooler", 190, 230, None, 280),
    ("Bajaj", "PX97-TORQUE", "Air Cooler", 120, 230, None, 175),
    ("Bajaj", "MAJESTY-RX11", "Room Heater", 1500, 230, None, 550),
    ("Havells", "CISTA-2000", "Room Heater", 2000, 230, None, 730),
    # utility and kitchen
    ("Kirloskar", "CHOTU-0.5HP", "Water Pump", 370, 230, None, 135),
    ("Crompton", "AQUAGOLD-1HP", "Water Pump", 750, 230, None, 270),
    ("IFB", "20SC2", "Microwave", 1200, 230, None, 110),
    ("LG", "MC2846BV", "Microwave", 1400, 230, None, 130),
    ("Samsung", "MS23K3513AK", "Microwave", 1150, 230, None, 105),
    ("Philips", "GC1905", "Iron", 1440, 230, None, 105),
    ("Bajaj", "MAJESTY-DX-6", "Iron", 1000, 230, None, 75),
    ("Preethi", "ZODIAC-MG218", "Mixer Grinder", 750, 230, None, 45),
    ("Bajaj", "REX-500", "Mixer Grinder", 500, 230, None, 30),
    ("Prestige", "PIC-20", "Induction Cooktop", 2000, 230, None, 365),
    ("Kent", "GRAND-PLUS", "Water Purifier", 60, 230, None, 130),
    ("Prestige", "PKOSS-1.5L", "Electric Kettle", 1500, 230, None, 55),
    ("Eureka Forbes", "QUICKCLEAN-1000", "Vacuum Cleaner", 1000, 230, None, 50),
    # electronics
    ("Dell", "INSPIRON-3520", "Laptop", 65, 230, None, 95),
    ("HP", "PAVILION-15", "Laptop", 45, 230, None, 66),
    ("TP-Link", "ARCHER-C6", "Wi-Fi Router", 12, 230, None, 105),
]

# type -> (category, priority, weather_type): the same values the Add Appliance presets use
TYPE_DEFAULTS = {
    "Ceiling Fan": ("Cooling", "essential", "none"), "BLDC Fan": ("Cooling", "essential", "none"),
    "Table Fan": ("Cooling", "essential", "none"),
    "Air Conditioner": ("Cooling", "flexible", "cooling"), "Air Cooler": ("Cooling", "flexible", "cooling"),
    "Room Heater": ("Heating", "flexible", "heating"), "Geyser": ("Water Heating", "flexible", "heating"),
    "Refrigerator": ("Kitchen", "essential", "none"),
    "LED Bulb": ("Lighting", "essential", "none"), "Tube Light": ("Lighting", "essential", "none"),
    "Television": ("Entertainment", "flexible", "none"),
    "Washing Machine": ("Laundry", "flexible", "none"), "Iron": ("Laundry", "flexible", "none"),
    "Water Pump": ("Utility", "essential", "none"),
    "Microwave": ("Kitchen", "flexible", "none"), "Mixer Grinder": ("Kitchen", "flexible", "none"),
    "Induction Cooktop": ("Kitchen", "flexible", "none"), "Water Purifier": ("Kitchen", "flexible", "none"),
    "Electric Kettle": ("Kitchen", "flexible", "none"), "Vacuum Cleaner": ("Utility", "flexible", "none"),
    "Laptop": ("Electronics", "flexible", "none"), "Wi-Fi Router": ("Electronics", "flexible", "none"),
}

# brands matched in sticker text (the catalog's own brands are added to this list)
EXTRA_BRANDS = ["Whirlpool", "Panasonic", "Hitachi", "Carrier", "Lloyd", "TCL", "Haier", "Bosch", "Orpat", "Butterfly",
                "Texmo", "Aquaguard", "Sharp", "Toshiba", "Mi", "Oppo", "Vu", "Kenstar", "Maharaja", "Pigeon"]

NOT_FOUND = "Model not found in the catalog. Enter the details manually."


def norm_model(s):
    """Model numbers compare ignoring case, spaces, dashes and slashes: 'ps-q19 ynze' == 'PSQ19YNZE'."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def seed(conn):
    """Insert the seed rows that are not there yet (safe to run on every start)."""
    for brand, model, typ, watt, volt, star, kwh in SEED:
        conn.execute("INSERT INTO appliances_catalog (brand, model_number, model_key, type, wattage, voltage, star_rating, annual_kwh) "
                     "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(model_key) DO NOTHING", (brand, model, norm_model(model), typ, watt, volt, star, kwh))


def _row(r):
    d = dict(r)
    d.pop("model_key", None)
    return d


def search(conn, query, limit=8):
    """Catalog rows for a model-number query. Returns (exact_match_or_None, [rows]).
    Order: exact (normalised) match, then models starting with the query, then models containing it (min 3 characters)."""
    key = norm_model(query)
    if len(key) < 3:
        return None, []
    rows = [_row(r) for r in conn.execute(
        "SELECT * FROM appliances_catalog WHERE model_key = ? OR model_key LIKE ? OR model_key LIKE ? "
        "ORDER BY (model_key = ?) DESC, (model_key LIKE ?) DESC, model_number LIMIT ?",
        (key, key + "%", "%" + key + "%", key, key + "%", limit)).fetchall()]
    exact = next((r for r in rows if norm_model(r["model_number"]) == key), None)
    return exact, rows


def form_fields(row):
    """What to put in the Add Appliance form for a catalog row (all fields stay editable in the UI)."""
    category, priority, weather = TYPE_DEFAULTS.get(row["type"], ("Other", "flexible", "none"))
    return {"name": f"{row['brand']} {row['type']}"[:60], "category": category, "wattage": row["wattage"],
            "priority": priority, "weather_type": weather, "quantity": 1}


# ------------------------------------------------------------------ sticker parsing
_WATT = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?|\d{3,5})\s*(kW|W|Watts?)\b", re.I)
_WATT_LABEL = re.compile(r"(?:rated\s*power|power\s*consumption|input\s*power|power|input|wattage|consumption|rating)\D{0,12}$", re.I)
_VOLT_RANGE = re.compile(r"(\d{3})\s*(?:-|–|~|to)\s*(\d{3})\s*V", re.I)
_VOLT = re.compile(r"(\d{3})\s*(?:V|Volts?)\b", re.I)
_FREQ = re.compile(r"(\d{2})\s*(?:-\s*\d{2}\s*)?Hz", re.I)
_STAR = re.compile(r"(?:(\d)\s*[- ]?\s*star|star\s*(?:rating|label)?\s*[:\-]?\s*(\d)|(\d)\s*★)", re.I)
_MODEL = re.compile(r"(?:model|mod\.?|m/n|type)\s*(?:no\.?|number|#)?\s*[:\-.=]?\s*([A-Z0-9][A-Z0-9\-/.]{3,24})", re.I)


def _watts(text):
    """Best wattage in the text: a value next to a power label wins, otherwise the largest plausible value."""
    found = []
    for m in _WATT.finditer(text):
        v = float(m.group(1).replace(",", "."))
        if m.group(2).lower() == "kw":
            v *= 1000
        if 1 <= v <= 10000:
            labelled = bool(_WATT_LABEL.search(text[max(0, m.start() - 24):m.start()]))
            found.append((labelled, v))
    if not found:
        return None
    labelled = [v for lab, v in found if lab]
    return labelled[0] if labelled else max(v for _, v in found)


def parse_sticker(conn, text):
    """Extract what is printed on a rating sticker (OCR or barcode text), then look the model up in the catalog.
    Returns {"fields": {...only what was found...}, "match": catalog row or None, "form": suggested form values}.
    A catalog match fills the whole form; without one only the values found on the sticker are returned."""
    text = re.sub(r"[ \t]+", " ", str(text or ""))[:5000]
    fields = {}
    w = _watts(text)
    if w:
        fields["wattage"] = round(w, 1) if w != int(w) else int(w)
    m = _VOLT_RANGE.search(text)
    if m:
        fields["voltage"] = round((int(m.group(1)) + int(m.group(2))) / 2)
    else:
        m = _VOLT.search(text)
        if m:
            fields["voltage"] = int(m.group(1))
    m = _FREQ.search(text)
    if m:
        fields["frequency"] = int(m.group(1))
    m = _STAR.search(text)
    if m:
        stars = int(next(g for g in m.groups() if g))
        if 1 <= stars <= 5:
            fields["star_rating"] = stars

    brands = {r["brand"] for r in conn.execute("SELECT DISTINCT brand FROM appliances_catalog")} | set(EXTRA_BRANDS)
    for b in sorted(brands, key=len, reverse=True):
        if re.search(r"(?<![A-Za-z])" + re.escape(b) + r"(?![A-Za-z])", text, re.I):
            fields["brand"] = b
            break

    # model: the labelled value first, then any token in the text that is a catalog model
    match = None
    labelled = [x.rstrip(".-/") for x in _MODEL.findall(text)]
    for cand in labelled:
        exact, _ = search(conn, cand, 1)
        if exact:
            match = exact
            break
    if match is None:
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/.]{3,24}", text):
            exact, _ = search(conn, token, 1)
            if exact:
                match = exact
                break
    if match:
        fields["model_number"] = match["model_number"]
        fields.setdefault("brand", match["brand"])
    elif labelled:
        fields["model_number"] = labelled[0]
    return {"fields": fields, "match": match, "form": form_fields(match) if match else None,
            "message": None if match else NOT_FOUND}

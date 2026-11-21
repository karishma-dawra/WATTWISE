from flask import Flask, request, jsonify
from flask_cors import CORS
from database import db_connection, init_db

#create the FLASK
app = Flask(__name__)

#enable CORS
CORS(app)

#create databse automatically
init_db()

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "WattWise backend is running!"}), 200

#save or update household budget
@app.route("/api/household", methods=["POST"])
def save_household():
    data = request.get_json()  # Read the JSON data sent by the frontend
    budget_value = data.get("budget_value")

    if budget_value is None:
        return jsonify({"error": "budget_value is required"}), 400

    name = data.get("name", "My Home")
    city = data.get("city", "Delhi")
    latitude = float(data.get("latitude", 28.6139))
    longitude = float(data.get("longitude", 77.2090))
    budget_type = data.get("budget_type", "kwh").lower()  # 'kwh' or 'rupees'
    tariff_rate = float(data.get("tariff_rate", 8.0))
    days_left = int(data.get("days_left", 30))
    budget_value = float(budget_value)

    # If the user entered Rupees, convert it into kWh units
    if budget_type == "rupees":
        monthly_budget_kwh = round(budget_value / tariff_rate, 2) if tariff_rate > 0 else 0.0
    else:
        monthly_budget_kwh = budget_value

    conn = db_connection()
    cursor = conn.cursor()

    # Check if a household already exists
    existing = cursor.execute("SELECT id FROM households WHERE id = 1").fetchone()

    if existing:
        cursor.execute("""
            UPDATE households
            SET name = ?, city = ?, latitude = ?, longitude = ?, budget_type = ?,
                budget_value = ?, monthly_budget_kwh = ?, tariff_rate = ?, days_left = ?
            WHERE id = 1
        """, (name, city, latitude, longitude, budget_type, budget_value, monthly_budget_kwh, tariff_rate, days_left))
    else:
        cursor.execute("""
            INSERT INTO households (id, name, city, latitude, longitude, budget_type,
                                    budget_value, monthly_budget_kwh, tariff_rate, days_left)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, city, latitude, longitude, budget_type, budget_value, monthly_budget_kwh, tariff_rate, days_left))

    conn.commit()
    conn.close()

    return jsonify({
        "message": "Household setup saved!",
        "name": name,
        "city": city,
        "monthly_budget_kwh": monthly_budget_kwh
    }), 201

@app.route("/api/household", methods=["GET"])
def get_household():
    conn = db_connection()
    row = conn.execute("SELECT * FROM households WHERE id = 1").fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "No household saved yet"}), 404

    return jsonify(dict(row)), 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)

@app.route("/api/appliances", methods=["POST"])
def add_appliance():
    data = request.get_json()
    name = data.get("name")
    wattage = data.get("wattage")
    daily_hours = data.get("daily_hours")
    quantity = int(data.get("quantity", 1))
    is_weather_sensitive = int(bool(data.get("is_weather_sensitive", False)))

    if not name or wattage is None or daily_hours is None:
        return jsonify({"error": "name, wattage, and daily_hours are required"}), 400

    conn = db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO appliances (household_id, name, wattage, quantity, daily_hours, is_weather_sensitive)
        VALUES (1, ?, ?, ?, ?, ?)
    """, (name, float(wattage), quantity, float(daily_hours), is_weather_sensitive))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({"message": "Appliance added", "id": new_id}), 201


# --- TASK 6: VIEW APPLIANCES API ---
@app.route("/api/appliances", methods=["GET"])
def view_appliances():
    conn = db_connection()
    rows = conn.execute("SELECT * FROM appliances WHERE household_id = 1").fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows]), 200


# --- TASK 7: EDIT & DELETE APPLIANCE APIs ---
@app.route("/api/appliances/<int:appliance_id>", methods=["PUT"])
def edit_appliance(appliance_id):
    data = request.get_json()
    conn = db_connection()
    cursor = conn.cursor()

    existing = cursor.execute("SELECT * FROM appliances WHERE id = ?", (appliance_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Appliance not found"}), 404

    name = data.get("name", existing["name"])
    wattage = float(data.get("wattage", existing["wattage"]))
    quantity = int(data.get("quantity", existing["quantity"]))
    daily_hours = float(data.get("daily_hours", existing["daily_hours"]))
    is_weather_sensitive = int(bool(data.get("is_weather_sensitive", existing["is_weather_sensitive"])))

    cursor.execute("""
        UPDATE appliances
        SET name = ?, wattage = ?, quantity = ?, daily_hours = ?, is_weather_sensitive = ?
        WHERE id = ?
    """, (name, wattage, quantity, daily_hours, is_weather_sensitive, appliance_id))

    conn.commit()
    conn.close()
    return jsonify({"message": "Appliance updated successfully"}), 200


@app.route("/api/appliances/<int:appliance_id>", methods=["DELETE"])
def delete_appliance(appliance_id):
    conn = db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM appliances WHERE id = ?", (appliance_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted == 0:
        return jsonify({"error": "Appliance not found"}), 404
    return jsonify({"message": "Appliance deleted successfully"}), 200
    
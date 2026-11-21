import sqlite3

DB_NAME = "wattwise.db"

def db_connection():
    conn=sqlite3.connect(DB_NAME)

    conn.row_factory= sqlite3.Row

    conn.execute("PRAGMA foreign_keys= ON")

    return conn

#creating database tables
def init_db():
    conn=db_connection()
    cursor=conn.cursor()

    #household table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS household(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT 'My Home',
            city TEXT NOT NULL DEFAULT 'Delhi',
            latitude REAL NOT NULL DEFAULT 28.6139,
            longitude REAL NOT NULL DEFAULT 77.2090,
            budget_type TEXT NOT NULL DEFAULT 'kwh',
            budget_value REAL NOT NULL,
            monthly_budget_kwh REAL NOT NULL,
            tariff_rate REAL NOT NULL DEFAULT 8.0,
            days_left INTEGER NOT NULL DEFAULT 30)""")

    #appliances table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appliances(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            household_id INTEGER NOT NULL DEFAULT 1,
            name TEXT NOT NULL,
            wattage REAL NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            daily_hours REAL NOT NULL,
            is_weather_sensitive INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (household_id) REFERENCES households (id)
        )""")

    conn.commit()
    conn.close()

if __name__=="__main__":
    init_db()
    print("WattWise SQLite database initialized!")
    
    
    

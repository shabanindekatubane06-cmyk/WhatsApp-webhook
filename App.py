import os
import sqlite3
import secrets
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

DATABASE = os.getenv("DATABASE_PATH", "rides.db")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
GRAPH_VERSION = os.getenv("WHATSAPP_GRAPH_VERSION", "v23.0")


# ---------- DATABASE ----------

def db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = db()

    connection.executescript("""
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        phone TEXT UNIQUE NOT NULL,
        address TEXT,
        verification_status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS drivers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        phone TEXT UNIQUE NOT NULL,
        licence_number TEXT,
        verification_status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS vehicles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER NOT NULL,
        registration_number TEXT UNIQUE NOT NULL,
        make TEXT,
        model TEXT,
        year INTEGER,
        colour TEXT,
        verification_status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        FOREIGN KEY(driver_id) REFERENCES drivers(id)
    );

    CREATE TABLE IF NOT EXISTS rides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        pickup TEXT NOT NULL,
        destination TEXT NOT NULL,
        status TEXT DEFAULT 'requested',
        driver_id INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY(customer_id) REFERENCES customers(id),
        FOREIGN KEY(driver_id) REFERENCES drivers(id)
    );

    CREATE TABLE IF NOT EXISTS webhook_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT UNIQUE,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)

    connection.commit()
    connection.close()


def now():
    return datetime.now(timezone.utc).isoformat()


# ---------- HEALTH ----------

@app.get("/")
def home():
    return jsonify({
        "service": "WhatsApp Ride Booking API",
        "status": "online"
    })


@app.get("/health")
def health():
    return jsonify({"status": "healthy"})


# ---------- CUSTOMER ----------

@app.post("/customers")
def create_customer():
    data = request.get_json(silent=True) or {}

    name = data.get("full_name")
    phone = data.get("phone")
    address = data.get("address")

    if not name or not phone:
        return jsonify({
            "error": "full_name and phone are required"
        }), 400

    connection = db()

    try:
        cursor = connection.execute("""
            INSERT INTO customers
            (full_name, phone, address, created_at)
            VALUES (?, ?, ?, ?)
        """, (name, phone, address, now()))

        connection.commit()

        return jsonify({
            "customer_id": cursor.lastrowid,
            "status": "pending_verification"
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({
            "error": "A customer with this phone number already exists"
        }), 409

    finally:
        connection.close()


# ---------- DRIVER ----------

@app.post("/drivers")
def create_driver():
    data = request.get_json(silent=True) or {}

    name = data.get("full_name")
    phone = data.get("phone")
    licence = data.get("licence_number")

    if not name or not phone:
        return jsonify({
            "error": "full_name and phone are required"
        }), 400

    connection = db()

    try:
        cursor = connection.execute("""
            INSERT INTO drivers
            (full_name, phone, licence_number, created_at)
            VALUES (?, ?, ?, ?)
        """, (name, phone, licence, now()))

        connection.commit()

        return jsonify({
            "driver_id": cursor.lastrowid,
            "status": "pending_verification"
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({
            "error": "A driver with this phone number already exists"
        }), 409

    finally:
        connection.close()


# ---------- VEHICLE / CAR REGISTRATION ----------

@app.post("/vehicles")
def register_vehicle():
    data = request.get_json(silent=True) or {}

    driver_id = data.get("driver_id")
    registration = data.get("registration_number")

    if not driver_id or not registration:
        return jsonify({
            "error": "driver_id and registration_number are required"
        }), 400

    connection = db()

    driver = connection.execute(
        "SELECT id FROM drivers WHERE id = ?",
        (driver_id,)
    ).fetchone()

    if not driver:
        connection.close()
        return jsonify({"error": "Driver not found"}), 404

    try:
        cursor = connection.execute("""
            INSERT INTO vehicles
            (driver_id, registration_number, make, model, year, colour, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            driver_id,
            registration,
            data.get("make"),
            data.get("model"),
            data.get("year"),
            data.get("colour"),
            now()
        ))

        connection.commit()

        return jsonify({
            "vehicle_id": cursor.lastrowid,
            "status": "pending_vehicle_verification"
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({
            "error": "That registration number is already registered"
        }), 409

    finally:
        connection.close()


# ---------- RIDE REQUEST ----------

@app.post("/rides")
def create_ride():
    data = request.get_json(silent=True) or {}

    customer_id = data.get("customer_id")
    pickup = data.get("pickup")
    destination = data.get("destination")

    if not customer_id or not pickup or not destination:
        return jsonify({
            "error": "customer_id, pickup and destination are required"
        }), 400

    connection = db()

    customer = connection.execute(
        "SELECT id FROM customers WHERE id = ?",
        (customer_id,)
    ).fetchone()

    if not customer:
        connection.close()
        return jsonify({"error": "Customer not found"}), 404

    cursor = connection.execute("""
        INSERT INTO rides
        (customer_id, pickup, destination, status, created_at)
        VALUES (?, ?, ?, 'requested', ?)
    """, (
        customer_id,
        pickup,
        destination,
        now()
    ))

    connection.commit()

    ride_id = cursor.lastrowid
    connection.close()

    return jsonify({
        "ride_id": ride_id,
        "status": "requested",
        "message": "Ride request created"
    }), 201


# ---------- WHATSAPP WEBHOOK VERIFICATION ----------

@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200

    return "Verification failed", 403


# ---------- WHATSAPP WEBHOOK RECEIVER ----------

@app.post("/webhook")
def receive_webhook():
    payload = request.get_json(silent=True) or {}

    # Store the event so it can be processed safely later.
    connection = db()

    event_id = secrets.token_hex(16)

    connection.execute("""
        INSERT INTO webhook_events
        (event_id, payload, created_at)
        VALUES (?, ?, ?)
    """, (
        event_id,
        str(payload),
        now()
    ))

    connection.commit()
    connection.close()

    # Return immediately so Meta knows the webhook was received.
    return jsonify({"received": True}), 200


# ---------- ADMIN / STATUS ----------

@app.get("/admin/summary")
def admin_summary():
    connection = db()

    customers = connection.execute(
        "SELECT COUNT(*) AS count FROM customers"
    ).fetchone()["count"]

    drivers = connection.execute(
        "SELECT COUNT(*) AS count FROM drivers"
    ).fetchone()["count"]

    vehicles = connection.execute(
        "SELECT COUNT(*) AS count FROM vehicles"
    ).fetchone()["count"]

    rides = connection.execute(
        "SELECT COUNT(*) AS count FROM rides"
    ).fetchone()["count"]

    connection.close()

    return jsonify({
        "customers": customers,
        "drivers": drivers,
        "vehicles": vehicles,
        "rides": rides
    })


# ---------- START ----------

init_db()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

import os, sqlite3, secrets, math
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, abort
from werkzeug.security import generate_password_hash, check_password_hash
from sklearn.ensemble import RandomForestRegressor
import numpy as np

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "livetrack.db")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-development-secret")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS participants (
            id TEXT PRIMARY KEY,
            room_code TEXT NOT NULL,
            display_name TEXT NOT NULL,
            phone_last4 TEXT,
            consent INTEGER NOT NULL DEFAULT 0,
            sharing INTEGER NOT NULL DEFAULT 0,
            joined_at TEXT NOT NULL,
            FOREIGN KEY(room_code) REFERENCES rooms(code)
        );
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            accuracy REAL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY(participant_id) REFERENCES participants(id)
        );
        CREATE INDEX IF NOT EXISTS idx_locations_participant
        ON locations(participant_id, recorded_at);
        """)
init_db()

def now():
    return datetime.now(timezone.utc).isoformat()

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

@app.get("/")
def home():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        error = "Incorrect password."
    return render_template("login.html", error=error)

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.get("/admin")
@admin_required
def admin():
    return render_template("admin.html")

@app.post("/api/rooms")
@admin_required
def create_room():
    code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
    with db() as con:
        con.execute("INSERT INTO rooms(code, created_at) VALUES (?, ?)", (code, now()))
    return jsonify({"code": code, "join_url": url_for("join", code=code, _external=True),
                    "dashboard_url": url_for("dashboard", code=code, _external=True)})

@app.get("/join/<code>")
def join(code):
    with db() as con:
        room = con.execute("SELECT * FROM rooms WHERE code=? AND active=1", (code.upper(),)).fetchone()
    if not room:
        abort(404)
    return render_template("join.html", code=code.upper())

@app.post("/api/join")
def api_join():
    data = request.get_json(force=True)
    code = str(data.get("code", "")).upper()
    name = str(data.get("name", "")).strip()[:60]
    phone = ''.join(c for c in str(data.get("phone", "")) if c.isdigit())
    consent = data.get("consent") is True
    if not name or len(phone) < 10 or not consent:
        return jsonify(error="Enter your name, a valid phone number, and give consent."), 400
    with db() as con:
        room = con.execute("SELECT code FROM rooms WHERE code=? AND active=1", (code,)).fetchone()
        if not room:
            return jsonify(error="Room not found or inactive."), 404
        pid = secrets.token_urlsafe(18)
        con.execute("""INSERT INTO participants
            (id, room_code, display_name, phone_last4, consent, sharing, joined_at)
            VALUES (?, ?, ?, ?, 1, 1, ?)""",
            (pid, code, name, phone[-4:], now()))
    session["participant_id"] = pid
    return jsonify({"ok": True, "participant_id": pid})

@app.get("/share")
def share():
    pid = session.get("participant_id")
    if not pid:
        return redirect(url_for("home"))
    return render_template("share.html")

@app.post("/api/location")
def save_location():
    pid = session.get("participant_id")
    if not pid:
        return jsonify(error="Please join a room first."), 401
    data = request.get_json(force=True)
    try:
        lat, lon = float(data["lat"]), float(data["lon"])
        accuracy = float(data.get("accuracy", 0))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Invalid coordinates."), 400
    with db() as con:
        participant = con.execute("SELECT sharing, consent FROM participants WHERE id=?", (pid,)).fetchone()
        if not participant or not participant["sharing"] or not participant["consent"]:
            return jsonify(error="Location sharing is stopped."), 403
        con.execute("INSERT INTO locations(participant_id,lat,lon,accuracy,recorded_at) VALUES(?,?,?,?,?)",
                    (pid, lat, lon, accuracy, now()))
    return jsonify(ok=True)

@app.post("/api/stop")
def stop_sharing():
    pid = session.get("participant_id")
    if not pid:
        return jsonify(error="Not joined."), 401
    with db() as con:
        con.execute("UPDATE participants SET sharing=0 WHERE id=?", (pid,))
    session.pop("participant_id", None)
    return jsonify(ok=True)

@app.get("/dashboard/<code>")
@admin_required
def dashboard(code):
    with db() as con:
        room = con.execute("SELECT code FROM rooms WHERE code=? AND active=1", (code.upper(),)).fetchone()
    if not room:
        abort(404)
    return render_template("dashboard.html", code=code.upper())

@app.get("/api/room/<code>/locations")
@admin_required
def room_locations(code):
    with db() as con:
        people = con.execute("""SELECT p.id,p.display_name,p.phone_last4,p.sharing,
            (SELECT lat FROM locations l WHERE l.participant_id=p.id ORDER BY id DESC LIMIT 1) lat,
            (SELECT lon FROM locations l WHERE l.participant_id=p.id ORDER BY id DESC LIMIT 1) lon,
            (SELECT accuracy FROM locations l WHERE l.participant_id=p.id ORDER BY id DESC LIMIT 1) accuracy,
            (SELECT recorded_at FROM locations l WHERE l.participant_id=p.id ORDER BY id DESC LIMIT 1) recorded_at
            FROM participants p WHERE p.room_code=?""", (code.upper(),)).fetchall()
    return jsonify([dict(x) for x in people])

def predict_next(points):
    # Supervised learning: recent coordinates -> next coordinate.
    # Needs at least 6 recorded points (5 input/output pairs).
    if len(points) < 6:
        return {"available": False, "message": "Collect at least 6 GPS points to train the demo model."}
    arr = np.array([[p["lat"], p["lon"]] for p in points], dtype=float)
    X, y = [], []
    for i in range(3, len(arr)):
        X.append(arr[i-3:i].flatten())
        y.append(arr[i])
    if len(X) < 3:
        return {"available": False, "message": "Not enough movement samples yet."}
    model = RandomForestRegressor(n_estimators=100, random_state=42, min_samples_leaf=1)
    model.fit(np.array(X), np.array(y))
    pred = model.predict(arr[-3:].flatten().reshape(1, -1))[0]
    return {"available": True, "lat": float(pred[0]), "lon": float(pred[1]),
            "note": "Experimental short-term estimate from this participant's recent shared points."}

@app.get("/api/predict/<participant_id>")
@admin_required
def prediction(participant_id):
    with db() as con:
        rows = con.execute("SELECT lat,lon FROM locations WHERE participant_id=? ORDER BY id",
                           (participant_id,)).fetchall()
    return jsonify(predict_next(rows))

@app.post("/api/demo/<code>")
@admin_required
def add_demo_points(code):
    # Synthetic route around Kolkata; clearly labelled as demo data.
    with db() as con:
        room = con.execute("SELECT code FROM rooms WHERE code=? AND active=1", (code.upper(),)).fetchone()
        if not room:
            return jsonify(error="Room not found."), 404
        pid = "DEMO-" + secrets.token_hex(5)
        con.execute("""INSERT INTO participants
            (id,room_code,display_name,phone_last4,consent,sharing,joined_at)
            VALUES(?,?,?,?,1,1,?)""", (pid, code.upper(), "DEMO (synthetic)", "0000", now()))
        # Synthetic points are not anyone's real location.
        for i in range(12):
            lat = 22.5726 + i * 0.0007
            lon = 88.3639 + i * 0.0005
            con.execute("INSERT INTO locations(participant_id,lat,lon,accuracy,recorded_at) VALUES(?,?,?,?,?)",
                        (pid, lat, lon, 5.0, now()))
    return jsonify(ok=True, participant_id=pid, message="Synthetic demo route added.")

@app.post("/api/delete/<participant_id>")
@admin_required
def delete_participant(participant_id):
    with db() as con:
        con.execute("DELETE FROM locations WHERE participant_id=?", (participant_id,))
        con.execute("DELETE FROM participants WHERE id=?", (participant_id,))
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(debug=True)

from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
import time, hashlib, requests

app = Flask(__name__)
app.secret_key = "TANMAY_WEATHER_API_ADMIN_SECRET_CHANGE_MET"

# Direct configuration — no .env file is used.
API_KEY = "TANMAY_WEATHER_API"
ADMIN_USERNAME = "tanmay"
ADMIN_PASSWORD_HASH = hashlib.sha256(b"2015").hexdigest()
ADMIN_GROUP_ID = "-1003520020754"
OWNER_ID = "8317791404"
PORT = 5000

cache = {}
CACHE_SECONDS = 600
request_stats = {"weather_requests": 0, "success": 0, "errors": 0, "started_at": int(time.time())}


def valid_key(key):
    return key == API_KEY


def admin_logged_in():
    return session.get("admin_authenticated") is True


def geocode(city, country=None):
    q = f"{city}, {country}" if country else city
    r = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": q, "count": 1, "language": "en", "format": "json"},
        timeout=10,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


def weather(city, country=None):
    loc = geocode(city, country)
    if not loc:
        return None
    lat, lon = loc["latitude"], loc["longitude"]
    key = f"{lat}:{lon}"
    now = time.time()
    if key in cache and now - cache[key][0] < CACHE_SECONDS:
        return cache[key][1]

    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 7,
            "timezone": "auto",
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    result = {
        "location": {
            "city": loc.get("name"),
            "country": loc.get("country"),
            "country_code": loc.get("country_code"),
            "latitude": lat,
            "longitude": lon,
            "timezone": data.get("timezone"),
        },
        "current": data.get("current", {}),
        "daily": data.get("daily", {}),
        "source": "Open-Meteo",
        "updated_at": int(now),
    }
    cache[key] = (now, result)
    return result


@app.get("/")
def home():
    return jsonify({
        "name": "TANMAY WEATHER API",
        "status": "online",
        "usage": "/weather?country=india&city=delhi&key=TANMAY_WEATHER_API",
        "admin_panel": "/admin",
    })


@app.get("/weather")
def get_weather():
    request_stats["weather_requests"] += 1
    key = request.args.get("key", "")
    city = request.args.get("city", "").strip()
    country = request.args.get("country", "").strip()
    if not valid_key(key):
        request_stats["errors"] += 1
        return jsonify({"ok": False, "error": "Invalid API key"}), 401
    if not city:
        request_stats["errors"] += 1
        return jsonify({"ok": False, "error": "city is required"}), 400
    try:
        result = weather(city, country or None)
        if not result:
            request_stats["errors"] += 1
            return jsonify({"ok": False, "error": "Location not found"}), 404
        request_stats["success"] += 1
        return jsonify({"ok": True, "data": result})
    except requests.RequestException as e:
        request_stats["errors"] += 1
        return jsonify({"ok": False, "error": "Weather provider unavailable", "detail": str(e)}), 502


ADMIN_HTML = """
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TANMAY WEATHER API — Admin</title>
<style>
body{margin:0;font-family:Arial,sans-serif;background:#0b1020;color:#fff}.wrap{max-width:1050px;margin:auto;padding:24px}.brand{font-size:28px;font-weight:800}.muted{color:#9aa5bd}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:22px 0}.card{background:#151c31;border:1px solid #29334f;border-radius:16px;padding:18px}.num{font-size:30px;font-weight:800;margin-top:8px}.ok{color:#43e38b}.danger{color:#ff6978}.btn{display:inline-block;background:#2563eb;color:white;border:0;border-radius:10px;padding:11px 16px;text-decoration:none;cursor:pointer;margin-right:8px}.red{background:#dc3545}.code{background:#080c16;padding:14px;border-radius:10px;overflow:auto}.login{max-width:420px;margin:80px auto}.input{width:100%;box-sizing:border-box;background:#0b1020;color:#fff;border:1px solid #33405f;border-radius:10px;padding:13px;margin:7px 0 14px}label{color:#b8c1d6}.err{background:#3b1720;color:#ff9daa;padding:10px;border-radius:9px;margin-bottom:15px}
</style></head><body><div class="wrap">{{content|safe}}</div></body></html>
"""


def render_page(content):
    return render_template_string(ADMIN_HTML, content=content)


@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH:
            session["admin_authenticated"] = True
            return redirect(url_for("admin_panel"))
        return render_page('''<div class="login"><div class="brand">🌦️ TANMAY WEATHER API</div><p class="muted">Admin Login</p><div class="err">Invalid username or password.</div><form method="post"><label>Username</label><input class="input" name="username" autocomplete="username" required><label>Password</label><input class="input" type="password" name="password" autocomplete="current-password" required><button class="btn">Login</button></form></div>''')

    if not admin_logged_in():
        return render_page('''<div class="login"><div class="brand">🌦️ TANMAY WEATHER API</div><p class="muted">Secure Admin Panel</p><form method="post"><label>Username</label><input class="input" name="username" autocomplete="username" required><label>Password</label><input class="input" type="password" name="password" autocomplete="current-password" required><button class="btn">Login</button></form></div>''')

    uptime = int(time.time() - request_stats["started_at"])
    html = f'''<div class="brand">🌦️ TANMAY WEATHER API</div><p class="muted">Admin Dashboard · @{ADMIN_USERNAME}</p>
    <div class="grid"><div class="card">Status<div class="num ok">● ONLINE</div></div><div class="card">Weather Requests<div class="num">{request_stats["weather_requests"]}</div></div><div class="card">Success<div class="num ok">{request_stats["success"]}</div></div><div class="card">Errors<div class="num danger">{request_stats["errors"]}</div></div><div class="card">Cache Entries<div class="num">{len(cache)}</div></div><div class="card">Uptime<div class="num">{uptime}s</div></div></div>
    <div class="card"><h3>API Configuration</h3><p>API Key: <b>{API_KEY}</b></p><p>Owner ID: <b>{OWNER_ID}</b></p><p>Admin Group: <b>{ADMIN_GROUP_ID}</b></p><p>Endpoint:</p><div class="code">/weather?country=india&amp;city=delhi&amp;key={API_KEY}</div></div><br>
    <a class="btn" href="/admin/test?city=delhi&country=india">Test Delhi Weather</a><a class="btn red" href="/admin/clear-cache">Clear Cache</a><a class="btn" href="/admin/logout">Logout</a>'''
    return render_page(html)


@app.get("/admin/test")
def admin_test():
    if not admin_logged_in():
        return redirect(url_for("admin_panel"))
    city = request.args.get("city", "delhi")
    country = request.args.get("country", "india")
    try:
        result = weather(city, country)
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/admin/clear-cache")
def clear_cache():
    if not admin_logged_in():
        return redirect(url_for("admin_panel"))
    cache.clear()
    return redirect(url_for("admin_panel"))


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_panel"))


@app.get("/admin/api/stats")
def admin_stats():
    if not admin_logged_in():
        return jsonify({"ok": False, "error": "Admin login required"}), 401
    return jsonify({"ok": True, "stats": request_stats, "cache_entries": len(cache)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)

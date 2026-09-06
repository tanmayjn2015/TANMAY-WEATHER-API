import os
from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
import requests
from datetime import datetime, timezone

app = Flask(__name__)
# Clean Vercel build: no .env, no os/os.getenv.
# Change these values before publishing if you want private admin/API access.
app.secret_key = "TANMAY_WEATHER_API_ADMIN_SECRET_CHANGE_METANDOPAPI"
API_KEY = "TANMAY_WEATHER_API"
ADMIN_USERNAME = "tanmay"
ADMIN_PASSWORD = "2015"

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain", 66: "Light freezing rain",
    67: "Heavy freezing rain", 71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
    82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"
}


def json_error(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def geocode(city, country=None):
    params = {"name": city, "count": 1, "language": "en", "format": "json"}
    if country:
        # Country is used only to improve the city search; Open-Meteo geocoding accepts name.
        params["name"] = f"{city}, {country}"
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search", params=params, timeout=12)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise ValueError("City not found")
    return results[0]


def get_weather(city, country=None):
    # Geocode on every request so the location is always resolved fresh.
    place = geocode(city, country)

    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,rain,weather_code,cloud_cover,pressure_msl,"
            "wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        ),
        "hourly": (
            "temperature_2m,relative_humidity_2m,precipitation_probability,"
            "precipitation,weather_code,wind_speed_10m"
        ),
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,precipitation_sum,sunrise,sunset"
        ),
        "timezone": "auto",
        "forecast_days": 7,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        # Explicitly request the latest provider response without application caching.
        "timeformat": "iso8601",
    }

    headers = {
        "User-Agent": "TANMAY-WEATHER-API/3.5",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params=params,
        headers=headers,
        timeout=15,
    )
    r.raise_for_status()

    data = r.json()
    current = data.get("current", {})
    code = current.get("weather_code")
    current["weather_description"] = WEATHER_CODES.get(code, "Unknown")

    daily = data.get("daily", {})
    daily["weather_description"] = [
        WEATHER_CODES.get(c, "Unknown")
        for c in daily.get("weather_code", [])
    ]

    return {
        "location": {
            "city": place.get("name"),
            "country": place.get("country"),
            "country_code": place.get("country_code"),
            "latitude": place.get("latitude"),
            "longitude": place.get("longitude"),
            "timezone": data.get("timezone"),
        },
        "current": current,
        "hourly": data.get("hourly", {}),
        "daily": daily,
        "source": "Open-Meteo",
        "provider_updated_at": current.get("time"),
        "api_updated_at": datetime.now(timezone.utc).isoformat(),
        "fresh_request": True,
    }

def authorized():
    supplied = request.args.get("key") or request.headers.get("X-API-Key")
    return supplied == API_KEY


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/")
def home():
    return jsonify({"ok": True, "name": "TANMAY WEATHER API", "version": "3.1.0",
                    "usage": "/weather?city=Delhi&country=India&key=TANMAY_WEATHER_API",
                    "fresh_data": True})


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "TANMAY WEATHER API", "version": "3.1.0",
                    "time": datetime.now(timezone.utc).isoformat()})


@app.get("/weather")
def weather():
    if not authorized():
        return json_error("Invalid API key", 401)
    city = request.args.get("city", "Delhi").strip()
    country = request.args.get("country", "India").strip()
    if not city:
        return json_error("city is required")
    try:
        return jsonify({"ok": True, "data": get_weather(city, country)})
    except requests.RequestException as exc:
        return json_error("Weather provider unavailable: " + str(exc), 502)
    except ValueError as exc:
        return json_error(str(exc), 404)
    except Exception as exc:
        return json_error("Internal weather error: " + str(exc), 500)


LOGIN_HTML = """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>TANMAY Weather Admin</title><style>body{font-family:system-ui;background:#0b1020;color:#fff;max-width:900px;margin:40px auto;padding:20px}input,button{padding:12px;margin:5px;border-radius:10px;border:0}button{cursor:pointer}pre{white-space:pre-wrap;background:#151c32;padding:16px;border-radius:12px}</style></head><body><h1>🌦️ TANMAY WEATHER ADMIN</h1>{% if error %}<p>{{error}}</p>{% endif %}<form method='post'><input name='username' placeholder='Username' required><input type='password' name='password' placeholder='Password' required><button>Login</button></form></body></html>"""
DASH_HTML = """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>TANMAY Weather Admin</title><style>body{font-family:system-ui;background:#0b1020;color:#fff;max-width:1100px;margin:30px auto;padding:20px}input,button{padding:12px;margin:5px;border-radius:10px;border:0}button{cursor:pointer}.card{background:#151c32;padding:20px;border-radius:16px;margin:15px 0}pre{white-space:pre-wrap;overflow:auto}</style></head><body><h1>🌦️ TANMAY WEATHER ADMIN</h1><div class='card'><form method='get'><input name='city' value='Delhi'><input name='country' value='India'><button>Fetch Fresh Weather</button></form></div>{% if data %}<div class='card'><pre>{{data}}</pre></div>{% endif %}<a href='/admin/logout'>Logout</a></body></html>"""


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not session.get("admin"):
        if request.method == "POST":
            if request.form.get("username") == ADMIN_USERNAME and request.form.get("password") == ADMIN_PASSWORD:
                session["admin"] = True
                return redirect(url_for("admin"))
            return render_template_string(LOGIN_HTML, error="Invalid login")
        return render_template_string(LOGIN_HTML, error=None)
    data = None
    city = request.args.get("city")
    if city:
        try:
            data = get_weather(city, request.args.get("country", "India"))
        except Exception as exc:
            data = {"error": str(exc)}
    return render_template_string(DASH_HTML, data=data)


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

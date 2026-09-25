from datetime import datetime, timezone
import secrets

import requests
from flask import Flask, jsonify, render_template_string, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "TANMAY_WEATHER_API_ADMIN_SECRET_CHANGE_METANDOPAPIS"

API_KEY = "TANMAY_WEATHER_API"
ADMIN_USERNAME = "tanmay"
ADMIN_PASSWORD = "2015"
VERSION = "4.1.0"

OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
INDIA_POST_PIN = "https://api.postalpincode.in/pincode/{pin}"

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain", 66: "Light freezing rain",
    67: "Heavy freezing rain", 71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
    82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def json_error(message, status=400, details=None):
    body = {"ok": False, "error": message}
    if details is not None:
        body["details"] = details
    return jsonify(body), status


def provider_get(url, params=None, timeout=15):
    headers = {
        "User-Agent": "TANMAY-WEATHER-API/4.1 (+https://tanmayweatherapi.vercel.app)",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
        "X-TANMAY-Request": secrets.token_hex(8),
    }
    # A fresh request is deliberately made for every API call. There is no
    # application-side weather cache, so stale values are never served by us.
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response


def geocode_city(city, country="India"):
    params = {
        "name": city,
        "count": 10,
        "language": "en",
        "format": "json",
    }
    response = provider_get(OPEN_METEO_GEOCODING, params=params, timeout=12)
    results = response.json().get("results") or []
    if not results:
        raise ValueError("City not found")

    country_lower = country.strip().lower() if country else ""
    if country_lower:
        matches = [r for r in results if str(r.get("country", "")).lower() == country_lower
                   or str(r.get("country_code", "")).lower() == country_lower]
        if matches:
            results = matches
    return results[0]


def geocode_coordinates(latitude, longitude, label=None):
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        raise ValueError("latitude and longitude must be valid numbers")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("latitude/longitude out of range")
    return {
        "name": label or "Coordinates",
        "country": None,
        "country_code": None,
        "latitude": lat,
        "longitude": lon,
    }


def geocode_pin(pin):
    pin = str(pin or "").strip()
    if not pin.isdigit() or len(pin) != 6:
        raise ValueError("Indian PIN code must be exactly 6 digits")

    response = provider_get(INDIA_POST_PIN.format(pin=pin), timeout=12)
    payload = response.json()
    if not isinstance(payload, list) or not payload or payload[0].get("Status") != "Success":
        raise ValueError("Indian PIN code not found")

    records = payload[0].get("PostOffice") or []
    if not records:
        raise ValueError("No post office data found for this PIN code")

    office = records[0]
    # India Post gives administrative names but not reliable latitude/longitude.
    # We use the PIN's postal locality as the geocoding query and verify the result
    # remains in India.
    locality_parts = [
        office.get("Name"), office.get("District"), office.get("State"), "India"
    ]
    locality = ", ".join(str(x).strip() for x in locality_parts if x)
    params = {"name": locality, "count": 10, "language": "en", "format": "json"}
    geo_response = provider_get(OPEN_METEO_GEOCODING, params=params, timeout=12)
    results = geo_response.json().get("results") or []
    india_results = [
        r for r in results
        if str(r.get("country_code", "")).lower() == "in"
    ]
    if not india_results:
        raise ValueError("PIN found, but its location could not be geocoded")

    place = india_results[0]
    return place, {
        "pin": pin,
        "post_office": office.get("Name"),
        "district": office.get("District"),
        "state": office.get("State"),
        "division": office.get("Division"),
        "region": office.get("Region"),
        "circle": office.get("Circle"),
        "block": office.get("Block"),
    }


def fetch_weather(place, extra_location=None):
    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": ",".join([
            "temperature_2m", "relative_humidity_2m", "apparent_temperature",
            "precipitation", "rain", "showers", "snowfall", "weather_code",
            "cloud_cover", "pressure_msl", "surface_pressure", "wind_speed_10m",
            "wind_direction_10m", "wind_gusts_10m", "is_day",
        ]),
        "hourly": ",".join([
            "temperature_2m", "relative_humidity_2m", "apparent_temperature",
            "precipitation_probability", "precipitation", "rain", "showers",
            "weather_code", "wind_speed_10m", "wind_direction_10m",
        ]),
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "precipitation_probability_max", "precipitation_sum", "sunrise", "sunset",
        ]),
        "timezone": "auto",
        "forecast_days": 7,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "timeformat": "iso8601",
    }

    response = provider_get(OPEN_METEO_FORECAST, params=params, timeout=18)
    data = response.json()
    current = data.get("current") or {}
    code = current.get("weather_code")
    current["weather_description"] = WEATHER_CODES.get(code, "Unknown")

    daily = data.get("daily") or {}
    daily["weather_description"] = [
        WEATHER_CODES.get(code, "Unknown") for code in daily.get("weather_code", [])
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
        "source": "TANMAY WETHER API",
        "provider_current_time": current.get("time"),
        "api_response_time_utc": datetime.now(timezone.utc).isoformat(),
        "fresh_request": True,
        "cache": "disabled",
        "location_details": extra_location or {},
    }


def get_weather_for_city(city, country="India"):
    return fetch_weather(geocode_city(city, country))


def authorized():
    supplied = request.args.get("key") or request.headers.get("X-API-Key")
    return secrets.compare_digest(str(supplied or ""), API_KEY)


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "name": "TANMAY WEATHER API",
        "version": VERSION,
        "realtime": True,
        "cache": "disabled",
        "endpoints": {
            "weather": "/weather?city=Delhi&country=India&key=TANMAY_WEATHER_API",
            "pin": "/pin?pin=202001&key=TANMAY_WEATHER_API",
            "coordinates": "/weather?lat=28.6139&lon=77.2090&key=TANMAY_WEATHER_API",
            "health": "/health",
        },
    })


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "TANMAY WEATHER API",
        "version": VERSION,
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "weather_cache": False,
    })


@app.get("/weather")
def weather():
    if not authorized():
        return json_error("Invalid API key", 401)

    lat = request.args.get("lat")
    lon = request.args.get("lon")
    city = (request.args.get("city") or "Delhi").strip()
    country = (request.args.get("country") or "India").strip()

    try:
        if (lat is None) != (lon is None):
            return json_error("Both lat and lon are required together")
        if lat is not None and lon is not None:
            place = geocode_coordinates(lat, lon, label=request.args.get("name"))
        else:
            if not city:
                return json_error("city is required")
            place = geocode_city(city, country)
        return jsonify({"ok": True, "data": fetch_weather(place)})
    except requests.RequestException as exc:
        return json_error("Weather/location provider unavailable", 502, str(exc))
    except ValueError as exc:
        return json_error(str(exc), 404)
    except Exception as exc:
        return json_error("Internal weather error", 500, str(exc))


@app.get("/pin")
def pin_weather():
    if not authorized():
        return json_error("Invalid API key", 401)

    pin = request.args.get("pin") or request.args.get("pincode") or request.args.get("postal_code")
    if not pin:
        return json_error("pin is required, for example /pin?pin=202001&key=TANMAY_WEATHER_API")

    try:
        place, pin_info = geocode_pin(pin)
        return jsonify({"ok": True, "data": fetch_weather(place, pin_info)})
    except requests.RequestException as exc:
        return json_error("PIN/weather provider unavailable", 502, str(exc))
    except ValueError as exc:
        return json_error(str(exc), 404)
    except Exception as exc:
        return json_error("Internal PIN weather error", 500, str(exc))


LOGIN_HTML = """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>TANMAY Weather Admin</title><style>body{font-family:system-ui;background:#0b1020;color:#fff;max-width:900px;margin:40px auto;padding:20px}input,button{padding:12px;margin:5px;border-radius:10px;border:0}button{cursor:pointer}a{color:#8ec5ff}</style></head><body><h1>🌦️ TANMAY WEATHER ADMIN</h1>{% if error %}<p>{{error}}</p>{% endif %}<form method='post'><input name='username' placeholder='Username' required><input type='password' name='password' placeholder='Password' required><button>Login</button></form></body></html>"""

DASH_HTML = """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>TANMAY Weather Admin</title><style>body{font-family:system-ui;background:#0b1020;color:#fff;max-width:1100px;margin:30px auto;padding:20px}input,button{padding:12px;margin:5px;border-radius:10px;border:0}button{cursor:pointer}.card{background:#151c32;padding:20px;border-radius:16px;margin:15px 0}pre{white-space:pre-wrap;overflow:auto}a{color:#8ec5ff}</style></head><body><h1>🌦️ TANMAY WEATHER ADMIN v{{version}}</h1><div class='card'><h3>City</h3><form method='get'><input name='city' value='Delhi'><input name='country' value='India'><button>Fetch Fresh Weather</button></form><h3>Indian PIN</h3><form method='get'><input name='pin' placeholder='6 digit PIN' pattern='[0-9]{6}'><button>Fetch PIN Weather</button></form></div>{% if data %}<div class='card'><pre>{{data}}</pre></div>{% endif %}<a href='/admin/logout'>Logout</a></body></html>"""


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
    try:
        if request.args.get("pin"):
            place, pin_info = geocode_pin(request.args.get("pin"))
            data = fetch_weather(place, pin_info)
        elif request.args.get("city"):
            data = get_weather_for_city(request.args.get("city"), request.args.get("country", "India"))
    except Exception as exc:
        data = {"error": str(exc)}
    return render_template_string(DASH_HTML, data=data, version=VERSION)


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

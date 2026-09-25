from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
import requests
from datetime import datetime, timezone
import re

# ================================================================
# TANMAY WEATHER API - SINGLE FILE CONFIGURATION
# No config.py / config.json required.
# Replace these placeholders before deployment. Keep this source private.
# ================================================================
API_KEY = "TANMAY_WEATHER_API"
ADMIN_USERNAME = "tanmay"
ADMIN_PASSWORD = "2015"
FLASK_SECRET = "TANMAY_WEATHERS_API_ADMIN_SECRET_CHANGE_METANDOPAPI"

app = Flask(__name__)
app.secret_key = FLASK_SECRET

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
UA = "TANMAY-WEATHER-API/5.4"

COUNTRY_ALIASES = {
    "india": "IN", "ind": "IN",
    "united states": "US", "usa": "US", "us": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "canada": "CA", "australia": "AU", "germany": "DE",
    "france": "FR", "italy": "IT", "spain": "ES", "japan": "JP",
    "china": "CN", "brazil": "BR", "mexico": "MX", "uae": "AE",
    "united arab emirates": "AE", "saudi arabia": "SA",
    "singapore": "SG", "new zealand": "NZ", "south africa": "ZA",
}

def normalize_country(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    upper = raw.upper()
    if re.fullmatch(r"[A-Z]{2}", upper):
        return upper
    return COUNTRY_ALIASES.get(raw.lower())

def json_error(message, status=400, **extra):
    body = {"ok": False, "error": message}
    body.update(extra)
    return jsonify(body), status

def geocode(name, country_code=None, count=10):
    params = {"name": str(name).strip(), "count": count, "language": "en", "format": "json"}
    if country_code:
        params["countryCode"] = str(country_code).strip().upper()
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params=params, timeout=12, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.json().get("results") or []

def geocode_city(city, country=None):
    # Use the provider's dedicated countryCode filter instead of embedding
    # the country name in the search text. This avoids false "city not found"
    # results for country names/aliases.
    results = geocode(city, (country or None), 10)
    if not results and country:
        results = geocode(f"{city}, {country}", None, 10)
    if not results:
        raise ValueError("City/place not found")
    if country:
        wanted = normalize_country(country) or str(country).strip().lower()
        filtered = [r for r in results if str(r.get("country_code", "")).lower() == str(wanted).lower() or str(r.get("country", "")).lower() == str(country).strip().lower()]
        if filtered:
            return filtered[0]
    return results[0]

def normalize_postal(value):
    return str(value).strip().replace(" ", "").replace("-", "")

def geocode_postal(postal, country=None):
    """Resolve a worldwide postal/ZIP code with layered fallbacks.

    Open-Meteo supports postal-code searches directly. For India, India Post
    is queried first for authoritative locality metadata, then Open-Meteo is
    used for coordinates. If a postal provider returns metadata but the
    locality is not indexed by Open-Meteo, a direct postal search and a
    Nominatim fallback are attempted.
    """
    postal = normalize_postal(postal)
    if not postal:
        raise ValueError("Postal/ZIP code is required")

    country_code = normalize_country(country)
    if country and not country_code:
        raise ValueError("Unsupported country. Use a 2-letter ISO code such as IN, US or GB.")

    # 1) Direct Open-Meteo postal-code search.
    try:
        results = geocode(postal, country_code or None, 100)
    except requests.RequestException:
        results = []
    target = postal.upper()
    for result in results:
        postcodes = [normalize_postal(x).upper() for x in (result.get("postcodes") or [])]
        if target in postcodes:
            result["pin"] = postal
            return result
    # Never silently return an unrelated location just because the provider
    # returned a fuzzy match. That was the source of incorrect postal results.

    # 2) India Post lookup. This is especially useful for Indian PINs such as
    # 244231 (Dhanaura, Amroha, Uttar Pradesh).
    if country_code == "IN" or (not country_code and re.fullmatch(r"\d{6}", postal)):
        try:
            r = requests.get(
                f"https://api.postalpincode.in/pincode/{postal}",
                timeout=12,
                headers={"User-Agent": UA},
            )
            r.raise_for_status()
            payload = r.json()
            if payload and payload[0].get("Status") == "Success" and payload[0].get("PostOffice"):
                offices = payload[0]["PostOffice"]
                # Prefer a Sub Office / Head Office, then the first available office.
                office = next((x for x in offices if x.get("BranchType") in ("Sub Post Office", "Head Post Office")), offices[0])
                candidates = [
                    office.get("Name"),
                    ", ".join(x for x in [office.get("Name"), office.get("District")] if x),
                    ", ".join(x for x in [office.get("District"), office.get("State")] if x),
                ]
                for query in candidates:
                    if not query:
                        continue
                    local_results = geocode(query, "IN", 20)
                    if local_results:
                        place = local_results[0]
                        place.update({
                            "pin": postal,
                            "post_office": office.get("Name"),
                            "district": office.get("District"),
                            "state": office.get("State"),
                            "region": office.get("Region"),
                            "division": office.get("Division"),
                        })
                        return place
        except (requests.RequestException, ValueError, TypeError):
            pass

    # 3) OpenStreetMap/Nominatim fallback for postal areas not indexed by
    # Open-Meteo. A descriptive User-Agent is required by Nominatim policy.
    try:
        params = {
            "postalcode": postal,
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 1,
        }
        if country_code:
            params["countrycodes"] = country_code.lower()
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params=params,
            timeout=12,
            headers={"User-Agent": UA + " (postal lookup)"},
        )
        r.raise_for_status()
        places = r.json() or []
        if places:
            item = places[0]
            address = item.get("address") or {}
            return {
                "name": address.get("city") or address.get("town") or address.get("village") or address.get("municipality") or postal,
                "country": address.get("country"),
                "country_code": (address.get("country_code") or country_code).upper() or None,
                "admin1": address.get("state"),
                "admin2": address.get("county") or address.get("state_district"),
                "latitude": float(item["lat"]),
                "longitude": float(item["lon"]),
                "pin": postal,
                "post_office": address.get("postcode"),
            }
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass

    raise ValueError("Postal/ZIP code not found. Provide country=XX for an unambiguous worldwide lookup.")

def get_weather_at_place(place, forecast_days=16):
    """Return a rich worldwide weather response from Open-Meteo."""
    try:
        forecast_days = max(1, min(int(forecast_days), 16))
    except (TypeError, ValueError):
        forecast_days = 16

    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": ",".join([
            # Keep this list limited to variables supported by Open-Meteo's
            # /v1/forecast `current` parameter. Extra fields such as visibility
            # and UV are merged from the hourly response below.
            "temperature_2m", "relative_humidity_2m", "apparent_temperature",
            "is_day", "precipitation", "rain", "showers", "snowfall",
            "weather_code", "cloud_cover", "pressure_msl", "surface_pressure",
            "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m"
        ]),
        "hourly": ",".join([
            "temperature_2m", "relative_humidity_2m", "dew_point_2m",
            "apparent_temperature", "precipitation_probability",
            "precipitation", "rain", "showers", "snowfall",
            "weather_code", "cloud_cover", "cloud_cover_low",
            "cloud_cover_mid", "cloud_cover_high", "pressure_msl",
            "surface_pressure", "visibility", "wind_speed_10m",
            "wind_direction_10m", "wind_gusts_10m", "uv_index",
            "uv_index_clear_sky", "is_day", "sunshine_duration",
            "evapotranspiration", "et0_fao_evapotranspiration",
            "vapour_pressure_deficit", "cape", "shortwave_radiation",
            "direct_radiation", "diffuse_radiation",
            "direct_normal_irradiance"
        ]),
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "apparent_temperature_max", "apparent_temperature_min",
            "precipitation_probability_max", "precipitation_sum",
            "rain_sum", "showers_sum", "snowfall_sum",
            "precipitation_hours", "sunrise", "sunset",
            "daylight_duration", "sunshine_duration", "wind_speed_10m_max",
            "wind_gusts_10m_max", "wind_direction_10m_dominant",
            "shortwave_radiation_sum", "et0_fao_evapotranspiration",
            "uv_index_max", "uv_index_clear_sky_max"
        ]),
        "timezone": "auto",
        "forecast_days": forecast_days,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "timeformat": "iso8601"
    }
    if not (-90 <= float(place["latitude"]) <= 90 and -180 <= float(place["longitude"]) <= 180):
        raise ValueError("Latitude must be between -90 and 90 and longitude between -180 and 180")

    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params=params,
        timeout=20,
        headers={"User-Agent": UA, "Cache-Control": "no-cache"}
    )
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        reason = data.get("reason") or "Weather provider rejected the request"
        raise ValueError(reason)

    current = dict(data.get("current") or {})
    current["weather_description"] = WEATHER_CODES.get(
        current.get("weather_code"), "Unknown"
    )

    hourly = dict(data.get("hourly") or {})

    # Add rich fields to `current` from the hourly dataset. Open-Meteo exposes
    # these as hourly variables rather than current variables. Match the
    # provider's current timestamp when possible, otherwise use the first
    # available hourly point.
    h_times = hourly.get("time") or []
    current_time = current.get("time")
    try:
        idx = h_times.index(current_time) if current_time in h_times else 0
    except (TypeError, ValueError):
        idx = 0
    for key in (
        "dew_point_2m", "precipitation_probability", "visibility", "uv_index",
        "uv_index_clear_sky", "sunshine_duration", "evapotranspiration",
        "et0_fao_evapotranspiration", "vapour_pressure_deficit", "cape",
        "shortwave_radiation", "direct_radiation", "diffuse_radiation",
        "direct_normal_irradiance"
    ):
        values = hourly.get(key)
        if isinstance(values, list) and values:
            current[key] = values[min(idx, len(values) - 1)]

    daily = dict(data.get("daily") or {})
    daily["weather_description"] = [
        WEATHER_CODES.get(code, "Unknown")
        for code in daily.get("weather_code", [])
    ]

    location = {
        "name": place.get("name"),
        "country": place.get("country"),
        "country_code": place.get("country_code"),
        "admin1": place.get("admin1"),
        "admin2": place.get("admin2"),
        "admin3": place.get("admin3"),
        "latitude": place.get("latitude"),
        "longitude": place.get("longitude"),
        "elevation_m": data.get("elevation"),
        "timezone": data.get("timezone"),
        "timezone_abbreviation": data.get("timezone_abbreviation"),
        "utc_offset_seconds": data.get("utc_offset_seconds"),
        "pin": place.get("pin"),
        "post_office": place.get("post_office"),
        "district": place.get("district"),
        "state": place.get("state"),
        "region": place.get("region"),
        "division": place.get("division")
    }

    return {
        "location": location,
        "current": current,
        "hourly": hourly,
        "hourly_units": data.get("hourly_units", {}),
        "daily": daily,
        "daily_units": data.get("daily_units", {}),
        "forecast_days": forecast_days,
        "source": "TANMAY WEATHER API ",
        "provider_generation_time_ms": data.get("generationtime_ms"),
        "provider_updated_at": current.get("time"),
        "api_updated_at": datetime.now(timezone.utc).isoformat(),
        "fresh_request": True
    }

def get_weather_by_pin(pin, country=None, forecast_days=16):
    return get_weather_at_place(geocode_postal(pin, country), forecast_days)

def get_weather(city, country=None, forecast_days=16):
    return get_weather_at_place(geocode_city(city, country), forecast_days)

def authorized():
    supplied = request.headers.get("X-API-Key") or request.args.get("key")
    configured = str(API_KEY or "").strip()
    if not configured or configured.startswith("PUT_") or configured.startswith("CHANGE_THIS"):
        return False
    return bool(supplied and supplied == configured)

@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-API-Version"] = "5.4.0"
    return response

@app.get("/")
def home():
    pin = request.args.get("pin")
    if pin is not None:
        if not authorized():
            return json_error("Invalid or missing API key", 401)
        try:
            return jsonify({
                "ok": True,
                "api": "TANMAY WEATHER API",
                "version": "5.4.0",
                "data": get_weather_by_pin(pin, request.args.get("country"), request.args.get("days", 16))
            })
        except requests.RequestException:
            return json_error("Weather/geocoding provider temporarily unavailable", 502)
        except ValueError as exc:
            return json_error(str(exc), 404)
        except Exception:
            return json_error("Internal weather error", 500)
    return jsonify({
        "ok": True,
        "name": "TANMAY WEATHER API",
        "version": "5.4.0",
        "realtime": True,
        "cache": "disabled",
        "authentication": "X-API-Key header (recommended) or key query parameter",
        "country_formats": "ISO-2 codes and common country names are accepted",
        "endpoints": {
            "pin": "/pin?pin=244231&country=IN&days=16",
            "worldwide_postal": "/pin?pin=SW1A1AA&country=GB&days=16",
            "weather": "/weather?city=Delhi&country=India&days=16",
            "coordinates": "/weather?lat=28.6139&lon=77.2090&days=16",
            "current": "/current?city=Delhi&country=India",
            "hourly": "/hourly?city=Delhi&country=India&days=16",
            "daily": "/daily?city=Delhi&country=India&days=16",
            "forecast": "/forecast?city=Delhi&country=India&days=16",
            "health": "/health"
        }
    })

@app.get("/pin")
def pin_weather():
    if not authorized():
        return json_error("Invalid or missing API key", 401)
    pin = request.args.get("pin", "").strip()
    if not pin:
        return json_error("pin is required")
    try:
        return jsonify({"ok": True, "data": get_weather_by_pin(pin, request.args.get("country"), request.args.get("days", 16))})
    except requests.RequestException:
        return json_error("Weather/geocoding provider temporarily unavailable", 502)
    except ValueError as exc:
        return json_error(str(exc), 404)
    except Exception:
        return json_error("Internal weather error", 500)

@app.get("/weather")
def weather():
    if not authorized():
        return json_error("Invalid or missing API key", 401)
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    try:
        if lat is not None or lon is not None:
            if lat is None or lon is None:
                return json_error("Both lat and lon are required")
            place = {
                "name": request.args.get("name", "Coordinates"),
                "country": request.args.get("country"),
                "country_code": request.args.get("country_code"),
                "latitude": float(lat),
                "longitude": float(lon),
            }
            return jsonify({"ok": True, "data": get_weather_at_place(place, request.args.get("days", 16))})
        city = request.args.get("city", "Delhi").strip()
        country = request.args.get("country", "India").strip()
        if not city:
            return json_error("city is required")
        return jsonify({"ok": True, "data": get_weather(city, country, request.args.get("days", 16))})
    except ValueError as exc:
        return json_error(str(exc), 400)
    except requests.RequestException:
        return json_error("Weather/geocoding provider temporarily unavailable", 502)
    except Exception:
        return json_error("Internal weather error", 500)

@app.get("/forecast")
def forecast_alias():
    """Universal forecast alias. Accepts pin, city or lat/lon and returns full data."""
    return weather() if not request.args.get("pin") else pin_weather()

@app.get("/current")
def current_weather():
    """Current conditions endpoint; returns the current object plus location metadata."""
    if not authorized():
        return json_error("Invalid or missing API key", 401)
    try:
        if request.args.get("pin"):
            result = get_weather_by_pin(request.args.get("pin"), request.args.get("country"), 1)
        elif request.args.get("lat") is not None or request.args.get("lon") is not None:
            if request.args.get("lat") is None or request.args.get("lon") is None:
                return json_error("Both lat and lon are required")
            place = {"name": request.args.get("name", "Coordinates"), "country": request.args.get("country"), "country_code": request.args.get("country_code"), "latitude": float(request.args["lat"]), "longitude": float(request.args["lon"])}
            result = get_weather_at_place(place, 1)
        else:
            result = get_weather(request.args.get("city", "Delhi"), request.args.get("country", "India"), 1)
        return jsonify({"ok": True, "location": result["location"], "current": result["current"], "source": result["source"]})
    except requests.RequestException:
        return json_error("Weather/geocoding provider temporarily unavailable", 502)
    except ValueError as exc:
        return json_error(str(exc), 404)
    except Exception:
        return json_error("Internal weather error", 500)

@app.get("/hourly")
def hourly_weather():
    """Hourly forecast endpoint with the complete hourly dataset."""
    if not authorized():
        return json_error("Invalid or missing API key", 401)
    try:
        if request.args.get("pin"):
            result = get_weather_by_pin(request.args.get("pin"), request.args.get("country"), request.args.get("days", 16))
        elif request.args.get("lat") is not None and request.args.get("lon") is not None:
            place = {"name": request.args.get("name", "Coordinates"), "country": request.args.get("country"), "country_code": request.args.get("country_code"), "latitude": float(request.args["lat"]), "longitude": float(request.args["lon"])}
            result = get_weather_at_place(place, request.args.get("days", 16))
        else:
            result = get_weather(request.args.get("city", "Delhi"), request.args.get("country", "India"), request.args.get("days", 16))
        return jsonify({"ok": True, "location": result["location"], "hourly": result["hourly"], "hourly_units": result["hourly_units"], "source": result["source"]})
    except requests.RequestException:
        return json_error("Weather/geocoding provider temporarily unavailable", 502)
    except ValueError as exc:
        return json_error(str(exc), 404)
    except Exception:
        return json_error("Internal weather error", 500)

@app.get("/daily")
def daily_weather():
    """Daily forecast endpoint with the complete daily dataset."""
    if not authorized():
        return json_error("Invalid or missing API key", 401)
    try:
        if request.args.get("pin"):
            result = get_weather_by_pin(request.args.get("pin"), request.args.get("country"), request.args.get("days", 16))
        elif request.args.get("lat") is not None and request.args.get("lon") is not None:
            place = {"name": request.args.get("name", "Coordinates"), "country": request.args.get("country"), "country_code": request.args.get("country_code"), "latitude": float(request.args["lat"]), "longitude": float(request.args["lon"])}
            result = get_weather_at_place(place, request.args.get("days", 16))
        else:
            result = get_weather(request.args.get("city", "Delhi"), request.args.get("country", "India"), request.args.get("days", 16))
        return jsonify({"ok": True, "location": result["location"], "daily": result["daily"], "daily_units": result["daily_units"], "source": result["source"]})
    except requests.RequestException:
        return json_error("Weather/geocoding provider temporarily unavailable", 502)
    except ValueError as exc:
        return json_error(str(exc), 404)
    except Exception:
        return json_error("Internal weather error", 500)

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "TANMAY WEATHER API",
        "version": "5.4.0",
        "time": datetime.now(timezone.utc).isoformat()
    })

LOGIN_HTML = """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>TANMAY Weather Admin</title><style>body{font-family:system-ui;background:#0b1020;color:#fff;max-width:900px;margin:40px auto;padding:20px}input,button{padding:12px;margin:5px;border-radius:10px;border:0}button{cursor:pointer}</style></head><body><h1>🌦️ TANMAY WEATHER ADMIN</h1>{% if error %}<p>{{error}}</p>{% endif %}<form method='post'><input name='username' placeholder='Username' required><input type='password' name='password' placeholder='Password' required><button>Login</button></form></body></html>"""
DASH_HTML = """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>TANMAY Weather Admin v5.4</title><style>body{font-family:system-ui;background:#0b1020;color:#fff;max-width:1100px;margin:20px auto;padding:16px}input,button{padding:13px;margin:5px;border-radius:10px;border:0;box-sizing:border-box}input{min-width:220px}button{cursor:pointer}.card{background:#151c32;padding:20px;border-radius:16px;margin:15px 0}pre{white-space:pre-wrap;overflow:auto;font-size:12px}label{display:block;margin-top:8px;font-weight:700}.muted{opacity:.75}</style></head><body><h1>🌦️ TANMAY WEATHER ADMIN</h1><p class='muted'>v5.4.0 • Worldwide weather • Fresh/no-cache</p><div class='card'><h2>City / Place</h2><form method='get'><label>City</label><input name='city' value='{{ request.args.get("city","Delhi") }}'><label>Country</label><input name='country' value='{{ request.args.get("country","India") }}'><button>Fetch City Weather</button></form></div><div class='card'><h2>Worldwide PIN / ZIP / Postal Code</h2><form method='get'><label>Postal code</label><input name='pin' placeholder='244231 / 24431 / SW1A1AA' value='{{ request.args.get("pin","") }}'><label>Country code (optional)</label><input name='pin_country' placeholder='IN / US / GB' value='{{ request.args.get("pin_country","") }}'><button>Fetch Postal Weather</button></form></div>{% if data %}<div class='card'><pre>{{data}}</pre></div>{% endif %}<a href='/admin/logout'>Logout</a></body></html>"""

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
    pin = request.args.get("pin", "").strip()
    city = request.args.get("city", "").strip()
    try:
        if pin:
            data = {"ok": True, "data": get_weather_by_pin(pin, request.args.get("pin_country", "").strip() or None, request.args.get("days", 16))}
        elif city:
            data = {"ok": True, "data": get_weather(city, request.args.get("country", "India"), request.args.get("days", 16))}
    except Exception as exc:
        data = {"ok": False, "error": str(exc)}
    return render_template_string(DASH_HTML, data=data)

@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

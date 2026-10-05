"""
Weather email bot.

Reads cities from cities.txt, gets current weather from Open-Meteo (free, no API key),
and emails a summary. Designed to run on a schedule via GitHub Actions.

Settings come from environment variables (set as GitHub Secrets):
  EMAIL_USER      - the Gmail address that sends the email
  EMAIL_PASSWORD  - a Gmail *App Password* (not your normal password)
  EMAIL_TO        - where to send it (optional; defaults to EMAIL_USER;
                    can be several addresses separated by commas)
  UNITS           - "imperial" (F, mph) or "metric" (C, km/h). Default imperial.
  SMTP_HOST/PORT  - optional; default smtp.gmail.com:465
  DRY_RUN         - set to 1 to print the email instead of sending it
"""

import json
import os
import smtplib
import ssl
import sys
import urllib.parse
import urllib.request
from email.message import EmailMessage

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WX_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Light freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Violent showers",
    85: "Light snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Severe thunderstorm with hail",
}

US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa",
    "ks": "kansas", "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire",
    "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina",
    "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee",
    "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}


def get_json(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=20) as resp:
        return json.load(resp)


def load_cities(path):
    cities = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                cities.append(line)
    return cities


def geocode(entry):
    """'Austin, TX' / 'Paris, France' / 'London, GB' -> best matching place."""
    parts = [p.strip() for p in entry.split(",") if p.strip()]
    name, qualifiers = parts[0], [q.lower() for q in parts[1:]]
    data = get_json(GEO_URL, {"name": name, "count": 20, "language": "en", "format": "json"})
    results = data.get("results") or []

    def matches(r, q):
        fields = {
            (r.get("country_code") or "").lower(),
            (r.get("country") or "").lower(),
            (r.get("admin1") or "").lower(),
            (r.get("admin2") or "").lower(),
        }
        return q in fields or US_STATES.get(q) in fields

    for r in results:
        if all(matches(r, q) for q in qualifiers):
            return r
    return None


def get_weather(place, units):
    imperial = units == "imperial"
    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "weather_code,wind_speed_10m,precipitation",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": 1,
        "timezone": "auto",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
        "precipitation_unit": "inch" if imperial else "mm",
    }
    return get_json(WX_URL, params)


def format_city(entry, place, wx, units):
    imperial = units == "imperial"
    t = "°F" if imperial else "°C"
    w = "mph" if imperial else "km/h"
    p = "in" if imperial else "mm"
    c, d = wx["current"], wx["daily"]
    label = ", ".join(x for x in [place.get("name"), place.get("admin1"), place.get("country_code")] if x)
    local_time = c["time"].replace("T", " ")
    rain_chance = d["precipitation_probability_max"][0]
    return "\n".join([
        f"{label}  (local time {local_time})",
        f"  {WEATHER_CODES.get(c['weather_code'], 'Unknown')}, {c['temperature_2m']:.0f}{t} "
        f"(feels like {c['apparent_temperature']:.0f}{t})",
        f"  Today: high {d['temperature_2m_max'][0]:.0f}{t} / low {d['temperature_2m_min'][0]:.0f}{t}, "
        f"{rain_chance if rain_chance is not None else '?'}% chance of rain",
        f"  Humidity {c['relative_humidity_2m']}%  ·  Wind {c['wind_speed_10m']:.0f} {w}  ·  "
        f"Precip now {c['precipitation']} {p}",
    ])


def clean(value):
    """Remove spaces, line breaks and invisible characters picked up by copy-paste."""
    return "".join(ch for ch in (value or "") if ch.isprintable() and not ch.isspace())


def send_email(subject, body):
    user = clean(os.environ.get("EMAIL_USER"))
    password = clean(os.environ.get("EMAIL_PASSWORD"))
    to = ",".join(clean(a) for a in (os.environ.get("EMAIL_TO") or user).split(",") if clean(a))
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")

    if not user or "@" not in user:
        sys.exit("EMAIL_USER secret is missing or isn't an email address.")
    if not password:
        sys.exit("EMAIL_PASSWORD secret is missing.")
    if host == "smtp.gmail.com" and len(password) != 16:
        print(f"Warning: EMAIL_PASSWORD is {len(password)} characters; a Gmail App Password "
              "is 16 letters. Make sure you used the App Password, not your normal password.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)

    # Safe diagnostics: never prints the password itself.
    local, _, domain = user.partition("@")
    print(f"Sender: {local[:2]}***@{domain}  (password: {len(password)} chars, "
          f"{'letters only' if password.isalpha() else 'contains non-letters'}, "
          f"{'plain ASCII' if password.isascii() else 'HAS NON-ASCII CHARACTERS'})")
    print(f"Recipients: {len(to.split(','))}")

    ctx = ssl.create_default_context()
    attempts = [(465, "PLAIN"), (587, "PLAIN"), (465, "LOGIN"), (587, "LOGIN")]
    last_error = None
    for port, mech in attempts:
        try:
            if port == 465:
                s = smtplib.SMTP_SSL(host, 465, context=ctx, timeout=30)
            else:
                s = smtplib.SMTP(host, 587, timeout=30)
                s.ehlo()
                s.starttls(context=ctx)
            with s:
                s.ehlo()
                print(f"Port {port}: server offers AUTH {s.esmtp_features.get('auth', '(none)')}; "
                      f"trying {mech}...")
                s.user, s.password = user, password
                if mech == "PLAIN":
                    s.auth("PLAIN", s.auth_plain)
                else:
                    s.auth("LOGIN", s.auth_login, initial_response_ok=False)
                s.send_message(msg)
                print(f"Email sent via port {port} ({mech}).")
                return
        except smtplib.SMTPAuthenticationError as e:
            reply = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else e.smtp_error
            sys.exit(f"Gmail rejected the login (code {e.smtp_code}): {reply}\n"
                     "Check that EMAIL_USER is the Gmail address and EMAIL_PASSWORD is a 16-letter "
                     "App Password from https://myaccount.google.com/apppasswords for that same account.")
        except (smtplib.SMTPException, OSError) as e:
            print(f"  -> failed: {type(e).__name__}: {e}")
            last_error = e

    sys.exit(f"Could not send email on any port/method. Last error: {last_error}\n"
             "Gmail is hanging up during login. Most likely the App Password doesn't belong to the "
             "EMAIL_USER account, it was revoked, or Google blocked the sign-in. Check "
             "https://myaccount.google.com/notifications for a 'sign-in blocked' alert, then make a "
             "new App Password and paste it into the EMAIL_PASSWORD secret.")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    units = os.environ.get("UNITS", "imperial").strip().lower()
    cities = load_cities(os.path.join(here, "cities.txt"))
    if not cities:
        sys.exit("cities.txt has no cities in it.")

    sections, headline = [], []
    for entry in cities:
        try:
            place = geocode(entry)
            if not place:
                sections.append(f"{entry}\n  Couldn't find this city - check the spelling in cities.txt.")
                continue
            wx = get_weather(place, units)
            sections.append(format_city(entry, place, wx, units))
            headline.append(f"{place['name']} {wx['current']['temperature_2m']:.0f}°")
        except Exception as e:  # one bad city shouldn't stop the rest
            sections.append(f"{entry}\n  Error getting weather: {e}")

    subject = "Weather: " + (", ".join(headline) if headline else "update")
    body = "\n\n".join(sections) + "\n\n-- Weather data: Open-Meteo.com"

    if os.environ.get("DRY_RUN") == "1":
        print("SUBJECT:", subject, "\n")
        print(body)
    else:
        send_email(subject, body)
        print("Sent:", subject)


if __name__ == "__main__":
    main()

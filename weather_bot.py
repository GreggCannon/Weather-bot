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


def city_row(place, wx, units):
    """Turn one city's weather into a dict of display values."""
    imperial = units == "imperial"
    t = "°F" if imperial else "°C"
    w = "mph" if imperial else "km/h"
    c, d = wx["current"], wx["daily"]
    rain = d["precipitation_probability_max"][0]
    hour = c["time"].split("T")[1]
    h, m = int(hour[:2]), hour[3:5]
    local = f"{(h % 12) or 12}:{m} {'AM' if h < 12 else 'PM'}"
    return {
        "City": ", ".join(x for x in [place.get("name"), place.get("admin1"), place.get("country_code")] if x),
        "Conditions": WEATHER_CODES.get(c["weather_code"], "Unknown"),
        "Temp": f"{c['temperature_2m']:.0f}{t}",
        "Feels like": f"{c['apparent_temperature']:.0f}{t}",
        "High / Low": f"{d['temperature_2m_max'][0]:.0f}° / {d['temperature_2m_min'][0]:.0f}°",
        "Rain": f"{rain}%" if rain is not None else "?",
        "Humidity": f"{c['relative_humidity_2m']}%",
        "Wind": f"{c['wind_speed_10m']:.0f} {w}",
        "Local time": local,
        "_temp_f": c["temperature_2m"] if imperial else c["temperature_2m"] * 9 / 5 + 32,
    }


COLUMNS = ["City", "Conditions", "Temp", "Feels like", "High / Low", "Rain", "Humidity", "Wind", "Local time"]


def temp_color(temp_f):
    """Soft background colour for the temperature cell."""
    if temp_f >= 95: return "#fcd9d4"
    if temp_f >= 80: return "#fde8cf"
    if temp_f >= 60: return "#fdf5d3"
    if temp_f >= 40: return "#dcecf7"
    return "#d4e2f7"


def text_table(rows, problems):
    """Fixed-width table for email apps that don't show HTML."""
    widths = {col: max(len(col), *(len(r[col]) for r in rows)) for col in COLUMNS} if rows else {}
    lines = []
    if rows:
        lines.append("  ".join(col.ljust(widths[col]) for col in COLUMNS))
        lines.append("  ".join("-" * widths[col] for col in COLUMNS))
        for r in rows:
            lines.append("  ".join(r[col].ljust(widths[col]) for col in COLUMNS))
    for p in problems:
        lines.append(f"\n{p}")
    lines.append("\n-- Weather data: Open-Meteo.com")
    return "\n".join(lines)


def html_table(rows, problems, updated):
    """Email-safe HTML table (inline styles only, since email apps ignore stylesheets)."""
    from html import escape
    th = ("padding:10px 12px;text-align:left;font-size:12px;font-weight:600;color:#ffffff;"
          "background:#2b4c7e;white-space:nowrap;border-bottom:2px solid #1d355a;")
    td = "padding:10px 12px;font-size:14px;color:#1f2933;border-bottom:1px solid #e4e7eb;white-space:nowrap;"
    head = "".join(f'<th style="{th}">{escape(col)}</th>' for col in COLUMNS)
    body = []
    for i, r in enumerate(rows):
        bg = "#ffffff" if i % 2 == 0 else "#f5f7fa"
        cells = []
        for col in COLUMNS:
            style = td + f"background:{bg};"
            if col == "City":
                style += "font-weight:600;"
            if col == "Temp":
                style += f"background:{temp_color(r['_temp_f'])};font-weight:700;font-size:16px;"
            cells.append(f'<td style="{style}">{escape(r[col])}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    notes = "".join(f'<p style="color:#b42318;font-size:13px;margin:8px 0;">{escape(p)}</p>' for p in problems)
    return f"""<!doctype html>
<html><body style="margin:0;padding:16px;background:#eef1f5;font-family:Segoe UI,Arial,Helvetica,sans-serif;">
  <div style="max-width:960px;margin:0 auto;">
    <h2 style="margin:0 0 4px;color:#1f2933;font-size:20px;">Weather update</h2>
    <p style="margin:0 0 14px;color:#616e7c;font-size:13px;">{escape(updated)}</p>
    <div style="overflow-x:auto;">
      <table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;background:#ffffff;border:1px solid #d9dee5;">
        <thead><tr>{head}</tr></thead>
        <tbody>{''.join(body)}</tbody>
      </table>
    </div>
    {notes}
    <p style="margin:14px 0 0;color:#9aa5b1;font-size:11px;">Weather data: Open-Meteo.com</p>
  </div>
</body></html>"""


def clean(value):
    """Remove spaces, line breaks and invisible characters picked up by copy-paste."""
    return "".join(ch for ch in (value or "") if ch.isprintable() and not ch.isspace())


def send_email(subject, body, html=None):
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
    if html:
        msg.add_alternative(html, subtype="html")

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

    rows, problems, headline = [], [], []
    for entry in cities:
        try:
            place = geocode(entry)
            if not place:
                problems.append(f"Couldn't find \"{entry}\" - check the spelling in cities.txt.")
                continue
            wx = get_weather(place, units)
            rows.append(city_row(place, wx, units))
            headline.append(f"{place['name']} {wx['current']['temperature_2m']:.0f}°")
        except Exception as e:  # one bad city shouldn't stop the rest
            problems.append(f"Error getting weather for \"{entry}\": {e}")

    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(os.environ.get("TIMEZONE", "America/Chicago")))
    except Exception:
        now = datetime.now(timezone(timedelta(hours=-5)))
    updated = "As of " + now.strftime("%A, %B %d, %Y at %I:%M %p").replace(" 0", " ")

    subject = "Weather: " + (", ".join(headline) if headline else "update")
    body = updated + "\n\n" + text_table(rows, problems)
    html = html_table(rows, problems, updated)

    if os.environ.get("DRY_RUN") == "1":
        print("SUBJECT:", subject, "\n")
        print(body)
        with open(os.path.join(here, "preview.html"), "w", encoding="utf-8") as f:
            f.write(html)
        print("\n(HTML preview written to preview.html)")
    else:
        send_email(subject, body, html)
        print("Sent:", subject)


if __name__ == "__main__":
    main()

"""Outside weather from Open-Meteo (free, no key). Defaults to Canoas, RS."""

import json
import logging
import os
import time
import urllib.parse
import urllib.request

log = logging.getLogger("buddy.weather")

LATITUDE = float(os.environ.get("BUDDY_WEATHER_LAT", "-29.92"))
LONGITUDE = float(os.environ.get("BUDDY_WEATHER_LON", "-51.18"))
PLACE = os.environ.get("BUDDY_WEATHER_PLACE", "Canoas")
REFRESH_SECONDS = 15 * 60

# WMO weather codes -> (short Portuguese description, icon)
_CODES = [
    ((0,), "céu limpo", "☀"),
    ((1, 2), "poucas nuvens", "⛅"),
    ((3,), "nublado", "☁"),
    ((45, 48), "neblina", "🌫"),
    ((51, 53, 55, 56, 57), "garoa", "🌦"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "chuva", "🌧"),
    ((71, 73, 75, 77, 85, 86), "neve", "❄"),
    ((95, 96, 99), "trovoada", "⛈"),
]


def describe(code: int, is_day: bool = True) -> tuple[str, str]:
    for codes, text, icon in _CODES:
        if code in codes:
            if not is_day and icon in ("☀", "⛅"):
                icon = "☾"
            return text, icon
    return "tempo incerto", "·"


def parse(data: dict) -> dict:
    current, daily = data["current"], data.get("daily", {})
    text, icon = describe(int(current["weather_code"]), bool(current.get("is_day", 1)))
    return {
        "place": PLACE,
        "temp": round(float(current["temperature_2m"])),
        "min": round(float(daily["temperature_2m_min"][0])) if daily.get("temperature_2m_min") else None,
        "max": round(float(daily["temperature_2m_max"][0])) if daily.get("temperature_2m_max") else None,
        "text": text,
        "icon": icon,
        "updated_at": time.time(),
    }


def fetch(timeout: float = 10.0) -> dict:
    query = urllib.parse.urlencode({
        "latitude": LATITUDE, "longitude": LONGITUDE,
        "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": "America/Sao_Paulo", "forecast_days": 1,
    })
    with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=timeout) as resp:
        return parse(json.loads(resp.read()))

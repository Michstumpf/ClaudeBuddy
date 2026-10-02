"""Outside weather and city search from Open-Meteo (free, no key).

The city comes from the Buddy's settings; without one, or if the search
fails, it falls back to Canoas, RS (or BUDDY_WEATHER_LAT/LON/PLACE).
"""

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


def parse(data: dict, place: str = PLACE) -> dict:
    current, daily = data["current"], data.get("daily", {})
    text, icon = describe(int(current["weather_code"]), bool(current.get("is_day", 1)))
    return {
        "place": place,
        "temp": round(float(current["temperature_2m"])),
        "min": round(float(daily["temperature_2m_min"][0])) if daily.get("temperature_2m_min") else None,
        "max": round(float(daily["temperature_2m_max"][0])) if daily.get("temperature_2m_max") else None,
        "text": text,
        "icon": icon,
        "updated_at": time.time(),
    }


# Brazilian states, so "São José, SC" can pick the right São José.
UF = {"AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
      "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
      "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
      "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
      "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
      "SE": "Sergipe", "TO": "Tocantins"}


def pick(results: list[dict], hint: str = "") -> dict | None:
    """Best match: one whose state/country matches the hint ("SC", "Santa Catarina",
    "Portugal"…), else the first Brazilian result, else the first one."""
    if not results:
        return None
    hint = hint.strip().lower()
    if hint:
        state = UF.get(hint.upper(), hint).lower()
        for r in results:
            if state in (r.get("admin1") or "").lower() or hint in ((r.get("country") or "").lower(), (r.get("country_code") or "").lower()):
                return r
    return next((r for r in results if r.get("country_code") == "BR"), results[0])


def geocode(query: str, timeout: float = 10.0) -> dict | None:
    """'Porto Alegre' or 'São José, SC' -> {"name", "admin1", "country_code", "lat", "lon"}; None if not found."""
    name, _, hint = query.partition(",")
    if not name.strip():
        return None
    params = urllib.parse.urlencode({"name": name.strip(), "count": 10, "language": "pt", "format": "json"})
    with urllib.request.urlopen(f"https://geocoding-api.open-meteo.com/v1/search?{params}", timeout=timeout) as resp:
        best = pick(json.loads(resp.read()).get("results") or [], hint)
    if not best:
        return None
    return {"name": best["name"], "admin1": best.get("admin1") or "", "country_code": best.get("country_code") or "",
            "lat": float(best["latitude"]), "lon": float(best["longitude"])}


def fetch(lat: float = LATITUDE, lon: float = LONGITUDE, place: str = PLACE, timeout: float = 10.0) -> dict:
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": "auto", "forecast_days": 1,  # the city's own time zone
    })
    with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=timeout) as resp:
        return parse(json.loads(resp.read()), place)

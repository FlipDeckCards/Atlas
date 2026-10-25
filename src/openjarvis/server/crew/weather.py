"""Gale's one tool: the weather, from Open-Meteo (free, no key, no account). A place name is looked up, then a short forecast is read back.

Read only: it never posts or changes anything.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Dict, Optional

import httpx

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
DEFAULT_PLACE = "Los Angeles"
MAX_DAYS = 7

# WMO weather codes, the way Open-Meteo reports them
CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
         56: "freezing drizzle", 57: "heavy freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
         71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers",
         86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail"}

ClientFactory = Callable[[], httpx.AsyncClient]


class WeatherError(Exception):
    """A failed lookup, with a message that is safe to show."""


def _clip(v: Any, n: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


def _num(v: Any, digits: int = 0) -> str:
    try:
        return f"{float(v):.{digits}f}" if digits else str(int(round(float(v))))
    except (TypeError, ValueError):
        return "?"


async def forecast(place: str, days: int = 3, cf: Optional[ClientFactory] = None, metric: bool = False) -> str:
    """'Now' and the next few days for a place, in plain short lines. Raises WeatherError when the place is unknown or the service cannot be reached."""
    place = _clip(place, 80) or DEFAULT_PLACE
    try:
        days = min(MAX_DAYS, max(1, int(days or 3)))
    except (TypeError, ValueError):
        days = 3
    cf = cf or (lambda: httpx.AsyncClient(timeout=15))
    try:
        async with cf() as c:
            g = await c.get(GEOCODE, params={"name": place.split(",")[0].strip(), "count": 5, "language": "en", "format": "json"})
            if g.status_code >= 400:
                raise WeatherError(f"The weather service answered {g.status_code}. Try again in a minute.")
            hits = (g.json() or {}).get("results") or []
            if not hits:
                raise WeatherError(f'I could not find a place called "{place}". Try the city and state or country.')
            want = [w.strip().lower() for w in place.split(",")[1:] if w.strip()]
            hit = next((h for h in hits if want and any(w in (str(h.get("admin1", "")) + " " + str(h.get("country", "")) + " " + str(h.get("country_code", ""))).lower() for w in want)), hits[0])
            f = await c.get(FORECAST, params={
                "latitude": hit["latitude"], "longitude": hit["longitude"], "timezone": "auto", "forecast_days": days,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
                "temperature_unit": "celsius" if metric else "fahrenheit", "wind_speed_unit": "kmh" if metric else "mph"})
            if f.status_code >= 400:
                raise WeatherError(f"The weather service answered {f.status_code}. Try again in a minute.")
            data = f.json() or {}
    except WeatherError:
        raise
    except Exception as exc:
        raise WeatherError(f"The weather service could not be reached ({type(exc).__name__}). Try again in a minute.") from None
    deg, spd = ("°C", "km/h") if metric else ("°F", "mph")
    cur, daily = data.get("current") or {}, data.get("daily") or {}
    name = ", ".join(x for x in (hit.get("name"), hit.get("admin1"), hit.get("country_code")) if x)
    lines = [f"Weather for {name}:",
             f"Now: {CODES.get(cur.get('weather_code'), 'unknown')}, {_num(cur.get('temperature_2m'))}{deg} (feels like {_num(cur.get('apparent_temperature'))}{deg}), "
             f"wind {_num(cur.get('wind_speed_10m'))} {spd}, humidity {_num(cur.get('relative_humidity_2m'))}%."]
    for i, day in enumerate(daily.get("time") or []):
        try:
            label = "Today" if i == 0 else dt.date.fromisoformat(day).strftime("%a %b %d")
        except ValueError:
            label = day
        get = lambda k: (daily.get(k) or [None] * (i + 1))[i]          # noqa: E731
        rain = get("precipitation_probability_max")
        lines.append(f"{label}: {CODES.get(get('weather_code'), 'unknown')}, high {_num(get('temperature_2m_max'))}{deg}, low {_num(get('temperature_2m_min'))}{deg}"
                     + (f", {_num(rain)}% chance of rain" if rain is not None else "") + f", wind up to {_num(get('wind_speed_10m_max'))} {spd}.")
    return "\n".join(lines)

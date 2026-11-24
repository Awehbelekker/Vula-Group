"""The forecast for a spot, hour by hour, and a plain rating for a lesson (24 Nov 2026).

`forecast` reads the swell (height, period, direction) and the wind (speed, gusts, direction, in
knots), the air temperature and the chance of rain for one day at one position, from Open-Meteo's
marine and weather forecasts. Only the position and the date are sent — never a business, a
customer or a lesson. Hours are South African time.

`rate` turns one hour into good / marginal / poor for a sport and a level, in code, with the reason
in words. The limits below are a first rule of thumb, not any business's own: a model swell height
is the open sea's, and every beach turns it into something different. The team is always told the
forecast's own figures beside the rating, and decides for itself.

Open-Meteo's free service is for non-commercial use. With `conditions_api_key` set, the paid
service is used (its own hosts, the key on each request).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)

ICON = {"good": "🟢", "marginal": "🟡", "poor": "🔴"}
_MARINE = "wave_height,wave_period,wave_direction,swell_wave_height,swell_wave_period,swell_wave_direction"
_WIND = "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation_probability"
_POINTS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
_CACHE: Dict[Tuple[float, float, str], Tuple[float, Dict[int, dict]]] = {}
_TTL = 3600.0


def compass(degrees: Any) -> str:
    try:
        return _POINTS[int((float(degrees) % 360) / 22.5 + 0.5) % 16]
    except (TypeError, ValueError):
        return ""


def _hosts() -> Tuple[str, str, str]:
    from config import settings
    key = (getattr(settings, "conditions_api_key", "") or "").strip()
    if key:
        return "https://customer-marine-api.open-meteo.com", "https://customer-api.open-meteo.com", key
    return "https://marine-api.open-meteo.com", "https://api.open-meteo.com", ""


async def _read(url: str, params: dict) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


def merge(marine: dict, weather: dict) -> Dict[int, dict]:
    """{hour of the day: {wave_m, period_s, wave_from, swell_m, swell_s, swell_from, wind_kn, gust_kn,
    wind_from, air_c, rain_pct}} from the two answers. A figure the forecast didn't give is absent."""
    out: Dict[int, dict] = {}
    names = (("wave_height", "wave_m"), ("wave_period", "period_s"), ("wave_direction", "wave_from"),
             ("swell_wave_height", "swell_m"), ("swell_wave_period", "swell_s"), ("swell_wave_direction", "swell_from"),
             ("wind_speed_10m", "wind_kn"), ("wind_gusts_10m", "gust_kn"), ("wind_direction_10m", "wind_from"),
             ("temperature_2m", "air_c"), ("precipitation_probability", "rain_pct"))
    for answer in (marine, weather):
        hourly = (answer or {}).get("hourly") or {}
        times: List[str] = hourly.get("time") or []
        for theirs, ours in names:
            values = hourly.get(theirs)
            if not isinstance(values, list):
                continue
            for stamp, value in zip(times, values):
                if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                try:
                    hour = int(str(stamp)[11:13])
                except ValueError:
                    continue
                out.setdefault(hour, {})[ours] = value
    return out


async def forecast(lat: float, lng: float, day: date) -> Dict[int, dict]:
    """One day's hours at a position (kept an hour). Raises when neither forecast answers."""
    key = (round(float(lat), 3), round(float(lng), 3), day.isoformat())
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    marine_host, weather_host, api_key = _hosts()
    base = {"latitude": key[0], "longitude": key[1], "timezone": "Africa/Johannesburg",
            "start_date": key[2], "end_date": key[2]}
    if api_key:
        base["apikey"] = api_key
    marine, weather = await asyncio.gather(
        _read(f"{marine_host}/v1/marine", {**base, "hourly": _MARINE}),
        _read(f"{weather_host}/v1/forecast", {**base, "hourly": _WIND, "wind_speed_unit": "kn"}),
        return_exceptions=True)
    if isinstance(marine, Exception) and isinstance(weather, Exception):
        raise RuntimeError(f"no forecast: {type(marine).__name__}, {type(weather).__name__}")
    hours = merge({} if isinstance(marine, Exception) else marine, {} if isinstance(weather, Exception) else weather)
    if not hours:
        raise RuntimeError("the forecast had no hours for that day")
    _CACHE[key] = (time.monotonic(), hours)
    return hours


def describe(hour: dict) -> str:
    """The forecast's own figures in a line: 'swell 1.4 m at 10 s from SW, wind 5 kn SE gusting 9'."""
    bits = []
    if hour.get("wave_m") is not None:
        s = f"swell {hour['wave_m']:.1f} m"
        if hour.get("period_s") is not None:
            s += f" at {hour['period_s']:.0f} s"
        if hour.get("wave_from") is not None:
            s += f" from {compass(hour['wave_from'])}"
        bits.append(s)
    if hour.get("wind_kn") is not None:
        s = f"wind {hour['wind_kn']:.0f} kn"
        if hour.get("wind_from") is not None:
            s += f" {compass(hour['wind_from'])}"
        if hour.get("gust_kn") is not None:
            s += f" gusting {hour['gust_kn']:.0f}"
        bits.append(s)
    if hour.get("rain_pct") is not None and hour["rain_pct"] >= 30:
        bits.append(f"{hour['rain_pct']:.0f}% chance of rain")
    return ", ".join(bits) or "no forecast for this hour"


# (marginal above, poor above) — None: no limit
_LIMITS = {
    ("surf", "learner"): {"wave": (1.8, 2.5), "gust": (20, 30)},
    ("surf", "rider"): {"wave": (3.0, 4.0), "gust": (25, 35)},
    ("sup", "any"): {"wave": (1.0, 1.5), "wind": (10, 15)},
}


def rate(sport: str, level: str, hour: dict) -> dict:
    """{"rating": good | marginal | poor, "reasons": [...]} for one hour. A figure the forecast
    didn't give is not judged; with nothing to judge the rating is marginal ('no forecast')."""
    reasons: List[str] = []
    worst = 0

    def over(value: Any, limits: Tuple[float, float], marginal: str, poor: str) -> None:
        nonlocal worst
        if value is None:
            return
        if value > limits[1]:
            worst = max(worst, 2)
            reasons.append(poor)
        elif value > limits[0]:
            worst = max(worst, 1)
            reasons.append(marginal)

    wave, wind, gust, rain = hour.get("wave_m"), hour.get("wind_kn"), hour.get("gust_kn"), hour.get("rain_pct")
    if all(v is None for v in (wave, wind, gust, rain)):
        return {"rating": "marginal", "reasons": ["no forecast for this hour"]}
    if sport == "surf":
        learner = level in ("", "first_timer", "beginner")
        lim = _LIMITS[("surf", "learner" if learner else "rider")]
        over(wave, lim["wave"], "a solid swell" + (" for learners" if learner else ""),
             "a big swell" + (" for learners" if learner else ""))
        over(gust, lim["gust"], "gusty", "very strong wind")
        if wave is not None and wave < (0.3 if learner else 0.5):
            worst = max(worst, 1)
            reasons.append("very small")
    elif sport == "sup":
        lim = _LIMITS[("sup", "any")]
        over(wave, lim["wave"], "some swell", "too much swell")
        over(wind, lim["wind"], "breezy", "too windy to paddle")
    elif sport == "kite":
        if wind is not None:
            if wind < 10:
                worst = 2
                reasons.append("not enough wind")
            elif wind < 14:
                worst = max(worst, 1)
                reasons.append("light wind")
        over(gust, (28, 35), "strong gusts", "too strong")
    else:
        over(rain, (30, 60), "rain possible", "rain likely")
        over(gust, (25, 35), "gusty", "very strong wind")
    return {"rating": ("good", "marginal", "poor")[worst], "reasons": reasons}


def day_view(hours: Dict[int, dict], sport: str = "surf", level: str = "") -> List[dict]:
    """The daylight hours (06:00–19:00) with their figures, the line and the rating — for a screen."""
    return [{"hour": f"{h:02d}:00", **hours[h], "text": describe(hours[h]), **rate(sport, level, hours[h])}
            for h in sorted(hours) if 6 <= h <= 19]

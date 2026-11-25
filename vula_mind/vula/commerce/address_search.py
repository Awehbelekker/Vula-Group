"""
vula/commerce/address_search.py — finding a street address (courier forms, 24 Nov 2026).

The person types an address and presses Find; up to five matches come back already split into the
parts a courier wants (street, suburb, town, province, postal code) with the position, so they pick
one instead of typing every field.

Asked only on that press, never as they type: OpenStreetMap's free lookup forbids search-as-you-type
and allows about one request a second, so calls are spaced and the answers are kept for the life of
the process. Only the words typed are sent — no business, no person, no contact details.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_LOOKUP = "https://nominatim.openstreetmap.org/search"
_REVERSE = "https://nominatim.openstreetmap.org/reverse"
_SUGGEST = "https://photon.komoot.io/api/"          # made for search-as-you-type; fair use, no key
_SA_BOX = "16.3,-35.0,33.0,-22.0"                   # South Africa, to rank its places first
_WARD = re.compile(r"\bward\s+\d+\s*$", re.I)       # "Cape Town Ward 115" is a voting ward, not a suburb
_AGENT = "VulaCommerce/1.0 (address lookup)"
_SPACING_SECONDS = 1.1
_KEEP = 500

_PROVINCE_CODES = {"EC", "FS", "GP", "KZN", "LP", "MP", "NC", "NW", "WC"}
_OLD_CODES = {"GT": "GP", "NL": "KZN", "NP": "LP"}             # older ISO codes still in the map data
_PROVINCE_NAMES = {"eastern cape": "EC", "free state": "FS", "gauteng": "GP", "kwazulu-natal": "KZN",
                   "kwazulu natal": "KZN", "limpopo": "LP", "mpumalanga": "MP", "northern cape": "NC",
                   "north west": "NW", "north-west": "NW", "western cape": "WC"}

_cache: dict = {}
_lock: Optional[asyncio.Lock] = None
_last = 0.0


class LookupDown(Exception):
    """The address lookup couldn't be reached or refused. The form still works by hand."""


def _province(addr: dict) -> str:
    code = str(addr.get("ISO3166-2-lvl4") or "").upper().replace("ZA-", "")
    code = _OLD_CODES.get(code, code)
    if code in _PROVINCE_CODES:
        return code
    return _PROVINCE_NAMES.get(str(addr.get("state") or "").strip().lower(), "")


def address_parts(row: dict) -> Optional[dict]:
    """One lookup result as the fields of an address form. None when it has no position, or names
    no street and no place (a bare province or the country is not an address)."""
    a = row.get("address") if isinstance(row.get("address"), dict) else {}
    try:
        lat, lng = float(row["lat"]), float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    road = str(a.get("road") or a.get("pedestrian") or a.get("residential") or "").strip()
    number = str(a.get("house_number") or "").strip()
    street = f"{number} {road}".strip() if road else ""
    area = next((str(a[k]) for k in ("suburb", "neighbourhood", "quarter", "city_district", "village", "hamlet")
                 if a.get(k) and not _WARD.search(str(a[k]))), "")
    city = next((str(a[k]) for k in ("city", "town", "municipality", "village", "county") if a.get(k)), "")
    if not street and not area and not city:
        return None
    code = re.sub(r"\D", "", str(a.get("postcode") or ""))[:4]
    place = str(a.get("amenity") or a.get("shop") or a.get("building") or a.get("office") or "").strip()
    label = ", ".join(x for x in (place, street, area if area != city else "", city, code) if x)
    return {"label": label[:160], "place": place[:80], "street_address": street[:120], "local_area": area[:80],
            "city": city[:80], "zone": _province(a), "code": code, "lat": lat, "lng": lng,
            "has_number": bool(number)}


def _typed_number(query: str) -> str:
    """The street number the person typed first ("12 Main Road", "12A Main Rd"), or ''."""
    m = re.match(r"\s*(\d{1,5}[a-zA-Z]?)\s+\S", query or "")
    return m.group(1) if m else ""


def suggestion_parts(feature: dict, typed_number: str = "") -> Optional[dict]:
    """One search-as-you-type result as the form's fields. South Africa only. The map often knows
    the street but not the house: then the number the person typed is kept in front of it."""
    p = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    if str(p.get("countrycode") or "").upper() != "ZA":
        return None
    try:
        lng, lat = (float(x) for x in (feature.get("geometry") or {}).get("coordinates")[:2])
    except (TypeError, ValueError):
        return None
    name = str(p.get("name") or "").strip()
    is_street = p.get("osm_key") == "highway" or p.get("type") == "street"
    road = str(p.get("street") or (name if is_street else "")).strip()
    number = str(p.get("housenumber") or "").strip()
    typed = "" if number else (typed_number if road else "")
    street = f"{number or typed} {road}".strip() if road else ""
    area = next((str(p[k]) for k in ("locality", "district") if p.get(k) and not _WARD.search(str(p[k]))), "")
    city = str(p.get("city") or p.get("county") or "").strip()
    if p.get("type") in ("city", "district", "locality") and name and not road:
        area, city = (name if name != city else area), city or name
    if not street and not area and not city:
        return None
    code = re.sub(r"\D", "", str(p.get("postcode") or ""))[:4]
    place = "" if is_street or not road else name
    label = ", ".join(x for x in (place, street, area if area != city else "", city, code) if x)
    return {"label": label[:160], "place": place[:80], "street_address": street[:120], "local_area": area[:80],
            "city": city[:80], "zone": _PROVINCE_NAMES.get(str(p.get("state") or "").strip().lower(), ""),
            "code": code, "lat": lat, "lng": lng, "has_number": bool(number or typed),
            "number_typed": bool(typed)}


_suggest_cache: dict = {}


async def suggest(query: str, limit: int = 5) -> list:
    """Addresses for what has been typed so far (search-as-you-type). [] when too little is typed,
    nothing matches, or the service can't be reached — suggestions are a help, never an error."""
    q = " ".join((query or "").split())[:120]
    key = q.lower()
    if len(key) < 4:
        return []
    if key in _suggest_cache:
        return _suggest_cache[key]
    try:
        async with httpx.AsyncClient(timeout=4.0, headers={"User-Agent": _AGENT}) as client:
            resp = await client.get(_SUGGEST, params={"q": q, "limit": 8, "lang": "en", "bbox": _SA_BOX})
        feats = resp.json().get("features") if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        log.info("address suggestions unavailable: %s", type(exc).__name__)
        return []
    if not isinstance(feats, list):
        return []
    number, out, seen = _typed_number(q), [], set()
    for f in feats:
        parts = suggestion_parts(f, number) if isinstance(f, dict) else None
        if parts and parts["label"] not in seen:
            seen.add(parts["label"])
            out.append(parts)
    out = out[:max(1, min(int(limit), 8))]
    if len(_suggest_cache) > _KEEP * 4:
        _suggest_cache.clear()
    _suggest_cache[key] = out
    return out


async def at(lat: float, lng: float) -> Optional[dict]:
    """The address at a pin dropped on the map, as the form's fields. The position returned is the
    pin's own (where the person put it), not the map's nearest building. None when there is no
    address there (the sea); raises LookupDown when the lookup can't be reached."""
    global _lock, _last
    if not (-35.5 <= lat <= -21.5 and 16.0 <= lng <= 33.5):
        return None
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        wait = _SPACING_SECONDS - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": _AGENT}) as client:
                resp = await client.get(_REVERSE, params={"lat": f"{lat:.6f}", "lon": f"{lng:.6f}", "format": "json",
                                                          "addressdetails": 1, "zoom": 18})
        except httpx.HTTPError as exc:
            raise LookupDown("unreachable") from exc
        finally:
            _last = time.monotonic()
    if resp.status_code != 200:
        raise LookupDown(f"answered {resp.status_code}")
    row = resp.json()
    if not isinstance(row, dict) or row.get("error") or str((row.get("address") or {}).get("country_code") or "").lower() != "za":
        return None
    parts = address_parts({**row, "lat": lat, "lon": lng})
    return parts


async def search(query: str, limit: int = 5) -> list:
    """Up to `limit` South African addresses matching what was typed, split into parts. [] when
    nothing matches (or too little was typed to ask); raises LookupDown when the lookup can't be
    reached — a failure is never kept."""
    global _lock, _last
    q = " ".join((query or "").split())[:200]
    key = q.lower()
    if len(key) < 5:
        return []
    if key in _cache:
        return _cache[key]
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        wait = _SPACING_SECONDS - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": _AGENT}) as client:
                resp = await client.get(_LOOKUP, params={
                    "q": q, "format": "json", "addressdetails": 1,
                    "limit": max(1, min(int(limit), 8)), "countrycodes": "za"})
        except httpx.HTTPError as exc:
            raise LookupDown("unreachable") from exc
        finally:
            _last = time.monotonic()
    if resp.status_code != 200:
        raise LookupDown(f"answered {resp.status_code}")
    rows = resp.json()
    out, seen, typed = [], set(), _typed_number(q)
    for row in rows if isinstance(rows, list) else []:
        parts = address_parts(row) if isinstance(row, dict) else None
        if parts and typed and parts["street_address"] and not parts["has_number"]:
            # the map knows the street, not the house: keep the number the person typed
            parts.update(street_address=f"{typed} {parts['street_address']}"[:120], has_number=True, number_typed=True,
                         label=f"{typed} {parts['label']}"[:160])
        if parts and parts["label"] not in seen:
            seen.add(parts["label"])
            out.append(parts)
    if len(_cache) > _KEEP:
        _cache.clear()
    _cache[key] = out
    log.info("address search: %d result(s)", len(out))
    return out

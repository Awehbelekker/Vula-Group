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
                 if a.get(k)), "")
    city = next((str(a[k]) for k in ("city", "town", "municipality", "village", "county") if a.get(k)), "")
    if not street and not area and not city:
        return None
    code = re.sub(r"\D", "", str(a.get("postcode") or ""))[:4]
    place = str(a.get("amenity") or a.get("shop") or a.get("building") or a.get("office") or "").strip()
    label = ", ".join(x for x in (place, street, area if area != city else "", city, code) if x)
    return {"label": label[:160], "place": place[:80], "street_address": street[:120], "local_area": area[:80],
            "city": city[:80], "zone": _province(a), "code": code, "lat": lat, "lng": lng,
            "has_number": bool(number)}


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
    out, seen = [], set()
    for row in rows if isinstance(rows, list) else []:
        parts = address_parts(row) if isinstance(row, dict) else None
        if parts and parts["label"] not in seen:
            seen.add(parts["label"])
            out.append(parts)
    if len(_cache) > _KEEP:
        _cache.clear()
    _cache[key] = out
    log.info("address search: %d result(s)", len(out))
    return out

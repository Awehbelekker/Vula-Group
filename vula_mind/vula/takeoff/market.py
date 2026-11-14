"""
vula/takeoff/market.py — the current market price of a building material, looked up first.

Trade costing, phase 1 (14 Nov 2026; Ian: "Vula learns, and later only looks up the KB on cost
and updates pricing from the web"). A market price is a fact Vula keeps:

  price_for(description)   the newest CHECKED price on file (vula/takeoff/market_checks.py: on
                           the supplier's page and in a plausible band). Only when there is none,
                           or it is older than STALE_DAYS for its kind, does Vula search the web —
                           and what it finds is checked the same way and saved, so the next ask is
                           a lookup again.

Market prices live in vula_material_rates (shared, never a business's own). They never enter a
business's price book: what a business paid stays its own, and always comes first.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Ian, 14 Nov 2026: materials are re-checked after 30 days, labour after 90.
STALE_DAYS = {"material": 30, "plant": 30, "labour": 90}
_STOP = {"price", "prices", "cost", "per", "the", "and", "for", "with", "south", "africa", "rand"}


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 2 and w not in _STOP]


def _matches(label: str, description: str) -> bool:
    have = set(_words(label))
    have |= {w.rstrip("s") for w in have}
    return all(w in have or w.rstrip("s") in have for w in _words(description))


def _age_days(scraped_at: Any) -> Optional[float]:
    try:
        at = datetime.fromisoformat(str(scraped_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(at.tzinfo) if at.tzinfo else datetime.now()
    return (now - at).total_seconds() / 86400


def _shape(row: Dict[str, Any], kind: str, refreshed: bool) -> Dict[str, Any]:
    age = _age_days(row.get("scraped_at"))
    return {
        "label": row.get("label"), "unit": row.get("unit"),
        "price_cents": int(round(float(row.get("mid") or 0) * 100)),
        "low_cents": int(round(float(row.get("low") or 0) * 100)),
        "high_cents": int(round(float(row.get("high") or 0) * 100)),
        "supplier": row.get("source"), "url": row.get("url") or "",
        "scraped_at": row.get("scraped_at"), "age_days": None if age is None else round(age, 1),
        "stale": age is None or age > STALE_DAYS.get(kind, 30), "refreshed": refreshed,
        "where": f"{row.get('source') or 'a supplier'}'s website"
                 + (f", {str(row.get('scraped_at'))[:10]}" if row.get("scraped_at") else ""),
    }


def lookup(description: str, unit: Optional[str] = None, kind: str = "material",
           db: Any = None) -> Optional[Dict[str, Any]]:
    """The newest checked price whose label has every word of `description` (and the unit, when
    given), or None. No web, no model."""
    from vula.takeoff.market_checks import norm_unit
    if db is None:
        from vula.takeoff.construction_rates_scraper import RatesDatabase
        db = RatesDatabase()
    want_unit = norm_unit(unit) if unit else ""
    rows = [r for r in db.get_all()
            if r.get("checked") and _matches(r.get("label") or "", description)
            and (not want_unit or norm_unit(r.get("unit")) == want_unit)]
    if not rows:
        return None
    rows.sort(key=lambda r: str(r.get("scraped_at") or ""), reverse=True)
    return _shape(rows[0], kind, refreshed=False)


async def research_price(description: str, unit: Optional[str] = None, db: Any = None) -> int:
    """Search the web for the item's price, read the pages, keep only prices that check out on
    their page, and save them. Returns how many were saved. No personal or business details
    are ever in the search — only the material."""
    from vula import research
    from vula.takeoff.construction_rates_scraper import RateExtractor, RatesDatabase
    db = db or RatesDatabase()
    item = " ".join(_words(description)) or description
    queries = [f"{item} price South Africa", f"buy {item} South Africa"]
    hits = await research._search(queries)
    pages = await research.read_pages(hits)
    extractor = RateExtractor()
    saved = 0
    for page in pages:
        supplier = urlparse(page.get("url") or "").netloc.removeprefix("www.")
        prompt = (f"The page is a South African supplier's or price guide's page. Prices for: {item}"
                  + (f" (sold per {unit})" if unit else "") + ".")
        cat = await extractor._extract_one(page.get("url") or "", supplier, prompt, page.get("text") or "")
        for rate in cat.rates:
            if rate.checked:
                db.upsert(rate)
                saved += 1
    log.info("market price research for %r: %d page(s) read, %d checked price(s) saved",
             item, len(pages), saved)
    return saved


async def price_for(description: str, unit: Optional[str] = None, kind: str = "material",
                    refresh: bool = True, db: Any = None) -> Optional[Dict[str, Any]]:
    """The current market price: a lookup when a fresh checked price is on file; otherwise one
    web search, saved, then the lookup again. A stale price that couldn't be refreshed comes back
    with stale=True (and its date), never silently as current."""
    found = lookup(description, unit, kind, db=db)
    if found and not found["stale"]:
        return found
    if not refresh:
        return found
    try:
        saved = await research_price(description, unit, db=db)
    except Exception as exc:  # noqa: BLE001 — a failed search never loses the price on file
        log.warning("market price refresh failed for %r: %s", description, type(exc).__name__)
        saved = 0
    if saved:
        again = lookup(description, unit, kind, db=db)
        if again:
            again["refreshed"] = True
            return again
    return found

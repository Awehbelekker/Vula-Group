"""
vula/commerce/product_rivals.py — the competing products a business's product stands against,
for its reps only (26 Nov 2026, migration 315).

Floornet's field guide lists, beside each of its carpets, the rival ranges it competes with, so a
rep in front of an architect can answer "what's your version of Conqueror?" by searching the
rival's name. Here a business keeps that list per product (Products › a product › Spec sheet ›
Competes with), and a product guide made for the team (`audience` 'team') shows it and finds a
product by a rival's name.

It is the business's own sales knowledge: it never goes to a client. It is kept in its own table
(`commerce_product_rivals`), never on the product row — the public product routes hand out whole
rows — and only team routes and a team guide read it. A client guide never loads it.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TABLE = "commerce_product_rivals"
MAX_PER_PRODUCT = 30


class RivalError(ValueError):
    """Said to the person in plain words."""


def _db():
    from vula.commerce import service
    return service._client()


def _text(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()[:limit]


def clean(rows: Any) -> List[Dict[str, str]]:
    """[{brand, name, note}] or one text with a "Brand: product" per line → the stored rows.
    A line with no colon is a product name with no brand. Refused in words."""
    if rows in (None, "", []):
        return []
    if isinstance(rows, str):
        rows = [ln for ln in rows.splitlines() if ln.strip()]
    if not isinstance(rows, list):
        raise RivalError("The list of competing products wasn't readable — try again.")
    out: List[Dict[str, str]] = []
    seen = set()
    for r in rows:
        if isinstance(r, str):
            line = r.strip().lstrip("•-* ").strip()
            note = ""
            if "(" in line and line.endswith(")"):
                line, note = line[:line.rindex("(")].strip(), line[line.rindex("(") + 1:-1]
            brand, name = (line.split(":", 1) if ":" in line else ("", line))
        elif isinstance(r, dict):
            brand, name, note = r.get("brand"), r.get("name"), r.get("note")
        else:
            continue
        brand, name, note = _text(brand, 40), _text(name, 80), _text(note, 120)
        if not name:
            if brand:
                raise RivalError(f'"{brand}:" needs the product\'s name after it, like "{brand}: Conqueror".')
            continue
        if re.search(r"https?://|www\.", f"{brand} {name} {note}", re.I):
            raise RivalError("Leave web addresses out of the competing products.")
        key = (brand.lower(), name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"brand": brand, "name": name, "note": note})
    if len(out) > MAX_PER_PRODUCT:
        raise RivalError(f"Keep it to {MAX_PER_PRODUCT} competing products per product.")
    return out


def _own_product(tenant_id: str, product_id: str) -> dict:
    rows = (_db().table("commerce_products").select("id,name").eq("tenant_id", tenant_id)
            .eq("id", product_id).limit(1).execute().data or [])
    if not rows:
        raise RivalError("That product isn't there any more.")
    return rows[0]


def for_product(tenant_id: str, product_id: str) -> List[Dict[str, str]]:
    rows = (_db().table(TABLE).select("brand,name,note,sort_order").eq("tenant_id", tenant_id)
            .eq("product_id", product_id).limit(MAX_PER_PRODUCT + 5).execute().data or [])
    rows.sort(key=lambda r: int(r.get("sort_order") or 0))
    return [{"brand": r.get("brand") or "", "name": r.get("name") or "", "note": r.get("note") or ""}
            for r in rows]


def for_products(tenant_id: str, product_ids: List[str]) -> Dict[str, List[Dict[str, str]]]:
    """{product id: rivals} for the given products — every row, read page by page."""
    from vula.db_pages import all_rows
    ids = [str(i) for i in product_ids if i]
    if not ids:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for start in range(0, len(ids), 200):
        part = ids[start:start + 200]
        try:
            rows = all_rows(lambda: _db().table(TABLE).select("product_id,brand,name,note,sort_order")
                            .eq("tenant_id", tenant_id).in_("product_id", part), cap=20000)
        except Exception as exc:  # noqa: BLE001 — before migration 315: simply none
            log.debug("rivals not read: %s", exc)
            return {}
        for r in rows:
            out.setdefault(str(r["product_id"]), []).append(r)
    return {pid: [{"brand": r.get("brand") or "", "name": r.get("name") or "", "note": r.get("note") or ""}
                  for r in sorted(rs, key=lambda r: int(r.get("sort_order") or 0))]
            for pid, rs in out.items()}


def replace(tenant_id: str, product_id: str, rows: Any, actor: Optional[dict] = None) -> List[Dict[str, str]]:
    """The product's whole list, as given (an empty list clears it); read back."""
    product = _own_product(tenant_id, product_id)
    cleaned = clean(rows)
    _db().table(TABLE).delete().eq("tenant_id", tenant_id).eq("product_id", product_id).execute()
    if cleaned:
        now = datetime.now(timezone.utc).isoformat()
        _db().table(TABLE).insert([
            {"tenant_id": tenant_id, "product_id": product_id, "brand": r["brand"] or None, "name": r["name"],
             "note": r["note"] or None, "sort_order": i, "created_by": (actor or {}).get("email"), "created_at": now}
            for i, r in enumerate(cleaned)]).execute()
    back = for_product(tenant_id, product_id)
    if [(r["brand"], r["name"], r["note"]) for r in back] != [(r["brand"], r["name"], r["note"]) for r in cleaned]:
        raise RivalError("The list didn't save — try again.")
    try:
        from vula.api.merchant_audit import audit
        audit(tenant_id, actor, "product_rivals_set", product_id=product_id, product=product.get("name"),
              count=len(back))
    except Exception:  # noqa: BLE001
        pass
    return back


def label(r: Dict[str, str]) -> str:
    """'Belgotex Conqueror' / 'Conqueror' — how a rival reads on a card."""
    return " ".join(x for x in (r.get("brand"), r.get("name")) if x)

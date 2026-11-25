"""
vula/commerce/product_guides.py — spec sheets on products, and product guides a business sends.

Ian, 25 Nov 2026: Floornet's "Field Guide for Specifiers" (one HTML page with its products typed
into it by hand: grade, construction, benefits, a specs table, colours and a "check stock on
WhatsApp" button) is "a really nice tool to have" — Vula's own design, white-labelled for any
flooring contractor or distributor (Gerflor and others), and for a business to send its own
clients.

Here it comes from the business's own products, so it is never out of date:

  * A product carries a spec sheet (`commerce_products.specs`, migration 312), cleaned by
    `clean_specs`: a grade line, benefits, label/value rows, a link to the maker's page. Never a
    price — prices stay `price_cents`.
  * A guide (`commerce_product_guides`) is a secret link (`token`) to a choice of the products —
    categories and/or chosen products, both empty = every listed product — titled for a client,
    with or without prices. The page (`vula/api/product_guides.py`) wears the business's own name,
    logo and colour; it is never indexed; turned off, the link stops working.
  * "Check stock on WhatsApp" opens the business's own number with the product, the colour picked
    and the guide named, typed in. Vula's shop assistant answers it from real stock — the page
    itself never says a quantity.

Prices are integer cents from the database (`service.effective_price_cents`), never worked out in
the browser. Nothing here is written by a model.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TABLE = "commerce_product_guides"

MAX_ROWS = 30
MAX_BENEFITS = 8
MAX_PRODUCTS = 500
MAX_CATEGORIES = 40
_COLOUR_NAMES = {"colour", "colours", "color", "colors", "kleur", "shade", "shades", "finish", "finishes"}


class GuideError(ValueError):
    """Said to the person in plain words."""


def _db():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()[:limit]


def _https(v: Any) -> str:
    u = _text(v, 500)
    if not u:
        return ""
    if not re.match(r"^https://[^\s/]+\.[^\s]+$", u, re.I):
        raise GuideError("The link must be a full web address starting with https://")
    return u


# ── the spec sheet on a product ───────────────────────────────────────────────────────────────

def clean_specs(raw: Any) -> Optional[Dict[str, Any]]:
    """{grade, benefits, rows, link} as typed → the stored shape, or None when it's empty.

    `rows` may be [[label, value]], [{label, value}] or one text with a "Label: value" per line —
    the dashboard sends what the person typed. Refuses, in words, what can't be kept."""
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise GuideError("The spec sheet wasn't readable — try again.")
    grade = _text(raw.get("grade"), 80)

    benefits_raw = raw.get("benefits") or []
    if isinstance(benefits_raw, str):
        benefits_raw = benefits_raw.splitlines()
    benefits: List[str] = []
    for b in benefits_raw:
        t = _text(b, 140).lstrip("•-* ").strip()
        if t and t.lower() not in {x.lower() for x in benefits}:
            benefits.append(t)
    if len(benefits) > MAX_BENEFITS:
        raise GuideError(f"Keep it to {MAX_BENEFITS} benefits — the ones a client should read first.")

    rows_raw = raw.get("rows") or []
    if isinstance(rows_raw, str):
        rows_raw = [ln for ln in rows_raw.splitlines() if ln.strip()]
    rows: List[List[str]] = []
    for r in rows_raw:
        if isinstance(r, str):
            if ":" not in r:
                raise GuideError(f'"{_text(r, 40)}" needs a label and a value, like "Width: 4 m".')
            label, value = r.split(":", 1)
        elif isinstance(r, dict):
            label, value = r.get("label"), r.get("value")
        elif isinstance(r, (list, tuple)) and len(r) == 2:
            label, value = r
        else:
            continue
        label, value = _text(label, 48), _text(value, 300)
        if not label and not value:
            continue
        if not label or not value:
            raise GuideError(f'"{label or value}" needs a label and a value, like "Width: 4 m".')
        if any(x[0].lower() == label.lower() for x in rows):
            raise GuideError(f'"{label}" is there twice. Put both values on one line.')
        rows.append([label, value])
    if len(rows) > MAX_ROWS:
        raise GuideError(f"Keep it to {MAX_ROWS} lines of specs.")

    link = _https(raw.get("link"))
    out = {k: v for k, v in (("grade", grade), ("benefits", benefits), ("rows", rows), ("link", link)) if v}
    return out or None


def colours(product: dict) -> List[str]:
    """The product's colours, from its own options (Products › Sizes and colours), else the
    'Colours' line of its spec sheet."""
    for opt in product.get("options") or []:
        if isinstance(opt, dict) and _text(opt.get("name"), 40).lower() in _COLOUR_NAMES:
            vals = opt.get("values") or []
            if isinstance(vals, str):
                vals = vals.split(",")
            return [t for t in (_text(v, 60) for v in vals) if t][:60]
    for label, value in ((product.get("specs") or {}).get("rows") or []):
        if str(label).strip().lower() in _COLOUR_NAMES:
            return [t for t in (_text(v, 60) for v in re.split(r"[,·;]", str(value))) if t][:60]
    return []


# ── guides ────────────────────────────────────────────────────────────────────────────────────

def _clean_guide(data: dict, tenant_id: str, partial: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not partial or "title" in data:
        title = _text(data.get("title"), 120)
        if not title:
            raise GuideError("Give the guide a title, like \"Flooring options for Riverside Offices\".")
        out["title"] = title
    if not partial or "client_name" in data:
        out["client_name"] = _text(data.get("client_name"), 120) or None
    if not partial or "intro" in data:
        intro = re.sub(r"[ \t]+", " ", str(data.get("intro") or "")).strip()[:600]
        if re.search(r"https?://|www\.", intro, re.I):
            raise GuideError("Leave web addresses out of the intro — the guide links to your products itself.")
        out["intro"] = intro or None
    if not partial or "categories" in data:
        cats = [c for c in dict.fromkeys(_text(c, 80) for c in (data.get("categories") or [])) if c]
        if len(cats) > MAX_CATEGORIES:
            raise GuideError("That's too many categories for one guide.")
        out["categories"] = cats
    if not partial or "product_ids" in data:
        ids = [i for i in dict.fromkeys(_text(i, 64) for i in (data.get("product_ids") or [])) if i]
        if len(ids) > MAX_PRODUCTS:
            raise GuideError(f"A guide can hold up to {MAX_PRODUCTS} products.")
        if ids:
            own = {str(p["id"]) for p in _products(tenant_id) if str(p.get("id")) in set(ids)}
            missing = [i for i in ids if i not in own]
            if missing:
                raise GuideError("Some of those products aren't yours or have been removed — pick them again.")
        out["product_ids"] = ids
    if not partial or "show_prices" in data:
        out["show_prices"] = bool(data.get("show_prices"))
    if partial and "active" in data:
        out["active"] = bool(data.get("active"))
    return out


def _audit(tenant_id: str, actor: Optional[dict], action: str, **detail: Any) -> None:
    try:
        from vula.api.merchant_audit import audit
        audit(tenant_id, actor, action, **detail)
    except Exception:  # noqa: BLE001
        pass


def link(tenant_id: str, token: str) -> str:
    """The guide's address: on the business's own shop address when it has one live, so the
    client sees the business's name, else Vula's."""
    from vula.commerce import web_basket
    base = web_basket.public_base(tenant_id)
    return f"{base}/guide/{token}" if base else f"/guide/{token}"


def _view(row: dict) -> dict:
    out = {k: row.get(k) for k in ("id", "title", "client_name", "intro", "categories", "product_ids",
                                   "show_prices", "active", "views", "last_viewed_at", "created_by",
                                   "created_at", "updated_at")}
    out["categories"] = list(out.get("categories") or [])
    out["product_ids"] = list(out.get("product_ids") or [])
    out["url"] = link(row["tenant_id"], row["token"])
    return out


def list_guides(tenant_id: str) -> List[dict]:
    rows = (_db().table(TABLE).select("*").eq("tenant_id", tenant_id)
            .order("created_at", desc=True).limit(500).execute().data or [])
    return [_view(r) for r in rows]


def _row(tenant_id: str, guide_id: str) -> dict:
    rows = (_db().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", guide_id)
            .limit(1).execute().data or [])
    if not rows:
        raise GuideError("That guide isn't there any more.")
    return rows[0]


def create(tenant_id: str, data: dict, actor: Optional[dict] = None) -> dict:
    row = {"tenant_id": tenant_id, "token": secrets.token_urlsafe(18), "active": True, "views": 0,
           "created_by": (actor or {}).get("email"), "created_at": _now(), "updated_at": _now(),
           **_clean_guide(data or {}, tenant_id)}
    _db().table(TABLE).insert(row).execute()
    saved = (_db().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("token", row["token"])
             .limit(1).execute().data or [])
    if not saved:
        raise GuideError("The guide wasn't saved — try again.")
    _audit(tenant_id, actor, "product_guide_created", guide_id=saved[0]["id"], title=row["title"])
    return _view(saved[0])


def update(tenant_id: str, guide_id: str, data: dict, actor: Optional[dict] = None) -> dict:
    _row(tenant_id, guide_id)
    patch = _clean_guide(data or {}, tenant_id, partial=True)
    if not patch:
        raise GuideError("Nothing to change.")
    patch["updated_at"] = _now()
    _db().table(TABLE).update(patch).eq("tenant_id", tenant_id).eq("id", guide_id).execute()
    saved = _row(tenant_id, guide_id)
    for k, v in patch.items():
        if k != "updated_at" and saved.get(k) != v and not (isinstance(v, list) and list(saved.get(k) or []) == v):
            raise GuideError("The change didn't save — try again.")
    _audit(tenant_id, actor, "product_guide_changed", guide_id=guide_id,
           fields=sorted(k for k in patch if k != "updated_at"))
    return _view(saved)


def renew(tenant_id: str, guide_id: str, actor: Optional[dict] = None) -> dict:
    """A new secret link: the old one stops working (sent to the wrong person, say)."""
    _row(tenant_id, guide_id)
    token = secrets.token_urlsafe(18)
    _db().table(TABLE).update({"token": token, "updated_at": _now()}) \
        .eq("tenant_id", tenant_id).eq("id", guide_id).execute()
    saved = _row(tenant_id, guide_id)
    if saved.get("token") != token:
        raise GuideError("The new link didn't save — try again.")
    _audit(tenant_id, actor, "product_guide_new_link", guide_id=guide_id)
    return _view(saved)


def delete(tenant_id: str, guide_id: str, actor: Optional[dict] = None) -> None:
    _row(tenant_id, guide_id)
    _db().table(TABLE).delete().eq("tenant_id", tenant_id).eq("id", guide_id).execute()
    _audit(tenant_id, actor, "product_guide_deleted", guide_id=guide_id)


def by_token(token: str) -> Optional[dict]:
    """The live guide behind a link, or None (unknown, turned off, or a paused business)."""
    t = (token or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", t):
        return None
    try:
        rows = _db().table(TABLE).select("*").eq("token", t).limit(1).execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.warning("product guide: lookup failed: %s", exc)
        return None
    if not rows or not rows[0].get("active") or rows[0].get("token") != t:
        return None
    try:
        from vula.api import tenants
        if not tenants.is_active(rows[0]["tenant_id"]):
            return None
    except Exception:  # noqa: BLE001
        pass
    return rows[0]


def record_view(row: dict) -> None:
    try:
        _db().table(TABLE).update({"views": int(row.get("views") or 0) + 1, "last_viewed_at": _now()}) \
            .eq("tenant_id", row["tenant_id"]).eq("id", row["id"]).execute()
    except Exception:  # noqa: BLE001
        pass


# ── what the page shows ───────────────────────────────────────────────────────────────────────

def _products(tenant_id: str) -> List[dict]:
    from vula.commerce import service
    from vula.db_pages import all_rows
    rows = all_rows(lambda: _db().table("commerce_products").select("*").eq("tenant_id", tenant_id),
                    cap=5000)
    return [r for r in rows if not r.get("archived")
            and r.get("product_type") != service.TICKET_PRODUCT_TYPE
            and (r.get("status") or "active") in ("active", "unlisted")]


def _category_labels(tenant_id: str) -> Dict[str, Any]:
    try:
        rows = (_db().table("commerce_categories").select("key,label,sort_order")
                .eq("tenant_id", tenant_id).order("sort_order").execute().data or [])
    except Exception:  # noqa: BLE001
        rows = []
    return {r["key"]: (r.get("label") or r["key"], i) for i, r in enumerate(rows)}


def category_label(key: str, labels: Dict[str, Any]) -> str:
    return (labels.get(key) or (None,))[0] or (key or "Other").replace("_", " ").strip().title()


def chosen(tenant_id: str, guide: dict) -> List[dict]:
    """The guide's products: chosen ones (listed or unlisted) plus whole categories (listed
    products only); both empty = every listed product."""
    rows = _products(tenant_id)
    ids = [str(i) for i in guide.get("product_ids") or []]
    cats = set(guide.get("categories") or [])
    if not ids and not cats:
        return [r for r in rows if (r.get("status") or "active") == "active"]
    pick = {str(r["id"]): r for r in rows if str(r["id"]) in set(ids)}
    for r in rows:
        if r.get("category") in cats and (r.get("status") or "active") == "active":
            pick.setdefault(str(r["id"]), r)
    return list(pick.values())


def _unit(p: dict) -> str:
    sold = str(p.get("sold_by") or "").strip().lower()
    return {"kg": "kg", "m2": "m²", "m²": "m²", "sqm": "m²", "m": "m", "metre": "m", "box": "box",
            "roll": "roll", "pack": "pack"}.get(sold, "")


def item(p: dict, show_prices: bool, labels: Dict[str, Any]) -> dict:
    from vula.commerce import service
    from vula.commerce.web_basket import sized
    specs = p.get("specs") if isinstance(p.get("specs"), dict) else {}
    imgs = [sized(u, 900) for u in [p.get("image_url")] + list(p.get("images") or [])
            if isinstance(u, str) and u.startswith("https://")]
    rows = [[str(a), str(b)] for a, b in (specs.get("rows") or [])
            if str(a).strip().lower() not in _COLOUR_NAMES]
    if p.get("pack_size") and not any(r[0].lower() in ("pack", "pack size", "packaging", "box") for r in rows):
        rows.append(["Pack size", _text(p["pack_size"], 120)])
    desc = re.sub(r"\s+", " ", str(p.get("description") or "")).strip()
    out = {
        "id": str(p["id"]), "name": _text(p.get("name"), 160) or "Product",
        "cat": p.get("category") or "other", "cat_label": category_label(p.get("category") or "", labels),
        "desc": desc[:900], "imgs": list(dict.fromkeys(imgs))[:6],
        "grade": specs.get("grade") or "", "benefits": list(specs.get("benefits") or []),
        "rows": rows, "link": specs.get("link") or "", "colours": colours(p),
        "in_stock": bool(p.get("in_stock")),
    }
    if show_prices:
        cents = service.effective_price_cents(p)
        if cents > 0:
            out["cents"] = cents
            out["unit"] = _unit(p)
            if p.get("sale_price_cents") and cents == int(p["sale_price_cents"]) and int(p.get("price_cents") or 0) > cents:
                out["was_cents"] = int(p["price_cents"])
    return out


def page_data(guide: dict) -> dict:
    """Everything the guide page shows, from the database."""
    tenant_id = guide["tenant_id"]
    labels = _category_labels(tenant_id)
    products = chosen(tenant_id, guide)
    order = {k: v[1] for k, v in labels.items()}
    products.sort(key=lambda p: (order.get(p.get("category"), 999), category_label(p.get("category") or "", labels),
                                 int(p.get("sort_order") or 0), str(p.get("name") or "").lower()))
    items = [item(p, bool(guide.get("show_prices")), labels) for p in products]
    cats: Dict[str, str] = {}
    for i in items:
        cats.setdefault(i["cat"], i["cat_label"])
    return {"title": guide.get("title") or "Product guide", "client": guide.get("client_name") or "",
            "intro": guide.get("intro") or "", "items": items, "cats": cats,
            "prices": bool(guide.get("show_prices"))}


def stock_message(shop: str, product: str, colour: str = "", guide_title: str = "") -> str:
    """What "Check stock on WhatsApp" types in (the page builds the same words; this is the
    reference the tests hold it to)."""
    lines = [f"Hi {shop}, please can you check stock for {product}."]
    lines.append(f"Colour: {colour}" if colour else "Colour: ")
    lines.append("Quantity: ")
    if guide_title:
        lines.append(f"(From your guide: {guide_title})")
    return "\n".join(lines)

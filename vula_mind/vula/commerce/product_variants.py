"""Sizes and colours for a product, made in one go (24 Nov 2026 audit: the screen added one
variant at a time and a business with 4 colours × 5 sizes typed 20 rows; a colour couldn't have
its own photo; SKU, barcode and cost couldn't be set on a product at all).

- `clean_options` — the option names and their values, as the person typed them: trimmed,
  duplicates (any case) dropped, at most MAX_OPTIONS options, MAX_VALUES values each and
  MAX_COMBOS combinations. Refused with a plain message, never cut short silently.
- `generate` — every combination not already a variant is made; existing ones are kept as they
  are (price, stock, SKU, barcode, photo), whatever case they were typed in. A variant whose
  values aren't in the new options is left alone and listed, never deleted (it may be on orders).
  The product's option names become the new ones. Read back: every combination must be there.
- `set_option_photo` — one photo on every variant of one value ("all the red ones"), https only.
- `barcode_owner` — a barcode is on one product or one variant of the business, across both
  tables (each has its own unique index, but not across them, so a scan could find two).
"""
from __future__ import annotations

import itertools
import logging
import uuid
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

MAX_OPTIONS = 3
MAX_VALUES = 30
MAX_COMBOS = 100
MAX_TEXT = 60


class VariantError(ValueError):
    """A plain message for the person, refused before anything is written."""


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    from vula.commerce import service
    return service._now()


def _text(v: Any) -> str:
    return " ".join(str(v or "").split())[:MAX_TEXT]


def clean_options(options: Any) -> List[Dict[str, Any]]:
    """[{name, values: [...]}] as typed; values may also be one comma-separated string."""
    out: List[Dict[str, Any]] = []
    seen_names = set()
    for opt in options or []:
        if not isinstance(opt, dict):
            continue
        name = _text(opt.get("name"))
        raw = opt.get("values")
        if isinstance(raw, str):
            raw = raw.split(",")
        values, seen = [], set()
        for v in raw or []:
            t = _text(v)
            if t and t.lower() not in seen:
                seen.add(t.lower())
                values.append(t)
        if not name and not values:
            continue
        if not name:
            raise VariantError("Give each option a name, like Size or Colour.")
        if name.lower() in seen_names:
            raise VariantError(f"'{name}' is there twice. Give each option its own name.")
        if not values:
            raise VariantError(f"Add at least one {name.lower()}, like the ones you sell.")
        if len(values) > MAX_VALUES:
            raise VariantError(f"{name} has {len(values)} values. {MAX_VALUES} is the most one option can have.")
        seen_names.add(name.lower())
        out.append({"name": name, "values": values})
    if len(out) > MAX_OPTIONS:
        raise VariantError(f"{MAX_OPTIONS} options is the most a product can have (for example Size, Colour and Material).")
    combos = 1
    for o in out:
        combos *= len(o["values"])
    if out and combos > MAX_COMBOS:
        raise VariantError(f"That makes {combos} versions. {MAX_COMBOS} is the most one product can have; "
                           "split it into separate products.")
    return out


def combinations(options: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not options:
        return []
    names = [o["name"] for o in options]
    return [dict(zip(names, vals)) for vals in itertools.product(*[o["values"] for o in options])]


def _key(option_values: Any) -> tuple:
    ov = option_values if isinstance(option_values, dict) else {}
    return tuple(sorted((str(k).strip().lower(), str(v).strip().lower()) for k, v in ov.items()))


def _variants(tenant_id: str, product_id: str) -> List[dict]:
    return (_client().table("commerce_product_variants").select("*")
            .eq("tenant_id", tenant_id).eq("product_id", product_id)
            .order("sort_order").execute().data or [])


def _product(tenant_id: str, product_id: str) -> Optional[dict]:
    rows = (_client().table("commerce_products").select("id,name,options,images,image_url")
            .eq("tenant_id", tenant_id).eq("id", product_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def _cents_or_none(v: Any, what: str) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise VariantError(f"{what} must be an amount in cents.")
    if n < 0:
        raise VariantError(f"{what} can't be below zero.")
    return n


def generate(tenant_id: str, product_id: str, options: Any, *, price_cents: Any = None,
             stock_quantity: Any = None) -> Dict[str, Any]:
    opts = clean_options(options)
    if not opts:
        raise VariantError("Add an option, like Size, and the sizes you sell.")
    product = _product(tenant_id, product_id)
    if not product:
        raise LookupError("Product not found.")
    price = _cents_or_none(price_cents, "Price")
    if price == 0:
        price = None
    stock = None
    if stock_quantity not in (None, ""):
        from vula.commerce import service
        stock = service.qty(stock_quantity)
        if stock < 0:
            raise VariantError("Stock can't be below zero.")
    wanted = combinations(opts)
    existing = _variants(tenant_id, product_id)
    have = {_key(v.get("option_values")): v for v in existing}
    new = [c for c in wanted if _key(c) not in have]
    start = max([int(v.get("sort_order") or 0) for v in existing] or [0]) + 1
    rows = []
    for i, ov in enumerate(new):
        row = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "product_id": product_id,
               "option_values": ov, "sort_order": start + i, "in_stock": True,
               "created_at": _now(), "updated_at": _now()}
        if price is not None:
            row["price_cents"] = price
        if stock is not None:
            row["stock_quantity"] = stock
            row["in_stock"] = stock > 0
        rows.append(row)
    if rows:
        _client().table("commerce_product_variants").insert(rows).execute()
    (_client().table("commerce_products").update({"options": [o["name"] for o in opts], "updated_at": _now()})
     .eq("tenant_id", tenant_id).eq("id", product_id).execute())
    after = _variants(tenant_id, product_id)
    keys = {_key(v.get("option_values")) for v in after}
    missing = [c for c in wanted if _key(c) not in keys]
    if missing:
        raise RuntimeError(f"{len(missing)} of the versions didn't save. Try again.")
    wanted_keys = {_key(c) for c in wanted}
    left = [v for v in after if _key(v.get("option_values")) not in wanted_keys and not v.get("archived")]
    archived = [v for v in after if _key(v.get("option_values")) in wanted_keys and v.get("archived")]
    return {"created": len(rows), "kept": len(wanted) - len(rows), "variants": after,
            "options": opts,
            "not_in_options": [{"id": v["id"], "option_values": v.get("option_values")} for v in left],
            "hidden": [{"id": v["id"], "option_values": v.get("option_values")} for v in archived]}


def https_image(url: Any) -> Optional[str]:
    """An https address, or None to clear. Anything else is refused."""
    if url in (None, ""):
        return None
    u = str(url).strip()
    if not u.startswith("https://") or len(u) > 2000 or any(c in u for c in " \n\r\t\"'<>"):
        raise VariantError("A photo must be an https:// web address.")
    return u


def set_option_photo(tenant_id: str, product_id: str, option: Any, value: Any,
                     image_url: Any) -> Dict[str, Any]:
    url = https_image(image_url)
    opt, val = _text(option).lower(), _text(value).lower()
    if not opt or not val:
        raise VariantError("Say which option and which value the photo is for.")
    if not _product(tenant_id, product_id):
        raise LookupError("Product not found.")
    matched = []
    for v in _variants(tenant_id, product_id):
        ov = {str(k).strip().lower(): str(x).strip().lower() for k, x in (v.get("option_values") or {}).items()}
        if ov.get(opt) == val:
            matched.append(v["id"])
    if not matched:
        raise VariantError(f"No version of this product has {option} {value}.")
    (_client().table("commerce_product_variants").update({"image_url": url, "updated_at": _now()})
     .eq("tenant_id", tenant_id).in_("id", matched).execute())
    back = (_client().table("commerce_product_variants").select("id,image_url")
            .eq("tenant_id", tenant_id).in_("id", matched).execute().data or [])
    if len(back) != len(matched) or any((b.get("image_url") or None) != url for b in back):
        raise RuntimeError("The photo didn't save on every version. Try again.")
    return {"updated": len(matched), "image_url": url}


def barcode_owner(tenant_id: str, barcode: Any, *, product_id: Optional[str] = None,
                  variant_id: Optional[str] = None) -> Optional[str]:
    """The name of what already carries this barcode (not this product/variant), or None."""
    code = str(barcode or "").strip()
    if not code:
        return None
    try:
        prods = (_client().table("commerce_products").select("id,name")
                 .eq("tenant_id", tenant_id).eq("barcode", code).limit(2).execute().data or [])
        for p in prods:
            if variant_id or p.get("id") != product_id:
                return p.get("name") or "another product"
        vars_ = (_client().table("commerce_product_variants").select("id,product_id,option_values")
                 .eq("tenant_id", tenant_id).eq("barcode", code).limit(2).execute().data or [])
    except Exception as exc:
        log.debug("barcode check skipped: %s", exc)
        return None
    for v in vars_:
        if v.get("id") != variant_id:
            name = (_product(tenant_id, v.get("product_id")) or {}).get("name") or "another product"
            label = " / ".join(str(x) for x in (v.get("option_values") or {}).values())
            return f"{name} ({label})" if label else name
    return None

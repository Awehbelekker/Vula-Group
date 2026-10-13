"""
vula/commerce/product_families.py — one product range with its colours, out of a catalogue that
lists every colour as its own product.

2026-10-13 (SeedleMe, 325 handmade concrete basins and sinks): the shop lists "Round Concave
Concrete Basin – Black – 42 x 14cm", "… – Burnt Orange – …", "… – Charcoal – …" and so on as
separate products — 325 rows that are really ~70 ranges in up to 13 colours. A customer asking
"what colours does the Countertop Butler come in?", a WhatsApp menu (10 rows a list) and
WhatsApp's native catalogue (one product with a colour picker) all need the range, not the row.

Deterministic and name-based: a product's colour is the colour phrase in its name, and its range
is the rest of the name with filler words ("concrete", "bespoke", "sink"/"basin", "made to
order") ignored — so "Gaia's Ark Black 53 x 30 x 11.5cm" and "Gaia's Ark Ivory Concrete Sink
53 x 30 x 11.5cm" are one range, while a finish that changes the price ("Namibian Dream",
"Exposed Aggregate") stays its own range. Real variants (option_values "Colour") are already a
range and are left alone. Nothing is written; callers group on read.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

# Longest first, so "burnt orange" wins over "orange" and "nguni pattern grey" over "grey".
COLOURS: Tuple[str, ...] = tuple(sorted({
    "nguni pattern grey", "peach blossom", "bright blue", "ocean blue", "burnt orange",
    "choc plum", "dark grey", "light grey", "off white", "black", "blue", "charcoal", "green",
    "grey", "gray", "ivory", "sandstone", "terracotta", "white", "red", "yellow", "pink",
    "brown", "beige", "cream", "orange", "purple", "navy", "olive", "gold", "silver", "copper",
    "natural", "teal", "mint", "rust", "slate", "stone",
}, key=len, reverse=True))

_COLOUR_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in COLOURS) + r")\b", re.I)
# Words that don't tell two ranges apart (the shop uses "sink" and "basin" for the same thing).
_FILLER = frozenset("""
    concrete cement bespoke handmade hand made sink sinks basin basins bathroom kitchen the a
    to order smooth finish plain cm mm x high quality in rsa
""".split())
_MADE_TO_ORDER = re.compile(r"\(\s*made\s*to\s*order\s*\)", re.I)


def colour_of(name: str) -> Optional[str]:
    m = _COLOUR_RE.search(name or "")
    return m.group(1).title() if m else None


def _dims(text: str) -> str:
    """'30x30x13cm' / '30 x 30 x 13 cm' → '30 x 30 x 13'; '13,5' → '13.5'."""
    return re.sub(r"(\d)\s*[x×]\s*(?=\d)", r"\1 x ", (text or "").replace(",", "."))


def _title(name: str, colour: Optional[str]) -> str:
    t = _MADE_TO_ORDER.sub("", name or "")
    if colour:
        t = re.sub(r"\b" + re.escape(colour) + r"\b", "", t, count=1, flags=re.I)
    t = re.sub(r"\s*[–-]\s*[–-]\s*", " – ", t)             # "Basin –  – 42" → "Basin – 42"
    t = re.sub(r"(\d)\s*[x×]\s*(?=\d)", r"\1 x ", t)          # "30x30x13cm" → "30 x 30 x 13cm"
    return re.sub(r"\s{2,}", " ", t).strip(" –-.,")


def range_key(name: str) -> str:
    colour = colour_of(name)
    words = re.findall(r"[a-z]+|\d+(?:\.\d+)?", _dims(_title(name, colour)).lower())
    return " ".join(sorted(w for w in words if w not in _FILLER))


def range_id(key: str) -> str:
    """Short stable id for a range — fits a WhatsApp list-row id and a catalogue group id."""
    return hashlib.md5(key.encode()).hexdigest()[:10]


def group(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Products → ranges, in first-seen order. A range: {id, key, title, colours: [{colour,
    product}], min_cents, max_cents, image_url, category, product (when it has one member)}.
    A product with no colour in its name, or the only one of its kind, is a range of one."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for p in products:
        key = range_key(p.get("name") or "")
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(p)
    out = []
    for key in order:
        members = buckets[key]
        titles = Counter(_title(p.get("name") or "", colour_of(p.get("name") or "")) for p in members)
        # The commonest title; on a tie the shortest — "Gaia's Ark 53 x 30 x 11.5cm" over
        # "Gaia's Ark Concrete Sink 53 x 30 x 11.5cm".
        title = sorted(titles.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]
        colours, seen = [], set()
        for p in sorted(members, key=lambda p: (colour_of(p.get("name") or "") or "")):
            c = colour_of(p.get("name") or "")
            if c and c.lower() not in seen:
                seen.add(c.lower())
                colours.append({"colour": c, "product": p})
        prices = [int(p.get("price_cents") or 0) for p in members if p.get("price_cents")]
        out.append({
            "id": range_id(key), "key": key,
            "title": title if len(members) > 1 and colours else (members[0].get("name") or title),
            "colours": colours if len(members) > 1 else [],
            "members": members,
            "product": members[0] if len(members) == 1 else None,
            "min_cents": min(prices) if prices else 0, "max_cents": max(prices) if prices else 0,
            "image_url": next((p.get("image_url") for p in members if p.get("image_url")), None),
            "category": members[0].get("category"),
        })
    return out


def find_range(products: List[Dict[str, Any]], rid: str) -> Optional[Dict[str, Any]]:
    return next((r for r in group(products) if r["id"] == rid), None)


def rands(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


def price_label(r: Dict[str, Any]) -> str:
    if r["min_cents"] and r["min_cents"] != r["max_cents"]:
        return f"from {rands(r['min_cents'])}"
    return rands(r["min_cents"]) if r["min_cents"] else "price on request"

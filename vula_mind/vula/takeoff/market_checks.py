"""
vula/takeoff/market_checks.py — is a price read off a supplier's web page real?

14 Nov 2026 (trade costing, phase 1). The weekly scrape kept Cashbuild face bricks at R0.35,
R0.45, R0.50 and R0.55 each on different runs — the model reading the page was guessing, and
nothing checked it. A market price is now kept only when BOTH hold:

  grounded   its figures are printed on the page it came from (compared as amounts, the way
             extraction_quality checks a document's figures — "R 72 500" never grounds 7 250)
  plausible  it sits inside a broad band for what it is and how it's sold (a brick each, a
             50 kg bag of cement, a m³ of sand…). The bands only catch nonsense — a R0.50 brick,
             a R5 bag of cement — they are not price guidance. An item that matches no family
             has no band and is kept on grounding alone.

No model is involved: everything here is patterns and arithmetic.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── units ────────────────────────────────────────────────────────────────────

_UNIT_ALIASES = {
    "no": "each", "nr": "each", "ea": "each", "each": "each", "unit": "each", "item": "each",
    "piece": "each", "pc": "each", "pcs": "each", "brick": "each", "block": "each",
    "m2": "m2", "m²": "m2", "sqm": "m2", "sq m": "m2", "per m2": "m2",
    "m3": "m3", "m³": "m3", "cube": "m3", "cubic metre": "m3",
    "m": "m", "m run": "m", "lm": "m", "metre": "m", "meter": "m", "running metre": "m",
    "kg": "kg", "t": "t", "ton": "t", "tonne": "t",
    "bag": "bag", "pocket": "bag", "sack": "bag",
    "l": "l", "litre": "l", "liter": "l", "tin": "tin", "bucket": "tin",
    "roll": "roll", "sheet": "sheet", "box": "box", "pack": "box", "pallet": "pallet",
    "day": "day", "hour": "hour", "hr": "hour",
}


def norm_unit(unit: Any) -> str:
    u = re.sub(r"\s+", " ", str(unit or "").strip().lower()).strip(". ")
    u = u.removeprefix("per ").removeprefix("/")
    return _UNIT_ALIASES.get(u, u)


# ── families and bands (rand per unit, VAT in or out — retail web prices) ─────
# (family, label pattern, {unit: (low, high)}). First match wins, so specific before general.
# Broad on purpose: wide enough for any SA retailer and pack size, narrow enough to refuse a
# figure that can't be the price of the thing (a R0.50 brick).
_FAMILIES: List[Tuple[str, "re.Pattern", Dict[str, Tuple[float, float]]]] = [
    # Tools and equipment named after a material ("brick trowel", "tile cutter") have no band.
    ("tool", re.compile(r"trowel|hammer|spade|shovel|wheelbarrow|spirit\s*level|cutter|grinder|"
                        r"\bdrill\b|\bsaw\b|\bbrush\b|\broller\b|float\b|bolster|chisel|\bmixer\b", re.I), {}),
    ("sand", re.compile(r"\bsand\b", re.I),
     {"m3": (150, 2000), "t": (100, 1500), "bag": (10, 150), "each": (10, 150), "kg": (0.1, 5)}),
    ("brickforce", re.compile(r"brick\s*force|brickforce|wire\s*reinforc", re.I),
     {"roll": (20, 600), "m": (1, 30), "each": (20, 600)}),
    ("wall_tie", re.compile(r"wall\s*ties?", re.I), {"each": (0.5, 30), "box": (20, 1500)}),
    ("brick", re.compile(r"\bbricks?\b|\bpavers?\b|\bmaxi\b|\bimperial\b", re.I),
     {"each": (1, 40), "pallet": (300, 20000), "m2": (40, 2000)}),
    ("block", re.compile(r"\bblocks?\b|breeze\s*block", re.I),
     {"each": (3, 120), "pallet": (500, 25000), "m2": (60, 2500)}),
    ("tile_adhesive", re.compile(r"tile\s*(adhesive|glue|fix)|adhesive", re.I),
     {"bag": (40, 900), "each": (40, 900), "kg": (2, 60)}),
    ("grout", re.compile(r"\bgrout", re.I), {"bag": (20, 600), "each": (20, 600), "kg": (5, 150)}),
    ("plaster", re.compile(r"\bplaster\b|rhinolite|cretestone|skim", re.I),
     {"bag": (40, 600), "each": (40, 600), "m2": (20, 800)}),
    ("cement", re.compile(r"\bcement\b|\bopc\b|\bcem\s*(i|ii)\b", re.I),
     {"bag": (40, 300), "each": (40, 300), "kg": (1, 8), "t": (1200, 6000)}),
    ("stone", re.compile(r"\bstone\b|aggregate|\bgravel\b|crusher", re.I),
     {"m3": (200, 2500), "t": (150, 1800), "bag": (10, 150), "each": (10, 150)}),
    ("ready_mix", re.compile(r"ready\s*-?\s*mix|readymix|concrete\s*mix", re.I),
     {"m3": (900, 4500), "bag": (30, 300), "each": (30, 300)}),
    ("rebar", re.compile(r"\brebar\b|reinforcing\s*bar|\by\d{2}\b|\bmesh\b|\bref\s*\d{3}\b", re.I),
     {"m": (5, 400), "each": (20, 3000), "t": (10000, 40000), "sheet": (100, 5000), "kg": (8, 60)}),
    ("dpc", re.compile(r"\bdpc\b|damp\s*proof", re.I), {"roll": (50, 3000), "m": (1, 80), "m2": (2, 120)}),
    ("tile", re.compile(r"\btiles?\b|porcelain|ceramic", re.I),
     {"m2": (40, 4000), "box": (60, 6000), "each": (5, 2000)}),
    ("paint", re.compile(r"\bpaint\b|\bprimer\b|\bundercoat\b|\bplascon\b|\bdulux\b|\bpva\b|\benamel\b", re.I),
     {"l": (20, 600), "tin": (60, 6000), "each": (60, 6000)}),
    ("roof_sheet", re.compile(r"\bibr\b|corrugat|roof\s*sheet|\bsheeting\b", re.I),
     {"m": (40, 1200), "sheet": (80, 6000), "each": (80, 6000), "m2": (60, 1500)}),
    ("timber", re.compile(r"\btimber\b|\bpine\b|\bsa\s*pine\b|\bbrandering\b|\brafter\b|\bpurlin\b|\b\d{2}\s*x\s*\d{2,3}\b", re.I),
     {"m": (5, 600), "each": (10, 4000)}),
    ("gypsum_board", re.compile(r"gypsum|rhino\s*board|ceiling\s*board|drywall|plasterboard", re.I),
     {"sheet": (60, 1500), "each": (60, 1500), "m2": (30, 800)}),
    ("pipe", re.compile(r"\bpipe\b|\bpvc\b|\bupvc\b|\bhdpe\b|\bcopper\b", re.I),
     {"m": (5, 1500), "each": (10, 5000)}),
    ("cable", re.compile(r"\bcable\b|\bwire\b|\bsurfix\b|\bconduit\b", re.I),
     {"m": (2, 600), "roll": (50, 15000), "each": (5, 15000)}),
    ("labour", re.compile(r"\blabou?r\b|bricklayer|\bplasterer\b|\bgeneral\s*worker\b|artisan", re.I),
     {"day": (150, 4000), "hour": (20, 600), "m2": (20, 800)}),
]


def family_of(label: str) -> Optional[str]:
    for name, pattern, _bands in _FAMILIES:
        if pattern.search(label or ""):
            return name
    return None


def band_for(family: Optional[str], unit: str) -> Optional[Tuple[float, float]]:
    for name, _pattern, bands in _FAMILIES:
        if name == family:
            return bands.get(norm_unit(unit))
    return None


# ── grounding ────────────────────────────────────────────────────────────────

def _cents(value: float) -> int:
    return int(round(float(value) * 100))


def grounded(low: float, high: float, page_text: str) -> bool:
    """Both figures (one when they're equal) are printed on the page, compared as amounts."""
    from vula.commerce.extraction_quality import _amounts_in_text
    printed = _amounts_in_text(page_text or "")
    figures = {_cents(low), _cents(high)}
    return all(f in printed for f in figures if f > 0)


def check(label: str, unit: str, low: float, high: float, page_text: str) -> Tuple[bool, str, Optional[str]]:
    """(keep, reason, family). reason is "" when kept, else "not_on_page" / "out_of_band"."""
    family = family_of(label)
    if not grounded(low, high, page_text):
        return False, "not_on_page", family
    band = band_for(family, unit)
    if band is not None and not (band[0] <= low <= band[1] and band[0] <= high <= band[1]):
        return False, "out_of_band", family
    return True, "", family


def check_rates(rates: Iterable[Any], page_text: str) -> Tuple[List[Any], List[Tuple[Any, str]]]:
    """Split extracted rates (anything with label/unit/low/high) into kept and (dropped, reason).
    Kept rates get .family and .checked set."""
    kept: List[Any] = []
    dropped: List[Tuple[Any, str]] = []
    for r in rates:
        ok, reason, family = check(r.label, r.unit, r.low, r.high, page_text)
        if ok:
            r.family = family or ""
            r.checked = True
            kept.append(r)
        else:
            dropped.append((r, reason))
    return kept, dropped

"""
vula/commerce/costing_history.py — start costing from what the business already paid.

14 Oct 2026 (Ian: "so Vula will take past invoices on materials and labour, and ask the tenant
questions to process it accurately?"). The price book (vula/commerce/price_book.py) already holds
every priced line of every filed supplier invoice, and the bank lines hold what was paid in wages
and running costs. Costing used none of it: a new material started with no price and the labour
rate and overhead were typed in blind. This module turns that history into SUGGESTIONS — the
owner confirms each one, nothing is set on a guess:

    material prices   the item on past invoices that matches the material ("CEMENT MIX 25KG" for
                      "concrete mix"), its latest paid price, the pack size read from the wording,
                      and the cost per base unit — with VAT taken off or left on according to how
                      THAT invoice was priced and whether the business is VAT registered
    labour            what wages/casual labour came to per month, over the hours the team works
    overhead          rent, utilities, insurance… per month, spread by the rule the owner picks

An invoice line's wording rarely matches what the business calls a material, so a match is
always offered, never assumed; once the owner picks one it's remembered (the price movement
records the invoice item's key) so the next suggestion for that material uses it.

All arithmetic is Decimal; money is integer cents, rounded once.
"""
from __future__ import annotations

import difflib
import logging
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from vula.commerce import costing
from vula.commerce.costing import D, CostingError, rands

log = logging.getLogger(__name__)

VAT = D("1.15")

# "CEMENT MIX 25KG", "Sealer 5L", "Pigment 500 g", "6 x 500ml", "Rebar 6m"
_PACK = re.compile(r"(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(kg|kgs|g|gr|l|lt|ltr|litre|liter|ml|m)\b",
                   re.I)
_PACK_UNIT = {"kgs": "kg", "gr": "g", "lt": "l", "ltr": "l", "litre": "l", "liter": "l"}

# What a business means by running costs it spreads over what it makes (job_costing's overhead
# without the project accounts). Marketing, delivery and packaging are left out: they belong to
# selling, or to a product's own recipe (a box per basin), not to making.
OVERHEAD_ACCOUNTS = ("rent", "utilities", "insurance", "equipment", "bank_charges",
                     "professional_fees", "other_expense")
LABOUR_ACCOUNTS = ("wages", "casual_labour")
_ACCOUNT_LABEL = {"rent": "rent", "utilities": "electricity & water", "insurance": "insurance",
                  "equipment": "equipment", "bank_charges": "bank charges",
                  "professional_fees": "professional fees", "other_expense": "other running costs",
                  "wages": "wages", "casual_labour": "casual labour"}
_NOT_MATERIAL = ("labour", "delivery", "plant")
_STOP = {"the", "and", "of", "for", "with", "mix", "per", "x"}


def _db():
    return costing._db()


def pack_from_text(text: str) -> Optional[tuple]:
    """(size, unit) of one pack from an invoice line's wording — "CEMENT 50KG" → (50, 'kg'),
    "6 x 500ml" → (3000, 'ml'). None when the wording names no measurable size."""
    m = None
    for m in _PACK.finditer(text or ""):
        pass                                  # the last size named is the pack ("2x4 timber 3m" → 3 m)
    if not m:
        return None
    count = D(m.group(1)) if m.group(1) else D(1)
    size = D(m.group(2).replace(",", ".")) * count
    unit = _PACK_UNIT.get(m.group(3).lower(), m.group(3).lower())
    if size <= 0:
        return None
    return size, unit


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) >= 3 and w not in _STOP]


def _word_hit(want: str, key_words: List[str]) -> bool:
    for k in key_words:
        if k == want or (len(k) >= 3 and want.startswith(k)) or (len(want) >= 4 and k.startswith(want)):
            return True
        if difflib.SequenceMatcher(None, want, k).ratio() >= 0.8:
            return True
        common = len(_common_prefix(want, k))         # sealer ↔ sealant, pigment ↔ pigments
        if common >= 4 and common >= 0.6 * min(len(want), len(k)):
            return True
    return False


def _common_prefix(a: str, b: str) -> str:
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return a[:n]


def _match_score(name: str, key: str) -> float:
    """How well a material's name matches an invoice item: the share of the name's words found
    in the item (cement ↔ CEM, sealer ↔ SEALANT, concrete ↔ CONCRETE), 0 when any is missing."""
    want = _words(name)
    if not want:
        return 0.0
    kw = _words(key)
    hits = sum(1 for w in want if _word_hit(w, kw))
    if hits < len(want):
        return 0.0
    exact = sum(1 for w in want if w in kw)            # "sand" ranks SAND above SANDING DISC
    return 1.0 + exact / len(want)


def remembered_key(tenant_id: str, material_id: str) -> Optional[str]:
    """The invoice item the owner last matched this material to (recorded on its price movement)."""
    try:
        rows = (_db().table("commerce_material_movements").select("ref_id")
                .eq("tenant_id", tenant_id).eq("material_id", material_id).eq("ref_type", "price_book")
                .order("created_at", desc=True).limit(1).execute().data or [])
    except Exception:
        return None
    return rows[0].get("ref_id") if rows else None


def _vat_basis(fields: Dict[str, Any]) -> str:
    """How an invoice's line prices were written: 'incl' (lines add up to the total), 'excl'
    (lines + VAT make the total), 'no_vat' (no VAT on it), or 'unknown'."""
    from vula.commerce.extraction_quality import _line_total_cents
    try:
        total = abs(int(round(float(fields.get("total_cents") or 0))))
        vat = abs(int(round(float(fields.get("vat_cents") or 0))))
    except (TypeError, ValueError):
        return "unknown"
    items = [it for it in (fields.get("line_items") or []) if isinstance(it, dict)]
    lines = sum(_line_total_cents(it) for it in items)
    if not total or not lines:
        return "unknown"
    if not vat:
        return "no_vat"
    if abs(lines - total) <= 100:
        return "incl"
    if abs(lines + vat - total) <= 100 or abs(round(lines * 1.15) - total) <= 100:
        return "excl"
    return "unknown"


def _doc_fields(tenant_id: str, doc_id: Optional[str]) -> Dict[str, Any]:
    if not doc_id or str(doc_id).startswith("worker:"):
        return {}
    try:
        rows = (_db().table("vula_filed_documents").select("fields").eq("tenant_id", tenant_id)
                .eq("id", doc_id).limit(1).execute().data or [])
    except Exception:
        return {}
    return (rows[0].get("fields") or {}) if rows else {}


def cost_basis_price(price_cents: int, basis: str, vat_registered: bool) -> tuple:
    """(the price that counts as cost, note). A VAT-registered business claims supplier VAT back,
    so its cost is the price before VAT; for one that isn't, the VAT is part of the cost."""
    p = D(int(price_cents))
    if basis == "incl" and vat_registered:
        return int((p / VAT).quantize(D(1), rounding=ROUND_HALF_UP)), "VAT taken off (you claim it back)"
    if basis == "excl" and not vat_registered:
        return int((p * VAT).quantize(D(1), rounding=ROUND_HALF_UP)), "VAT added (you can't claim it back)"
    if basis == "unknown":
        return int(p), "as on the invoice — check whether it includes VAT"
    return int(p), ""


def _per_base(price_cents: int, pack: Optional[tuple], obs_unit: Optional[str],
              base_unit: Optional[str]) -> Optional[tuple]:
    """(cost per base unit in rands, base unit, pack size, pack unit) for one purchase at
    `price_cents`: per the pack the wording names, else per the invoice line's own unit."""
    price = D(int(price_cents)) / 100
    if pack:
        size, unit = pack
        try:
            base = costing.base_unit_for(unit)
        except CostingError:
            return None
        if base_unit and base != base_unit:
            pack = None
        else:
            qty = costing.to_base(size, unit, {"base_unit": base, "name": ""})
            return (price / qty).quantize(D("0.000001"), rounding=ROUND_HALF_UP), base, size, unit
    u = (obs_unit or "each").lower()
    if u in costing.UNITS:
        base, factor = costing.UNITS[u]
        if base_unit and base != base_unit:
            return None
        return (price / factor).quantize(D("0.000001"), rounding=ROUND_HALF_UP), base, None, None
    if not base_unit or base_unit == "each":
        return price.quantize(D("0.000001")), "each", None, None
    return None


def show_unit_cost(unit_cost: Any, base_unit: str) -> str:
    c = D(str(unit_cost))
    if base_unit == "g":
        return f"R{c * 1000:,.2f} per kg"
    if base_unit == "ml":
        return f"R{c * 1000:,.2f} per l"
    return f"R{c:,.2f} " + ("each" if base_unit == "each" else f"per {base_unit}")


def material_rows(tenant_id: str) -> List[Dict[str, Any]]:
    """Every priced material line in the business's price book (fetch once per setup run)."""
    from vula.commerce import price_book
    return [r for r in price_book._fetch(tenant_id) if (r.get("kind") or "material") not in _NOT_MATERIAL]


def price_suggestions(tenant_id: str, name: str, *, material: Optional[Dict[str, Any]] = None,
                      limit: int = 3, rows: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The invoice items that could be this material, best first, each with what it would cost
    per base unit. Empty when the business has no matching invoice line."""
    from vula.commerce import price_book
    base_unit = (material or {}).get("base_unit")
    remembered = remembered_key(tenant_id, material["id"]) if material and material.get("id") else None
    rows = material_rows(tenant_id) if rows is None else rows
    if not rows:
        return []
    scored: Dict[str, float] = {}
    for r in rows:
        key = r.get("norm_key") or ""
        if key in scored:
            continue
        s = 3.0 if remembered and key == remembered else _match_score(name, key)
        if s > 0:
            scored[key] = s
    if not scored:
        return []
    picked = [r for r in rows if r.get("norm_key") in scored]
    try:
        registered = costing._vat(tenant_id)[0]
    except Exception:
        registered = False
    out = []
    for roll in price_book.rollup(picked):
        key = price_book.norm_key(roll.get("description") or "")
        obs = sorted((r for r in picked if r.get("norm_key") == key and (r.get("unit") or "each") == roll["unit"]),
                     key=lambda r: (r.get("source_kind") in price_book._PAID, r.get("observed_on") or ""),
                     reverse=True)
        latest = obs[0] if obs else {}
        basis = _vat_basis(_doc_fields(tenant_id, latest.get("doc_id")))
        price_cents, vat_note = cost_basis_price(roll["latest_cents"], basis, registered)
        per = _per_base(price_cents, pack_from_text(roll.get("description") or ""), roll.get("unit"), base_unit)
        if not per:
            continue
        unit_cost, base, size, punit = per
        out.append({
            "key": key, "description": roll.get("description"), "supplier": roll.get("latest_supplier"),
            "latest_on": roll.get("latest_on"), "invoice_price_cents": roll["latest_cents"],
            "price_cents": price_cents, "vat_basis": basis, "vat_note": vat_note,
            "low_cents": roll["low_cents"], "high_cents": roll["high_cents"],
            "observations": roll["observations"], "basis": roll["basis"],
            "unit_cost": unit_cost, "base_unit": base, "pack_size": size, "pack_unit": punit,
            "score": scored.get(key, 0.0),
        })
    out.sort(key=lambda s: (-s["score"], s["basis"] != "paid", -s["observations"], s["description"] or ""))
    return out[:limit]


def suggestion_line(s: Dict[str, Any], n: Optional[int] = None) -> str:
    """'1. CEMENT MIX 25KG — R95.00 (Build It, 2026-09-03; 4 times R89.00–R98.00) → R3.80 per kg'"""
    seen = (f"; {s['observations']} times {rands(s['low_cents'])}–{rands(s['high_cents'])}"
            if s["observations"] > 1 and s["low_cents"] != s["high_cents"] else "")
    who = ", ".join(x for x in (s.get("supplier"), s.get("latest_on")) if x)
    head = f"{n}. " if n is not None else ""
    note = f" ({s['vat_note']})" if s.get("vat_note") else ""
    return (f"{head}*{s['description']}* — {rands(s['invoice_price_cents'])}"
            + (f" ({who}{seen})" if who or seen else "")
            + f" → {show_unit_cost(s['unit_cost'], s['base_unit'])}{note}")


def set_price(tenant_id: str, material: Dict[str, Any], unit_cost: Any, *, key: Optional[str] = None,
              note: Optional[str] = None, actor: Optional[str] = None) -> Dict[str, Any]:
    """Set a material's cost per base unit (the owner confirmed it), record where it came from,
    and read it back. With stock on hand this replaces the average — the owner said so."""
    cost = D(str(unit_cost)).quantize(D("0.000001"), rounding=ROUND_HALF_UP)
    if cost <= 0:
        raise CostingError("A price has to be more than R0.")
    db = _db()
    db.table("commerce_materials").update({"unit_cost": float(cost)}) \
        .eq("tenant_id", tenant_id).eq("id", material["id"]).execute()
    try:
        db.table("commerce_material_movements").insert({
            "tenant_id": tenant_id, "material_id": material["id"], "delta": 0,
            "qty_after": float(material.get("stock_qty") or 0), "unit_cost": float(cost),
            "unit_cost_after": float(cost), "reason": "adjust",
            "ref_type": "price_book" if key else "price", "ref_id": key, "actor": actor,
            "note": (note or "price set")[:300]}).execute()
    except Exception as exc:
        log.debug("price movement not recorded: %s", exc)
    back = costing.find_material(tenant_id, material["name"]) or {}
    if back.get("unit_cost") is None or abs(D(str(back["unit_cost"])) - cost) > D("0.000001"):
        raise CostingError(f"{material['name']}'s price couldn't be confirmed — nothing changed.")
    return back


# ── Labour and running costs from the bank ───────────────────────────────────────────────

def _bank_out(tenant_id: str, accounts: tuple, since: str) -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages
    try:
        rows = _all_pages(lambda: (_db().table("commerce_bank_transactions")
                                   .select("txn_date,amount_cents,direction,account_code,project,match_status")
                                   .eq("tenant_id", tenant_id).eq("direction", "out")
                                   .in_("account_code", list(accounts)).gte("txn_date", since)))
    except Exception as exc:
        log.debug("bank read for costing suggestions failed: %s", exc)
        return []
    return [r for r in rows if r.get("match_status") != "ignored" and not r.get("project")]


def _months(rows: List[Dict[str, Any]], since: date, today: date) -> Decimal:
    """Months the figures cover: from the first line (or the window start) to today, at least 1."""
    dates = sorted(str(r.get("txn_date"))[:10] for r in rows if r.get("txn_date"))
    start = max(since, date.fromisoformat(dates[0])) if dates else since
    days = max((today - start).days, 1)
    return max(D(days) / D("30.4375"), D(1)).quantize(D("0.01"))


def bank_suggestions(tenant_id: str, *, days: int = 90, today: Optional[date] = None) -> Dict[str, Any]:
    """What the bank says labour and running costs come to a month (lines not on a project).
    {labour_monthly_cents, overhead_monthly_cents, overhead_by_account: [(label, cents)], months}"""
    today = today or date.today()
    since = today - timedelta(days=days)
    labour = _bank_out(tenant_id, LABOUR_ACCOUNTS, since.isoformat())
    over = _bank_out(tenant_id, OVERHEAD_ACCOUNTS, since.isoformat())
    months = _months(labour + over, since, today)

    def monthly(cents: int) -> int:
        return int((D(cents) / months).quantize(D(1), rounding=ROUND_HALF_UP))
    by: Dict[str, int] = {}
    for r in over:
        by[r["account_code"]] = by.get(r["account_code"], 0) + abs(int(r.get("amount_cents") or 0))
    labour_total = sum(abs(int(r.get("amount_cents") or 0)) for r in labour)
    return {
        "months": months, "since": since.isoformat(),
        "labour_monthly_cents": monthly(labour_total) if labour else None,
        "overhead_monthly_cents": monthly(sum(by.values())) if over else None,
        "overhead_by_account": sorted(((_ACCOUNT_LABEL.get(k, k), monthly(v)) for k, v in by.items()),
                                      key=lambda kv: -kv[1]),
    }


def hours_per_month(hours_per_week: Any) -> Decimal:
    """52 weeks ÷ 12 months of the team's weekly hours."""
    return (costing.dec(hours_per_week) * 52 / 12).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def per(monthly_cents: Optional[int], count: Any) -> Optional[int]:
    """A monthly amount over a monthly count (hours, batches, units), in whole cents."""
    if not monthly_cents or count is None:
        return None
    c = costing.dec(count)
    if c <= 0:
        return None
    return int((D(int(monthly_cents)) / c).quantize(D(1), rounding=ROUND_HALF_UP))

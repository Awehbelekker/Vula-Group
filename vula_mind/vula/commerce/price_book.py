"""
vula/commerce/price_book.py — the tenant's price book, learned from its own documents.

2026-09-28 (Ian: "the QS rates are not adapting… so many costs, even the BOQ on HPC, and Vula
hasn't picked up any cost from invoices or labour"). digg-demo had ~1,000 priced line items on
filed invoices, quotes and BOQs and 2 rows in vula_qs_rates: a document's prices were filed and
never used again. Every filed document's priced lines are now recorded as observations
(migration 184, replaced whenever the document is filed again or re-read), casual workers' day
rates too, and rates() rolls them up per item into a learned rate.

A learned rate never changes the tenant's own vula_qs_rates — manual rates always come first
in qs.search_rates; learned ones follow, labelled with where they came from, and a manual rate
the documents have moved away from is flagged (drift) for the owner to update or not.

All money is integer cents. The rate shown is the median of what was actually PAID (invoices,
expenses, labour payments); only when nothing was paid is it the median of what was quoted or
priced in a BOQ — a quote is what someone asked for, not what the job cost.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date, datetime
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from vula.commerce import service

log = logging.getLogger(__name__)

_TABLE = "vula_price_observations"

SOURCE_KIND = {
    "Invoice": "invoice",
    "Quote / Estimate": "quote",
    "Bill of Quantities (BOQ)": "boq",
    "Menu / Price List": "quote",
}
_PAID = ("invoice", "expense", "labour_payment")

# Lines that are money on a document but not a price of anything.
_NOT_A_RATE = re.compile(
    r"^\s*(sub[\s-]*total|total|vat|tax|discount|rounding|balance|deposit|amount due|"
    r"contingenc|p\s*&\s*g|preliminar|carried forward|brought forward|b/f|c/f)"
    r"|\b(contingency|% of|percent of)\b", re.IGNORECASE)

_LABOUR = re.compile(
    r"\b(labou?r|install(ation|ing)?|fitting|workmanship|artisan|tiler|tiling labour|plumber|"
    r"electrician|painter|carpenter|builder|general worker|day ?works?|per day|hourly|"
    r"man[\s-]?hours?|call[\s-]?out|commissioning|supervision)\b", re.IGNORECASE)
_PLANT = re.compile(r"\b(hire|rental|rent|jacks?|scaffold(ing)?|skip|tlb|bobcat|mixer hire|"
                    r"compactor|generator hire)\b", re.IGNORECASE)
_DELIVERY = re.compile(r"\b(deliver(y|ies|ed)?|transport|freight|courier|cartage|collection fee)\b",
                       re.IGNORECASE)

# A unit written into the description when the extraction had no unit field (most older rows):
# "Screed 2mm (405 sqm x R168)", "Sand per bag", "Cable per m".
_UNIT_IN_TEXT = [
    (re.compile(r"\b(m2|m²|sqm|sq\.?\s*m|square met(re|er)s?)\b", re.I), "m2"),
    (re.compile(r"\b(m3|m³|cube|cubic met(re|er)s?)\b", re.I), "m3"),
    (re.compile(r"\bper\s+(day|shift)\b|\bday rate\b|\bdays?\b(?=.*labou?r)", re.I), "day"),
    (re.compile(r"\bper\s+(hour|hr)\b|\bhourly\b", re.I), "hour"),
    (re.compile(r"\bper\s+(bag|pocket)\b", re.I), "bag"),
    (re.compile(r"\b(per\s+)?(lm|linear m(et(re|er))?s?|running m(et(re|er))?s?)\b", re.I), "m"),
    (re.compile(r"\bper\s+(kg|kilo)\b", re.I), "kg"),
]
_UNIT_ALIASES = {"sqm": "m2", "m²": "m2", "m^2": "m2", "square metre": "m2", "m³": "m3", "cube": "m3",
                 "lm": "m", "ea": "each", "no": "each", "nr": "each", "no.": "each", "item": "each",
                 "pc": "each", "pcs": "each", "unit": "each", "units": "each", "days": "day",
                 "hr": "hour", "hrs": "hour", "hours": "hour", "l": "litre", "lt": "litre"}


def _client():
    return service._client()


def norm_key(description: str) -> str:
    """The item's identity across documents: the same normalisation the materials roll-up
    uses (service._norm_name), so "SOLID 12mm BOARD 3.6 X 1.2 TE" matches itself across
    invoices however the punctuation came out."""
    return service._norm_name(description)[:200]


def kind_of(description: str) -> str:
    text = description or ""
    if _DELIVERY.search(text):
        return "delivery"
    if _LABOUR.search(text):
        return "labour"
    if _PLANT.search(text):
        return "plant"
    return "material"


def unit_of(description: str, unit: Optional[str]) -> Optional[str]:
    u = (unit or "").strip().lower().rstrip(".")
    if u:
        return _UNIT_ALIASES.get(u, u)[:20]
    for pat, name in _UNIT_IN_TEXT:
        if pat.search(description or ""):
            return name
    return None


def _num(v: Any) -> Optional[float]:
    return service._num(v)


def _date(v: Any) -> Optional[str]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        return None


def observations_for(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One observation per priced line of a filed document row (vula_filed_documents shape).
    A line counts when it has a positive unit price, given or derived from total ÷ quantity,
    and names something (not a subtotal, VAT, contingency…). Refunds/credit notes add nothing."""
    source_kind = SOURCE_KIND.get(row.get("category") or "")
    fields = row.get("fields") or {}
    if not source_kind or service._is_refund_row(row):
        return []
    supplier = service._party_of(fields) or None
    observed = _date(fields.get("date")) or _date(row.get("created_at"))
    out = []
    for li in fields.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        desc = re.sub(r"\s+", " ", str(li.get("description") or "")).strip()
        key = norm_key(desc)
        if len(key) < 3 or not re.search(r"[a-z]", key) or _NOT_A_RATE.search(desc):
            continue
        qty = _num(li.get("quantity"))
        unit_c = _num(li.get("unit_price_cents"))
        if unit_c is None or unit_c <= 0:
            tot = _num(li.get("total_cents"))
            if tot and tot > 0:
                unit_c = tot / qty if qty and qty > 0 else tot
                qty = qty if qty and qty > 0 else 1
        if not unit_c or unit_c <= 0:
            continue
        out.append({
            "tenant_id": row.get("tenant_id"), "doc_id": str(row.get("id")) if row.get("id") else None,
            "source_kind": source_kind, "supplier": supplier, "project": row.get("project") or None,
            "description": desc[:300], "norm_key": key,
            "unit": unit_of(desc, li.get("unit")), "kind": (li.get("kind") or kind_of(desc))[:20],
            "section": (str(li.get("section")).strip()[:120] or None) if li.get("section") else None,
            "quantity": qty, "unit_price_cents": int(round(unit_c)), "observed_on": observed,
        })
    return out


def record_from_document(tenant_id: str, row: Dict[str, Any]) -> int:
    """Replace this document's observations with what it says now. Returns how many lines were
    recorded. Best-effort: never raises (filing must never fail because of the price book)."""
    doc_id = row.get("id")
    if not tenant_id or not doc_id:
        return 0
    obs = observations_for({**row, "tenant_id": tenant_id})
    try:
        db = _client()
        db.table(_TABLE).delete().eq("tenant_id", tenant_id).eq("doc_id", str(doc_id)).execute()
        if obs:
            db.table(_TABLE).insert(obs).execute()
        return len(obs)
    except Exception as exc:
        log.debug("price book record skipped for %s (run migration 184?): %s", doc_id, exc)
        return 0


def set_project(tenant_id: str, doc_id: str, project: Optional[str]) -> None:
    """A document was (re)assigned to a project — its prices follow it."""
    try:
        (_client().table(_TABLE).update({"project": project})
         .eq("tenant_id", tenant_id).eq("doc_id", str(doc_id)).execute())
    except Exception as exc:
        log.debug("price book project update skipped: %s", exc)


def record_worker_rates(tenant_id: str) -> int:
    """Casual workers' day/week rates (commerce_workers, migration 059) as labour observations,
    so "what do we pay a general worker per day" is answered from what the business pays."""
    try:
        workers = (_client().table("commerce_workers").select("id,name,type,rate_cents,rate_period,active")
                   .eq("tenant_id", tenant_id).execute().data or [])
    except Exception as exc:
        log.debug("worker rates skipped: %s", exc)
        return 0
    n = 0
    for w in workers:
        cents = int(w.get("rate_cents") or 0)
        if cents <= 0 or w.get("active") is False:
            try:        # a worker let go / rate removed stops counting
                (_client().table(_TABLE).delete().eq("tenant_id", tenant_id)
                 .eq("doc_id", f"worker:{w['id']}").execute())
            except Exception:
                pass
            continue
        period = (w.get("rate_period") or "daily").lower()
        unit = "week" if period.startswith("week") else "day"
        label = f"{(w.get('type') or 'casual').strip().title()} worker labour per {unit}"
        doc_id = f"worker:{w['id']}"
        obs = {"tenant_id": tenant_id, "doc_id": doc_id, "source_kind": "labour_payment",
               "supplier": w.get("name"), "description": label, "norm_key": norm_key(label),
               "unit": unit, "kind": "labour", "quantity": 1, "unit_price_cents": cents,
               "observed_on": date.today().isoformat()}
        try:
            db = _client()
            db.table(_TABLE).delete().eq("tenant_id", tenant_id).eq("doc_id", doc_id).execute()
            db.table(_TABLE).insert(obs).execute()
            n += 1
        except Exception as exc:
            log.debug("worker rate record skipped: %s", exc)
    return n


def _matches(key: str, query: str) -> bool:
    words = [w for w in norm_key(query).split() if len(w) >= 2]
    return all(w in key for w in words)


def _fetch(tenant_id: str, query: str = "", kind: Optional[str] = None,
           project: Optional[str] = None) -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages
    words = [w for w in norm_key(query).split() if len(w) >= 3]

    def make():
        q = (_client().table(_TABLE)
             .select("description,norm_key,unit,kind,section,supplier,project,source_kind,"
                     "quantity,unit_price_cents,observed_on,doc_id")
             .eq("tenant_id", tenant_id))
        if words:
            q = q.ilike("norm_key", f"%{max(words, key=len)}%")
        if kind:
            q = q.eq("kind", kind)
        if project:
            q = q.eq("project", project)
        return q.order("observed_on", desc=True)
    try:
        rows = _all_pages(make)
    except Exception as exc:
        log.debug("price book read failed (run migration 184?): %s", exc)
        return []
    return [r for r in rows if not query or _matches(r.get("norm_key") or "", query)]


def _money(cents: Optional[int]) -> Optional[str]:
    return None if cents is None else f"R{cents / 100:,.2f}"


def rollup(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Observations → one learned rate per item (and unit, so R/m² and R/each never mix)."""
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r.get("norm_key"), r.get("unit")), []).append(r)
    out = []
    for (_key, unit), obs in groups.items():
        obs.sort(key=lambda r: (r.get("observed_on") or ""), reverse=True)
        paid = [r for r in obs if r.get("source_kind") in _PAID]
        basis = paid or obs
        prices = [int(r["unit_price_cents"]) for r in basis]
        latest = basis[0]
        counts = Counter(r.get("source_kind") for r in obs)
        suppliers = {(r.get("supplier") or "").strip().lower() for r in obs if r.get("supplier")}
        rate = int(round(median(prices)))
        parts = [f"{n} {k.replace('_', ' ')}{'s' if n > 1 else ''}" for k, n in counts.most_common()]
        out.append({
            "description": latest.get("description"), "unit": unit or "each",
            "kind": Counter(r.get("kind") for r in obs).most_common(1)[0][0],
            "rate_cents": rate, "rate": round(rate / 100, 2),
            "basis": "paid" if paid else "quoted",
            "latest_cents": int(latest["unit_price_cents"]), "latest_on": latest.get("observed_on"),
            "latest_supplier": latest.get("supplier"),
            "low_cents": min(prices), "high_cents": max(prices),
            "observations": len(obs), "suppliers": len(suppliers),
            "projects": sorted({r["project"] for r in obs if r.get("project")}),
            "source": (f"Learned from {', '.join(parts)}"
                       + (f", last {latest.get('observed_on')}" if latest.get("observed_on") else "")),
            "learned": True,
        })
    out.sort(key=lambda r: (-r["observations"], r["description"] or ""))
    return out


def rates(tenant_id: str, query: str = "", *, kind: Optional[str] = None,
          project: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    return rollup(_fetch(tenant_id, query, kind, project))[:limit]


def drift(manual: Dict[str, Any], learned: Dict[str, Any], threshold: float = 0.10) -> Optional[Dict[str, Any]]:
    """A manual rate the documents have moved away from by more than `threshold` (same unit).
    Returns a note for the rate screen — never a write."""
    try:
        mine = float(manual.get("rate") or 0)
    except (TypeError, ValueError):
        return None
    if mine <= 0 or (manual.get("unit") or "each").lower() not in (learned.get("unit"), "item"):
        return None
    theirs = learned["rate_cents"] / 100
    change = (theirs - mine) / mine
    if abs(change) <= threshold:
        return None
    return {"learned_rate": round(theirs, 2), "change_pct": round(change * 100, 1),
            "source": learned.get("source")}


def summary(tenant_id: str) -> Dict[str, Any]:
    """What the price book holds — for the "what Vula learned" report."""
    rows = _fetch(tenant_id)
    items = rollup(rows)
    return {
        "priced_lines": len(rows),
        "items": len(items),
        "labour_rates": sum(1 for i in items if i["kind"] == "labour"),
        "suppliers": len({(r.get("supplier") or "").lower() for r in rows if r.get("supplier")}),
        "projects": len({r["project"] for r in rows if r.get("project")}),
        "as_of": datetime.utcnow().isoformat(timespec="seconds"),
    }

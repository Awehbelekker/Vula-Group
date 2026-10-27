"""
vula/commerce/boq_online.py — a project's BOQ priced in the dashboard, no spreadsheet needed.

2026-10-27 (Ian: "can the tenant just do the BOQ online if needed and not download"). The same
bill the Excel template holds (vula/commerce/boq_template.py), kept per project in
vula_boq_drafts (migration 227):

- lines: section (an ASAQS trade), the rate-library item it came from (if any), description,
  unit, quantity, rate, area / room. The library is the template's: the tenant's own rate list,
  then its price book's learned rates.
- Every amount is worked out here — quantity × rate, rounded half-up to the cent — never taken
  from the browser; the bill total and the section totals likewise.
- "Start from the QS's bill": the lines of the latest BOQ filed under the project (read from its
  cells by vula/ingestion/boq_sheet.py) become the working bill, each section filed under its
  ASAQS trade where it names one.
- "Use as the project budget": the total and section totals become the project's budget
  (vula_project_boq) — unless the project's signed baseline is locked, which only a variation
  changes (project_programme.set_baseline).
- Print: the priced bill as a page in the tenant's brand; Excel: the template, pre-filled.

All money is integer cents; every query is scoped by tenant_id.
"""
from __future__ import annotations

import html
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_TABLE = "vula_boq_drafts"
_MAX_LINES = 1000
_BOQ_CATEGORY = "Bill of Quantities (BOQ)"


def _client():
    from vula.commerce import service
    return service._client()


def _key(name: Optional[str]) -> str:
    from vula.commerce.service import project_key
    return project_key(name)


def _qty(v: Any) -> Optional[Decimal]:
    try:
        q = Decimal(str(v).replace(",", ".").replace(" ", "")) if v not in (None, "") else None
    except InvalidOperation:
        return None
    return q if q is not None and q >= 0 else None


def _cents(v: Any) -> Optional[int]:
    try:
        c = int(v)
    except (TypeError, ValueError):
        return None
    return c if c >= 0 else None


def amount_cents(qty: Decimal, rate_cents: int) -> int:
    return int((qty * rate_cents).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def clean_lines(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The lines as stored: a line needs a description; quantity and rate are numbers of 0 or
    more (a blank one is kept as blank, priced later); the amount is worked out, never taken."""
    out = []
    for r in (raw or [])[:_MAX_LINES]:
        desc = " ".join(str(r.get("description") or "").split())[:300]
        if not desc:
            continue
        qty, rate = _qty(r.get("qty")), _cents(r.get("rate_cents"))
        out.append({
            "section": " ".join(str(r.get("section") or "").split())[:90],
            "key": (str(r.get("key") or "").strip() or None),
            "description": desc,
            "unit": " ".join(str(r.get("unit") or "").split())[:20] or None,
            "qty": str(qty) if qty is not None else None,
            "rate_cents": rate,
            "amount_cents": amount_cents(qty, rate) if qty is not None and rate is not None else None,
            "area": " ".join(str(r.get("area") or "").split())[:80] or None,
            "kind": (str(r.get("kind") or "").strip()[:30] or None),
        })
    return out


def section_totals(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{"section", "budget_cents"}] — ASAQS trade order first, then any other section as it comes."""
    from vula.commerce.boq_template import TRADES
    sums: Dict[str, int] = {}
    for li in lines:
        s = li.get("section") or "General"
        sums[s] = sums.get(s, 0) + int(li.get("amount_cents") or 0)
    order = [t for t in TRADES if t in sums] + [s for s in sums if s not in TRADES]
    return [{"section": s, "budget_cents": sums[s]} for s in order]


def _draft(tenant_id: str, project: str) -> Optional[Dict[str, Any]]:
    try:
        rows = (_client().table(_TABLE).select("project,lines,total_cents,updated_by,updated_at")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception as exc:
        log.debug("BOQ draft read failed (run migration 227?): %s", exc)
        return None
    return next((r for r in rows if _key(r.get("project")) == _key(project)), None)


def _budget(tenant_id: str, project: str) -> Dict[str, Any]:
    try:
        rows = (_client().table("vula_project_boq").select("project,title,total_cents,baseline_locked,source_job")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception:
        rows = []
    return next((r for r in rows if _key(r.get("project")) == _key(project)), {}) or {}


def filed_bill(tenant_id: str, project: str) -> Optional[Dict[str, Any]]:
    """The latest BOQ filed under the project that carries lines (a spreadsheet read first)."""
    from vula.commerce.ledger import _all_pages
    try:
        rows = _all_pages(lambda: _client().table("vula_filed_documents")
                          .select("id,project,filename,fields,created_at")
                          .eq("tenant_id", tenant_id).eq("category", _BOQ_CATEGORY))
    except Exception as exc:
        log.debug("filed BOQ read failed: %s", exc)
        return None
    mine = [r for r in rows if _key(r.get("project")) == _key(project) and (r.get("fields") or {}).get("line_items")]
    if not mine:
        return None
    mine.sort(key=lambda r: ((r.get("fields") or {}).get("line_items_source") == "sheet", str(r.get("created_at") or "")))
    return mine[-1]


def load(tenant_id: str, project: str) -> Dict[str, Any]:
    from vula.commerce.service import canonical_project
    name = canonical_project(tenant_id, project) or project
    d = _draft(tenant_id, name) or {}
    lines = d.get("lines") or []
    bill = filed_bill(tenant_id, name)
    budget = _budget(tenant_id, name)
    return {"project": d.get("project") or name, "lines": lines, "total_cents": int(d.get("total_cents") or 0),
            "sections": section_totals(lines), "updated_at": d.get("updated_at"), "updated_by": d.get("updated_by"),
            "budget_cents": int(budget.get("total_cents") or 0) if budget else 0,
            "budget_is_this_bill": budget.get("source_job") == "online_boq",
            "baseline_locked": bool(budget.get("baseline_locked")),
            "filed_bill": ({"filename": bill.get("filename"), "lines": len((bill.get("fields") or {}).get("line_items") or []),
                            "filed": str(bill.get("created_at") or "")[:10]} if bill else None)}


def save(tenant_id: str, project: str, raw_lines: List[Dict[str, Any]], by: Optional[str] = None) -> Dict[str, Any]:
    """Store the bill (lines cleaned, amounts worked out here) and read it back."""
    from vula.commerce import service
    name = service.canonical_project(tenant_id, project) or project
    lines = clean_lines(raw_lines)
    total = sum(int(li.get("amount_cents") or 0) for li in lines)
    existing = _draft(tenant_id, name)
    row = {"tenant_id": tenant_id, "project": (existing or {}).get("project") or name, "lines": lines,
           "total_cents": total, "updated_by": by, "updated_at": service._now()}
    _client().table(_TABLE).upsert(row, on_conflict="tenant_id,project").execute()
    back = _draft(tenant_id, name)
    if not back or int(back.get("total_cents") or 0) != total or len(back.get("lines") or []) != len(lines):
        raise RuntimeError("the BOQ was not saved")
    return load(tenant_id, name)


def from_filed_bill(tenant_id: str, project: str) -> List[Dict[str, Any]]:
    """The filed bill's lines as working lines — not saved until the owner saves them."""
    from vula.commerce.boq_template import trade_of, unit_label
    bill = filed_bill(tenant_id, project)
    if not bill:
        return []
    raw = []
    for li in (bill.get("fields") or {}).get("line_items") or []:
        if not isinstance(li, dict) or li.get("variation"):
            continue
        sec = (li.get("section") or "").strip()
        # a bill heading that names a trade ("MASONRY") becomes that trade; a room heading
        # ("Guest Bathroom") stays as the bill wrote it; no heading → the item's own trade
        section = (trade_of("", sec) if sec else None) or sec or trade_of(li.get("description") or "") or ""
        qty = li.get("quantity")
        rate = li.get("unit_price_cents")
        if rate is None and li.get("total_cents") is not None and qty in (None, 1):
            rate = li.get("total_cents")
        raw.append({"section": section,
                    "description": li.get("description"), "unit": unit_label(li.get("unit")) if li.get("unit") else None,
                    "qty": qty if qty is not None else 1, "rate_cents": rate, "area": li.get("area"),
                    "kind": "Bill rate"})
    return clean_lines(raw)


def use_as_budget(tenant_id: str, project: str) -> Dict[str, Any]:
    """The saved bill becomes the project's budget, read back. A locked signed baseline is never
    replaced — a change to it is a variation."""
    from vula.commerce import service
    d = load(tenant_id, project)
    if d["baseline_locked"]:
        return {"error": "This project's signed baseline is locked — changes to it go through a variation order."}
    if not d["lines"]:
        return {"error": "Save the bill first — it has no lines yet."}
    service.upsert_project_boq(tenant_id, d["project"], d["total_cents"], title="Online BOQ",
                               source_job="online_boq", sections=d["sections"])
    back = _budget(tenant_id, d["project"])
    if int(back.get("total_cents") or -1) != d["total_cents"]:
        return {"error": "The budget wasn't updated — please try again."}
    return {"project": d["project"], "budget_cents": d["total_cents"], "sections": d["sections"]}


def _r(cents: Optional[int]) -> str:
    return "" if cents is None else f"R{cents / 100:,.2f}".replace(",", " ")


def print_html(tenant_id: str, project: str, brand: Dict[str, Any]) -> str:
    """The priced bill as a printable page: lines by section with subtotals, the bill total."""
    from vula.commerce.boq_template import TRADES
    d = load(tenant_id, project)
    e = html.escape
    accent = e(brand.get("accent_color") or "#2C5545")
    name = e(brand.get("name") or "")
    logo = brand.get("logo_url")
    by_sec: Dict[str, List[Dict[str, Any]]] = {}
    for li in d["lines"]:
        by_sec.setdefault(li.get("section") or "General", []).append(li)
    order = [t for t in TRADES if t in by_sec] + [s for s in by_sec if s not in TRADES]
    body = []
    for s in order:
        body.append(f"<tr class='sec'><td colspan='6'>{e(s)}</td></tr>")
        for n, li in enumerate(by_sec[s], 1):
            desc = e(li["description"]) + (f" <span class='a'>— {e(li['area'])}</span>" if li.get("area") else "")
            body.append(f"<tr><td class='c'>{n}</td><td>{desc}</td><td class='c'>{e(li.get('unit') or '')}</td>"
                        f"<td class='n'>{e(li.get('qty') or '')}</td><td class='n'>{_r(li.get('rate_cents'))}</td>"
                        f"<td class='n'>{_r(li.get('amount_cents'))}</td></tr>")
        sub = sum(int(li.get("amount_cents") or 0) for li in by_sec[s])
        body.append(f"<tr class='sub'><td colspan='5'>{e(s)} total</td><td class='n'>{_r(sub)}</td></tr>")
    logo_html = f"<img src='{e(logo)}' alt='{name}' class='logo'>" if logo else f"<h2>{name}</h2>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Priced bill · {e(d['project'])}</title><style>
body{{font-family:Arial,sans-serif;color:#1e1e1e;margin:24px}}.logo{{max-height:60px}}
h1{{color:{accent};font-size:20px;margin:12px 0 2px}}p{{margin:2px 0;color:#555}}
table{{width:100%;border-collapse:collapse;margin-top:14px;font-size:12px}}
th{{background:{accent};color:#fff;text-align:left;padding:5px}}td{{padding:4px 5px;border-bottom:1px solid #ddd;vertical-align:top}}
td.n{{text-align:right;white-space:nowrap}}td.c{{text-align:center}}.a{{color:#777}}
tr.sec td{{font-weight:bold;color:{accent};padding-top:10px}}tr.sub td{{font-weight:bold;border-top:1px solid {accent}}}
tr.tot td{{font-weight:bold;font-size:14px;border-top:2px solid {accent};border-bottom:3px double {accent}}}
@media print{{body{{margin:10mm}}}}</style></head><body>{logo_html}
<h1>Priced bill of quantities — {e(d['project'])}</h1><p>{name}</p>
<table><tr><th>Item</th><th>Description</th><th>Unit</th><th>Qty</th><th>Rate</th><th>Amount</th></tr>
{''.join(body)}<tr class='tot'><td colspan='5'>Bill total</td><td class='n'>{_r(d['total_cents'])}</td></tr></table>
<script>window.onload=()=>window.print()</script></body></html>"""


def workbook(tenant_id: str, project: str) -> bytes:
    """The Excel template, pre-filled with this bill."""
    from vula.commerce import boq_template
    d = load(tenant_id, project)
    data, _ = boq_template.build(tenant_id, lines=d["lines"], project=d["project"])
    return data

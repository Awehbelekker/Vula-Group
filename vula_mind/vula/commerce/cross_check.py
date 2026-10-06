"""
vula/commerce/cross_check.py — documents ↔ books ↔ bank, and VAT in vs out.

2026-09-28 (Ian): "can the tenant see incoming VAT vs outgoing, to see what they could claim if
they were VAT registered? Is it checking the bank statement against invoices to be paid? Basic
cross-checking of allocations, operating costs, invoices and the bank statement."

Read-only production counts the same day showed why:
- filed invoices/quotes with an amount that never reached the books: digg-demo 64, off-the-hook 57
  (never matched, never in payables or VAT);
- bank lines matched to anything: digg-demo 3 of 372, off-the-hook 2 of 742 — supplier-bill
  matching needs the supplier's name on the bank line, and "HPC DOORS" names nobody;
- VAT: vat_return() answered only {"vat_registered": False} for an unregistered business, and
  for a registered one backed 15/115 out of every standard-rated bank line, card coffee included.

report() lists, each with the fix:
  unbooked        filed bills/quotes with an amount but no books record  → book_unbooked()
  bills_unpaid    supplier bills not marked paid, with the bank debit that probably paid each
                  (amount within 1%, dated from 3 days before to 60 after) → confirm the match
  no_document     bank payments with no invoice/receipt on file (no VAT claim without a tax
                  invoice), grouped by counterparty
  sales_unpaid    the tenant's own invoices not paid, with a likely credit
  unexplained_in  money in that matches no invoice, order or settlement
  to_categorise   lines Vula couldn't categorise
vat() gives input VAT from real tax invoices (supplier VAT number on file or not), the VAT the
business charges / would charge, the net per month, and — unregistered — 12-month sales against
the compulsory registration threshold.

Nothing here writes, except book_unbooked(), which runs the normal commit path on documents
that were already filed with a verified total. All money is integer cents.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_MONEY_CATS = ("Invoice", "Quote / Estimate", "Bill of Quantities (BOQ)")
_NO_DOC_EXPECTED = {"owner_drawings", "bank_charges", "casual_labour", "wages", "bank_cash"}
_MIN_NO_DOC_CENTS = 20000          # payments under R200 aren't worth chasing a document for


def _client():
    from vula.commerce import service
    return service._client()


def _pages(make) -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages
    try:
        return _all_pages(make)
    except Exception as exc:
        log.debug("cross-check read failed: %s", exc)
        return []


def _d(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _r(c: Optional[int]) -> str:
    return "—" if c is None else f"R{c / 100:,.2f}"


# ── data ──────────────────────────────────────────────────────────────────────

def _bank(tenant_id: str, since: Optional[str], until: Optional[str]) -> List[Dict[str, Any]]:
    def make():
        q = (_client().table("commerce_bank_transactions")
             .select("id,txn_date,description,payee,amount_cents,direction,account_code,"
                     "categorized_by,match_status,matched_invoice_id,matched_expense_id,"
                     "matched_order_id,project,vat_cents")
             .eq("tenant_id", tenant_id))
        if since:
            q = q.gte("txn_date", since)
        if until:
            q = q.lte("txn_date", until)
        return q.order("txn_date")
    return [r for r in _pages(make) if r.get("match_status") != "ignored"]


def _invoices(tenant_id: str, direction: str) -> List[Dict[str, Any]]:
    def make():
        return (_client().table("commerce_invoices")
                .select("id,invoice_number,direction,doc_type,status,supplier,supplier_id,"
                        "customer_name,project,issue_date,total_cents,vat_cents")
                .eq("tenant_id", tenant_id).eq("direction", direction).order("issue_date"))
    return _pages(make)


def _unbooked_docs(tenant_id: str) -> List[Dict[str, Any]]:
    def make():
        return (_client().table("vula_filed_documents")
                .select("id,category,filename,fields,project,created_at,source,commerce_invoice_id")
                .eq("tenant_id", tenant_id).in_("category", list(_MONEY_CATS))
                .is_("commerce_invoice_id", "null").order("created_at", desc=True))
    out = []
    for r in _pages(make):
        f = r.get("fields") or {}
        try:
            total = int(f.get("total_cents") or 0)
        except (TypeError, ValueError):
            total = 0
        if total > 0 and not f.get("_unverified_figures"):
            out.append({"id": r["id"], "category": r["category"], "filename": r.get("filename"),
                        "supplier": f.get("supplier"), "date": f.get("date"),
                        "total_cents": total, "project": r.get("project")})
    return out


# ── matching (proposals only) ────────────────────────────────────────────────

def _candidates(amount: int, when: Optional[date], pool: List[Dict[str, Any]],
                before: int = 3, after: int = 60) -> List[Dict[str, Any]]:
    tol = max(100, int(amount * 0.01))
    out = []
    for t in pool:
        if abs(int(t.get("amount_cents") or 0) - amount) > tol:
            continue
        td = _d(t.get("txn_date"))
        if when and td and not (when - timedelta(days=before) <= td <= when + timedelta(days=after)):
            continue
        out.append(t)
    return out


def _free(t: Dict[str, Any]) -> bool:
    return (t.get("match_status") != "matched" and not t.get("matched_invoice_id")
            and not t.get("matched_expense_id") and not t.get("matched_order_id"))


# ── the report ───────────────────────────────────────────────────────────────

def report(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
    since = since or (date.today() - timedelta(days=180)).isoformat()
    bank = _bank(tenant_id, since, until)
    out_free = [t for t in bank if t.get("direction") == "out" and _free(t)]
    in_free = [t for t in bank if t.get("direction") == "in" and _free(t)]

    unbooked = _unbooked_docs(tenant_id)

    # Supplier bills not paid → the debit that probably paid each (unique proposals only).
    bills = [b for b in _invoices(tenant_id, "inbound")
             if b.get("doc_type") == "invoice" and b.get("status") != "paid"]
    used: set = set()
    bills_unpaid = []
    for b in bills:
        c = [t for t in _candidates(int(b.get("total_cents") or 0), _d(b.get("issue_date")), out_free)
             if t["id"] not in used]
        prop = c[0] if len(c) == 1 else None
        if prop:
            used.add(prop["id"])
        bills_unpaid.append({"invoice_id": b["id"], "invoice_number": b.get("invoice_number"),
                             "supplier": b.get("supplier"), "issue_date": b.get("issue_date"),
                             "total_cents": int(b.get("total_cents") or 0), "project": b.get("project"),
                             "likely_payment": ({"txn_id": prop["id"], "txn_date": prop.get("txn_date"),
                                                 "description": prop.get("description"),
                                                 "amount_cents": prop.get("amount_cents")} if prop else None),
                             "possible_payments": len(c)})

    # Payments with no document behind them, grouped by counterparty.
    from vula.commerce.merchants import merchant_key
    groups: Dict[str, Dict[str, Any]] = {}
    for t in out_free:
        if t["id"] in used or (t.get("account_code") in _NO_DOC_EXPECTED):
            continue
        if int(t.get("amount_cents") or 0) < _MIN_NO_DOC_CENTS:
            continue
        key = merchant_key(t.get("payee") or t.get("description")) or (t.get("description") or "?").lower()
        g = groups.setdefault(key, {"counterparty": t.get("payee") or t.get("description"),
                                    "lines": 0, "total_cents": 0, "last": None, "projects": set()})
        g["lines"] += 1
        g["total_cents"] += int(t.get("amount_cents") or 0)
        g["last"] = max(filter(None, [g["last"], t.get("txn_date")]), default=None)
        if t.get("project"):
            g["projects"].add(t["project"])
    no_document = sorted(({**g, "projects": sorted(g["projects"])} for g in groups.values()),
                         key=lambda g: -g["total_cents"])

    # The tenant's own invoices not yet paid → a likely credit.
    sales = [s for s in _invoices(tenant_id, "outbound")
             if s.get("doc_type") == "invoice" and s.get("status") not in ("paid", "cancelled", "draft")]
    sales_unpaid = []
    for s in sales:
        c = _candidates(int(s.get("total_cents") or 0), _d(s.get("issue_date")), in_free, 3, 120)
        sales_unpaid.append({"invoice_id": s["id"], "invoice_number": s.get("invoice_number"),
                             "customer": s.get("customer_name"), "issue_date": s.get("issue_date"),
                             "total_cents": int(s.get("total_cents") or 0),
                             "likely_payment": ({"txn_id": c[0]["id"], "txn_date": c[0].get("txn_date"),
                                                 "description": c[0].get("description"),
                                                 "amount_cents": c[0].get("amount_cents")} if len(c) == 1 else None)})

    unexplained_in = in_free
    unexplained_total = sum(int(t.get("amount_cents") or 0) for t in in_free)
    to_categorise = [t for t in bank if t.get("categorized_by") == "default"]

    return {
        "since": since, "until": until,
        "bank_lines": len(bank),
        "bank_matched": sum(1 for t in bank if not _free(t)),
        "unbooked": unbooked,
        "unbooked_total_cents": sum(u["total_cents"] for u in unbooked),
        "bills_unpaid": bills_unpaid,
        "bills_with_likely_payment": sum(1 for b in bills_unpaid if b["likely_payment"]),
        "no_document": no_document[:50],
        "no_document_total_cents": sum(g["total_cents"] for g in no_document),
        "sales_unpaid": sales_unpaid,
        "money_in_unmatched_lines": len(in_free),
        "money_in_unmatched_cents": unexplained_total,
        "unexplained_in_sample": [{"txn_id": t["id"], "txn_date": t.get("txn_date"),
                                   "description": t.get("description"), "amount_cents": t.get("amount_cents")}
                                  for t in sorted(unexplained_in, key=lambda t: -int(t.get("amount_cents") or 0))[:15]],
        "to_categorise": len(to_categorise),
        # a statement date read wrong (day/month swapped) lands in the future — digg-demo had one
        "future_dated": [{"txn_id": t["id"], "txn_date": t.get("txn_date"), "description": t.get("description")}
                         for t in _bank(tenant_id, date.today().isoformat(), None)
                         if (_d(t.get("txn_date")) or date.min) > date.today()],
    }


def summary_text(rep: Dict[str, Any]) -> str:
    parts = [f"Cross-check since {rep['since']}: {rep['bank_matched']} of {rep['bank_lines']} bank lines matched to a document."]
    if rep["unbooked"]:
        parts.append(f"{len(rep['unbooked'])} filed bills/quotes ({_r(rep['unbooked_total_cents'])}) aren't in the books yet.")
    if rep["bills_unpaid"]:
        parts.append(f"{len(rep['bills_unpaid'])} supplier bills aren't marked paid — "
                     f"{rep['bills_with_likely_payment']} have a bank payment that looks like theirs.")
    if rep["no_document"]:
        parts.append(f"{_r(rep['no_document_total_cents'])} was paid out with no invoice or receipt on file "
                     "(no VAT claim without a tax invoice).")
    if rep.get("future_dated"):
        parts.append(f"{len(rep['future_dated'])} bank line(s) are dated in the future — probably a date read wrong.")
    if rep["to_categorise"]:
        parts.append(f"{rep['to_categorise']} bank lines still need a category.")
    return " ".join(parts)


# ── the one write: book filed documents that never reached the books ─────────

async def book_unbooked(tenant_id: str, limit: int = 200) -> Dict[str, Any]:
    """Run the normal commit path (supplier match, bill/quote row, catalog) for filed money
    documents that have a verified total but no books record. Same path every intake channel
    uses; a document it can't commit stays as it is."""
    from vula.api.whatsapp import _CATEGORY_TO_DOC_TYPE
    from vula.commerce import service

    def make():
        return (_client().table("vula_filed_documents")
                .select("id,category,fields,project,source,commerce_invoice_id")
                .eq("tenant_id", tenant_id).in_("category", list(_MONEY_CATS))
                .is_("commerce_invoice_id", "null"))
    ids = {u["id"] for u in _unbooked_docs(tenant_id)}
    rows = [r for r in _pages(make) if r["id"] in ids][:limit]
    booked, skipped = 0, 0
    for r in rows:
        fields = dict(r.get("fields") or {})
        fields.pop("_unverified_figures", None)
        fields.setdefault("doc_type", _CATEGORY_TO_DOC_TYPE.get(r["category"], "invoice"))
        try:
            res = await service.commit_inbound_document(
                tenant_id, fields, auto_commit=True, source=r.get("source") or "backfill",
                filed_document_id=r["id"], project=r.get("project"),
                is_boq=(r["category"] == "Bill of Quantities (BOQ)"))
            if res.get("committed") is False:
                skipped += 1
            else:
                booked += 1
        except Exception as exc:
            log.warning("book_unbooked: %s failed: %s", r["id"], exc)
            skipped += 1
    return {"booked": booked, "skipped": skipped, "candidates": len(rows)}


# ── VAT in vs out ────────────────────────────────────────────────────────────

def vat(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
    from config import settings
    from vula.commerce.accounting import is_vat_registered
    registered = is_vat_registered(tenant_id)
    since = since or (date.today() - timedelta(days=365)).isoformat()
    sd, ud = _d(since), _d(until) if until else None

    def in_range(v):
        d = _d(v)
        return d and d >= sd and (not ud or d <= ud)

    try:
        suppliers = {s["id"]: s for s in (_client().table("commerce_suppliers").select("id,name,tax_id")
                                         .eq("tenant_id", tenant_id).limit(2000).execute().data or [])}
    except Exception:
        suppliers = {}
    months: Dict[str, Dict[str, int]] = defaultdict(lambda: {"input_claimable": 0, "input_no_vat_number": 0,
                                                              "sales": 0, "output": 0})
    bills_with_vat = bills_without_number = bills_no_vat = 0
    for b in _invoices(tenant_id, "inbound"):
        if b.get("doc_type") != "invoice" or not in_range(b.get("issue_date")):
            continue
        m = str(b.get("issue_date"))[:7]
        v = int(b.get("vat_cents") or 0)
        if v <= 0:
            bills_no_vat += 1
            continue
        if (suppliers.get(b.get("supplier_id")) or {}).get("tax_id"):
            months[m]["input_claimable"] += v
            bills_with_vat += 1
        else:
            months[m]["input_no_vat_number"] += v
            bills_without_number += 1
    # Sales: the bank's money in on sales (what was actually received), else the tenant's own
    # invoices. Registered: output VAT is inside what was received (15/115). Unregistered: VAT
    # would have been charged ON TOP (15%) — the business keeps its price and adds VAT.
    bank_sales = [t for t in _bank(tenant_id, since, until)
                  if t.get("direction") == "in" and t.get("account_code") == "sales"]
    for t in bank_sales:
        m = str(t.get("txn_date"))[:7]
        c = int(t.get("amount_cents") or 0)
        months[m]["sales"] += c
        months[m]["output"] += int(round(c * 15 / 115)) if registered else int(round(c * 0.15))
    rows = []
    for m in sorted(months):
        x = months[m]
        rows.append({"month": m, **x, "net_cents": x["output"] - x["input_claimable"]})
    total = lambda k: sum(r[k] for r in rows)   # noqa: E731
    out = {
        "vat_registered": registered, "since": since, "until": until, "months": rows,
        "input_claimable_cents": total("input_claimable"),
        "input_no_vat_number_cents": total("input_no_vat_number"),
        "output_cents": total("output"), "sales_cents": total("sales"),
        "net_cents": total("output") - total("input_claimable"),
        "bills_with_vat": bills_with_vat, "bills_vat_but_no_supplier_number": bills_without_number,
        "bills_without_vat": bills_no_vat,
    }
    # 12-month sales against the compulsory registration threshold.
    yr = [t for t in _bank(tenant_id, (date.today() - timedelta(days=365)).isoformat(), None)
          if t.get("direction") == "in" and t.get("account_code") == "sales"]
    sales_12m = sum(int(t.get("amount_cents") or 0) for t in yr)
    # The compulsory threshold in force today (R2.3m from 1 Apr 2026 — vula/commerce/tax.py);
    # the config value is only an override.
    from vula.commerce.tax import vat_threshold
    threshold = int(settings.vat_registration_threshold_override_cents or vat_threshold()[0])
    out.update({"sales_12m_cents": sales_12m, "registration_threshold_cents": threshold,
                "over_threshold": (not registered) and sales_12m > threshold})
    if registered:
        out["text"] = (f"Since {since}: VAT on sales {_r(out['output_cents'])}, VAT you can claim from tax "
                       f"invoices {_r(out['input_claimable_cents'])} — net {'payable' if out['net_cents'] >= 0 else 'refundable'} "
                       f"{_r(abs(out['net_cents']))}.")
    else:
        out["text"] = (f"If registered since {since}: you'd charge {_r(out['output_cents'])} VAT on "
                       f"{_r(out['sales_cents'])} of sales and could claim {_r(out['input_claimable_cents'])} "
                       f"from suppliers' tax invoices — net {_r(abs(out['net_cents']))} "
                       f"{'to SARS' if out['net_cents'] >= 0 else 'back from SARS'}.")
    if out["input_no_vat_number_cents"]:
        out["text"] += (f" Another {_r(out['input_no_vat_number_cents'])} of VAT is on bills whose supplier "
                        "has no VAT number on file — add it to claim it.")
    if out["over_threshold"]:
        out["text"] += (f" Sales in the last 12 months ({_r(sales_12m)}) are above the compulsory "
                        f"registration threshold ({_r(threshold)}) — check with your accountant.")
    return out

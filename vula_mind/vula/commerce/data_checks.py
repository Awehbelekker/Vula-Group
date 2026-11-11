"""vula/commerce/data_checks.py — what a business adds is checked before Vula relies on it (10 Nov 2026).

Ian, after a client's project was put right by hand: "We need processes so when tenants add data it gets
processed correctly by Vula and AI." Every check here is something that went wrong on that project:

  at_filing(...)   runs as a document is filed (doc_filing.file_document — every intake route: WhatsApp,
                   email, the dashboard, Google Drive, the chat skill), before anything relies on it:
                     · a date read years away from when it was filed, or in the future ("2022-09-26"
                       for a slip filed in 2026 — the till time 11:04:22 read as a date)
                     · an internal cost sheet in a category a client link shows (a "project bill
                       variance" with actual costs, filed as a BOQ) — marked internal, so it never
                       reaches a client; its category (and the budget it sets) stays
                     · an invoice or quote with no supplier
                   The document is flagged for review and the sender told; nothing is thrown away.
  sweep(tenant)    the business's health list (dashboard; GET admin/data-health): the same checks on
                   everything already filed, plus what only shows across records —
                     · a bill booked on a different project from its document
                     · likely duplicate suppliers (supplier_identity)
                     · a supplier bill still unpaid while a bank payment of exactly its amount sits
                       unmatched beside it
                     · job payments in the bank with no project
                     · client links whose project has no agreed fee, or whose balance disagrees with
                       the invoices (project_tracker warnings)

Everything is tenant-scoped and read-only except at_filing's flags on the document being filed.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Categories whose documents a client link can show (project_file.FOLDERS, client=True).
CLIENT_CATEGORIES = frozenset({"Invoice", "Delivery Note", "Quote / Estimate", "Fee Proposal / Schedule",
                               "Drawing / Plan", "Bill of Quantities (BOQ)", "Specification",
                               "Programme / Schedule"})
SUPPLIER_CATEGORIES = frozenset({"Invoice", "Delivery Note", "Quote / Estimate"})
# Documents whose date drives the books. A specification, statement, drawing or report can rightly be
# years old (a 2022 drawing revision); a supplier invoice or slip read as 2015 almost never is.
DATED_CATEGORIES = frozenset({"Invoice", "Delivery Note", "Quote / Estimate", "Proof of Payment", "Receipt",
                              "Credit Note", "Expense Receipt", "Till Slip"})
RECENT_DAYS = 180
_INTERNAL_NAME = re.compile(r"\b(variance|cost\s*(report|statement|analysis|sheet)|reconcil|margin|profit|"
                            r"actuals?\b|internal|labou?r\s*reconciliation)", re.I)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LABOUR_AND_JOB_CODES = frozenset({"cost_of_sales", "casual_labour", "subcontractors"})


def _client():
    from vula.commerce.service import _client as c
    return c()


def _issue(code: str, message: str, **extra) -> Dict[str, Any]:
    return {"code": code, "message": message, **extra}


# ── one document, as it's filed ──────────────────────────────────────────────────────────────

def date_problem(value: Optional[str], filed: date) -> Optional[str]:
    """Why a document's date can't be right, or None."""
    v = str(value or "")[:10]
    if not _ISO.match(v):
        return None
    try:
        d = date.fromisoformat(v)
    except ValueError:
        return f"The date {v} isn't a real date."
    if d > filed + timedelta(days=31):
        return f"The date reads as {d:%d %b %Y}, in the future."
    if abs((filed - d).days) > 400:
        return f"The date reads as {d:%d %b %Y}, more than a year from when it was sent ({filed:%d %b %Y})."
    return None


def looks_internal(category: str, filename: str, fields: Dict[str, Any]) -> bool:
    """A cost sheet the business keeps for itself: actual costs, margins or a reconciliation."""
    if category not in CLIENT_CATEGORIES:
        return False
    if _INTERNAL_NAME.search(filename or ""):
        return True
    rows = list(fields.get("sections") or []) + list(fields.get("line_items") or [])
    return any(isinstance(r, dict) and any(k in r for k in ("actual_cents", "margin_cents", "profit_cents"))
               for r in rows)


def at_filing(category: str, filename: str, fields: Optional[Dict[str, Any]],
              filed: Optional[date] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(fields with the checks recorded, the problems found). Never raises."""
    fields = dict(fields or {})
    issues: List[Dict[str, Any]] = []
    try:
        filed = filed or datetime.now(timezone.utc).date()
        why = (date_problem(fields.get("date") or fields.get("issue_date"), filed)
               if category in DATED_CATEGORIES else None)
        if why:
            issues.append(_issue("date", f"{why} Check the date on the document — I haven't relied on it."))
        if looks_internal(category or "", filename or "", fields):
            fields["_internal"] = True
            issues.append(_issue("internal", "This looks like an internal cost sheet (actual costs or a "
                                 "reconciliation), so I've kept it off client links."))
        if (category in SUPPLIER_CATEGORIES and not (fields.get("supplier") or fields.get("supplier_name"))):
            issues.append(_issue("no_supplier", "I couldn't read who this is from. Reply with the supplier's name."))
        if issues:
            fields["_checks"] = [i["code"] for i in issues]
    except Exception as exc:  # noqa: BLE001 — a check must never stop a document being filed
        log.warning("filing checks skipped: %s", exc)
    return fields, issues


def notice(issues: List[Dict[str, Any]]) -> str:
    """The sender's message: one line per problem."""
    return "\n".join(f"⚠️ {i['message']}" for i in issues)


# ── the business's health list ───────────────────────────────────────────────────────────────

def _r(cents: int) -> str:
    return ("-" if cents < 0 else "") + f"R{abs(cents) / 100:,.2f}"


def _rows(make) -> List[Dict[str, Any]]:
    try:
        from vula.commerce.ledger import _all_pages
        return _all_pages(make)
    except Exception as exc:  # noqa: BLE001
        log.debug("health read skipped: %s", exc)
        return []


def _documents(tenant_id: str) -> List[Dict[str, Any]]:
    return _rows(lambda: _client().table("vula_filed_documents")
                 .select("id,project,category,filename,fields,created_at,status,supplier_id,commerce_invoice_id")
                 .eq("tenant_id", tenant_id).neq("status", "duplicate"))


def document_issues(docs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {"date": [], "internal": [], "no_supplier": []}
    recent = (datetime.now(timezone.utc).date() - timedelta(days=RECENT_DAYS)).isoformat()
    for d in sorted(docs, key=lambda d: str(d.get("created_at") or ""), reverse=True):
        f = d.get("fields") or {}
        if f.get("page_of"):
            continue
        filed = str(d.get("created_at") or "")[:10]
        try:
            filed_d = date.fromisoformat(filed)
        except ValueError:
            continue
        label = " ".join(str(x) for x in (f.get("supplier") or f.get("supplier_name"),
                                          f.get("document_number") or f.get("invoice_number")) if x)
        item = {"id": d["id"], "project": d.get("project"), "category": d.get("category"),
                "filename": label or (d.get("filename") if not str(d.get("filename") or "").startswith("image-wamid")
                                      else f"{d.get('category') or 'Document'} sent {filed_d:%d %b %Y}")}
        why = (date_problem(f.get("date") or f.get("issue_date"), filed_d)
               if d.get("category") in DATED_CATEGORIES else None)
        if why:
            out["date"].append({**item, "detail": why})
        if not f.get("_internal") and d.get("project") and looks_internal(d.get("category") or "", d.get("filename") or "", f):
            out["internal"].append({**item, "detail": "Would show on the project's client link."})
        if (d.get("category") in SUPPLIER_CATEGORIES and not d.get("supplier_id") and filed >= recent
                and not (f.get("supplier") or f.get("supplier_name"))):
            out["no_supplier"].append({**item, "detail": f.get("document_number") or f.get("invoice_number") or ""})
    return out


def bill_project_mismatches(tenant_id: str, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from vula.commerce.service import project_key
    linked = {str(d["commerce_invoice_id"]): d for d in docs if d.get("commerce_invoice_id") and d.get("project")}
    if not linked:
        return []
    bills = _bills(tenant_id)
    out = []
    for b in bills:
        d = linked.get(str(b["id"]))
        if d and b.get("project") and project_key(b["project"]) != project_key(d["project"]):
            out.append({"id": b["id"], "bill": b.get("invoice_number"), "supplier": b.get("supplier"),
                        "detail": f"Bill under {b['project']}, its document under {d['project']}."})
    return out


def _bills(tenant_id: str) -> List[Dict[str, Any]]:
    return _rows(lambda: _client().table("commerce_invoices").select("id,invoice_number,supplier,project,status,total_cents")
                 .eq("tenant_id", tenant_id).neq("status", "cancelled"))


def bill_amount_mismatches(tenant_id: str, docs: List[Dict[str, Any]],
                           bills: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """A bill booked at a different amount from its own document (Sea Point Hire 113631: the invoice says
    R2,000.00, the bill R2,300.00)."""
    linked = {str(d["commerce_invoice_id"]): d for d in docs if d.get("commerce_invoice_id")}
    if not linked:
        return []
    out = []
    for b in (bills if bills is not None else _bills(tenant_id)):
        d = linked.get(str(b["id"]))
        doc_c = (d.get("fields") or {}).get("total_cents") if d else None
        try:
            doc_c, bill_c = abs(int(doc_c)), abs(int(b.get("total_cents") or 0))
        except (TypeError, ValueError):
            continue
        if doc_c and bill_c and abs(doc_c - bill_c) > 100:
            why = (" 15% VAT was added because the document was read as priced excluding VAT — right only if "
                   "it says so and the supplier is VAT registered." if abs(bill_c - (doc_c + doc_c * 15 // 100)) <= 1 else "")
            out.append({"id": b["id"], "project": b.get("project"), "bill": b.get("invoice_number"),
                        "supplier": b.get("supplier"),
                        "detail": f"Booked at {_r(bill_c)}, the document says {_r(doc_c)}.{why}"})
    return out


def unpaid_bills_with_payment(tenant_id: str) -> List[Dict[str, Any]]:
    """Supplier bills still open while a bank payment of exactly their amount, near their date, sits unmatched."""
    bills = _rows(lambda: _client().table("commerce_invoices")
                  .select("id,invoice_number,supplier,total_cents,issue_date,status,doc_type,direction,project")
                  .eq("tenant_id", tenant_id).eq("direction", "inbound").in_("status", ["draft", "sent", "overdue"]))
    bills = [b for b in bills if (b.get("doc_type") or "invoice") == "invoice" and int(b.get("total_cents") or 0) > 0]
    if not bills:
        return []
    bank = _rows(lambda: _client().table("commerce_bank_transactions")
                 .select("id,txn_date,amount_cents,description,matched_invoice_id,matched_expense_id,direction")
                 .eq("tenant_id", tenant_id).eq("direction", "out").is_("matched_invoice_id", "null"))
    by_amount: Dict[int, List[Dict[str, Any]]] = {}
    for t in bank:
        if not t.get("matched_expense_id"):
            by_amount.setdefault(abs(int(t.get("amount_cents") or 0)), []).append(t)
    out = []
    for b in bills:
        try:
            issued = date.fromisoformat(str(b.get("issue_date"))[:10])
        except ValueError:
            continue
        for t in by_amount.get(int(b["total_cents"]), []):
            try:
                paid = date.fromisoformat(str(t.get("txn_date"))[:10])
            except ValueError:
                continue
            if -10 <= (paid - issued).days <= 60 and _payment_names_bill(b, t):
                out.append({"id": b["id"], "bill": b.get("invoice_number"), "supplier": b.get("supplier"),
                            "project": b.get("project"),
                            "detail": f"{_r(int(b['total_cents']))} — paid on {paid:%d %b %Y}? ({t.get('description') or ''})"})
                break
    return out


def _payment_names_bill(bill: Dict[str, Any], txn: Dict[str, Any]) -> bool:
    """A bank line is this bill's payment only with more than the amount to go on: the supplier's name or
    the bill's number in the bank line, or an amount nobody pays by chance (not a round hundred)."""
    text = re.sub(r"[^a-z0-9]", " ", str(txn.get("description") or "").lower())
    words = {w for w in re.findall(r"[a-z0-9]+", str(bill.get("supplier") or "").lower())
             if len(w) >= 4 and w not in {"pty", "ltd", "trading", "services", "supplies", "hardware", "centre"}}
    if words & set(text.split()):
        return True
    number = re.sub(r"[^a-z0-9]", "", str(bill.get("invoice_number") or "").lower())
    if len(number) >= 4 and number in text.replace(" ", ""):
        return True
    return int(bill.get("total_cents") or 0) % 10000 != 0


def unallocated_job_payments(tenant_id: str, days: int = 120) -> Dict[str, Any]:
    since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    rows = _rows(lambda: _client().table("commerce_bank_transactions")
                 .select("id,txn_date,amount_cents,description,account_code,project,direction,match_status")
                 .eq("tenant_id", tenant_id).eq("direction", "out").is_("project", "null").gte("txn_date", since))
    mine = [r for r in rows if r.get("account_code") in LABOUR_AND_JOB_CODES and r.get("match_status") != "ignored"]
    return {"count": len(mine), "total_cents": sum(abs(int(r.get("amount_cents") or 0)) for r in mine),
            "items": [{"id": r["id"], "date": r.get("txn_date"), "detail": f"{_r(abs(int(r['amount_cents'] or 0)))} · "
                                                                            f"{r.get('description') or ''}"}
                      for r in sorted(mine, key=lambda r: str(r.get("txn_date") or ""), reverse=True)[:25]]}


def kept_off_links(tenant_id: str) -> List[Dict[str, Any]]:
    """Documents on a project with a live client link that the link leaves off because they name
    another of the business's projects (vula/commerce/project_share_docs.other_projects) — the owner
    sees why, and can show one anyway."""
    from vula.commerce import project_file as pf, project_share_docs as sd
    shares = _rows(lambda: _client().table("vula_project_shares").select("project,expires_at,revoked_at")
                   .eq("tenant_id", tenant_id).is_("revoked_at", "null"))
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for project in sorted({s["project"] for s in shares if str(s.get("expires_at") or "") > now}):
        try:
            docs = pf._documents(tenant_id, project)
            organised = {str(r["id"]): r for r in pf.organise(docs, pf._Suppliers([], ""))["rows"]}
            for doc_id, named in sd.other_projects_by_doc(tenant_id, project, docs).items():
                row = organised.get(doc_id)
                if not row or not row.get("client_ok", row.get("client", True)):
                    continue
                d = next(x for x in docs if str(x["id"]) == doc_id)
                f = d.get("fields") or {}
                what = " ".join(x for x in (str(f.get("supplier") or ""), str(f.get("document_number") or "")) if x) \
                    or d.get("filename") or "A document"
                out.append({"id": doc_id, "project": project, "filename": d.get("filename"),
                            "detail": f"{what} names {', '.join(named)} — kept off {project}'s client link",
                            "action": "show_anyway"})
        except Exception as exc:  # noqa: BLE001
            log.debug("kept-off check skipped for %s: %s", project, exc)
    return out


def link_warnings(tenant_id: str) -> List[Dict[str, Any]]:
    """The owner's warnings for every project with a live client link."""
    from vula.commerce import project_tracker as pt
    shares = _rows(lambda: _client().table("vula_project_shares").select("project,view,expires_at,revoked_at")
                   .eq("tenant_id", tenant_id).is_("revoked_at", "null"))
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for project in sorted({s["project"] for s in shares if str(s.get("expires_at") or "") > now}):
        try:
            for w in pt.position(tenant_id, project).get("warnings") or []:
                out.append({"project": project, "detail": w})
        except Exception as exc:  # noqa: BLE001
            log.debug("link check skipped for %s: %s", project, exc)
    return out


_TITLES = {
    "link": "Client links to check before the client relies on them",
    "date": "Documents with a date that can't be right",
    "internal": "Internal cost sheets a client link would show",
    "no_supplier": "Invoices and quotes with no supplier (last 6 months)",
    "bill_project": "Bills on a different project from their document",
    "bill_amount": "Bills booked at a different amount from their document",
    "unpaid_paid": "Supplier bills still unpaid with a matching bank payment",
    "duplicates": "Suppliers on file more than once",
    "unallocated": "Job payments in the bank with no project",
    "multi_site": "Invoices for several jobs — confirm the split",
    "other_project": "Kept off a client link: names another of your projects",
}


def sweep(tenant_id: str) -> Dict[str, Any]:
    """The business's health list: [{code, title, count, items}], most important first."""
    sections: List[Dict[str, Any]] = []

    def add(code: str, items: List[Dict[str, Any]], **extra) -> None:
        if items:
            sections.append({"code": code, "title": _TITLES[code], "count": len(items), "items": items[:25], **extra})

    add("link", link_warnings(tenant_id))
    try:
        add("other_project", kept_off_links(tenant_id))
    except Exception as exc:  # noqa: BLE001
        log.debug("kept-off list skipped: %s", exc)
    docs = _documents(tenant_id)
    found = document_issues(docs)
    try:
        from vula.commerce import site_split
        add("multi_site", site_split.pending(tenant_id, docs))
    except Exception as exc:  # noqa: BLE001
        log.debug("site split list skipped: %s", exc)
    add("internal", found["internal"])
    add("bill_project", bill_project_mismatches(tenant_id, docs))
    add("bill_amount", bill_amount_mismatches(tenant_id, docs))
    add("date", found["date"])
    add("unpaid_paid", unpaid_bills_with_payment(tenant_id))
    add("no_supplier", found["no_supplier"])
    try:
        import asyncio
        from vula.commerce import service, supplier_identity as si
        suppliers = (_client().table("commerce_suppliers").select("*").eq("tenant_id", tenant_id)
                     .limit(2000).execute().data or [])
        # sweep() runs in a worker thread (the API calls it with asyncio.to_thread), so it has its own loop
        own_names, own_numbers = asyncio.run(service.own_business_names(tenant_id))
        others, own_acc = si.split_own(suppliers, own_names, own_numbers)
        add("duplicates", [{"id": g["keep"]["id"], "detail": f"{g['keep']['name']} ← "
                                                           f"{', '.join(d['name'] for d in g['duplicates'])}"}
                           for g in si.find_duplicates(others, own_acc)])
    except Exception as exc:  # noqa: BLE001
        log.debug("duplicate check skipped: %s", exc)
    un = unallocated_job_payments(tenant_id)
    if un["count"]:
        sections.append({"code": "unallocated", "title": _TITLES["unallocated"], "count": un["count"],
                         "total_cents": un["total_cents"], "items": un["items"]})
    return {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "issues": sum(s["count"] for s in sections), "sections": sections}

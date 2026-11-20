"""vula/commerce/supplier_statements.py — a supplier's statement against the books (20 Nov 2026, Ian, DIGG).

Quicktint's two statements said DIGG owed R36,832.07 across 32 invoices and 26 payments; Vula had none of the
invoices and none of the payments matched a bank line, so the books said R0. The accountant ties every
supplier's statement to the creditors ledger; this does it for them, in code, reading only.

  read_statements(tenant, limit)  read the lines of filed supplier statements not read yet (the PDF's own
                                  text, vula/ingestion/supplier_statement.py), kept as `fields.statement`
  check(tenant)                   per supplier: each statement invoice → a bill in the books / a document on
                                  file / missing; each payment → a bank line, or not in the bank lines (and
                                  which accounts have no statement for that month); the statement's balance
                                  against what the books say is owed
  request_text(row)               the message asking the supplier for the invoices that are missing

Nothing here books, pays or sends anything.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CATEGORY = "Account Statement"
READ_LIMIT = 20
BILL_DAYS = 5            # an invoice found as a bill of the same amount within 5 days of its date
PAY_DAYS = (-3, 5)       # a payment found as a bank line from 3 days before to 5 after
TOLERANCE = 1


def _client():
    from vula.commerce import service
    return service._client()


def _day(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _statements(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce.doc_audit import DOC_COLS, _pages, live
    return [d for d in _pages(lambda: _client().table("vula_filed_documents").select(DOC_COLS)
                              .eq("tenant_id", tenant_id).eq("category", CATEGORY).order("id")) if live(d)]


def _needs_read(doc: Dict[str, Any]) -> bool:
    st = (doc.get("fields") or {}).get("statement")
    return bool(doc.get("file_url")) and (not isinstance(st, dict) or st.get("hash") != doc.get("content_hash"))


def read_statements(tenant_id: str, docs: Optional[List[Dict[str, Any]]] = None,
                    limit: int = READ_LIMIT) -> Dict[str, int]:
    """Read the lines of up to `limit` filed statements not read yet. The result (or that it couldn't be read
    to the cent) is kept on the document with the file's hash, so a file is read once."""
    from vula.commerce import doc_audit, reread
    from vula.ingestion.supplier_statement import parse
    docs = _statements(tenant_id) if docs is None else docs
    todo = [d for d in docs if _needs_read(d)][:limit]
    read = unread = 0
    for d in todo:
        try:
            data = asyncio.run(reread._download(d["file_url"]))
            text = doc_audit.pdf_text(data) if data else ""
        except Exception as exc:  # noqa: BLE001
            log.debug("statement %s not read: %s", d.get("id"), exc)
            text = ""
        parsed = parse(text) if text else None
        f = dict(d.get("fields") or {})
        f["statement"] = ({**parsed, "hash": d.get("content_hash")} if parsed
                          else {"reconciled": False, "hash": d.get("content_hash"),
                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        _client().table("vula_filed_documents").update({"fields": f}).eq("tenant_id", tenant_id).eq("id", d["id"]).execute()
        d["fields"] = f
        read += bool(parsed)
        unread += not parsed
    return {"read": read, "unread": unread, "left": max(0, sum(1 for d in docs if _needs_read(d)))}


def _own_words(tenant_id: str) -> set:
    from vula.commerce import bank_rec
    from vula.commerce.books_review import _words
    try:
        return set().union(*(_words(n) for n in bank_rec._own_names_now(tenant_id)))
    except Exception:  # noqa: BLE001
        return set()


def _supplier_of(tenant_id: str, names: List[str], ids: Dict[str, str]) -> Optional[str]:
    """The supplier record a statement is from — never the business itself (a statement addressed to
    "Aweh Be Lekker t/a Digg" is to DIGG, not from it)."""
    from vula.commerce.books_review import _alike, _words
    own = _own_words(tenant_id)
    for n in names:
        if n and n.strip().lower() in ids and not (_words(n) & own):
            return ids[n.strip().lower()]
    want = {w for n in names if n for w in _words(n) if not any(_alike(w, o) for o in own)}
    for name, sid in ids.items():
        if want and any(_alike(a, b) for a in want for b in _words(name)):
            return sid
    return None


def check(tenant_id: str) -> Dict[str, Any]:
    """Each supplier with a statement read to the cent, against the books. Reads only."""
    from vula.commerce import books_review, doc_audit, statement_files
    from vula.commerce.ledger import _all_pages
    docs = [d for d in _statements(tenant_id) if ((d.get("fields") or {}).get("statement") or {}).get("reconciled")]
    unread = sum(1 for d in _statements(tenant_id) if not ((d.get("fields") or {}).get("statement") or {}).get("reconciled"))
    if not docs:
        return {"suppliers": [], "unread": unread}
    ids = books_review._supplier_ids(tenant_id)
    names_by_id: Dict[str, List[str]] = {}
    for n, sid in ids.items():
        names_by_id.setdefault(sid, []).append(n)
    groups: Dict[str, Dict[str, Any]] = {}
    for d in docs:
        st = d["fields"]["statement"]
        # the statement's own "Name :" line first; what was read as its supplier only when that's missing
        name = st.get("supplier") or (d.get("fields") or {}).get("supplier") or "?"
        sid = _supplier_of(tenant_id, [name], ids)
        key = sid or name.strip().lower()
        g = groups.setdefault(key, {"supplier_id": sid, "supplier": name,
                                    "statements": [], "lines": []})
        g["statements"].append({"doc_id": d["id"], "filename": d.get("filename"), "account_name": st.get("account_name"),
                                "from": st.get("from"), "to": st.get("to"), "balance_cents": st.get("balance_cents")})
        g["lines"] += st.get("lines") or []
    all_docs = [x for x in doc_audit._pages(lambda: _client().table("vula_filed_documents").select(doc_audit.DOC_COLS)
                                            .eq("tenant_id", tenant_id).eq("category", "Invoice").order("id")) if doc_audit.live(x)]
    bills = [b for b in _all_pages(lambda: _client().table("commerce_invoices")
                                   .select("id,invoice_number,supplier,supplier_id,status,total_cents,total_paid_cents,issue_date,doc_type")
                                   .eq("tenant_id", tenant_id).eq("direction", "inbound").order("id"))
             if (b.get("doc_type") or "invoice") == "invoice" and b.get("status") != "cancelled"]
    lines = [r for r in _all_pages(lambda: _client().table(books_review.BANK).select(books_review.LINE_COLS)
                                   .eq("tenant_id", tenant_id).eq("direction", "out").order("id")) if books_review._live(r)]
    try:
        cov = statement_files.coverage(tenant_id)
    except Exception:  # noqa: BLE001
        cov = {"accounts": []}
    out = []
    for g in groups.values():
        names = set(n for n in (names_by_id.get(g["supplier_id"] or "", []) + [g["supplier"] or ""]) if n)
        words = set().union(*(books_review._words(n) for n in names)) if names else set()

        def theirs(name: Any, sid: Any = None) -> bool:
            if g["supplier_id"] and sid and str(sid) == str(g["supplier_id"]):
                return True
            return any(books_review._alike(a, b) for a in books_review._words(name) for b in words)
        s_docs = [x for x in all_docs if theirs(doc_audit._sup(x.get("fields") or {}), x.get("supplier_id"))]
        s_bills = [b for b in bills if theirs(b.get("supplier"), b.get("supplier_id"))]
        by_bill = {str(b["id"]): b for b in s_bills}
        invoices, used_bills = [], set()
        paid_on: Dict[str, int] = {}
        for ln in g["lines"]:
            if ln["kind"] in ("payment", "credit") and ln.get("number"):
                paid_on[doc_audit._alnum(ln["number"])] = paid_on.get(doc_audit._alnum(ln["number"]), 0) + ln["amount_cents"]
        for ln in (x for x in g["lines"] if x["kind"] == "invoice"):
            n = doc_audit._alnum(ln.get("number"))
            doc = next((x for x in s_docs if n and doc_audit._alnum(doc_audit._num(x.get("fields") or {})) == n), None)
            bill = by_bill.get(str((doc or {}).get("commerce_invoice_id") or ""))
            if bill is None:
                day = _day(ln.get("date"))
                bill = next((b for b in s_bills if str(b["id"]) not in used_bills
                             and abs(int(b.get("total_cents") or 0) - ln["amount_cents"]) <= TOLERANCE
                             and day and _day(b.get("issue_date")) and abs((_day(b["issue_date"]) - day).days) <= BILL_DAYS), None)
            if bill:
                used_bills.add(str(bill["id"]))
            open_cents = max(0, ln["amount_cents"] - paid_on.get(n, 0))
            invoices.append({"number": ln.get("number"), "date": ln.get("date"), "cents": ln["amount_cents"],
                             "open_cents": open_cents,
                             "status": "in_books" if bill and bill.get("status") != "draft" else
                                       "draft" if bill else "on_file" if doc else "missing",
                             "bill": {"id": bill["id"], "number": bill.get("invoice_number"), "status": bill.get("status"),
                                      "cents": int(bill.get("total_cents") or 0)} if bill else None,
                             "doc_id": (doc or {}).get("id")})
        # payments, several invoices paid on one day tried as one bank payment first
        by_day: Dict[str, List[Dict[str, Any]]] = {}
        for ln in (x for x in g["lines"] if x["kind"] == "payment"):
            by_day.setdefault(ln.get("date") or "", []).append(ln)
        used_lines, payments = set(), []

        def find(cents: int, day: Optional[date]) -> Optional[Dict[str, Any]]:
            for r in sorted(lines, key=lambda r: abs(((_day(r.get("txn_date")) or day) - day).days) if day else 0):
                g_ = (_day(r.get("txn_date")) - day).days if day and _day(r.get("txn_date")) else None
                if (str(r["id"]) not in used_lines and abs(int(r.get("amount_cents") or 0) - cents) <= TOLERANCE
                        and g_ is not None and PAY_DAYS[0] <= g_ <= PAY_DAYS[1]):
                    used_lines.add(str(r["id"]))
                    return r
            return None
        for d_, pays in sorted(by_day.items()):
            day = _day(d_)
            total = sum(p["amount_cents"] for p in pays)
            hit = find(total, day) if len(pays) > 1 else None
            groups_ = [(pays, hit)] if hit else [([p], find(p["amount_cents"], day)) for p in pays]
            for ps, r in groups_:
                month = (d_ or "")[:7]
                gaps = [a["account"] for a in cov.get("accounts") or []
                        if any(m["month"] == month and not m["statement"] for m in a.get("months") or [])]
                payments.append({"date": d_, "cents": sum(p["amount_cents"] for p in ps),
                                 "numbers": [p.get("number") for p in ps],
                                 "line": {"id": r["id"], "date": str(r.get("txn_date"))[:10], "description": r.get("description"),
                                          "account": books_review._mask(r.get("bank_account"))} if r else None,
                                 "gaps": [] if r else gaps})
        open_in_books = sum(books_review._owed(b) for b in s_bills if b.get("status") in ("sent", "overdue", "part_paid"))
        drafts = sum(int(b.get("total_cents") or 0) for b in s_bills if b.get("status") == "draft")
        balance = sum(int(s.get("balance_cents") or 0) for s in g["statements"])
        missing = [i for i in invoices if i["status"] == "missing"]
        out.append({"supplier": g["supplier"], "supplier_id": g["supplier_id"], "statements": g["statements"],
                    "balance_cents": balance, "owed_in_books_cents": open_in_books, "draft_bills_cents": drafts,
                    "difference_cents": balance - open_in_books, "invoices": invoices,
                    "open_invoices": [i for i in invoices if i["open_cents"]],
                    "missing": missing, "missing_cents": sum(i["cents"] for i in missing),
                    "payments": payments, "payments_not_found": [p for p in payments if not p["line"]],
                    "text": (f"{g['supplier']}'s statement{'s' if len(g['statements']) > 1 else ''} say you owe "
                             f"R{balance / 100:,.2f}; the books say R{open_in_books / 100:,.2f}"
                             + (f" (plus R{drafts / 100:,.2f} in draft bills)" if drafts else "") + ". "
                             f"{len(missing)} of {len(invoices)} invoices on the statement aren't in Vula; "
                             f"{sum(1 for p in payments if not p['line'])} of {len(payments)} payments aren't in the bank lines.")})
    out.sort(key=lambda s: -abs(s["difference_cents"]))
    return {"suppliers": out, "unread": unread}


def findings(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for s in c["suppliers"]:
        if s["missing"] or s["difference_cents"] or s["payments_not_found"]:
            out.append({"kind": "supplier_statement", "severity": "problem" if s["difference_cents"] else "attention",
                        "rows": [st["doc_id"] for st in s["statements"]], "cents": s["difference_cents"], "text": s["text"]})
    if c.get("unread"):
        out.append({"kind": "statement_unread", "severity": "info", "rows": [], "cents": 0,
                    "text": f"{c['unread']} supplier statements couldn't be read line by line (a photo, or another "
                            "layout) — their balance is all Vula has."})
    return out


def request_text(s: Dict[str, Any], business: str = "") -> str:
    """The message asking the supplier for copies of the invoices that aren't in Vula."""
    nums = [i["number"] for i in s.get("missing") or [] if i.get("number")]
    accounts = ", ".join(sorted({st.get("account_name") for st in s.get("statements") or [] if st.get("account_name")}))
    return (f"Hi {s.get('supplier') or 'there'}, could you please send us copies of these invoices"
            + (f" (account: {accounts})" if accounts else "") + f": {', '.join(nums)}. Thank you"
            + (f" — {business}" if business else "") + ".")

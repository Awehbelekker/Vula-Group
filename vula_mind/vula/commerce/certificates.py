"""vula/commerce/certificates.py — payment certificates (20 Nov 2026, Ian, DIGG).

A payment certificate is the principal agent's / QS's statement of what the client owes the contractor for
work done (Vizion Developments certifying DIGG's work at the Cape Town High Performance Centre). It is not a
bill: DIGG invoices the certified amount and the client pays it. DIGG's certificates were filed as "Invoice"
with Vizion as the supplier, so they could have been booked as costs; each one actually matches one of
DIGG's own invoices and one payment received ("Magtape Credit Hpc-PC2" = certificate 2).

  looks_like(filename, summary, fields)  a certificate by its own title or summary (never by a mention)
  is_certificate(doc)                    a filed document that is one (category, or its title says so)
  read(doc)                              number, amount certified, date, parties — from whatever was read
  match(tenant)                          each certificate ↔ the business's invoice ↔ the money received,
                                         and payments naming a certificate that isn't on file
  label(tenant, doc_id)                  file a document as a Payment Certificate (read back, audited)

All matching is in code: amounts to the cent (one cent apart is allowed: banks round), dates in a window,
and the certificate number in the bank reference ("PC5", "PC09") where the business uses one.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

CATEGORY = "Payment Certificate"
# the document IS a certificate: its file name says so, or its summary opens by saying so
_TITLE_RX = re.compile(r"\b(?:interim\s+)?payment\s+certificate\b|\bvaluation\s+certificate\b", re.I)
_SUMMARY_RX = re.compile(r"^\s*(?:this\s+(?:document\s+)?is\s+)?(?:an?\s+|the\s+)?(?:interim\s+)?"
                         r"(?:payment|valuation)\s+certificate\b", re.I)
_NO_RX = re.compile(r"certificate\s*(?:no\.?|number|nr\.?|#)?\s*:?\s*0*(\d{1,4})\b", re.I)
_RAND_RX = re.compile(r"R\s?(\d{1,3}(?:[ ,]\d{3})*(?:\.\d{2}))")
_PC_RX = re.compile(r"(?<![a-z])pc[\s\-_.#]*(?:no\.?\s*)?0*(\d{1,4})(?!\d)", re.I)
TOLERANCE = 1                      # cents: a bank's rounding (DIGG: R49,025.39 certified, R49,025.40 paid)
INVOICE_DAYS = (-15, 45)           # the business's invoice: from 15 days before to 45 after the certificate
PAID_DAYS = (-5, 90)               # the money received: from 5 days before to 90 after
AMOUNT_KEYS = ("amount_certified_cents", "amount_due_cents", "net_amount_due_cents", "total_cents")
FLOAT_KEYS = ("amount_certified", "amount_due", "subtotal", "total")


def _client():
    from vula.commerce import service
    return service._client()


def _cents(v: Any) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v) if isinstance(v, int) else int((Decimal(str(v).replace(",", "").replace(" ", "")) * 100)
                                                       .to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def _day(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def looks_like(filename: Optional[str], summary: Optional[str], fields: Optional[Dict[str, Any]] = None) -> bool:
    """A payment certificate by its own title (file name) or by its summary opening with what it is.
    A document that only mentions one ("our invoice for Payment Certificate No. 4") is not."""
    f = fields or {}
    if str(f.get("report_type") or f.get("document_type") or "").strip().lower() in ("payment certificate",
                                                                                       "interim payment certificate"):
        return True
    return bool(_TITLE_RX.search(filename or "")) or bool(_SUMMARY_RX.search(summary or ""))


def is_certificate(doc: Dict[str, Any]) -> bool:
    return doc.get("category") == CATEGORY or looks_like(doc.get("filename"), doc.get("summary"), doc.get("fields"))


def read(doc: Dict[str, Any]) -> Dict[str, Any]:
    """What a certificate says, from whichever shape it was read in (a certificate filed as an invoice has
    `total_cents`; one filed as a report `amount_due_cents`; an older read rand floats). Never guessed:
    a figure not found is None."""
    f = doc.get("fields") or {}
    kept = f.get("certificate") if isinstance(f.get("certificate"), dict) else {}
    number = kept.get("number")
    if number is None:
        for k in ("certificate_number", "invoice_no", "document_number"):
            m = re.search(r"\d{1,4}", str(f.get(k) or ""))
            if m:
                number = int(m.group())
                break
    if number is None:
        for text in (doc.get("filename") or "", doc.get("summary") or ""):
            m = _NO_RX.search(text)
            if m:
                number = int(m.group(1))
                break
    amount = kept.get("amount_cents")
    if amount is None:
        amount = next((_cents(f.get(k)) for k in AMOUNT_KEYS if _cents(f.get(k))), None)
    if amount is None:
        amount = next((_cents(f.get(k)) for k in FLOAT_KEYS if _cents(f.get(k))), None)
    if amount is None:
        m = _RAND_RX.search(doc.get("summary") or "")
        amount = _cents(m.group(1).replace(" ", "").replace(",", "")) if m else None
    return {"number": int(number) if number is not None else None, "amount_cents": amount,
            "date": str(kept.get("date") or f.get("date") or f.get("valuation_date") or "")[:10] or None,
            "employer": kept.get("employer") or f.get("employer") or f.get("customer") or None,
            "contractor": kept.get("contractor") or f.get("contractor") or f.get("client") or None,
            "issuer": kept.get("issuer") or f.get("issuer") or f.get("vendor") or f.get("supplier") or None,
            "project": doc.get("project")}


def pc_number(text: Optional[str]) -> Optional[int]:
    """The certificate a bank reference names ("Magtape Credit Hpc-PC2", "Int-Banking Pmt Frm Hpc-PC09")."""
    m = _PC_RX.search(text or "")
    return int(m.group(1)) if m else None


def _documents(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce.doc_audit import DOC_COLS, _pages, live
    return [d for d in _pages(lambda: _client().table("vula_filed_documents").select(DOC_COLS)
                              .eq("tenant_id", tenant_id).order("id")) if live(d)]


def _sales(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce.doc_audit import _pages
    rows = _pages(lambda: _client().table("commerce_invoices")
                  .select("id,invoice_number,customer_name,direction,doc_type,status,total_cents,issue_date,project")
                  .eq("tenant_id", tenant_id).eq("direction", "outbound").order("id"))
    return [r for r in rows if (r.get("doc_type") or "invoice") == "invoice" and r.get("status") != "cancelled"]


def _receipts(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce.books_review import BANK, LINE_COLS, _live
    from vula.commerce.ledger import _all_pages
    rows = _all_pages(lambda: _client().table(BANK).select(LINE_COLS).eq("tenant_id", tenant_id)
                      .eq("direction", "in").order("id"))
    return [r for r in rows if _live(r)]


def _gap(a: Optional[date], b: Optional[date]) -> Optional[int]:
    return (b - a).days if a and b else None


def _ends_with(number: Any, n: int) -> bool:
    return bool(re.search(rf"(?<!\d)0*{n}$", str(number or "").strip()))


def own_invoices(tenant_id: str, docs: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The business's own invoices as documents on file (issued BY it), live, with a total."""
    from vula.commerce import doc_audit
    docs = _documents(tenant_id) if docs is None else docs
    own = doc_audit.own_names(tenant_id)
    return [d for d in docs if d.get("category") == "Invoice" and not is_certificate(d)
            and doc_audit.is_own(d.get("fields") or {}, own) and doc_audit._int((d.get("fields") or {}).get("total_cents"))]


def match(tenant_id: str, docs: Optional[List[Dict[str, Any]]] = None,
          sales: Optional[List[Dict[str, Any]]] = None,
          receipts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Each certificate with the business's invoice for it and the money received for it."""
    from vula.commerce import doc_audit
    docs = _documents(tenant_id) if docs is None else docs
    sales = _sales(tenant_id) if sales is None else sales
    receipts = _receipts(tenant_id) if receipts is None else receipts
    # one certificate per number: a copy filed twice (one read, one not) is one certificate — the copy
    # filed as a certificate, else the one whose amount was read, stands for it
    certs, seen = [], {}
    for d in sorted((d for d in docs if is_certificate(d)),
                    key=lambda d: (d.get("category") != CATEGORY, read(d)["amount_cents"] is None, str(d["id"]))):
        n = read(d)["number"]
        if n is not None and n in seen:
            continue
        if n is not None:
            seen[n] = d["id"]
        certs.append(d)
    ours = own_invoices(tenant_id, docs) if certs else []
    # what the business invoiced: its invoices in Vula, and its own invoices on file not (yet) in Vula
    invoiced = ([{"kind": "invoice", "id": s["id"], "number": s.get("invoice_number"), "cents": int(s.get("total_cents") or 0),
                  "date": str(s.get("issue_date") or "")[:10], "status": s.get("status")} for s in sales]
                + [{"kind": "document", "id": d["id"], "number": doc_audit._num(d.get("fields") or {}) or None,
                    "cents": abs(doc_audit._int((d.get("fields") or {}).get("total_cents")) or 0),
                    "date": str((d.get("fields") or {}).get("date") or "")[:10], "in_invoices": bool(d.get("commerce_invoice_id")),
                    "filename": d.get("filename")} for d in ours])
    uses_pc = False
    rows, used_rec = [], set()
    for d in sorted(certs, key=lambda d: (read(d)["number"] or 0, str(read(d)["date"] or ""))):
        c = read(d)
        amt, day, n = c["amount_cents"], _day(c["date"]), c["number"]
        inv = None
        if amt:
            cands = [i for i in invoiced if abs(i["cents"] - amt) <= TOLERANCE
                     and (lambda g: g is None or INVOICE_DAYS[0] <= g <= INVOICE_DAYS[1])(_gap(day, _day(i["date"])))]
            cands.sort(key=lambda i: (not (n and _ends_with(i["number"], n)), i["kind"] != "invoice",
                                      abs(_gap(day, _day(i["date"])) or 0)))
            inv = cands[0] if cands else None
        rec = None
        named = [r for r in receipts if n and pc_number(r.get("description")) == n and str(r["id"]) not in used_rec]
        if named:
            rec, uses_pc = named[0], True
        elif amt:
            cands = [r for r in receipts if str(r["id"]) not in used_rec
                     and abs(int(r.get("amount_cents") or 0) - amt) <= TOLERANCE
                     and (lambda g: g is not None and PAID_DAYS[0] <= g <= PAID_DAYS[1])(_gap(day, _day(r.get("txn_date"))))]
            cands.sort(key=lambda r: abs(_gap(day, _day(r.get("txn_date"))) or 0))
            rec = cands[0] if cands else None
        if rec:
            used_rec.add(str(rec["id"]))
        rows.append({"doc_id": d["id"], "filename": d.get("filename"), "labelled": d.get("category") == CATEGORY,
                     "category": d.get("category"), **c, "invoice": inv,
                     "received": {"id": rec["id"], "date": str(rec.get("txn_date"))[:10], "cents": int(rec.get("amount_cents") or 0),
                                  "description": rec.get("description")} if rec else None})
    have = {r["number"] for r in rows if r["number"] is not None}
    # payments naming a certificate that isn't on file — only where the business's references name them
    unmatched = []
    if uses_pc:
        for r in receipts:
            n = pc_number(r.get("description"))
            if n is not None and n not in have and str(r["id"]) not in used_rec:
                inv = next((i for i in invoiced if abs(i["cents"] - int(r.get("amount_cents") or 0)) <= TOLERANCE), None)
                unmatched.append({"number": n, "id": r["id"], "date": str(r.get("txn_date"))[:10],
                                  "cents": int(r.get("amount_cents") or 0), "description": r.get("description"),
                                  "invoice": inv})
        unmatched.sort(key=lambda x: (x["number"], x["date"]))
    return {"certificates": rows, "unmatched_receipts": unmatched}


def findings(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The books check's section: what's missing between certificates, invoices and money received."""
    def rands(c: Optional[int]) -> str:
        return f"R{(c or 0) / 100:,.2f}"
    out = []
    for r in m["certificates"]:
        label = f"Payment certificate {r['number'] or '?'}" + (f" ({r['date']})" if r.get("date") else "")
        if not r["labelled"]:
            out.append({"kind": "certificate_label", "severity": "attention", "rows": [r["doc_id"]], "cents": r["amount_cents"],
                        "text": f"{label} is filed as \"{r['category']}\" — it's a certificate of what the client owes, "
                                "not a bill. File it as a Payment Certificate."})
        if r["amount_cents"] and not r["invoice"]:
            out.append({"kind": "certificate_not_invoiced", "severity": "attention", "rows": [r["doc_id"]],
                        "cents": r["amount_cents"],
                        "text": f"{label} certifies {rands(r['amount_cents'])}, and no invoice for that amount is on file "
                                "or in Invoices — invoice it, or add the invoice."})
        elif r["invoice"] and r["invoice"]["kind"] == "document" and not r["invoice"].get("in_invoices"):
            out.append({"kind": "certificate_invoice_not_in_invoices", "severity": "attention", "rows": [r["invoice"]["id"]],
                        "cents": r["amount_cents"],
                        "text": f"{label}: the business's invoice {r['invoice']['number'] or ''} is on file but not in "
                                "Invoices — add it, so the payment has an invoice behind it."})
        if r["amount_cents"] and not r["received"]:
            out.append({"kind": "certificate_unpaid", "severity": "info", "rows": [r["doc_id"]], "cents": r["amount_cents"],
                        "text": f"{label} for {rands(r['amount_cents'])}: no payment received for it in the bank lines yet."})
    for u in m["unmatched_receipts"]:
        out.append({"kind": "receipt_no_certificate", "severity": "attention", "rows": [u["id"]], "cents": u["cents"],
                    "text": f"{rands(u['cents'])} received {u['date']} for certificate {u['number']} "
                            f"(\"{u['description']}\") — "
                            + ("the invoice is on file but not the certificate." if u["invoice"]
                               else "neither the certificate nor the business's invoice is on file.")})
    return out


def label(tenant_id: str, doc_id: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """File a document as a Payment Certificate: category set, what it says kept in `fields.certificate`,
    and marked never-a-bill (it's what the client owes the business). Read back and audited."""
    from vula.commerce import doc_audit
    doc = doc_audit._doc(tenant_id, doc_id)
    if not is_certificate(doc):
        raise doc_audit._refused("That document doesn't say it's a payment certificate.")
    c = read(doc)
    f = dict(doc.get("fields") or {})
    f["certificate"] = {k: c[k] for k in ("number", "amount_cents", "date", "employer", "contractor", "issuer")}
    f.setdefault("_not_a_bill", {"by": doc_audit._who(actor), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "reason": "A payment certificate — what the client owes the business, not a bill."})
    back = doc_audit._save_fields(tenant_id, doc_id, f, category=CATEGORY)
    doc_audit._audit(tenant_id, actor, "document_labelled_certificate", doc_id=doc_id, number=c["number"],
                     cents=c["amount_cents"])
    return {"ok": back.get("category") == CATEGORY and bool((back.get("fields") or {}).get("certificate")),
            "certificate": (back.get("fields") or {}).get("certificate")}


def numbers_in(rows: Iterable[Dict[str, Any]]) -> List[int]:
    return sorted({r["number"] for r in rows if r.get("number") is not None})

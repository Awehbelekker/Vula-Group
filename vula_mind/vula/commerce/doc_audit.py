"""vula/commerce/doc_audit.py — the books check, part two: the documents behind the money (Phase 0b).

19 Nov 2026 (Ian, DIGG: "make sure all DIGG stuff is accurate" before its accountant has access). The
books check (books_audit.py) audits the bank lines. The accountant also works from the documents behind
them, and those were read by a model. A read-only survey of DIGG the same day: of 322 supplier invoices
on file 64 had no supplier and 62 no total, 52 had a subtotal and VAT that don't make the total, 105 were
on file with no bill in the books, 15 documents carried figures never found in the document, 5 VAT
numbers sat under two spellings of a supplier, and 476 learned prices didn't say whether VAT was in them.

The sections it adds to the books check (same shape as books_audit._section):

  doc_figures     a money document whose figures don't match its own file, or don't add up: the total,
                  VAT or subtotal not printed in the PDF, its number or the supplier's VAT number not in
                  it, subtotal + VAT ≠ total, lines short of the total, the supplier/date/total missing,
                  figures the read itself couldn't find, a date that can't be right
  doc_books       the documents and the books disagree: an invoice on file with no bill, a bill booked at
                  a different amount or under a different supplier from its own document
  doc_duplicates  the same document filed twice, or the same supplier bill booked twice (a cost counted
                  twice)
  suppliers       duplicate supplier records, one VAT number under two names, VAT numbers that aren't
                  valid SA ones, VAT claimed on bills with no supplier VAT number (registered only)
  price_vat       learned prices that don't say whether VAT was in them (estimates, not the books)
  access          who can see and change the books (names and roles only)

The file check reads each PDF's own text with PyMuPDF (no OCR, no model) and keeps what it found on the
document as fields._file_check, keyed by the file's content hash, so a file is read once; it is the only
thing the check writes, and it never changes a figure. Everything else is read only, worked out in code
from the business's own rows, integer cents. The fixes (below) are made by an owner or manager, each read
back and written to vula_merchant_audit.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Documents that carry money the books depend on. Quotes and BOQs are prices, not the books; bank
# statements are read by the FNB reader (reconciled to the cent) and checked by books_audit.
MONEY_CATS = ("Invoice", "Credit Note", "Proof of Payment", "Expense Receipt", "Receipt", "Till Slip")
BOOK_CATS = ("Invoice",)                         # each should have a bill in the books (supplier credit notes: by hand)
FILE_CHECK_LIMIT = 150                           # PDFs read per run; the rest next time
FILE_CHECK_WORKERS = 6
MONEY_KEYS = ("total_cents", "vat_cents", "subtotal_cents", "amount_cents")
ID_KEYS = ("document_number", "tax_id")
DOC_COLS = ("id,category,filename,fields,status,content_hash,file_url,mime,commerce_invoice_id,supplier_id,"
            "created_at,project,summary")
BILL_COLS = ("id,invoice_number,supplier,supplier_id,direction,doc_type,status,total_cents,vat_cents,"
             "subtotal_cents,total_paid_cents,issue_date,project")
FIELD_LABELS = {"total_cents": "total", "vat_cents": "VAT", "subtotal_cents": "subtotal", "amount_cents": "amount",
                "document_number": "document number", "tax_id": "supplier's VAT number"}


def _refused(msg: str) -> Exception:
    from vula.commerce.books_review import Refused as R
    return R(msg)


def _client():
    from vula.commerce.service import _client as c
    return c()


def _rands(cents: Any) -> str:
    try:
        c = int(cents)
    except (TypeError, ValueError):
        return "—"
    return f"{'-' if c < 0 else ''}R{abs(c) / 100:,.2f}"


def _int(v: Any) -> Optional[int]:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _pages(make) -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages
    return _all_pages(make)


def _section(key: str, title: str, findings: List[Dict[str, Any]], headline: str,
             problem: bool = False) -> Dict[str, Any]:
    from vula.commerce.books_audit import _section as s
    return s(key, title, findings, headline, problem=problem)


def _sup(f: Dict[str, Any]) -> str:
    return str(f.get("supplier") or f.get("supplier_name") or f.get("vendor") or "").strip()


def _num(f: Dict[str, Any]) -> str:
    return str(f.get("document_number") or f.get("invoice_number") or f.get("invoice_no") or "").strip()


def _first_word(name: str) -> str:
    w = (name or "").lower().split()
    return re.sub(r"[^a-z0-9]", "", w[0]) if w else ""


def _alnum(s: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(s or "").lower())


def live(doc: Dict[str, Any]) -> bool:
    """A document that counts: not kept as a duplicate or set aside, not a later page of another."""
    f = doc.get("fields") or {}
    return doc.get("status") not in ("duplicate", "ignored") and not f.get("page_of")


def money_docs(tenant_id: str) -> List[Dict[str, Any]]:
    """Money documents that count — never a payment certificate filed as an invoice (20 Nov 2026: DIGG's
    Vizion certificates were "invoices" with Vizion as the supplier; vula/commerce/certificates.py)."""
    from vula.commerce.certificates import is_certificate
    return [d for d in _pages(lambda: _client().table("vula_filed_documents").select(DOC_COLS)
                              .eq("tenant_id", tenant_id).in_("category", list(MONEY_CATS)).order("id"))
            if live(d) and not is_certificate(d)]


def shown(doc: Dict[str, Any]) -> Dict[str, Any]:
    """A document as the review screen shows it (no file link: it's opened through the server)."""
    f = doc.get("fields") or {}
    return {"id": doc["id"], "category": doc.get("category"), "filename": doc.get("filename"),
            "supplier": _sup(f) or None, "number": _num(f) or None, "date": str(f.get("date") or "")[:10] or None,
            "total_cents": _int(f.get("total_cents") if f.get("total_cents") is not None else f.get("amount_cents")),
            "vat_cents": _int(f.get("vat_cents")), "subtotal_cents": _int(f.get("subtotal_cents")),
            "project": doc.get("project"), "bill_id": doc.get("commerce_invoice_id"),
            "customer": _customer(f) or None}


def _customer(f: Dict[str, Any]) -> str:
    """Who a document is addressed to, as read (an invoice's customer / bill-to)."""
    v = f.get("customer") or f.get("client") or f.get("bill_to") or f.get("customer_name") or ""
    if isinstance(v, dict):
        v = v.get("name") or ""
    return str(v).strip()


def own_names(tenant_id: str) -> Tuple[List[str], List[str]]:
    """Every name and number the business itself goes by (service.own_business_names). Fail-open: none."""
    from vula.commerce import service
    try:
        return asyncio.run(service.own_business_names(tenant_id))
    except Exception:  # noqa: BLE001
        return [], []


def is_own(f: Dict[str, Any], own: Tuple[List[str], List[str]]) -> bool:
    """The document was issued BY the business (its own invoice to a client, not a supplier's bill)."""
    from vula.commerce.service import is_own_name
    names, numbers = own
    if not names:
        return False
    sup = _sup(f)
    parts = [sup] + [p.strip(" ,-") for p in re.split(r"\bt\s*/\s*a\b|\btrading as\b", sup, flags=re.I)]
    tax = f.get("tax_id") or f.get("supplier_vat_number")
    return any(is_own_name(p, names, numbers, tax) for p in parts if p)


# ── the file itself ──────────────────────────────────────────────────────────

def pdf_text(data: bytes) -> str:
    """The text layer of a PDF (PyMuPDF only: no OCR, no model). '' for anything else."""
    if not data or not data[:5].startswith(b"%PDF"):
        return ""
    try:
        try:
            import pymupdf as fitz
        except ImportError:  # older PyMuPDF
            import fitz
        with fitz.open(stream=data, filetype="pdf") as pdf:
            return "\n".join(page.get_text() for page in pdf)
    except Exception as exc:  # noqa: BLE001
        log.debug("pdf text not read: %s", exc)
        return ""


def check_against(fields: Dict[str, Any], text: str) -> Dict[str, Any]:
    """What a document's own text doesn't back up: {text: bool, missing: [{field, cents|value}]}.
    A total must be printed as that amount (extraction_quality._amounts_in_text compares whole
    amounts, not digit strings); a document or VAT number must appear, ignoring spaces and punctuation."""
    from vula.commerce.extraction_quality import _amounts_in_text
    if len(re.sub(r"\s", "", text or "")) < 20:
        return {"text": False, "missing": []}
    on_page = _amounts_in_text(text)
    page = _alnum(text)
    missing: List[Dict[str, Any]] = []
    for k in MONEY_KEYS:
        c = _int(fields.get(k))
        if c and abs(c) >= 100 and abs(c) not in on_page:
            missing.append({"field": k, "cents": c})
    for k in ID_KEYS:
        v = _alnum(fields.get(k))
        if len(v) >= 3 and v not in page:
            missing.append({"field": k, "value": str(fields.get(k))[:40]})
    return {"text": True, "missing": missing}


def needs_file_check(doc: Dict[str, Any]) -> bool:
    fc = (doc.get("fields") or {}).get("_file_check") or {}
    return bool(doc.get("file_url")) and (not fc or fc.get("hash") != (doc.get("content_hash") or ""))


async def _check_one(tenant_id: str, doc: Dict[str, Any], sem: asyncio.Semaphore) -> Optional[Dict[str, Any]]:
    from vula.commerce.reread import _download
    async with sem:
        try:
            data = await _download(doc["file_url"])
        except Exception as exc:  # noqa: BLE001
            log.debug("file check: %s not downloaded: %s", doc.get("id"), exc)
            return None
    got = check_against(doc.get("fields") or {}, pdf_text(data))
    return {"hash": doc.get("content_hash") or "", "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **got}


def refresh_file_checks(tenant_id: str, docs: List[Dict[str, Any]], limit: int = FILE_CHECK_LIMIT) -> Dict[str, int]:
    """Read the PDFs not yet checked (or changed since) and keep what each contains on the document.
    Only fields._file_check is written (merged into the fields as they are now). Runs in a worker thread."""
    todo = [d for d in docs if needs_file_check(d)]
    batch = todo[:limit]

    async def go():
        sem = asyncio.Semaphore(FILE_CHECK_WORKERS)
        return await asyncio.gather(*(_check_one(tenant_id, d, sem) for d in batch))

    if not batch:
        return {"checked": 0, "left": 0, "failed": 0}
    results = asyncio.run(go())
    done = failed = 0
    db = _client()
    for d, res in zip(batch, results):
        if res is None:
            failed += 1
            continue
        try:
            cur = (db.table("vula_filed_documents").select("fields").eq("tenant_id", tenant_id).eq("id", d["id"])
                   .limit(1).execute().data or [])
            if not cur:
                continue
            fields = dict(cur[0].get("fields") or {})
            fields["_file_check"] = res
            db.table("vula_filed_documents").update({"fields": fields}).eq("tenant_id", tenant_id) \
                .eq("id", d["id"]).execute()
            d["fields"] = fields
            done += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("file check not kept for %s: %s", d.get("id"), exc)
            failed += 1
    return {"checked": done, "left": len(todo) - len(batch), "failed": failed}


# ── the checks ───────────────────────────────────────────────────────────────

def figure_problems(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Everything wrong with one document's figures, each {code, text, problem: bool}. A document the
    business confirmed as right (for the file it has now) has none."""
    from vula.commerce import data_checks, doc_quality
    from vula.commerce.extraction_quality import lines_short_cents, totals_disagree
    f = doc.get("fields") or {}
    conf = f.get("_confirmed") or {}
    if conf and conf.get("hash") == (doc.get("content_hash") or ""):
        return []
    out: List[Dict[str, Any]] = []
    fc = f.get("_file_check") or {}
    if fc.get("hash") == (doc.get("content_hash") or "") and fc.get("missing"):
        for m in fc["missing"]:
            what = FIELD_LABELS.get(m.get("field"), m.get("field"))
            shown_v = _rands(m["cents"]) if "cents" in m else m.get("value")
            out.append({"code": "not_in_file", "problem": True,
                        "text": f"The {what} read as {shown_v} isn't in the document itself."})
    if f.get("_unverified_figures"):
        out.append({"code": "unverified", "problem": True,
                    "text": "When it was read, some figures couldn't be found in the document."})
    if totals_disagree(f):
        out.append({"code": "totals", "problem": False,
                    "text": (f"Subtotal {_rands(f.get('subtotal_cents'))} + VAT {_rands(f.get('vat_cents'))} "
                             f"doesn't make the total {_rands(f.get('total_cents'))}.")})
    short = lines_short_cents(f)
    if short:
        out.append({"code": "lines_short", "problem": False,
                    "text": f"The lines read come to {_rands(short)} less than the total — a line was missed."})
    missing = doc_quality.missing_details(doc.get("category"), f)
    if missing:
        out.append({"code": "details", "problem": False,
                    "text": "No " + ", ".join(doc_quality.LABELS.get(k, k) for k in missing) + " was read."})
    if doc.get("category") in data_checks.DATED_CATEGORIES:
        try:
            filed = date.fromisoformat(str(doc.get("created_at") or "")[:10])
            why = data_checks.date_problem(f.get("date") or f.get("issue_date"), filed)
        except ValueError:
            why = None
        if why:
            out.append({"code": "date", "problem": False, "text": why})
    return out


def figure_findings(docs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    findings, counts = [], defaultdict(int)
    for d in docs:
        probs = figure_problems(d)
        if not probs:
            continue
        for p in probs:
            counts[p["code"]] += 1
        s = shown(d)
        findings.append({"kind": "doc_figures", "severity": "problem" if any(p["problem"] for p in probs) else "attention",
                         "rows": [d["id"]], "doc": s, "problems": [p["code"] for p in probs],
                         "text": f"{_label(s)}: " + " ".join(p["text"] for p in probs)})
    return findings, dict(counts)


def _label(s: Dict[str, Any]) -> str:
    bits = [s.get("category") or "Document"]
    if s.get("supplier"):
        bits.append(f"from {s['supplier']}")
    if s.get("number"):
        bits.append(str(s["number"]))
    if s.get("date"):
        bits.append(f"({s['date']})")
    return " ".join(bits)


def bills(tenant_id: str) -> List[Dict[str, Any]]:
    return _pages(lambda: _client().table("commerce_invoices").select(BILL_COLS).eq("tenant_id", tenant_id)
                  .eq("direction", "inbound").neq("status", "cancelled").order("id"))


def unbooked(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Invoices on file with a total and no bill — never in payables, VAT or the books."""
    out = []
    for d in docs:
        f = d.get("fields") or {}
        if (d.get("category") in BOOK_CATS and not d.get("commerce_invoice_id") and (_int(f.get("total_cents")) or 0) > 0
                and not f.get("_not_a_bill")):
            out.append(d)
    return out


def books_findings(tenant_id: str, docs: List[Dict[str, Any]], bill_rows: List[Dict[str, Any]],
                   own: Optional[Tuple[List[str], List[str]]] = None) -> List[Dict[str, Any]]:
    """19 Nov 2026 (DIGG's first run): of 43 invoices on file and not booked, 8 were DIGG's OWN invoices to
    clients (R836k), 2 more had the same amount as one of them with the client read as the supplier, and
    several were addressed to another business. None of those is a cost to book: own invoices are listed
    as `our_invoice` (never offered for booking), and one addressed to someone else says so."""
    from vula.commerce import data_checks
    from vula.commerce.service import is_own_name
    from vula.commerce.supplier_identity import same_name
    own = own if own is not None else own_names(tenant_id)
    out: List[Dict[str, Any]] = []
    pending = unbooked(docs)
    ours = [d for d in pending if is_own(d.get("fields") or {}, own)]
    our_totals = {abs(_int((d.get("fields") or {}).get("total_cents")) or 0): d for d in ours} | {
        abs(_int((d.get("fields") or {}).get("total_cents")) or 0): d for d in docs
        if d not in pending and is_own(d.get("fields") or {}, own)}
    for d in pending:
        s = shown(d)
        if d in ours:
            out.append({"kind": "our_invoice", "severity": "attention", "rows": [d["id"]], "doc": s, "cents": s["total_cents"],
                        "text": (f"{_label(s)} for {_rands(s['total_cents'])} is the business's own invoice"
                                 f"{' to ' + s['customer'] if s.get('customer') else ''} — a sale, not a bill. "
                                 "Check it's in Invoices; it is never booked as a cost here.")})
            continue
        twin = our_totals.get(abs(s["total_cents"] or 0))
        if twin is not None:
            t = shown(twin)
            out.append({"kind": "our_invoice", "severity": "attention", "rows": [d["id"], twin["id"]], "doc": s,
                        "cents": s["total_cents"], "twin": t,
                        "text": (f"{_label(s)} for {_rands(s['total_cents'])} has the same amount as the business's own "
                                 f"invoice {t.get('number') or ''} — most likely that invoice with the client read as the "
                                 "supplier. Not a bill.")})
            continue
        cust = s.get("customer")
        to_other = bool(cust and own[0] and not is_own_name(cust, own[0], own[1]))
        out.append({"kind": "not_booked", "severity": "attention" if to_other else "problem", "rows": [d["id"]],
                    "doc": {**s, "addressed_to_other": to_other}, "cents": s["total_cents"],
                    "text": (f"{_label(s)} for {_rands(s['total_cents'])} is on file but not in the books."
                             + (f" It's addressed to {cust}, not the business — book it only if the business paid it."
                                if to_other else ""))})
    for m in data_checks.bill_amount_mismatches(tenant_id, docs, bill_rows):
        doc = next((d for d in docs if str(d.get("commerce_invoice_id")) == str(m["id"])), None)
        out.append({"kind": "bill_amount", "severity": "problem", "rows": [m["id"]] + ([doc["id"]] if doc else []),
                    "bill": m["id"], "doc": shown(doc) if doc else None,
                    "text": f"Bill {m.get('bill') or ''} from {m.get('supplier') or 'a supplier'}: {m['detail']}"})
    by_id = {str(b["id"]): b for b in bill_rows}
    for d in docs:
        b = by_id.get(str(d.get("commerce_invoice_id") or ""))
        ds, bs = _sup(d.get("fields") or {}), (b or {}).get("supplier") or ""
        if b and ds and bs and not same_name(ds, bs) and _first_word(ds) != _first_word(bs):
            out.append({"kind": "bill_supplier", "severity": "attention", "rows": [b["id"], d["id"]], "bill": b["id"],
                        "doc": shown(d),
                        "text": f"Bill {b.get('invoice_number') or ''} is booked under {bs}, its document says {ds}."})
    return out


def _doc_key(f: Dict[str, Any]) -> Optional[Tuple[str, str, int]]:
    from vula.integrations.doc_filing import _doc_number
    sup, num, tot = _first_word(_sup(f)), _doc_number(_num(f)), _int(f.get("total_cents"))
    return (sup, num, abs(tot)) if sup and num and tot else None


def duplicate_findings(docs: List[Dict[str, Any]], bill_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from vula.integrations.doc_filing import _doc_number
    out: List[Dict[str, Any]] = []
    groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for d in docs:
        k = _doc_key(d.get("fields") or {})
        if k:
            groups[("doc",) + k].append(d)
        if d.get("content_hash"):
            groups[("hash", d["content_hash"])].append(d)
    seen: set = set()
    for k, ds in groups.items():
        ids = tuple(sorted(str(d["id"]) for d in ds))
        if len(ds) < 2 or ids in seen:
            continue
        seen.add(ids)
        first = sorted(ds, key=lambda d: str(d.get("created_at") or ""))
        s = shown(first[0])
        out.append({"kind": "doc_duplicate", "severity": "attention", "rows": [d["id"] for d in first],
                    "docs": [shown(d) for d in first], "keep": first[0]["id"],
                    "text": f"{_label(s)} for {_rands(s['total_cents'])} is on file {len(ds)} times."})
    bgroups: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = defaultdict(list)
    for b in bill_rows:
        if (b.get("doc_type") or "invoice") != "invoice":
            continue
        sup, num, tot = _first_word(b.get("supplier") or ""), _doc_number(b.get("invoice_number")), _int(b.get("total_cents"))
        if sup and num and tot:
            bgroups[(sup, num, abs(tot))].append(b)
    for (_, _, tot), bs in bgroups.items():
        if len(bs) < 2:
            continue
        bs = sorted(bs, key=lambda b: (str(b.get("issue_date") or ""), str(b["id"])))
        out.append({"kind": "bill_duplicate", "severity": "problem", "rows": [b["id"] for b in bs],
                    "bills": [_bill_shown(b) for b in bs], "keep": bs[0]["id"], "cents": tot * (len(bs) - 1),
                    "text": (f"Bill {bs[0].get('invoice_number')} from {bs[0].get('supplier')} for {_rands(tot)} is in the "
                             f"books {len(bs)} times — a cost counted {len(bs)} times.")})
    return out


def _bill_shown(b: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": b["id"], "number": b.get("invoice_number"), "supplier": b.get("supplier"),
            "date": str(b.get("issue_date") or "")[:10] or None, "status": b.get("status"),
            "total_cents": _int(b.get("total_cents")), "vat_cents": _int(b.get("vat_cents")),
            "paid_cents": _int(b.get("total_paid_cents")) or 0, "project": b.get("project")}


def supplier_findings(tenant_id: str, docs: List[Dict[str, Any]], bill_rows: List[Dict[str, Any]],
                      registered: Optional[bool]) -> List[Dict[str, Any]]:
    from vula.commerce import service, supplier_identity as si
    from vula.commerce.extraction_quality import valid_sa_vat
    out: List[Dict[str, Any]] = []
    try:
        sups = _pages(lambda: _client().table("commerce_suppliers").select("*").eq("tenant_id", tenant_id).order("id"))
    except Exception as exc:  # noqa: BLE001
        log.debug("suppliers not read: %s", exc)
        sups = []
    try:
        own_names, own_numbers = asyncio.run(service.own_business_names(tenant_id))
    except Exception:  # noqa: BLE001
        own_names, own_numbers = [], []
    others, own_acc = si.split_own(sups, own_names, own_numbers)
    for g in si.find_duplicates(others, own_acc):
        names = [g["keep"].get("name")] + [d.get("name") for d in g["duplicates"]]
        out.append({"kind": "supplier_duplicate", "severity": "attention",
                    "rows": [g["keep"]["id"]] + [d["id"] for d in g["duplicates"]], "keep": g["keep"]["id"],
                    "suppliers": [{"id": s["id"], "name": s.get("name"), "tax_id": s.get("tax_id")}
                                  for s in [g["keep"]] + g["duplicates"]],
                    "why": g.get("why"), "text": "One supplier on file as " + " / ".join(n for n in names if n) + "."})
    by_vat: Dict[str, set] = defaultdict(set)
    vat_docs: Dict[str, List[str]] = defaultdict(list)
    for d in docs:
        f = d.get("fields") or {}
        v, s = re.sub(r"\D", "", str(f.get("tax_id") or "")), _sup(f)
        if valid_sa_vat(v) and s:
            by_vat[v].add(s)
            vat_docs[v].append(d["id"])
    for v, names in by_vat.items():
        if len({_first_word(n) for n in names}) > 1:
            out.append({"kind": "vat_number_names", "severity": "attention", "rows": vat_docs[v][:50],
                        "vat_number": v, "names": sorted(names),
                        "text": f"VAT number {v} is on documents from {' / '.join(sorted(names))} — one supplier, or a misread."})
    for s in others:
        t = str(s.get("tax_id") or "").strip()
        if t and not valid_sa_vat(t):
            out.append({"kind": "vat_number_invalid", "severity": "attention", "rows": [s["id"]],
                        "supplier": {"id": s["id"], "name": s.get("name"), "tax_id": t},
                        "text": f"{s.get('name')}'s VAT number {t} isn't a valid South African one (10 digits starting with 4)."})
    if registered:
        valid = {str(s["id"]) for s in sups if valid_sa_vat(s.get("tax_id"))}
        claimed = [b for b in bill_rows if (_int(b.get("vat_cents")) or 0) > 0 and str(b.get("supplier_id") or "") not in valid]
        if claimed:
            out.append({"kind": "vat_no_number", "severity": "problem", "rows": [b["id"] for b in claimed][:200],
                        "cents": sum(_int(b.get("vat_cents")) or 0 for b in claimed),
                        "text": (f"{len(claimed)} bills claim {_rands(sum(_int(b.get('vat_cents')) or 0 for b in claimed))} "
                                 "VAT with no supplier VAT number on file — no input VAT without a valid tax invoice.")})
    return out


def price_vat_findings(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        rows = _pages(lambda: _client().table("vula_price_observations").select("id,doc_id,source_kind,vat_basis")
                      .eq("tenant_id", tenant_id).eq("vat_basis", "unknown").order("id"))
    except Exception as exc:  # noqa: BLE001
        log.debug("price VAT basis not read: %s", exc)
        return []
    by_kind: Dict[str, int] = defaultdict(int)
    by_doc: Dict[str, int] = defaultdict(int)
    for r in rows:
        by_kind[r.get("source_kind") or "other"] += 1
        if r.get("doc_id") and not str(r["doc_id"]).startswith("worker:"):
            by_doc[str(r["doc_id"])] += 1
    if not rows:
        return []
    docs = sorted(by_doc.items(), key=lambda kv: -kv[1])
    return [{"kind": "price_vat_unknown", "severity": "attention", "count": len(rows), "by_kind": dict(by_kind),
             "rows": [d for d, _ in docs[:200]], "documents": len(docs),
             "text": (f"{len(rows)} learned prices from {len(docs)} documents don't say whether VAT was in them ("
                      + ", ".join(f"{n} from {k}s" for k, n in sorted(by_kind.items(), key=lambda kv: -kv[1]))
                      + ") — estimates mark them; say per document.")}]


def price_vat_documents(tenant_id: str, ids: List[str], limit: int = 50) -> List[Dict[str, Any]]:
    """The documents behind prices with no VAT basis, as the review screen names them (largest first)."""
    ids = [str(i) for i in ids[:limit]]
    rows: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(ids), 150):
        try:
            for r in (_client().table("vula_filed_documents").select("id,category,filename,fields")
                      .eq("tenant_id", tenant_id).in_("id", ids[i:i + 150]).execute().data or []):
                rows[str(r["id"])] = r
        except Exception as exc:  # noqa: BLE001
            log.debug("price VAT documents not read: %s", exc)
    out = []
    for i in ids:
        r = rows.get(i)
        if r:
            f = r.get("fields") or {}
            out.append({"id": r["id"], "category": r.get("category"), "filename": r.get("filename"),
                        "supplier": _sup(f) or None, "date": str(f.get("date") or "")[:10] or None})
    return out


def access_findings(tenant_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from vula.api.tenant_auth import ACCOUNTANT, _FULL_TEAM_ROLES
    try:
        team = (_client().table("vula_team_members").select("id,name,role,access,active,expires_on")
                .eq("tenant_id", tenant_id).limit(500).execute().data or [])
    except Exception:  # noqa: BLE001
        team = []
    try:
        logins = (_client().table("vula_tenant_users").select("role").eq("tenant_id", tenant_id)
                  .limit(500).execute().data or [])
    except Exception:  # noqa: BLE001
        logins = []
    roles: Dict[str, int] = defaultdict(int)
    for lg in logins:
        roles[lg.get("role") or "staff"] += 1
    people = []
    for m in team:
        role = (m.get("role") or "").lower()
        full = (role in _FULL_TEAM_ROLES) or (role != ACCOUNTANT and not (m.get("access") or []))
        people.append({"name": m.get("name"), "role": role or "staff", "active": m.get("active") is not False,
                       "full": bool(full and m.get("active") is not False and role != ACCOUNTANT),
                       "why": ("owner or manager" if role in _FULL_TEAM_ROLES else
                               "no sections chosen, so everything" if full and role != ACCOUNTANT else None),
                       "expires_on": m.get("expires_on") if role == ACCOUNTANT else None})
    out = []
    loose = [p for p in people if p["full"] and p["why"] and p["why"].startswith("no sections")]
    if loose:
        out.append({"kind": "full_by_default", "severity": "attention", "rows": [],
                    "text": (f"{len(loose)} team member{'s' if len(loose) != 1 else ''} ("
                             + ", ".join(p['name'] or '?' for p in loose)
                             + ") can see and change everything, because no sections were chosen for them.")})
    return out, {"people": people, "logins": dict(roles)}


# ── into the books check ─────────────────────────────────────────────────────

def sections(tenant_id: str, check_files: bool = True) -> List[Dict[str, Any]]:
    """The document sections for books_audit.run, in order."""
    from vula.commerce.accounting import is_vat_registered
    docs = money_docs(tenant_id)
    files = refresh_file_checks(tenant_id, docs) if check_files else {"checked": 0, "left": 0, "failed": 0}
    unchecked = sum(1 for d in docs if needs_file_check(d))
    bill_rows = bills(tenant_id)
    try:
        registered = is_vat_registered(tenant_id)
    except Exception:  # noqa: BLE001
        registered = None
    out = []

    f_fig, counts = figure_findings(docs)
    head = (f"{len(f_fig)} of {len(docs)} money documents need a look." if f_fig
            else f"All {len(docs)} money documents add up.")
    kept = [((d.get("fields") or {}).get("_file_check") or {}) for d in docs if d.get("file_url") and not needs_file_check(d)]
    with_text = sum(1 for fc in kept if fc.get("text"))
    no_text = len(kept) - with_text
    no_file = sum(1 for d in docs if not d.get("file_url"))
    head += (f" {with_text} checked against their own file"
             + (f", {no_text} have no text to check (a photo or scan)" if no_text else "")
             + (f", {no_file} have no file kept" if no_file else "")
             + (f", {unchecked} not checked yet — run the check again" if unchecked else "") + ".")
    sec = _section("doc_figures", "Documents whose figures don't match the file", f_fig, head,
                   problem=any(f["severity"] == "problem" for f in f_fig))
    sec["counts"] = counts
    sec["files"] = {**files, "unchecked": unchecked, "with_text": with_text, "no_text": no_text, "no_file": no_file}
    out.append(sec)

    f_books = books_findings(tenant_id, docs, bill_rows)
    nb = [f for f in f_books if f["kind"] == "not_booked"]
    ours = [f for f in f_books if f["kind"] == "our_invoice"]
    diff = [f for f in f_books if f["kind"] in ("bill_amount", "bill_supplier")]
    out.append(_section("doc_books", "Documents and the books disagree", f_books,
                        "Every invoice on file is in the books at its own amount." if not f_books else
                        f"{len(nb)} supplier invoices on file aren't in the books"
                        f" ({_rands(sum(f.get('cents') or 0 for f in nb))}); "
                        f"{len(ours)} are the business's own invoices ({_rands(sum(f.get('cents') or 0 for f in ours))}), "
                        f"not bills; {len(diff)} bills differ from their document.",
                        problem=any(f["severity"] == "problem" for f in f_books)))

    f_dup = duplicate_findings(docs, bill_rows)
    db_ = [f for f in f_dup if f["kind"] == "bill_duplicate"]
    out.append(_section("doc_duplicates", "The same document or bill twice", f_dup,
                        "No document or bill is counted twice." if not f_dup else
                        f"{len(f_dup) - len(db_)} documents on file more than once; {len(db_)} bills in the books "
                        f"more than once ({_rands(sum(f.get('cents') or 0 for f in db_))} counted twice).",
                        problem=bool(db_)))

    f_sup = supplier_findings(tenant_id, docs, bill_rows, registered)
    out.append(_section("suppliers", "Suppliers", f_sup,
                        "Each supplier is on file once, with a valid VAT number where one is given." if not f_sup else
                        f"{len(f_sup)} things to tidy in the suppliers.",
                        problem=any(f["severity"] == "problem" for f in f_sup)))

    f_pv = price_vat_findings(tenant_id)
    out.append(_section("price_vat", "Prices with no VAT basis", f_pv,
                        "Every learned price says whether VAT was in it." if not f_pv else f_pv[0]["text"]))

    try:     # payment certificates ↔ the business's invoices ↔ money received (20 Nov 2026)
        from vula.commerce import certificates
        m = certificates.match(tenant_id)
        f_cert = certificates.findings(m)
        if m["certificates"] or f_cert:
            open_ = [f for f in f_cert if f["severity"] != "info"]
            out.append(_section("certificates", "Payment certificates, invoices and money received", f_cert,
                                f"{len(m['certificates'])} payment certificates on file"
                                f" ({', '.join(str(n) for n in certificates.numbers_in(m['certificates']))}); "
                                + ("each has its invoice and its payment." if not open_ else
                                   f"{len(open_)} things to put right — "
                                   f"{len(m['unmatched_receipts'])} payments name a certificate that isn't on file.")))
    except Exception as exc:  # noqa: BLE001
        log.warning("certificate check failed for %s: %s", tenant_id, exc)

    try:     # suppliers' statements against the books (20 Nov 2026)
        from vula.commerce import supplier_statements as ss
        if check_files:
            ss.read_statements(tenant_id)
        c = ss.check(tenant_id)
        f_ss = ss.findings(c)
        if c["suppliers"] or f_ss:
            off = [s for s in c["suppliers"] if s["missing"] or s["difference_cents"] or s["payments_not_found"]]
            out.append(_section("supplier_statements", "Suppliers' statements against the books", f_ss,
                                f"{len(c['suppliers'])} suppliers' statements read line by line; "
                                + ("each agrees with the books." if not off else
                                   f"{len(off)} don't agree — "
                                   f"{sum(len(s['missing']) for s in off)} invoices on them aren't in Vula, "
                                   f"{_rands(sum(s['difference_cents'] for s in off))} more owed than the books show."),
                                problem=any(f["severity"] == "problem" for f in f_ss)))
    except Exception as exc:  # noqa: BLE001
        log.warning("supplier statement check failed for %s: %s", tenant_id, exc)

    f_acc, people = access_findings(tenant_id)
    full = [p for p in people["people"] if p["full"]]
    sec = _section("access", "Who can see and change the books", f_acc,
                   f"{len(full)} people can see and change everything: "
                   + (", ".join(f"{p['name'] or '?'} ({p['why']})" for p in full) or "only the owner's login") + ".")
    sec["people"] = people
    out.append(sec)
    return out


# ── the worklist (books_review.review), as things are now ────────────────────

def worklist(tenant_id: str) -> Dict[str, Any]:
    """The document groups for the review screen, re-read now (no file is downloaded: the file check
    kept on each document is used), so a fixed item drops off without running the check again."""
    docs = money_docs(tenant_id)
    bill_rows = bills(tenant_id)
    figures = [{**f["doc"], "problems": f["problems"], "text": f["text"], "severity": f["severity"]}
               for f in figure_findings(docs)[0]]
    books = books_findings(tenant_id, docs, bill_rows)
    dups = duplicate_findings(docs, bill_rows)
    try:
        from vula.commerce.accounting import is_vat_registered
        registered = is_vat_registered(tenant_id)
    except Exception:  # noqa: BLE001
        registered = None
    try:
        sup = supplier_findings(tenant_id, docs, bill_rows, registered)
    except Exception as exc:  # noqa: BLE001
        log.debug("supplier checks skipped: %s", exc)
        sup = []
    by_id = {str(b["id"]): b for b in bill_rows}
    mismatch = []
    for f in books:
        if f["kind"] in ("bill_amount", "bill_supplier"):
            b = by_id.get(str(f["bill"]))
            mismatch.append({"kind": f["kind"], "text": f["text"], "bill": _bill_shown(b) if b else None,
                             "doc": f.get("doc")})
    pv = price_vat_findings(tenant_id)
    price_vat = None
    if pv:
        price_vat = {k: pv[0][k] for k in ("count", "by_kind", "documents", "text")}
        price_vat["docs"] = price_vat_documents(tenant_id, pv[0].get("rows") or [])
    _, people = access_findings(tenant_id)
    try:
        from vula.commerce import certificates
        certs = certificates.match(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("certificate worklist failed for %s: %s", tenant_id, exc)
        certs = {"certificates": [], "unmatched_receipts": []}
    try:
        sales_on_file = sales_not_in_invoices(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("own-invoice worklist failed for %s: %s", tenant_id, exc)
        sales_on_file = []
    try:
        from vula.commerce import supplier_statements as ss
        sup_st = [{**x, "request": ss.request_text(x)} for x in ss.check(tenant_id)["suppliers"]
                  if x["missing"] or x["difference_cents"] or x["payments_not_found"]]
    except Exception as exc:  # noqa: BLE001
        log.warning("supplier statement worklist failed for %s: %s", tenant_id, exc)
        sup_st = []
    return {"certificates": certs, "sales_not_in_invoices": sales_on_file, "supplier_statements": sup_st,
            "doc_figures": figures,
            "not_booked": [{**f["doc"], "text": f["text"]} for f in books if f["kind"] == "not_booked"],
            "our_invoices": [{**f["doc"], "text": f["text"], "twin": f.get("twin")} for f in books
                             if f["kind"] == "our_invoice"],
            "bill_mismatch": mismatch,
            "doc_duplicates": [{"docs": f["docs"], "keep": f["keep"], "text": f["text"]}
                               for f in dups if f["kind"] == "doc_duplicate"],
            "bill_duplicates": [{"bills": f["bills"], "keep": f["keep"], "text": f["text"], "extra_cents": f["cents"]}
                                for f in dups if f["kind"] == "bill_duplicate"],
            "suppliers": [{k: v for k, v in f.items() if k not in ("severity",)} for f in sup],
            "price_vat": price_vat,
            "access": people}


# ── the business's own invoices on file but not in Invoices (20 Nov 2026) ─────
# DIGG's HPC invoices 02–04 (R625,272.65) were marked "a sale, in Invoices" — but were never added there,
# so the money received for them had no invoice behind it for the accountant.

SALE_DAYS = (-10, 120)            # money received from 10 days before the invoice to 120 after


def _same_number(a: Any, b: Any) -> bool:
    return bool(_alnum(a)) and _alnum(a) == _alnum(b)


def sales_not_in_invoices(tenant_id: str) -> List[Dict[str, Any]]:
    """Each own invoice on file that isn't in Invoices, with what in Invoices may already be it (same number,
    or the same amount — or that amount with 15% VAT added — within 30 days) and the money received that may
    be its payment (same amount to the cent or one cent, untied)."""
    from vula.commerce import certificates
    from vula.commerce.books_review import _shown as line_shown
    every = certificates.own_invoices(tenant_id)
    docs = [d for d in every if not d.get("commerce_invoice_id")]
    sales = certificates._sales(tenant_id)
    by_id = {str(i["id"]): i for i in sales}
    differs = []
    for d in every:     # in Invoices, but at a different amount from the invoice itself (HPC14: 15% VAT added)
        i = by_id.get(str(d.get("commerce_invoice_id") or ""))
        t = abs(_int((d.get("fields") or {}).get("total_cents")) or 0)
        if i and abs(int(i.get("total_cents") or 0) - t) > certificates.TOLERANCE:
            s = shown(d)
            differs.append({**s, "differs": {"id": i["id"], "number": i.get("invoice_number"),
                                             "cents": int(i.get("total_cents") or 0), "status": i.get("status")},
                            "in_invoices_as": [], "receipts": [],
                            "text": (f"{_label(s)} says {_rands(t)}, but it's in Invoices as {i.get('invoice_number')} at "
                                     f"{_rands(i.get('total_cents'))}"
                                     + (" — 15% VAT added" if abs(int(i.get('total_cents') or 0) - round(t * 1.15)) <= 1 else "")
                                     + ". Correct the invoice in Invoices to match.")})
    if not docs:
        return differs
    linked = {str(d.get("commerce_invoice_id")) for d in certificates._documents(tenant_id) if d.get("commerce_invoice_id")}
    receipts = [r for r in certificates._receipts(tenant_id) if not line_shown(r)["matched"]]
    out = []
    for d in docs:
        s = shown(d)
        total, day = abs(s["total_cents"] or 0), certificates._day(s["date"])
        maybe = []
        for i in sales:
            g = certificates._gap(day, certificates._day(i.get("issue_date")))
            near = g is None or abs(g) <= 30
            t = int(i.get("total_cents") or 0)
            if _same_number(i.get("invoice_number"), s["number"]):
                why = "same number"
            elif near and abs(t - total) <= certificates.TOLERANCE:
                why = "same amount"
            elif near and abs(t - round(total * 1.15)) <= 1:
                why = "this amount with 15% VAT added"
            else:
                continue
            maybe.append({"id": i["id"], "number": i.get("invoice_number"), "cents": t, "status": i.get("status"),
                          "date": str(i.get("issue_date") or "")[:10], "why": why,
                          "linked_to_a_document": str(i["id"]) in linked})
        cands = []
        for r in receipts:
            g = certificates._gap(day, certificates._day(r.get("txn_date")))
            if abs(int(r.get("amount_cents") or 0) - total) <= certificates.TOLERANCE and (
                    g is None or SALE_DAYS[0] <= g <= SALE_DAYS[1]):
                cands.append((abs(g or 0), line_shown(r)))
        cands.sort(key=lambda x: x[0])
        out.append({**s, "in_invoices_as": maybe, "receipts": [c for _, c in cands[:3]],
                    "text": (f"{_label(s)} for {_rands(s['total_cents'])}"
                             f"{' to ' + s['customer'] if s.get('customer') else ''} is the business's own invoice, "
                             "on file but not in Invoices.")})
    return sorted(out + differs, key=lambda x: (x.get("date") or "", x.get("number") or ""))


def link_invoice(tenant_id: str, doc_id: str, invoice_id: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """The business's own invoice on file IS that invoice already in Invoices (same total): tie them, so it
    isn't added twice. Nothing about the money changes."""
    from vula.commerce import certificates
    doc = _doc(tenant_id, doc_id)
    if doc.get("commerce_invoice_id"):
        raise _refused("That document is already tied to an invoice.")
    inv = next((i for i in certificates._sales(tenant_id) if str(i["id"]) == str(invoice_id)), None)
    if not inv:
        raise _refused("That invoice isn't in this business's Invoices.")
    total = abs(_int((doc.get("fields") or {}).get("total_cents")) or 0)
    if abs(int(inv.get("total_cents") or 0) - total) > certificates.TOLERANCE:
        raise _refused(f"{inv.get('invoice_number')} is {_rands(inv.get('total_cents'))} and the document says "
                       f"{_rands(total)} — correct the invoice first.")
    taken = (_client().table("vula_filed_documents").select("id").eq("tenant_id", tenant_id)
             .eq("commerce_invoice_id", invoice_id).limit(1).execute().data or [])
    if taken:
        raise _refused(f"{inv.get('invoice_number')} is already tied to another document.")
    back = _save_fields(tenant_id, doc_id, dict(doc.get("fields") or {}), commerce_invoice_id=invoice_id)
    _audit(tenant_id, actor, "own_invoice_tied", doc_id=doc_id, invoice_id=invoice_id)
    return {"ok": str(back.get("commerce_invoice_id")) == str(invoice_id), "invoice": inv.get("invoice_number")}


def add_to_invoices(tenant_id: str, doc_id: str, txn_id: Optional[str] = None,
                    actor: Optional[dict] = None) -> Dict[str, Any]:
    """Put the business's own invoice on file into Invoices, at the figures on the document, under its own
    number where that's free; with `txn_id`, that bank line (money received) is recorded as its payment on
    the bank date and tied to it — so the sale isn't counted twice and the accountant sees an invoice behind it.
    Refused while something in Invoices may already be it (fix or tie that first)."""
    from vula.commerce import certificates, service
    from vula.commerce.books_review import _lines, _live, _resync
    from vula.commerce.books_review import _shown as line_shown
    doc = _doc(tenant_id, doc_id)
    f = doc.get("fields") or {}
    if not live(doc) or doc.get("category") != "Invoice" or certificates.is_certificate(doc):
        raise _refused("Only the business's own invoices on file can be added to Invoices.")
    if not is_own(f, own_names(tenant_id)):
        raise _refused("That invoice isn't the business's own — a supplier's invoice is booked as a bill.")
    if doc.get("commerce_invoice_id"):
        raise _refused("That invoice is already in Invoices.")
    s = shown(doc)
    total = abs(s["total_cents"] or 0)
    if not total:
        raise _refused("The invoice has no total — correct its figures first.")
    if (f.get("_unverified_figures") or (f.get("_file_check") or {}).get("missing")) and not f.get("_confirmed"):
        raise _refused("Its figures weren't found in the file — correct or confirm them first.")
    pending = next((x for x in sales_not_in_invoices(tenant_id) if str(x["id"]) == str(doc_id)), None)
    clash = [m for m in (pending or {}).get("in_invoices_as") or [] if not m["linked_to_a_document"]]
    if clash:
        m = clash[0]
        raise _refused(f"{m['number']} in Invoices ({_rands(m['cents'])}, {m['why']}) may already be this invoice — "
                       "fix or cancel it first, so the sale isn't counted twice.")
    line = None
    if txn_id:
        rows = _lines(tenant_id, [txn_id])
        if not rows or not _live(rows[0]) or rows[0].get("direction") != "in":
            raise _refused("That bank line isn't money received that's in the books.")
        line = rows[0]
        if line_shown(line)["matched"]:
            raise _refused("That bank line is already tied to something.")
        if abs(int(line.get("amount_cents") or 0) - total) > certificates.TOLERANCE:
            raise _refused("The money received isn't the invoice's total.")
    created = asyncio.run(service.create_invoice(tenant_id, {
        "doc_type": "invoice", "customer_name": s.get("customer") or "Client", "status": "sent",
        "issue_date": s.get("date"), "due_date": str(f.get("due_date") or "")[:10] or None, "project": doc.get("project"),
        "prices_include_vat": True,
        "notes": f"The business's own invoice {s.get('number') or ''} on file ({doc.get('filename') or ''}).".strip(),
        "line_items": [{"description": f"As invoiced — {s.get('number') or doc.get('filename') or 'invoice on file'}",
                        "quantity": 1, "unit_price_cents": total}]}))
    if int(created.get("total_cents") or 0) != total:
        _client().table("commerce_invoices").update({"status": "cancelled", "cancel_reason": "total didn't match the file"}) \
            .eq("tenant_id", tenant_id).eq("id", created["id"]).execute()
        raise _refused("Invoices would have shown a different total from the document — nothing was added.")
    number = s.get("number")
    if number and not any(_same_number(x.get("invoice_number"), number) for x in certificates._sales(tenant_id)
                          if str(x["id"]) != str(created["id"])):
        try:
            _client().table("commerce_invoices").update({"invoice_number": number}).eq("tenant_id", tenant_id) \
                .eq("id", created["id"]).execute()
        except Exception as exc:  # noqa: BLE001 — Vula's own number stays
            log.debug("own invoice number not kept: %s", exc)
    back = _save_fields(tenant_id, doc_id, dict(f), commerce_invoice_id=created["id"])
    paid = None
    if line:
        on = str(line.get("txn_date"))[:10]
        asyncio.run(service.record_invoice_payment(tenant_id, created["id"], int(line["amount_cents"]),
                                                   payment_method="eft", note=f"Bank: {line.get('description') or ''}"[:200],
                                                   paid_at=on))
        _client().table("commerce_bank_transactions").update({"matched_invoice_id": created["id"], "match_status": "matched"}) \
            .eq("tenant_id", tenant_id).eq("id", txn_id).execute()
        _resync(tenant_id, [txn_id])
        paid = on
    inv = (_client().table("commerce_invoices").select("id,invoice_number,status,total_cents")
           .eq("tenant_id", tenant_id).eq("id", created["id"]).limit(1).execute().data or [{}])[0]
    ok = (str(back.get("commerce_invoice_id")) == str(created["id"]) and int(inv.get("total_cents") or 0) == total
          and (not line or inv.get("status") == "paid"))
    _audit(tenant_id, actor, "own_invoice_added", doc_id=doc_id, invoice_id=created["id"], cents=total, txn_id=txn_id)
    return {"ok": ok, "invoice": inv, "paid_on": paid}


# ── fixes (owner or manager; books_review's routes) ──────────────────────────

def _doc(tenant_id: str, doc_id: str) -> Dict[str, Any]:
    rows = (_client().table("vula_filed_documents").select(DOC_COLS).eq("tenant_id", tenant_id).eq("id", doc_id)
            .limit(1).execute().data or [])
    if not rows:
        raise _refused("That document isn't there.")
    return rows[0]


def _save_fields(tenant_id: str, doc_id: str, fields: Dict[str, Any], **cols: Any) -> Dict[str, Any]:
    _client().table("vula_filed_documents").update({"fields": fields, **cols}).eq("tenant_id", tenant_id) \
        .eq("id", doc_id).execute()
    return _doc(tenant_id, doc_id)


def _who(actor: Optional[dict]) -> str:
    return (actor or {}).get("email") or "owner"


def _audit(tenant_id: str, actor: Optional[dict], action: str, **detail: Any) -> None:
    from vula.commerce.books_review import _audit as a
    a(tenant_id, actor, action, **detail)


_EDITABLE = {"total_cents": "money", "vat_cents": "money", "subtotal_cents": "money", "amount_cents": "money",
             "document_number": "text", "tax_id": "text", "supplier": "text", "date": "date"}


def correct_figures(tenant_id: str, doc_id: str, changes: Dict[str, Any], reason: str,
                    actor: Optional[dict] = None) -> Dict[str, Any]:
    """Correct what was read off a document. The old values are kept in fields._corrected; the flags that
    the read couldn't find its figures are cleared, and the file check runs again on the next check.
    The bill booked from it is NOT changed here: if it now differs, the review lists it (match-document)."""
    from vula.commerce.books_review import _reason
    why = _reason(reason)
    doc = _doc(tenant_id, doc_id)
    f = dict(doc.get("fields") or {})
    done = []
    for k, v in (changes or {}).items():
        kind = _EDITABLE.get(k)
        if not kind or v is None or v == "":
            continue
        if kind == "money":
            c = _int(v)
            if c is None or abs(c) > 10_000_000_000:
                raise _refused(f"The {FIELD_LABELS.get(k, k)} must be an amount in cents.")
            new: Any = c
        elif kind == "date":
            try:
                new = date.fromisoformat(str(v)[:10]).isoformat()
            except ValueError:
                raise _refused("Give the date as YYYY-MM-DD.")
        else:
            new = str(v).strip()[:120]
            if k == "tax_id":
                from vula.commerce.extraction_quality import valid_sa_vat
                if not valid_sa_vat(new):
                    raise _refused("A South African VAT number is 10 digits starting with 4.")
        if f.get(k) != new:
            done.append({"field": k, "old": f.get(k), "new": new})
            f[k] = new
    if not done:
        raise _refused("Nothing to change — give the figures as they are on the document.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    f["_corrected"] = list(f.get("_corrected") or []) + [{**c, "by": _who(actor), "at": now, "reason": why}
                                                          for c in done]
    for k in ("_unverified_figures", "_file_check", "_confirmed"):
        f.pop(k, None)
    back = _save_fields(tenant_id, doc_id, f)
    bf = back.get("fields") or {}
    ok = all(bf.get(c["field"]) == c["new"] for c in done)
    _audit(tenant_id, actor, "document_figures_corrected", doc_id=doc_id, reason=why,
           changes=[{k: c[k] for k in ("field", "old", "new")} for c in done])
    out = {"ok": ok, "changed": [{k: c[k] for k in ("field", "old", "new")} for c in done], "doc": shown(back)}
    b = _linked_bill(tenant_id, back)
    if b and _int(b.get("total_cents")) != _int(bf.get("total_cents")) and bf.get("total_cents") is not None:
        out["bill_differs"] = _bill_shown(b)
    return out


def confirm(tenant_id: str, doc_id: str, note: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """The figures are right as read (a scan with no text, a total printed in a way the check can't see).
    Kept for this file only: a new copy of the file is checked again."""
    doc = _doc(tenant_id, doc_id)
    f = dict(doc.get("fields") or {})
    f["_confirmed"] = {"by": _who(actor), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "hash": doc.get("content_hash") or "", "note": (note or "").strip()[:300] or None}
    f.pop("_unverified_figures", None)
    back = _save_fields(tenant_id, doc_id, f)
    _audit(tenant_id, actor, "document_figures_confirmed", doc_id=doc_id, note=(note or "")[:200])
    return {"ok": bool((back.get("fields") or {}).get("_confirmed")), "doc": shown(back)}


def _linked_bill(tenant_id: str, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not doc.get("commerce_invoice_id"):
        return None
    rows = (_client().table("commerce_invoices").select(BILL_COLS).eq("tenant_id", tenant_id)
            .eq("id", doc["commerce_invoice_id"]).limit(1).execute().data or [])
    return rows[0] if rows else None


async def book(tenant_id: str, doc_id: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """Put one invoice on file into the books, through the same commit path every intake uses."""
    from vula.api.whatsapp import _CATEGORY_TO_DOC_TYPE
    from vula.commerce import service
    doc = _doc(tenant_id, doc_id)
    f = dict(doc.get("fields") or {})
    if doc.get("category") not in BOOK_CATS or not live(doc):
        raise _refused("Only a supplier invoice on file can be booked here.")
    if doc.get("commerce_invoice_id"):
        raise _refused("That document is already in the books.")
    names, numbers = await service.own_business_names(tenant_id)
    if is_own(f, (names, numbers)):
        raise _refused("That's the business's own invoice to a client — a sale, not a bill. It isn't booked as a cost.")
    if any(p["problem"] for p in figure_problems(doc)):
        raise _refused("Its figures aren't confirmed yet — correct them or say they're right first.")
    if not (_int(f.get("total_cents")) or 0) > 0:
        raise _refused("It has no total to book.")
    f.setdefault("doc_type", _CATEGORY_TO_DOC_TYPE.get(doc["category"], "invoice"))
    res = await service.commit_inbound_document(tenant_id, f, auto_commit=True, source=doc.get("source") or "books_review",
                                                filed_document_id=doc_id, project=doc.get("project"), notify=False)
    back = _doc(tenant_id, doc_id)
    ok = bool(back.get("commerce_invoice_id")) and res.get("committed") is not False
    _audit(tenant_id, actor, "document_booked", doc_id=doc_id, bill_id=back.get("commerce_invoice_id"),
           cents=_int(f.get("total_cents")))
    if not ok:
        raise _refused(res.get("message") or res.get("error") or "It couldn't be booked — the supplier may need choosing.")
    return {"ok": True, "bill_id": back.get("commerce_invoice_id"), "doc": shown(back)}


def not_a_bill(tenant_id: str, doc_id: str, reason: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """An invoice on file that isn't a cost to book here (the business's own invoice, a copy of something
    booked another way, a pro-forma)."""
    from vula.commerce.books_review import _reason
    why = _reason(reason)
    doc = _doc(tenant_id, doc_id)
    f = dict(doc.get("fields") or {})
    f["_not_a_bill"] = {"by": _who(actor), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": why}
    back = _save_fields(tenant_id, doc_id, f)
    _audit(tenant_id, actor, "document_not_a_bill", doc_id=doc_id, reason=why)
    return {"ok": bool((back.get("fields") or {}).get("_not_a_bill")), "doc": shown(back)}


def mark_duplicate(tenant_id: str, doc_id: str, of_id: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """This document is a second copy of another one on file (kept, never counted)."""
    if str(doc_id) == str(of_id):
        raise _refused("Choose the other copy.")
    doc, first = _doc(tenant_id, doc_id), _doc(tenant_id, of_id)
    if not live(first):
        raise _refused("The copy to keep is itself set aside.")
    if doc.get("commerce_invoice_id") and doc.get("commerce_invoice_id") != first.get("commerce_invoice_id"):
        raise _refused("This copy has its own bill in the books — cancel that bill as a duplicate first.")
    f = dict(doc.get("fields") or {})
    f["_duplicate_of"] = {"id": first["id"], "filename": first.get("filename"),
                          "filed": str(first.get("created_at") or "")[:10], "by": _who(actor)}
    back = _save_fields(tenant_id, doc_id, f, status="duplicate")
    _audit(tenant_id, actor, "document_marked_duplicate", doc_id=doc_id, of=of_id)
    return {"ok": back.get("status") == "duplicate"}


def _bill(tenant_id: str, bill_id: str) -> Dict[str, Any]:
    rows = (_client().table("commerce_invoices").select(BILL_COLS).eq("tenant_id", tenant_id).eq("id", bill_id)
            .limit(1).execute().data or [])
    if not rows or rows[0].get("direction") != "inbound":
        raise _refused("That supplier bill isn't there.")
    return rows[0]


def _has_payments(tenant_id: str, b: Dict[str, Any]) -> bool:
    if (b.get("status") or "") in ("paid", "part_paid") or (_int(b.get("total_paid_cents")) or 0) > 0:
        return True
    try:
        rows = (_client().table("commerce_invoice_payments").select("id").eq("tenant_id", tenant_id)
                .eq("invoice_id", b["id"]).limit(1).execute().data or [])
        return bool(rows)
    except Exception:  # noqa: BLE001
        return False


def cancel_duplicate_bill(tenant_id: str, bill_id: str, of_id: str, reason: str,
                          actor: Optional[dict] = None) -> Dict[str, Any]:
    """The second copy of a bill in the books, cancelled — never one that has been paid (that goes to the
    accountant: a payment may have been made twice)."""
    from vula.commerce import service
    from vula.commerce.books_review import _reason
    why = _reason(reason)
    if str(bill_id) == str(of_id):
        raise _refused("Choose the other copy.")
    b, first = _bill(tenant_id, bill_id), _bill(tenant_id, of_id)
    if (first.get("status") or "") == "cancelled":
        raise _refused("The bill to keep is itself cancelled.")
    if _has_payments(tenant_id, b):
        raise _refused("This copy has a payment against it — the bill may have been paid twice. Leave it for the "
                       "accountant, or cancel the copy with no payment.")
    if _int(b.get("total_cents")) != _int(first.get("total_cents")):
        raise _refused("The two bills aren't for the same amount.")
    now = datetime.now(timezone.utc).isoformat()
    _client().table("commerce_invoices").update({"status": "cancelled", "cancelled_at": now,
                                                 "cancel_reason": f"Duplicate of {first.get('invoice_number')}: {why}"[:300]}) \
        .eq("tenant_id", tenant_id).eq("id", bill_id).execute()
    back = _bill(tenant_id, bill_id)
    try:
        service._books_state(tenant_id, back)
    except Exception as exc:  # noqa: BLE001
        log.warning("cancelled duplicate bill %s not re-posted: %s", bill_id, exc)
    _audit(tenant_id, actor, "bill_cancelled_duplicate", bill_id=bill_id, of=of_id, reason=why,
           cents=_int(b.get("total_cents")))
    return {"ok": back.get("status") == "cancelled", "bill": _bill_shown(back)}


def match_document(tenant_id: str, bill_id: str, reason: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """Book an unpaid supplier bill at its own document's figures (total, VAT, subtotal)."""
    from vula.commerce import service
    from vula.commerce.books_review import _reason
    why = _reason(reason)
    b = _bill(tenant_id, bill_id)
    docs = (_client().table("vula_filed_documents").select(DOC_COLS).eq("tenant_id", tenant_id)
            .eq("commerce_invoice_id", bill_id).limit(5).execute().data or [])
    docs = [d for d in docs if live(d)]
    if len(docs) != 1:
        raise _refused("That bill has no single document behind it to match.")
    f = docs[0].get("fields") or {}
    if f.get("_unverified_figures") or any(p["problem"] for p in figure_problems(docs[0])):
        raise _refused("Its document's figures aren't confirmed yet — correct them or say they're right first.")
    if _has_payments(tenant_id, b):
        raise _refused("This bill has been paid (or part-paid) at its booked amount — check with the supplier; "
                       "the accountant decides the correction.")
    total = _int(f.get("total_cents"))
    if not total or total <= 0:
        raise _refused("The document has no total.")
    vat = _int(f.get("vat_cents")) or 0
    sub = _int(f.get("subtotal_cents"))
    patch = {"total_cents": total, "vat_cents": vat, "subtotal_cents": sub if sub is not None else total - vat}
    was = {k: _int(b.get(k)) for k in patch}
    _client().table("commerce_invoices").update(patch).eq("tenant_id", tenant_id).eq("id", bill_id).execute()
    back = _bill(tenant_id, bill_id)
    ok = all(_int(back.get(k)) == v for k, v in patch.items())
    try:
        service._books_state(tenant_id, back)
    except Exception as exc:  # noqa: BLE001
        log.warning("bill %s not re-posted after matching its document: %s", bill_id, exc)
    _audit(tenant_id, actor, "bill_matched_to_document", bill_id=bill_id, doc_id=docs[0]["id"], was=was, now=patch,
           reason=why)
    return {"ok": ok, "bill": _bill_shown(back), "was": was}


def set_vat_basis(tenant_id: str, doc_id: str, basis: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """Whether a document's prices include VAT, said by the business — kept on the document and on every
    price learned from it."""
    if basis not in ("incl", "excl", "no_vat"):
        raise _refused("Say incl (prices include VAT), excl (exclude VAT) or no_vat (supplier not VAT registered).")
    doc = _doc(tenant_id, doc_id)
    f = dict(doc.get("fields") or {})
    was = f.get("vat_basis")
    f["vat_basis"] = basis
    f["_vat_basis_by"] = {"by": _who(actor), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _save_fields(tenant_id, doc_id, f)
    _client().table("vula_price_observations").update({"vat_basis": basis}).eq("tenant_id", tenant_id) \
        .eq("doc_id", str(doc_id)).execute()
    left = (_client().table("vula_price_observations").select("id,vat_basis").eq("tenant_id", tenant_id)
            .eq("doc_id", str(doc_id)).limit(1000).execute().data or [])
    ok = all(r.get("vat_basis") == basis for r in left)
    _audit(tenant_id, actor, "document_vat_basis_set", doc_id=doc_id, was=was, now=basis, prices=len(left))
    return {"ok": ok, "prices": len(left), "basis": basis}

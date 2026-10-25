"""Money-document facts answered from the filed records, not from search (2026-10-25).

Ian: "I just want it to be accurate." A staff question like "When is the Solid Cape invoice due?"
or "What's Atlantis's VAT number?" asks for ONE field of ONE document. Every money document is
already filed with its extracted fields (vula_filed_documents.fields — supplier, total_cents,
due_date, vat_number, bank details…), checked at filing time against the source text. Reading
the field back is exact; asking a model to read it off search excerpts is not.

answer() runs before any model, for staff only:
  1. what is asked — due date, VAT number, bank details, document number, date, or total;
  2. which documents — by a number/code in the question (invoice no., reference), else by the
     supplier named (incl. a saved alias such as "Jack Hammer"), narrowed by the kind named
     (invoice, quote, proof of payment, delivery note) and "latest";
  3. one document → the field, worded by code, money in integer cents; a supplier-wide field
     (VAT number, bank details) that every matching document agrees on → that value; several
     documents → "Which one?" (vula/kb_learning.py), whose pick is answered here too.

Anything this can't answer exactly (no field asked, nothing named, the field not on the record)
returns None and the question goes the normal way. Bank details always carry "confirm with the
supplier before paying" — changed bank details are a common fraud.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MONEY_CATEGORIES = ("Invoice", "Quote / Estimate", "Proof of Payment", "Delivery Note")
_KIND_CATEGORY = [
    (re.compile(r"\bquot(e|es|ation)\b|\bestimate\b", re.I), "Quote / Estimate"),
    (re.compile(r"\bproof of payment\b|\bpop\b|\bpayment (notice|confirmation)\b", re.I), "Proof of Payment"),
    (re.compile(r"\bdelivery note\b", re.I), "Delivery Note"),
    (re.compile(r"\binvoices?\b|\bbill\b", re.I), "Invoice"),
]

# (field, question pattern, per-document?) — checked in this order; "total" last, as "total" and
# "amount" ride along in many questions about something else.
_FIELDS: List[Tuple[str, re.Pattern, bool]] = [
    ("due", re.compile(r"\bdue date\b|\bwhen (is|was|are|were)\b.*\bdue\b|\bdue (on|by)\b|"
                       r"\bpay(able)? by\b|\bwhen (do|must|should) (we|i) pay\b", re.I), True),
    ("vat_no", re.compile(r"\bvat\s*(reg(istration)?\.?\s*)?(no|nr|number|#)\b|\btax (id|number|no)\b", re.I), False),
    ("bank", re.compile(r"\bbank(ing)?\s*(details|account)\b|\bbranch code\b|\baccount (no|nr|number) (of|for)\b", re.I), False),
    ("number", re.compile(r"\b(invoice|quote|reference|ref|document|order)\s*(no|nr|number|#)\b", re.I), True),
    ("date", re.compile(r"\b(what|which) date\b|\bdated\b|\bwhen was (it|the \w+) (issued|invoiced|sent|dated)\b", re.I), True),
    ("total", re.compile(r"\b(total|amount|how much|what did .* cost|value of)\b", re.I), True),
]
_LATEST_RE = re.compile(r"\b(latest|last|most recent|newest)\b", re.I)
# Several documents at once, or a running balance — the supplier-history path answers those.
_PLURAL_RE = re.compile(r"\b(all|every|each|list|how many|total of all|in total|sum|spend|spent|"
                        r"owe|owing|outstanding|balance|so far|this (month|year|week)|per month)\b", re.I)

# Party-name words that say nothing about WHICH company.
_GENERIC_PARTY = {
    "pty", "ltd", "cc", "inc", "the", "and", "trading", "group", "holdings", "services", "supplies",
    "supply", "distributors", "distribution", "centre", "center", "south", "africa", "african",
    "company", "enterprises", "solutions", "international", "total", "invoice", "account", "cash",
    "general", "store", "stores", "shop", "sales", "projects", "construction", "products", "cape",
    "town", "johannesburg", "durban", "seafood", "fishing", "foods",
}
_ID_KEYS = ("document_number", "invoice_no", "invoice_number", "tax_invoice_number", "quote_no", "document_no", "doc_no",
            "reference", "order_number", "order_no", "delivery_note_no", "delivery_note_number",
            "customer_ref", "statement_number", "payment_reference")
_DUE_KEYS = ("due_date", "payment_due_date", "due_by")
_DATE_KEYS = ("invoice_date", "date", "quote_date", "document_date", "doc_date", "payment_date",
              "delivery_date")
_VAT_KEYS = ("tax_id", "supplier_vat_number", "vat_number", "vat_no", "vat_reg_no", "vendor_vat_number",
             "supplier_vat_reg_no", "vendor_vat", "vendor_vat_no")


def own_vat_numbers(rows: List[Dict[str, Any]]) -> set:
    """VAT numbers on the invoices of 3+ different suppliers: the business's own (its number is
    printed as the customer's), which older reads sometimes stored as the supplier's."""
    seen: Dict[str, set] = {}
    for r in rows:
        f = r.get("fields") or {}
        for k in _VAT_KEYS:
            v = re.sub(r"\D", "", str(f.get(k) or ""))
            if v:
                seen.setdefault(v, set()).add(_norm(_party(f)))
    return {v for v, parties in seen.items() if len(parties - {""}) >= 3}
_PARTY_KEYS = ("supplier", "supplier_name", "vendor", "vendor_name", "payee_name", "payee",
               "customer", "client")


def asked_field(question: str) -> Optional[Tuple[str, bool]]:
    for name, rx, per_doc in _FIELDS:
        if rx.search(question or ""):
            return name, per_doc
    return None


def _party(fields: Dict[str, Any]) -> str:
    for k in _PARTY_KEYS:
        v = fields.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


def _party_words(party: str) -> List[str]:
    return [w for w in _norm(party).split() if len(w) >= 4 and w not in _GENERIC_PARTY]


def _identifiers(question: str) -> List[str]:
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*", question or "")
    return [t.lower() for t in toks if len(t) >= 4 and not re.fullmatch(r"(19|20)\d\d", t)]


def _id_values(row: Dict[str, Any]) -> str:
    f = row.get("fields") or {}
    return " ".join([str(f.get(k) or "") for k in _ID_KEYS] + [row.get("filename") or ""]).lower()


def _date(v: Any) -> Optional[datetime]:
    if not v:
        return None
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _first(fields: Dict[str, Any], keys) -> Any:
    for k in keys:
        v = fields.get(k)
        if v not in (None, "", []):
            return v
    return None


def _doc_date(row: Dict[str, Any]) -> Optional[datetime]:
    return _date(_first(row.get("fields") or {}, _DATE_KEYS)) or _date(row.get("created_at"))


def _day(d: Optional[datetime], raw: Any = None) -> str:
    return d.strftime("%-d %b %Y") if d else (str(raw) if raw else "")


def _rand(cents: Optional[int]) -> str:
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    r, c = divmod(abs(int(cents)), 100)
    return f"{sign}R{r:,}.{c:02d}".replace(",", " ")


def _filename_number(filename: str) -> Optional[str]:
    """"Invoice_DO_IT_YOURSELF_HARDWARE_INV782520.PDF" -> "INV782520", "00097750.pdf" -> "00097750".
    Most extractions leave invoice_no empty; the number is usually in the file's name."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", filename or "")
    # A WhatsApp upload is filed as "<Kind> - <Party> 20261019-0947": that's the upload time.
    stem = re.sub(r"\b20\d{6}-\d{4}\b", " ", stem)
    nums = re.findall(r"[A-Za-z]{0,4}\d[\d\-]{3,}", stem)
    return nums[-1] if nums else None


def describe(row: Dict[str, Any], with_amount: bool = False) -> str:
    """"Invoice INV782520 from DO IT YOURSELF HARDWARE, 14 Oct 2026" — how an answer names its
    source. with_amount adds the total, to tell apart same-day documents in a "Which one?"."""
    from vula.commerce.service import _document_amount_cents
    f = row.get("fields") or {}
    num = _first(f, _ID_KEYS[:5]) or _filename_number(row.get("filename") or "")
    kind = (row.get("category") or "Document").split(" / ")[0]
    bits = [kind + (f" {num}" if num else "")]
    if _party(f):
        bits.append(f"from {_party(f)}")
    d = _doc_date(row)
    out = " ".join(bits) + (f", {_day(d)}" if d else "")
    if with_amount:
        cents = _document_amount_cents(f)
        out += f", {_rand(cents)}" if cents is not None else ""
    return out


def match_documents(rows: List[Dict[str, Any]], question: str,
                    supplier_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """The filed money documents the question names, newest first. [] when it names none."""
    q = _norm(question)
    qwords = set(q.split())
    for rx, cat in _KIND_CATEGORY:
        if rx.search(question or ""):
            rows = [r for r in rows if r.get("category") == cat]
            break
    ids = _identifiers(question)
    if ids:
        hit = [r for r in rows if any(i in _id_values(r) for i in ids)]
        if hit:
            rows = hit
        else:
            return []      # a number was given and nothing carries it: don't guess by name
    else:
        names = {_norm(n) for n in (supplier_names or []) if n}
        hit = []
        for r in rows:
            p = _norm(_party(r.get("fields") or {}))
            if not p:
                continue
            if p in names or any(n and (n in p or p in n) for n in names):
                hit.append(r)
            elif any(w in qwords for w in _party_words(p)):
                hit.append(r)
        rows = hit
        if not rows:
            return []
    rows = sorted(rows, key=lambda r: _doc_date(r) or datetime.min, reverse=True)
    if _LATEST_RE.search(question or ""):
        rows = rows[:1]
    return rows


def field_reply(row: Dict[str, Any], field: str, own_vats: Optional[set] = None) -> Optional[str]:
    """The asked field of one document, worded by code — or None when the record doesn't hold it."""
    from vula.commerce.service import _document_amount_cents
    f = row.get("fields") or {}
    src = describe(row)
    if field == "total":
        cents = _document_amount_cents(f)
        if cents is None:
            return None
        vat_note = " (amounts exclude VAT)" if f.get("amounts_exclude_vat") else ""
        return f"{src}: total {_rand(cents)}{vat_note}."
    if field == "due":
        raw = _first(f, _DUE_KEYS)
        if not raw:
            return None
        cents = _document_amount_cents(f)
        amt = f" — {_rand(cents)}" if cents is not None else ""
        return f"{src}: due {_day(_date(raw), raw)}{amt}."
    if field == "date":
        raw = _first(f, _DATE_KEYS)
        return f"{src}: dated {_day(_date(raw), raw)}." if raw else None
    if field == "number":
        num = _first(f, _ID_KEYS)
        return f"{src}: number {num}." if num else None
    if field == "vat_no":
        v = next((f[k] for k in _VAT_KEYS if f.get(k) not in (None, "")
                  and re.sub(r"\D", "", str(f[k])) not in (own_vats or set())), None)
        return f"{_party(f) or src}: VAT number {v} (from {src})." if v else None
    if field == "bank":
        b = _bank(f)
        return (f"{_party(f) or src}: {b} (from {src}). Please confirm bank details with the "
                f"supplier by phone before paying — changed bank details are a common fraud.") if b else None
    return None


def _bank(f: Dict[str, Any]) -> Optional[str]:
    sb = f.get("supplier_bank")
    if isinstance(sb, dict) and sb.get("account_number") and sb.get("bank_name"):
        out = f"{sb['bank_name']}, account {sb['account_number']}"
        out += f" ({sb['account_name']})" if sb.get("account_name") else ""
        return out + (f", branch code {sb['branch_code']}" if sb.get("branch_code") else "")
    acct = f.get("supplier_bank_account_number") or f.get("bank_account_number") or f.get("accountNo")
    bank = f.get("supplier_bank_name") or f.get("bank_name") or f.get("bank")
    if not acct and bank and f.get("account_number"):
        acct = f.get("account_number")      # an account number next to a bank name is the bank's
    if not (acct and bank):
        return None
    branch = f.get("supplier_bank_branch_code") or f.get("bank_branch_code") or f.get("branch_code") \
        or f.get("branchCode")
    return f"{bank}, account {acct}" + (f", branch code {branch}" if branch else "")


def _fetch(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce import service
    return (service._client().table("vula_filed_documents")
            .select("doc_id,filename,category,fields,created_at")
            .eq("tenant_id", tenant_id).in_("category", list(MONEY_CATEGORIES))
            .order("created_at", desc=True).limit(3000).execute().data or [])


async def answer(tenant_id: str, question: str, who: str = "", role: Optional[str] = None,
                 rows: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """The exact answer from the filed records, a "Which one?" question, or None (go the normal
    way). Staff only; every failure returns None."""
    from vula import kb_learning
    if not kb_learning.is_insider(role) or not question or _PLURAL_RE.search(question):
        return None
    asked = asked_field(question)
    if not asked:
        return None
    field, per_doc = asked
    try:
        if rows is None:
            import asyncio
            rows = await asyncio.to_thread(_fetch, tenant_id)
        names: List[str] = []
        if not _identifiers(question):
            from vula.commerce import service
            names = await service._resolve_supplier_names(tenant_id, question)
            if names and not service.mentions_supplier_name(question, names):
                names = []
        docs = match_documents(rows, question, names)
    except Exception as exc:
        logger.debug("filed-record answer skipped: %s", exc)
        return None
    if not docs:
        return None
    own = own_vat_numbers(rows) if field == "vat_no" else set()
    replies = [(d, field_reply(d, field, own)) for d in docs]
    replies = [(d, r) for d, r in replies if r]
    if not replies:
        return None
    if not per_doc:
        # A supplier-wide field: one value across their documents is the answer; differing
        # values are listed with where each came from, newest first, never one picked silently.
        values: Dict[str, Tuple[Dict[str, Any], str]] = {}
        for d, r in replies:
            key = _norm(r.split(" (from ")[0])
            values.setdefault(key, (d, r))
        if len(values) == 1:
            d, r = next(iter(values.values()))
            kb_learning.record_turn(tenant_id, who, role=role, question=question, outcome="answered",
                                    answer_docs=[d.get("doc_id")], skill="filed_records")
            return r
        lines = [r for _, r in list(values.values())[:3]]
        kb_learning.record_turn(tenant_id, who, role=role, question=question, outcome="answered",
                                answer_docs=[d.get("doc_id") for d, _ in list(values.values())[:3]],
                                skill="filed_records")
        return "Different documents show different details:\n" + "\n".join(lines)
    if len(replies) == 1:
        d, r = replies[0]
        kb_learning.record_turn(tenant_id, who, role=role, question=question, outcome="answered",
                                answer_docs=[d.get("doc_id")], skill="filed_records")
        return r
    options = [{"doc_id": str(d.get("doc_id") or ""), "label": describe(d, with_amount=True)}
               for d, _ in replies[:kb_learning.MAX_OPTIONS] if d.get("doc_id")]
    if len(options) < 2:
        return None
    kb_learning.record_turn(tenant_id, who, role=role, question=question, outcome="clarify",
                            candidates=options, skill="filed_records")
    more = (f"\n\nThere are {len(replies)} in all — or tell me the number or date."
            if len(replies) > len(options) else "")
    return kb_learning.clarify_message(options).replace("\n\nReply with the number.",
                                                        more + "\n\nReply with the number.")


def answer_for_doc(tenant_id: str, question: str, doc_id: str) -> Optional[str]:
    """After a pick: the asked field of that one document, or None (then search answers it)."""
    asked = asked_field(question)
    if not asked or not doc_id:
        return None
    try:
        from vula.commerce import service
        rows = (service._client().table("vula_filed_documents")
                .select("doc_id,filename,category,fields,created_at")
                .eq("tenant_id", tenant_id).eq("doc_id", doc_id).limit(1).execute().data or [])
    except Exception as exc:
        logger.debug("picked-document field skipped: %s", exc)
        return None
    return field_reply(rows[0], asked[0]) if rows else None

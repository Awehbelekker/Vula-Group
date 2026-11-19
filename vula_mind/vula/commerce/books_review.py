"""vula/commerce/books_review.py — the business works through its books audit, item by item (Phase 0d).

18 Nov 2026 (Ian, DIGG: "make sure Vula has its ducks in a row before the accountant has access").
The books audit (books_audit.py) and the rebuild dry run (books_rebuild.plan) name what's wrong; this
turns that into a worklist an owner or manager can fix from the dashboard, and the fixes themselves:

  run(tenant, person)        run the audit AND the dry run now; keep both (vula_books_audits)
  review(tenant)             the worklist, from the latest kept report, every item re-read as it is
                             NOW (so a fixed item drops off without a re-run)
  restore / set_aside        a bank line back in the books, or out of them — always with a reason
  transfer                   the two legs of money moved between the business's own accounts
  link                       "this bank line IS that payment" — for a document already paid or a card
                             expense: clears money in transit and the cost counted twice, records no
                             second payment
  set_date                   correct an expense's or a document's date (misread years)
  agree                      the business agrees the audit; only then can Vula sign it off
  documents (doc_audit.py)   the documents behind the money: correct what was read, say it's right,
                             book an invoice on file, not a bill, a duplicate document or bill, a bill
                             booked at its document's figures, whether a document's prices include VAT

Every fix is scoped to the business, keeps its reason, is read back, written to vula_merchant_audit, and
posts again in the one trusted book where that's on. Amounts are integer cents, worked out in code.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

MAX_IDS = 500
BANK = "commerce_bank_transactions"
LINE_COLS = ("id,txn_date,description,amount_cents,direction,match_status,account_code,categorized_by,"
             "excluded_reason,bank_account,matched_invoice_id,matched_expense_id,matched_order_id,worker_id,"
             "source_file")
EXPENSE_POSTED = ("submitted", "approved", "paid", "reimbursed")
FIRST_DATE = date(2015, 1, 1)
# the document groups that count as open (price_vat and access are for information)
DOC_GROUPS = ("doc_figures", "not_booked", "bill_mismatch", "doc_duplicates", "bill_duplicates", "suppliers")


class Refused(ValueError):
    """A fix that can't be done as asked — the message says why, in plain words."""


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ids(ids: Iterable[Any]) -> List[str]:
    out = [str(i) for i in dict.fromkeys(ids or []) if i]
    if not out:
        raise Refused("Choose at least one line.")
    if len(out) > MAX_IDS:
        raise Refused(f"At most {MAX_IDS} at a time.")
    return out


def _reason(reason: Optional[str]) -> str:
    r = (reason or "").strip()
    if len(r) < 3:
        raise Refused("Say why, in a few words — it's kept with the change for the accountant.")
    return r[:300]


def _audit(tenant_id: str, actor: Optional[dict], action: str, **detail: Any) -> None:
    try:
        from vula.api.merchant_audit import audit
        audit(tenant_id, actor, action, **detail)
    except Exception:  # noqa: BLE001
        pass


def _resync(tenant_id: str, ids: Iterable[str]) -> None:
    try:
        from vula.commerce import ledger_events
        if ledger_events.on(tenant_id):
            ledger_events.sync_bank_lines(tenant_id, list(ids))
    except Exception as exc:  # noqa: BLE001
        log.warning("books review: lines not re-posted to the books: %s", exc)


def _lines(tenant_id: str, ids: Iterable[str]) -> List[Dict[str, Any]]:
    ids = [str(i) for i in ids if i]
    out: List[Dict[str, Any]] = []
    for i in range(0, len(ids), 150):
        out += (_client().table(BANK).select(LINE_COLS).eq("tenant_id", tenant_id).in_("id", ids[i:i + 150])
                .execute().data or [])
    return out


def _mask(number: Any) -> Optional[str]:
    return f"…{str(number)[-4:]}" if number else None


def _shown(line: Dict[str, Any]) -> Dict[str, Any]:
    """A bank line as the screen shows it (the account number masked)."""
    return {"id": line["id"], "date": str(line.get("txn_date"))[:10], "description": line.get("description") or "",
            "amount_cents": int(line.get("amount_cents") or 0), "direction": line.get("direction"),
            "status": line.get("match_status"), "account_code": line.get("account_code"),
            "categorized_by": line.get("categorized_by"), "excluded_reason": line.get("excluded_reason"),
            "bank_account": _mask(line.get("bank_account")),
            "matched": bool(line.get("matched_invoice_id") or line.get("matched_expense_id")
                            or line.get("matched_order_id") or line.get("worker_id"))}


def _live(line: Dict[str, Any]) -> bool:
    from vula.commerce import ledger_events
    return ledger_events.is_live(line)


# ── run and keep ─────────────────────────────────────────────────────────────

def mask_books(rep: Dict[str, Any]) -> Dict[str, Any]:
    """The dry run as a business may see it: no full account numbers."""
    rec = rep.get("bank_reconciliation") or {}
    for a in rec.get("accounts") or []:
        a.pop("number", None)
        a.pop("bank_account_id", None)
    return rep


def accrual_start(tenant_id: str) -> str:
    """The business's accrual start, else the start of its current financial year (1 March)."""
    try:
        rows = (_client().table("commerce_books_settings").select("accrual_from").eq("tenant_id", tenant_id)
                .limit(1).execute().data or [])
        if rows and rows[0].get("accrual_from"):
            return str(rows[0]["accrual_from"])[:10]
    except Exception:  # noqa: BLE001
        pass
    today = date.today()
    return date(today.year if today.month >= 3 else today.year - 1, 3, 1).isoformat()


def run(tenant_id: str, person: str) -> Dict[str, Any]:
    """The books audit and the rebuild dry run together, kept as one report."""
    from vula.commerce import books_audit, books_rebuild
    rep = books_audit.run(tenant_id)
    try:
        rep["books"] = mask_books(books_rebuild.public(books_rebuild.plan(tenant_id, accrual_start(tenant_id),
                                                                         audit=False)))
    except Exception as exc:  # noqa: BLE001 — the audit stands on its own
        log.warning("books review: dry run failed for %s: %s", tenant_id, exc)
        rep["books"] = {"error": "The books couldn't be worked out just now."}
    rep["id"] = None
    try:
        got = (_client().table("vula_books_audits").insert({
            "tenant_id": tenant_id, "report": rep, "summary": rep.get("summary") or {},
            "created_by": person or "owner"}).execute().data or [])
        rep["id"] = got[0]["id"] if got else None
    except Exception as exc:  # noqa: BLE001
        log.warning("books review: report not kept for %s: %s", tenant_id, exc)
    return rep


def latest(tenant_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table("vula_books_audits").select("*").eq("tenant_id", tenant_id)
            .order("created_at", desc=True).limit(1).execute().data or [])
    return rows[0] if rows else None


# ── the worklist ─────────────────────────────────────────────────────────────

def _section(rep: Dict[str, Any], key: str) -> Dict[str, Any]:
    return next((s for s in rep.get("sections") or [] if s.get("key") == key), {}) or {}


def _candidates(doc: Dict[str, Any], free: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bank lines that could be this document's payment: the same amount, the money going the right way,
    not tied to anything yet, from 10 days before the document to 180 after. Nearest first."""
    want = abs(int(doc.get("open_cents") or 0))
    direction = "in" if int(doc.get("open_cents") or 0) > 0 else "out"
    try:
        when = date.fromisoformat(str(doc.get("date"))[:10])
    except ValueError:
        when = None
    out = []
    for ln in free:
        if ln.get("direction") != direction or int(ln.get("amount_cents") or 0) != want:
            continue
        try:
            d = date.fromisoformat(str(ln.get("txn_date"))[:10])
        except ValueError:
            continue
        if when and not (when - timedelta(days=10) <= d <= when + timedelta(days=180)):
            continue
        out.append((abs((d - when).days) if when else 0, ln))
    return [_shown(ln) for _, ln in sorted(out, key=lambda x: x[0])[:5]]


def review(tenant_id: str) -> Dict[str, Any]:
    row = latest(tenant_id)
    if not row:
        return {"report": None}
    rep = row.get("report") or {}
    books = rep.get("books") or {}
    db = _client()

    # set aside by mistake — still set aside now
    restore_ids = [r for f in _section(rep, "restore").get("findings") or [] for r in f.get("rows") or []]
    restore = [_shown(x) for x in _lines(tenant_id, restore_ids) if not _live(x)]

    dups = []
    for f in _section(rep, "duplicates").get("findings") or []:
        rows = [x for x in _lines(tenant_id, f.get("rows") or []) if _live(x)]
        if len(rows) > 1:
            dups.append({"lines": [_shown(x) for x in sorted(rows, key=lambda r: str(r["id"]))],
                         "extra_cents": int(f.get("cents") or 0)})

    transfers = []
    for f in _section(rep, "transfers").get("findings") or []:
        rows = _lines(tenant_id, f.get("rows") or [])
        if len(rows) == 2 and all(_live(x) for x in rows) and not all(x.get("account_code") == "transfer_clearing"
                                                                      for x in rows):
            transfers.append({"lines": [_shown(x) for x in rows], "cents": int(f.get("cents") or 0)})

    # every bank line once (paged): what's still to categorise, what's free to be a payment, what's tied
    try:
        from vula.commerce.ledger import _all_pages
        every = _all_pages(lambda: db.table(BANK).select(LINE_COLS).eq("tenant_id", tenant_id).order("id"))
    except Exception:  # noqa: BLE001
        every = []
    live = [x for x in every if _live(x)]
    free = [x for x in live if not _shown(x)["matched"]]
    to_categorise = sorted((_shown(x) for x in free
                            if x.get("categorized_by") in ("default", "asked") or not x.get("account_code")),
                           key=lambda x: x["date"], reverse=True)[:MAX_IDS]
    linked_docs = {str(x.get("matched_invoice_id") or x.get("matched_expense_id"))
                   for x in live if x.get("matched_invoice_id") or x.get("matched_expense_id")}
    in_transit = []
    for d in (books.get("in_transit") or {}).get("largest") or []:
        if str(d.get("id")) in linked_docs or d.get("kind") not in ("bill", "invoice", "expense"):
            continue
        in_transit.append({**d, "candidates": _candidates(d, free)})

    try:
        claims = [c for c in (db.table("commerce_expenses")
                              .select("id,date,description,supplier,amount_cents,status,paid_by_name,paid_with,reimbursable")
                              .eq("tenant_id", tenant_id).eq("reimbursable", True).limit(1000).execute().data or [])
                  if (c.get("status") or "") in ("submitted", "approved", "paid")]
    except Exception:  # noqa: BLE001
        claims = []

    try:
        drafts = (db.table("commerce_invoices")
                  .select("id,invoice_number,direction,supplier,customer_name,issue_date,total_cents,status,doc_type")
                  .eq("tenant_id", tenant_id).eq("status", "draft").eq("doc_type", "invoice")
                  .order("issue_date", desc=True).limit(MAX_IDS).execute().data or [])
    except Exception:  # noqa: BLE001
        drafts = []

    odd = []
    entry_ids = [r for f in _section(rep, "dates").get("findings") or [] for r in f.get("rows") or []]
    if entry_ids:
        try:
            heads = (db.table("journal_entries").select("id,entry_date,source_type,source_id,description")
                     .eq("tenant_id", tenant_id).in_("id", entry_ids[:150]).execute().data or [])
        except Exception:  # noqa: BLE001
            heads = []
        for h in heads:
            kind = "expense" if (h.get("source_type") or "").startswith("expense") else (
                "invoice" if "invoice" in (h.get("source_type") or "") or "bill" in (h.get("source_type") or "") else None)
            if kind:
                odd.append({"kind": kind, "id": h.get("source_id"), "date": str(h.get("entry_date"))[:10],
                            "what": (h.get("description") or "")[:120]})

    no_account = sum(1 for x in live if not (x.get("bank_account") or "").strip())

    # the documents behind the money (doc_audit, 19 Nov 2026), re-read now
    try:
        from vula.commerce import doc_audit
        docs = doc_audit.worklist(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("books review: document worklist failed for %s: %s", tenant_id, exc)
        docs = {}

    problems = [{"key": s.get("key"), "title": s.get("title"), "status": s.get("status"), "headline": s.get("headline")}
                for s in rep.get("sections") or []]
    open_now = {"restore": len(restore), "duplicates": len(dups), "transfers": len(transfers),
                "to_categorise": len(to_categorise), "in_transit": len(in_transit), "staff_claims": len(claims),
                "drafts": len(drafts), "odd_dates": len(odd), "no_account": no_account,
                **{k: len(docs.get(k) or []) for k in DOC_GROUPS}}
    return {"report": {"id": row.get("id"), "created_at": row.get("created_at"), "created_by": row.get("created_by"),
                       "agreed_by": row.get("agreed_by"), "agreed_at": row.get("agreed_at"),
                       "agreed_note": row.get("agreed_note"), "signed_off_by": row.get("signed_off_by"),
                       "signed_off_at": row.get("signed_off_at")},
            "checks": problems, "books": books, "open": open_now, "clean": not any(open_now.values()),
            "restore": restore, "duplicates": dups, "transfers": transfers, "to_categorise": to_categorise,
            "in_transit": in_transit, "staff_claims": claims, "drafts": drafts, "odd_dates": odd,
            "no_account": no_account, "documents": docs}


# ── fixes ────────────────────────────────────────────────────────────────────

def restore(tenant_id: str, ids: Iterable[str], reason: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """Set-aside lines back into the books (the audit found the right copy set aside with the wrong one)."""
    ids, why = _ids(ids), _reason(reason)
    rows = [r for r in _lines(tenant_id, ids) if (r.get("match_status") or "") == "ignored"]
    for r in rows:
        _client().table(BANK).update({"match_status": "unmatched", "excluded_reason": None}) \
            .eq("tenant_id", tenant_id).eq("id", r["id"]).execute()
    back = _lines(tenant_id, [r["id"] for r in rows])
    done = [r["id"] for r in back if r.get("match_status") == "unmatched"]
    _audit(tenant_id, actor, "bank_lines_restored", count=len(done), reason=why, ids=done[:50])
    _resync(tenant_id, done)
    return {"ok": len(done) == len(rows), "done": done, "skipped": [i for i in ids if i not in done]}


def set_aside(tenant_id: str, ids: Iterable[str], reason: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """Lines out of the books (a misread or doubled copy), with the reason kept on each line."""
    ids, why = _ids(ids), _reason(reason)
    rows = [r for r in _lines(tenant_id, ids) if _live(r)]
    for r in rows:
        _client().table(BANK).update({"match_status": "ignored", "excluded_reason": why}) \
            .eq("tenant_id", tenant_id).eq("id", r["id"]).execute()
    back = _lines(tenant_id, [r["id"] for r in rows])
    done = [r["id"] for r in back if r.get("match_status") == "ignored"]
    _audit(tenant_id, actor, "bank_lines_set_aside", count=len(done), reason=why, ids=done[:50])
    _resync(tenant_id, done)
    return {"ok": len(done) == len(rows), "done": done, "skipped": [i for i in ids if i not in done]}


def transfer(tenant_id: str, ids: Iterable[str], actor: Optional[dict] = None) -> Dict[str, Any]:
    """Two lines that are one movement between the business's own accounts: neither income nor a cost."""
    ids = _ids(ids)
    rows = _lines(tenant_id, ids)
    if len(rows) != 2 or {r.get("direction") for r in rows} != {"in", "out"} \
            or len({int(r.get("amount_cents") or 0) for r in rows}) != 1:
        raise Refused("A transfer is two lines: the same amount, one out and one in.")
    if not all(_live(r) for r in rows):
        raise Refused("One of those lines is set aside — restore it first.")
    from vula.commerce import ledger_v2
    ledger_v2.ensure_ledger_chart(tenant_id)
    for r in rows:
        _client().table(BANK).update({"account_code": "transfer_clearing", "vat_cents": 0, "vat_treatment": "none",
                                      "categorized_by": "owner"}).eq("tenant_id", tenant_id).eq("id", r["id"]).execute()
    back = _lines(tenant_id, ids)
    ok = all(r.get("account_code") == "transfer_clearing" for r in back)
    _audit(tenant_id, actor, "bank_transfer_confirmed", ids=ids, cents=int(rows[0].get("amount_cents") or 0))
    _resync(tenant_id, ids)
    return {"ok": ok, "done": ids if ok else []}


def link(tenant_id: str, txn_id: str, *, invoice_id: Optional[str] = None, expense_id: Optional[str] = None,
         actor: Optional[dict] = None) -> Dict[str, Any]:
    """This bank line IS the payment of a document already marked paid (or of a card / staff expense).
    Ties them together without recording a second payment — the money stops being 'in transit', and the
    line is no longer counted as a cost of its own."""
    if bool(invoice_id) == bool(expense_id):
        raise Refused("Choose the bill, invoice or expense this payment was for.")
    rows = _lines(tenant_id, [txn_id])
    if not rows or not _live(rows[0]):
        raise Refused("That bank line isn't in the books (it may be set aside).")
    ln = rows[0]
    if _shown(ln)["matched"]:
        raise Refused("That bank line is already tied to something.")
    amount = int(ln.get("amount_cents") or 0)
    db = _client()
    if invoice_id:
        docs = (db.table("commerce_invoices").select("id,direction,doc_type,status,total_cents,invoice_number")
                .eq("tenant_id", tenant_id).eq("id", invoice_id).limit(1).execute().data or [])
        if not docs:
            raise Refused("That document isn't there.")
        doc = docs[0]
        want = "outbound" if ln.get("direction") == "in" else "inbound"
        if (doc.get("direction") or "outbound") != want:
            raise Refused("Money in can only be a customer's payment, and money out a payment to a supplier.")
        if (doc.get("status") or "") != "paid":
            raise Refused("That document isn't marked paid. Use Bank › Match to record this as its payment.")
        already = sum(int(x.get("amount_cents") or 0) for x in
                      (db.table(BANK).select("amount_cents,match_status").eq("tenant_id", tenant_id)
                       .eq("matched_invoice_id", invoice_id).execute().data or []) if _live(x))
        if already + amount > int(doc.get("total_cents") or 0):
            raise Refused("That's more than the document's total with the payments already tied to it.")
        patch = {"matched_invoice_id": invoice_id, "match_status": "matched"}
        label = doc.get("invoice_number") or invoice_id
    else:
        exps = (db.table("commerce_expenses").select("id,status,amount_cents,paid_with,description")
                .eq("tenant_id", tenant_id).eq("id", expense_id).limit(1).execute().data or [])
        if not exps:
            raise Refused("That expense isn't there.")
        exp = exps[0]
        if ln.get("direction") != "out":
            raise Refused("An expense is paid with money going out.")
        if (exp.get("status") or "") not in EXPENSE_POSTED:
            raise Refused("That expense isn't booked yet.")
        if amount > int(exp.get("amount_cents") or 0):
            raise Refused("The bank line is more than the expense.")
        patch = {"matched_expense_id": expense_id, "match_status": "matched"}
        label = exp.get("description") or expense_id
    db.table(BANK).update(patch).eq("tenant_id", tenant_id).eq("id", txn_id).execute()
    back = _lines(tenant_id, [txn_id])[0]
    ok = all(back.get(k) == v for k, v in patch.items())
    _audit(tenant_id, actor, "bank_line_linked", txn_id=txn_id, invoice_id=invoice_id, expense_id=expense_id,
           cents=amount)
    _resync(tenant_id, [txn_id])
    return {"ok": ok, "line": _shown(back), "linked_to": label}


def set_date(tenant_id: str, kind: str, doc_id: str, new_date: str, reason: str,
             actor: Optional[dict] = None) -> Dict[str, Any]:
    """Correct a misread date on an expense (`date`) or a bill/invoice (`issue_date`)."""
    why = _reason(reason)
    try:
        d = date.fromisoformat(str(new_date)[:10])
    except ValueError:
        raise Refused("Give the date as YYYY-MM-DD.")
    if not (FIRST_DATE <= d <= date.today()):
        raise Refused("That date can't be right for this business's books.")
    db = _client()
    if kind == "expense":
        table, col = "commerce_expenses", "date"
    elif kind in ("invoice", "bill"):
        table, col = "commerce_invoices", "issue_date"
    else:
        raise Refused("Only an expense, bill or invoice date can be corrected here.")
    rows = db.table(table).select(f"id,{col}").eq("tenant_id", tenant_id).eq("id", doc_id).limit(1).execute().data or []
    if not rows:
        raise Refused("That document isn't there.")
    was = str(rows[0].get(col) or "")[:10]
    db.table(table).update({col: d.isoformat()}).eq("tenant_id", tenant_id).eq("id", doc_id).execute()
    back = db.table(table).select("*").eq("tenant_id", tenant_id).eq("id", doc_id).limit(1).execute().data or [{}]
    ok = str(back[0].get(col) or "")[:10] == d.isoformat()
    _audit(tenant_id, actor, "document_date_corrected", kind=kind, doc_id=doc_id, was=was, now=d.isoformat(),
           reason=why)
    try:
        if kind == "expense":
            from vula.commerce import expenses
            expenses.books_refresh(tenant_id, doc_id)
        else:
            from vula.commerce import service
            service._books_state(tenant_id, back[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("books review: %s %s not re-posted after its date changed: %s", kind, doc_id, exc)
    return {"ok": ok, "was": was, "now": d.isoformat()}


def agree(tenant_id: str, audit_id: str, note: str, person: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """The business agrees its latest audit (and names what it accepts as still open)."""
    row = latest(tenant_id)
    if not row or str(row.get("id")) != str(audit_id):
        raise Refused("Agree the latest check — run it again if the books changed.")
    if row.get("signed_off_by"):
        raise Refused("This check is already signed off.")
    note = (note or "").strip()[:1000]
    _client().table("vula_books_audits").update({"agreed_by": person or "owner", "agreed_at": _now(),
                                                 "agreed_note": note or None}) \
        .eq("tenant_id", tenant_id).eq("id", audit_id).execute()
    back = latest(tenant_id) or {}
    _audit(tenant_id, actor, "books_audit_agreed", audit_id=audit_id, note=note[:200])
    return {"ok": bool(back.get("agreed_by")), "agreed_by": back.get("agreed_by"), "agreed_at": back.get("agreed_at")}

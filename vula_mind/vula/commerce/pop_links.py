"""vula/commerce/pop_links.py — every proof of payment the business sent, linked to its supplier and the
bill(s) it paid.

20 Nov 2026 (Ian, DIGG): "a tenant will make one payment to multiple invoices — compare and see the amounts
paid match; when the tenant uploads a POP is it linked to the supplier?" DIGG had 65 proofs of payment on
file and none was linked to a supplier, a bill or a payment; the supplier record often holds the bank
account the money went to (Belekkie Skips: paid to R Engelbrecht's account).

  read(doc)                   amount, date, payee, payee account, payer, reference from what was read
  outgoing(tenant, pop)       money the business (or one of its people) paid — not a customer's POP
  supplier_for(tenant, pop)   the supplier paid: the payee's account number on the supplier record (last
                              4+ digits), else a payee word alike the supplier's name/aliases/account holder
  options(tenant, pop)        the supplier's bill, or 2–3 of its bills, adding up exactly to the amount
                              (−10…+120 days); bills another proof already stands for are left out
  review(tenant)              the unlinked proofs with their supplier and options, for Books review
  link(tenant, doc, bills)    record each open bill's payment once on the proof's date; a bill already paid
                              is only tied; kept in fields.pop_link, read back, audited
  dismiss(tenant, doc, why)   "not a supplier's bill" — kept, never offered again

A proof of payment is never a bank line (ledger_events.is_pop): when the statement line arrives it is tied
to the paid bill in Books review (Payments that pay a supplier's bill / Money in transit), so nothing is
paid twice. All matching in code; no model.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from itertools import combinations
from typing import Any, Dict, List, Optional
from vula.db_pages import all_rows

log = logging.getLogger(__name__)

CATEGORY = "Proof of Payment"
DAYS = (-10, 120)                 # the bill dated from 10 days after the payment to 120 days before it
MAX_BILLS = 3
MAX_OPTIONS = 5


class Refused(Exception):
    pass


def _client():
    from vula.commerce import service
    return service._client()


def _cents(v: Any) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return int(round(float(v))) if not isinstance(v, str) else int(round(float(v.replace(",", ""))))
    except (TypeError, ValueError):
        return None


def _digits(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def read(doc: Dict[str, Any]) -> Dict[str, Any]:
    """What the proof says, from the fields read at filing (several layouts' names)."""
    f = doc.get("fields") or {}
    payee_d, payer_d = f.get("payee_details") or {}, f.get("payer_details") or {}
    amount = next((c for c in (_cents(f.get(k)) for k in ("amount_cents", "payment_amount_cents", "total_cents"))
                   if c), None) or _cents(payer_d.get("amount_cents"))
    day = next((str(f.get(k))[:10] for k in ("date", "date_actioned", "payment_date") if f.get(k)), None)
    payee = next((str(v) for v in (f.get("payee_name"), payee_d.get("name"), f.get("payee"),
                                   f.get("recipient_name"), f.get("vendor"), f.get("supplier")) if v), "")
    account = _digits(f.get("payee_account_number") or payee_d.get("account_no_last_digits")
                      or f.get("payee_account_number_last_digits"))
    payer = next((str(v) for v in (f.get("payer"), payer_d.get("name"), f.get("payer_name"),
                                   f.get("sender_name")) if v), "")
    return {"id": doc["id"], "amount_cents": amount, "date": day, "payee": payee.strip(), "account": account,
            "payer": payer.strip(), "reference": str(f.get("reference") or "").strip(),
            "filename": doc.get("filename"), "link": f.get("pop_link")}


def _pops(tenant_id: str) -> List[Dict[str, Any]]:
    from vula.commerce.doc_audit import DOC_COLS, _pages, live
    rows = _pages(lambda: _client().table("vula_filed_documents").select(DOC_COLS)
                  .eq("tenant_id", tenant_id).eq("category", CATEGORY).order("id"))
    return [d for d in rows if live(d)]


def outgoing(tenant_id: str, pop: Dict[str, Any]) -> bool:
    """The business (or one of its people) paid it, and it wasn't paid to the business."""
    from vula.commerce import bank_rec
    from vula.commerce.books_review import _alike, _words
    own = set()
    try:
        own = set().union(*(_words(n) for n in bank_rec._own_names_now(tenant_id)))
    except Exception:  # noqa: BLE001
        pass
    said = lambda s: any(_alike(a, b) for a in _words(s) for b in own)   # noqa: E731
    if pop.get("payee") and said(pop["payee"]):
        return False                                   # paid to us: a customer's proof
    if not pop.get("payer"):
        return True                                    # the business's own banking app leaves it out
    if said(pop["payer"]):
        return True
    try:
        return bool(bank_rec._team_member_paying(tenant_id, pop["payer"]))
    except Exception:  # noqa: BLE001
        return False


def _suppliers(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        return (all_rows(lambda: _client().table("commerce_suppliers")
                .select("id,name,aliases,bank_account_name,bank_account_number")
                .eq("tenant_id", tenant_id), cap=5000))
    except Exception:  # noqa: BLE001
        return []


def supplier_for(tenant_id: str, pop: Dict[str, Any],
                 sups: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """{"id", "name", "how"} — by the account paid into (its last 4+ digits on one supplier record), else by
    the payee's name. None when it can't be told, or two suppliers fit."""
    from vula.commerce.books_review import _alike, _words
    sups = _suppliers(tenant_id) if sups is None else sups
    acct = pop.get("account") or ""
    if len(acct) >= 4:
        tail = acct[-6:] if len(acct) >= 6 else acct
        hits = [s for s in sups if len(_digits(s.get("bank_account_number"))) >= 4
                and _digits(s.get("bank_account_number")).endswith(tail)]
        if len(hits) == 1:
            return {"id": str(hits[0]["id"]), "name": hits[0].get("name"), "how": "the account paid into"}
    said = _words(pop.get("payee"))
    if not said:
        return None
    hits = []
    for s in sups:
        words = set().union(_words(s.get("name")), _words(s.get("bank_account_name")),
                            *(_words(a) for a in (s.get("aliases") or [])))
        if any(_alike(a, w) for a in said for w in words):
            hits.append(s)
    if len(hits) == 1:
        return {"id": str(hits[0]["id"]), "name": hits[0].get("name"), "how": "the payee's name"}
    return None


def _linked_bills(pops: List[Dict[str, Any]]) -> set:
    out = set()
    for d in pops:
        link = (d.get("fields") or {}).get("pop_link") or {}
        out |= {str(i) for i in (link.get("bill_ids") or [])}
    return out


def _supplier_bills(tenant_id: str, supplier: Dict[str, Any], taken: set) -> List[Dict[str, Any]]:
    """The supplier's bills a proof can stand for: open ones (draft/sent/overdue/part paid, something owed)
    and paid ones — by its record, or naming it on the bill — that no other proof stands for."""
    from vula.commerce.books_review import OPEN_BILL, _bills, _supplier_ids, _supplier_key
    ids = _supplier_ids(tenant_id)
    return [b for b in _bills(tenant_id, list(OPEN_BILL) + ["paid"])
            if _supplier_key(b, ids) == supplier["id"] and str(b["id"]) not in taken and _due(b) > 0]


def _due(b: Dict[str, Any]) -> int:
    """What a proof would stand for on this bill: what's still owed, or a paid bill's whole total."""
    total, paid = int(b.get("total_cents") or 0), int(b.get("total_paid_cents") or 0)
    return total if b.get("status") == "paid" else total - paid


def _bill_shown(b: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": b["id"], "number": b.get("invoice_number"), "supplier": b.get("supplier"),
            "date": str(b.get("issue_date"))[:10], "cents": _due(b), "status": b.get("status"),
            "already_paid": b.get("status") == "paid", "project": b.get("project")}


def _options(amount: int, day: date, bills: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    gap = lambda b: abs((day - date.fromisoformat(str(b["issue_date"])[:10])).days)   # noqa: E731
    cands = sorted([b for b in bills
                    if DAYS[0] <= (day - date.fromisoformat(str(b["issue_date"])[:10])).days <= DAYS[1]
                    and 0 < _due(b) <= amount], key=lambda b: (gap(b), str(b.get("invoice_number") or "")))[:20]
    singles = [[b] for b in cands if _due(b) == amount]
    if singles:
        return singles[:MAX_OPTIONS]
    out: List[List[Dict[str, Any]]] = []
    for n in range(2, MAX_BILLS + 1):
        out = [sorted(c, key=lambda b: str(b["issue_date"])) for c in combinations(cands, n)
               if sum(_due(b) for b in c) == amount]
        if out:
            break
    out.sort(key=lambda c: (max(gap(b) for b in c), [str(b.get("invoice_number") or "") for b in c]))
    return out[:MAX_OPTIONS]


def options(tenant_id: str, pop: Dict[str, Any], *, supplier: Optional[Dict[str, Any]] = None,
            taken: Optional[set] = None) -> List[Dict[str, Any]]:
    if not (pop.get("amount_cents") and pop.get("date")):
        return []
    supplier = supplier or supplier_for(tenant_id, pop)
    if not supplier:
        return []
    try:
        day = date.fromisoformat(pop["date"])
    except ValueError:
        return []
    bills = _supplier_bills(tenant_id, supplier, taken if taken is not None else _linked_bills(_pops(tenant_id)))
    return [{"bills": [_bill_shown(b) for b in o], "cents": sum(_due(b) for b in o)}
            for o in _options(int(pop["amount_cents"]), day, bills)]


def review(tenant_id: str) -> Dict[str, Any]:
    """The proofs of payment the business sent that aren't linked yet: each with the supplier found (and
    how) and the bill(s) it may have paid. {"items": [...], "no_supplier": n, "linked": n}"""
    pops = _pops(tenant_id)
    taken = _linked_bills(pops)
    sups = _suppliers(tenant_id)
    items, no_supplier, linked = [], 0, 0
    for d in pops:
        p = read(d)
        if p["link"]:
            linked += 1
            continue
        if not p["amount_cents"] or not outgoing(tenant_id, p):
            continue
        s = supplier_for(tenant_id, p, sups)
        if not s:
            no_supplier += 1
        items.append({**{k: p[k] for k in ("id", "amount_cents", "date", "payee", "payer", "reference", "filename")},
                      "account": f"…{p['account'][-4:]}" if len(p["account"]) >= 4 else None,
                      "supplier": s, "options": options(tenant_id, p, supplier=s, taken=taken) if s else []})
    items.sort(key=lambda x: (not x["options"], x["supplier"] is None, str(x["date"] or "")), reverse=False)
    return {"items": items, "no_supplier": no_supplier, "linked": linked,
            "with_options": sum(1 for x in items if x["options"])}


def _doc(tenant_id: str, doc_id: str) -> Dict[str, Any]:
    from vula.commerce.doc_audit import DOC_COLS
    rows = (_client().table("vula_filed_documents").select(DOC_COLS).eq("tenant_id", tenant_id)
            .eq("id", doc_id).limit(1).execute().data or [])
    if not rows or rows[0].get("category") != CATEGORY:
        raise Refused("That proof of payment isn't on file.")
    return rows[0]


def _who(actor: Optional[dict]) -> str:
    return (actor or {}).get("email") or (actor or {}).get("name") or "owner"


async def link(tenant_id: str, doc_id: str, bill_ids: Any, actor: Optional[dict] = None,
               amount_cents: Optional[int] = None, paid_on: Optional[str] = None) -> Dict[str, Any]:
    """This proof paid those bills (one, or up to three of one supplier, adding up exactly to it). Each
    open bill's payment is recorded once, dated the proof's date; a bill already paid is only tied.
    `amount_cents`/`paid_on` (the WhatsApp question's own figures) are used only where the filed
    document's fields didn't read them."""
    from vula.commerce import service
    from vula.commerce.doc_audit import _save_fields
    doc = _doc(tenant_id, doc_id)
    p = read(doc)
    p["amount_cents"] = p["amount_cents"] or amount_cents
    p["date"] = p["date"] or (str(paid_on)[:10] if paid_on else None)
    if p["link"]:
        raise Refused("That proof of payment is already linked.")
    ids = [str(i) for i in dict.fromkeys([bill_ids] if isinstance(bill_ids, str) else (bill_ids or [])) if i]
    if not ids:
        raise Refused("Choose the bill this payment paid.")
    if len(ids) > MAX_BILLS:
        raise Refused(f"One payment can be linked to at most {MAX_BILLS} bills here.")
    if not p["amount_cents"] or not p["date"]:
        raise Refused("The amount or date on this proof wasn't read — correct its figures first.")
    supplier = supplier_for(tenant_id, p)
    db = _client()
    got = {str(b["id"]): b for b in (db.table("commerce_invoices")
                                     .select("id,invoice_number,supplier,supplier_id,direction,doc_type,status,"
                                             "total_cents,total_paid_cents,project,issue_date")
                                     .eq("tenant_id", tenant_id).in_("id", ids).execute().data or [])}
    bills = [got.get(i) for i in ids]
    if any(b is None or (b.get("direction") or "outbound") != "inbound" or (b.get("doc_type") or "invoice") != "invoice"
           for b in bills):
        raise Refused("That supplier bill isn't there.")
    from vula.commerce.books_review import OPEN_BILL, _supplier_ids, _supplier_key
    sids = _supplier_ids(tenant_id)
    keys = {_supplier_key(b, sids) for b in bills}
    if len(keys) > 1:
        raise Refused("One payment can only be linked to bills from the same supplier.")
    if supplier and keys != {supplier["id"]}:
        raise Refused(f"Those bills aren't {supplier['name']}'s, who this payment went to.")
    for b in bills:
        if b.get("status") not in OPEN_BILL + ("paid",):
            raise Refused(f"{b.get('invoice_number') or 'That bill'} is {b.get('status')}.")
    if set(ids) & _linked_bills(_pops(tenant_id)):
        raise Refused("Another proof of payment is already linked to that bill.")
    shares = {str(b["id"]): _due(b) for b in bills}
    if sum(shares.values()) != p["amount_cents"]:
        what = "what's still owed on the bill" if len(bills) == 1 else "the bills together"
        raise Refused(f"The payment ({p['amount_cents'] / 100:,.2f}) isn't {what} ({sum(shares.values()) / 100:,.2f}).")
    recorded = []
    for b in bills:
        if b.get("status") != "paid":
            await service.record_invoice_payment(
                tenant_id, b["id"], shares[str(b["id"])], payment_method="eft",
                note=f"Proof of payment {p['date']}" + (f" ({p['reference']})" if p["reference"] else ""),
                paid_at=p["date"])
            recorded.append(str(b["id"]))
    f = dict(doc.get("fields") or {})
    sid = next(iter(keys))
    f["pop_link"] = {"supplier_id": sid if sid in sids.values() else None, "bill_ids": ids,
                     "bills": [{"id": str(b["id"]), "number": b.get("invoice_number"), "cents": shares[str(b["id"])],
                                "recorded": str(b["id"]) in recorded} for b in bills],
                     "by": _who(actor), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    cols = {"supplier_id": sid} if sid in sids.values() and not doc.get("supplier_id") else {}
    back = _save_fields(tenant_id, doc_id, f, **cols)
    after = {str(r["id"]): r for r in (db.table("commerce_invoices").select("id,status").eq("tenant_id", tenant_id)
                                       .in_("id", ids).execute().data or [])}
    ok = (((back.get("fields") or {}).get("pop_link") or {}).get("bill_ids") == ids
          and all((after.get(i) or {}).get("status") == "paid" for i in ids))
    try:
        from vula.commerce.books_review import _audit
        _audit(tenant_id, actor, "pop_linked", doc_id=doc_id, invoice_ids=ids, cents=p["amount_cents"],
               recorded=recorded)
    except Exception as exc:  # noqa: BLE001
        log.warning("pop link audit failed for %s: %s", tenant_id, exc)
    nums = ", ".join(str(b.get("invoice_number") or b["id"]) for b in bills)
    return {"ok": ok, "bills": [b.get("invoice_number") or b["id"] for b in bills], "recorded": len(recorded),
            "text": f"Linked to {nums}" + (f"; payment recorded on {p['date']}." if recorded else " (already paid).")}


def dismiss(tenant_id: str, doc_id: str, reason: str, actor: Optional[dict] = None) -> Dict[str, Any]:
    """Not a payment of a supplier's bill (a salary, a deposit, a refund…) — kept with the reason."""
    from vula.commerce.doc_audit import _save_fields
    if not (reason or "").strip():
        raise Refused("Say what it was, so the accountant can see.")
    doc = _doc(tenant_id, doc_id)
    f = dict(doc.get("fields") or {})
    if f.get("pop_link"):
        raise Refused("That proof of payment is already linked.")
    f["pop_link"] = {"none": reason.strip()[:200], "bill_ids": [], "by": _who(actor),
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    back = _save_fields(tenant_id, doc_id, f)
    try:
        from vula.commerce.books_review import _audit
        _audit(tenant_id, actor, "pop_not_a_bill", doc_id=doc_id, reason=reason.strip()[:200])
    except Exception:  # noqa: BLE001
        pass
    return {"ok": bool(((back.get("fields") or {}).get("pop_link") or {}).get("none"))}


def proofs_for_bill(tenant_id: str, bill_id: str) -> List[Dict[str, Any]]:
    """The proofs of payment linked to one bill (for the bill's own view)."""
    out = []
    for d in _pops(tenant_id):
        link = (d.get("fields") or {}).get("pop_link") or {}
        if str(bill_id) in {str(i) for i in (link.get("bill_ids") or [])}:
            p = read(d)
            out.append({"doc_id": d["id"], "date": p["date"], "amount_cents": p["amount_cents"],
                        "filename": d.get("filename")})
    return out

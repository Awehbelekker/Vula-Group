"""vula/commerce/ledger_events.py — every money event posted to the one book (Phase 1, PR 2).

18 Nov 2026 (Ian: "finish Vula's own books first"). For a business whose `books_v2` switch is on,
each place money moves posts here instead of the old cash-only entries in ledger.py. Accrual view
plus a cash view of the same entries (ledger_v2, migration 288).

The rule that makes the bank equal the statements:
  - a recorded payment (invoice, bill, order, company-card expense) posts against
    `clearing`, never the bank; cash goes to `cash_on_hand`, staff's own money to
    `staff_reimbursements`;
  - every live bank line posts bank ↔ something: `clearing` when it is matched to a payment Vula
    recorded (the pair nets to nothing), the account it is categorised to otherwise, `suspense`
    when Vula wasn't sure. A set-aside line stands for nothing.
So the bank in the books is exactly the statements, and what is left in clearing is a payment
with no bank line yet, or a bank line nobody matched — the reconciliation's list (PR 3).

Every entry is keyed by its source, so calling any of these again changes nothing; a change is a
reversal plus the next version (ledger_v2.repost). Money is integer cents, worked out here, never
by a model. Nothing here raises: a posting must never block the business action behind it.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional

from vula.commerce import ledger_v2 as lv

log = logging.getLogger(__name__)

AR, AP = "accounts_receivable", "accounts_payable"
BANK, CLEARING, CASH, STAFF, SUSPENSE = "bank_cash", "clearing", "cash_on_hand", "staff_reimbursements", "suspense"
# A bank line categorised to one of these is money moving between the business, its owner and
# its own accounts — never income or a cost, and never VAT.
BALANCE_SHEET_CODES = frozenset({"owner_capital", "owner_loan", "owner_drawings", "transfer_clearing",
                                 CLEARING, CASH, AR, AP})
UNSURE = frozenset({"default", "asked"})
LIVE_STATUSES = frozenset({"unmatched", "matched", "asked", "claim", "pending"})
DOC_TYPES_NOT_SALES = frozenset({"credit_note", "quote", "proforma"})


def _client():
    from vula.commerce import service
    return service._client()


# ── capture: what the books SHOULD hold, without writing (books_rebuild) ──────
# Inside `capture()`, every posting below records the entry it would make instead of making it,
# reads of the live journal see nothing, and settings/supplier lookups are read once per business.
_cap: ContextVar[Optional[Dict[str, Any]]] = ContextVar("ledger_events_capture", default=None)


@contextmanager
def capture(memo: Optional[Dict[tuple, Any]] = None) -> Iterator[List[Dict[str, Any]]]:
    """`memo` seeds the lookups, e.g. {("settings", tenant): {"accrual_from": "2026-03-01"}}."""
    box: Dict[str, Any] = {"entries": [], "memo": dict(memo or {})}
    token = _cap.set(box)
    try:
        yield box["entries"]
    finally:
        _cap.reset(token)


def _memo(key: tuple, make):
    box = _cap.get()
    if box is None:
        return make()
    if key not in box["memo"]:
        box["memo"][key] = make()
    return box["memo"][key]


def _entry(tenant_id: str, kw: Dict[str, Any]) -> Dict[str, Any]:
    return {"tenant_id": tenant_id, "source_type": kw["source_type"], "source_id": str(kw["source_id"]),
            "entry_date": (kw.get("entry_date") or _today())[:10], "description": kw.get("description") or "",
            "lines": kw["lines"], "doc_id": kw.get("doc_id"), "bank_txn_id": kw.get("bank_txn_id")}


def _post(tenant_id: str, **kw) -> Optional[str]:
    box = _cap.get()
    if box is not None:
        box["entries"].append(_entry(tenant_id, kw))
        return "captured"
    return lv.post(tenant_id, **kw)


def _repost(tenant_id: str, **kw) -> Optional[str]:
    box = _cap.get()
    if box is not None:
        kw.pop("reason", None)
        box["entries"].append(_entry(tenant_id, kw))
        return "captured"
    return lv.repost(tenant_id, **kw)


def _unpost(tenant_id: str, source_type: str, source_id: str, reason: str = "removed") -> Optional[str]:
    if _cap.get() is not None:
        return None
    return lv.unpost(tenant_id, source_type, source_id, reason=reason)


def _live(tenant_id: str, source_type: str, source_id: str) -> Optional[Dict[str, Any]]:
    if _cap.get() is not None:
        return None
    return lv.live_entry(tenant_id, source_type, source_id)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _d(v: Any) -> Optional[str]:
    return str(v)[:10] if v else None


def on(tenant_id: Optional[str]) -> bool:
    return bool(tenant_id) and lv.is_on(tenant_id)


def _ready(tenant_id: str) -> None:
    if _cap.get() is not None:
        return
    try:
        lv.ensure_ledger_chart(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("ledger chart not ready for %s: %s", tenant_id, exc)


def _settings(tenant_id: str) -> Dict[str, Any]:
    return _memo(("settings", tenant_id), lambda: _read_settings(tenant_id))


def _read_settings(tenant_id: str) -> Dict[str, Any]:
    try:
        rows = (_client().table("commerce_books_settings").select("*").eq("tenant_id", tenant_id)
                .limit(1).execute().data or [])
        return rows[0] if rows else {}
    except Exception:  # noqa: BLE001
        return {}


def _vat_registered(tenant_id: str) -> bool:
    from vula.commerce import accounting
    return _memo(("vat", tenant_id), lambda: accounting.is_vat_registered(tenant_id))


def _split(amount: int, vat_total: int, total: int, before: int = 0) -> int:
    """This payment's share of a document's VAT, in the document's own ratio. Worked out on the running
    total (what was paid before plus this), so the shares of every instalment add up to the
    document's VAT exactly — rounding each part on its own lost a cent here and there."""
    if amount <= 0 or total <= 0 or vat_total <= 0:
        return 0
    vat_total = min(vat_total, total)
    before = max(0, min(int(before or 0), total))
    after = min(before + amount, total)
    return max(0, min(after * vat_total // total - before * vat_total // total, amount))


def _fetch(table: str, tenant_id: str, row_id: str) -> Optional[Dict[str, Any]]:
    try:
        rows = (_client().table(table).select("*").eq("tenant_id", tenant_id).eq("id", row_id)
                .limit(1).execute().data or [])
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        log.warning("ledger: couldn't read %s %s: %s", table, row_id, exc)
        return None


def _number(inv: Dict[str, Any]) -> str:
    return str(inv.get("invoice_number") or inv.get("id"))


# ── invoices (money owed to the business) ────────────────────────────────────

def _on_accrual(tenant_id: str, inv: Dict[str, Any]) -> bool:
    """Is this document in the accrual books? Not when it was brought in from another app (it is in
    the opening balances) or dated before the business's accrual start."""
    from vula.commerce import ledger
    if ledger._migrated({"tenant_id": tenant_id, **inv}):
        return False
    # 18 Nov 2026: an order's own invoice (send_order_invoice, or a deposit/balance) is not a second
    # sale: the shop settles the order and the invoice together (_settle_order_invoice /
    # _settle_linked_order) and only one of them posts. So it never sits in debtors; its payment
    # is the sale, like the old books.
    if inv.get("order_id") and (inv.get("direction") or "outbound") == "outbound":
        return False
    start = _d(_settings(tenant_id).get("accrual_from"))
    when = _d(inv.get("issue_date") or inv.get("created_at"))
    return not (start and when and when < start)


def _sale_lines(total: int, vat: int, *, basis: Optional[str], sign: int = 1) -> List[Dict[str, Any]]:
    """The income side: Cr sales + Cr VAT output (sign -1 for the mirror: a credit note)."""
    side = (lambda c, a: lv.line(c, credit=a, basis=basis)) if sign > 0 else (lambda c, a: lv.line(c, a, basis=basis))
    out = [side("sales", total - vat)] if total - vat > 0 else []
    if vat > 0:
        out.append(side("vat_output", vat))
    return out


def invoice_state(tenant_id: str, invoice: Dict[str, Any]) -> Optional[str]:
    """The invoice's own entry (debtors / sales on accrual) follows its status: there while it is
    issued, part paid or paid; gone when it is a draft or cancelled. Returns the live entry id."""
    inv = invoice if invoice.get("total_cents") is not None else (_fetch("commerce_invoices", tenant_id, invoice["id"]) or invoice)
    if (inv.get("direction") or "outbound") == "inbound":
        return bill_state(tenant_id, inv)
    if (inv.get("doc_type") or "invoice") in DOC_TYPES_NOT_SALES:
        return None
    status = (inv.get("status") or "draft").lower()
    total = int(inv.get("total_cents") or 0)
    if status == "cancelled" and int(inv.get("total_paid_cents") or 0) > 0:
        status = "credited"          # settled by credit notes: they take it out of debtors, not this
    if status in ("draft", "cancelled", "void") or total <= 0 or not _on_accrual(tenant_id, inv):
        if _live(tenant_id, "invoice_issued", str(inv["id"])):
            return _unpost(tenant_id, "invoice_issued", str(inv["id"]), reason=f"invoice {status}")
        return None
    _ready(tenant_id)
    vat = max(0, min(int(inv.get("vat_cents") or 0), total))
    lines = [lv.line(AR, total, basis="accrual")] + _sale_lines(total, vat, basis="accrual")
    return _repost(tenant_id, source_type="invoice_issued", source_id=str(inv["id"]),
                     entry_date=_d(inv.get("issue_date") or inv.get("created_at")) or _today(),
                     description=f"Invoice {_number(inv)} issued ({inv.get('customer_name') or 'customer'})",
                     lines=lines, reason="invoice changed")


def _received(method: Optional[str]) -> str:
    return CASH if (method or "").lower() == "cash" else CLEARING


def invoice_payment(tenant_id: str, invoice: Dict[str, Any], *, amount: int, paid_at: Optional[str],
                    source_type: str, source_id: str, method: Optional[str] = None) -> Optional[str]:
    """Money received on an invoice: Dr clearing (both views) / Cr debtors (accrual) / Cr sales + VAT
    in the invoice's ratio (cash). An invoice outside the accrual books is a cash sale on both."""
    if amount <= 0:
        return None
    _ready(tenant_id)
    inv = invoice
    total = int(inv.get("total_cents") or 0)
    vat = _split(amount, int(inv.get("vat_cents") or 0), total, before=int(inv.get("total_paid_cents") or 0))
    money_in = lv.line(_received(method), amount)
    from vula.commerce import ledger
    if ledger._migrated({"tenant_id": tenant_id, **inv}):
        lines = [money_in, lv.line(AR, credit=amount)]          # owed in the opening balances
    elif _on_accrual(tenant_id, inv):
        invoice_state(tenant_id, {**inv, "status": inv.get("status") if inv.get("status") not in (None, "draft") else "sent"})
        lines = [money_in, lv.line(AR, credit=amount, basis="accrual")] + _sale_lines(amount, vat, basis="cash")
    else:
        lines = [money_in] + _sale_lines(amount, vat, basis=None)
    return _post(tenant_id, entry_date=_d(paid_at) or _today(),
                   description=f"Payment on invoice {_number(inv)}", source_type=source_type,
                   source_id=source_id, lines=lines, doc_id=inv.get("doc_id"))


def invoice_paid(tenant_id: str, invoice: Dict[str, Any]) -> Optional[str]:
    """Marked paid in one go: only what isn't paid yet (instalments and credits are in total_paid_cents)."""
    left = int(invoice.get("total_cents") or 0) - int(invoice.get("total_paid_cents") or 0)
    return invoice_payment(tenant_id, invoice, amount=left, paid_at=invoice.get("paid_at"),
                           source_type="invoice_paid", source_id=str(invoice["id"]),
                           method=invoice.get("payment_method"))


def credit_note(tenant_id: str, invoice: Dict[str, Any], credit: Dict[str, Any], amount: int) -> Optional[str]:
    """A credit note set against what the customer still owes: the sale and its VAT come off, and so
    does the debt (accrual only — no money moved, so the cash view has nothing)."""
    if amount <= 0 or not _on_accrual(tenant_id, invoice):
        return None
    _ready(tenant_id)
    vat = _split(amount, int(invoice.get("vat_cents") or 0), int(invoice.get("total_cents") or 0),
                 before=int(invoice.get("total_paid_cents") or 0))
    lines = _sale_lines(amount, vat, basis="accrual", sign=-1) + [lv.line(AR, credit=amount, basis="accrual")]
    return _post(tenant_id, entry_date=_d(credit.get("issue_date") or credit.get("created_at")) or _today(),
                   description=f"Credit note {_number(credit)} against invoice {_number(invoice)}",
                   source_type="credit_note", source_id=str(credit["id"]), lines=lines)


def invoice_refund(tenant_id: str, invoice: Dict[str, Any], credit: Dict[str, Any], amount: int) -> Optional[str]:
    """Money paid back under a credit note: the sale and VAT come off on both views, the money goes
    out through clearing (its bank line clears it)."""
    if amount <= 0:
        return None
    _ready(tenant_id)
    vat = _split(amount, int(invoice.get("vat_cents") or 0), int(invoice.get("total_cents") or 0))
    lines = _sale_lines(amount, vat, basis=None, sign=-1) + [lv.line(CLEARING, credit=amount)]
    return _post(tenant_id, entry_date=_d(credit.get("issue_date")) or _today(),
                   description=f"Refund under credit note {_number(credit)} (invoice {_number(invoice)})",
                   source_type="invoice_refund", source_id=str(credit["id"]), lines=lines)


# ── supplier bills (money the business owes) ─────────────────────────────────

def _bill_account(tenant_id: str, bill: Dict[str, Any]) -> str:
    from vula.commerce import ledger
    if bill.get("account_code"):
        return bill["account_code"]
    if _cap.get() is not None:      # a dry run reads the chart, never seeds it
        return _memo(("supplier_account", tenant_id, bill.get("supplier_id"), bill.get("supplier")),
                     lambda: _supplier_code_read_only(tenant_id, bill))
    return ledger._supplier_account_code(tenant_id, bill.get("supplier_id"), bill.get("supplier"))


def _supplier_code_read_only(tenant_id: str, bill: Dict[str, Any]) -> str:
    """ledger._supplier_account_code without accounting.ensure_chart (which adds missing accounts)."""
    if not (bill.get("supplier_id") or bill.get("supplier")):
        return "other_expense"
    try:
        db = _client()
        q = db.table("commerce_suppliers").select("category").eq("tenant_id", tenant_id)
        q = q.eq("id", bill["supplier_id"]) if bill.get("supplier_id") else q.eq("name", bill["supplier"])
        rows = q.limit(1).execute().data or []
        cat = ((rows[0].get("category") if rows else "") or "").strip().lower().replace(" ", "_")
        codes = _memo(("expense_codes", tenant_id), lambda: {
            a["code"] for a in (db.table("commerce_accounts").select("code,type").eq("tenant_id", tenant_id)
                                .execute().data or []) if a.get("type") == "expense"})
        return cat if cat and cat in codes else "other_expense"
    except Exception:  # noqa: BLE001
        return "other_expense"


def _claimable(tenant_id: str, vat: int) -> int:
    """Input VAT on a supplier's document is claimable only by a VAT-registered business; otherwise
    it is part of the cost (18 Nov 2026: DIGG isn't registered, and R56k of supplier VAT would have
    been booked as claimable)."""
    return vat if vat > 0 and _vat_registered(tenant_id) else 0


def _cost_lines(amount: int, vat: int, account: str, *, basis: Optional[str]) -> List[Dict[str, Any]]:
    out = [lv.line(account, amount - vat, basis=basis)] if amount - vat > 0 else []
    if vat > 0:
        out.append(lv.line("vat_input", vat, basis=basis))
    return out


def bill_state(tenant_id: str, bill: Dict[str, Any]) -> Optional[str]:
    """A supplier bill is in creditors from when it stops being a draft (a draft is a scan nobody has
    checked yet) until it is cancelled."""
    status = (bill.get("status") or "draft").lower()
    total = int(bill.get("total_cents") or 0)
    if (bill.get("doc_type") or "invoice") in DOC_TYPES_NOT_SALES:
        return None
    if status in ("draft", "cancelled", "void") or total <= 0 or not _on_accrual(tenant_id, bill):
        if _live(tenant_id, "bill_recorded", str(bill["id"])):
            return _unpost(tenant_id, "bill_recorded", str(bill["id"]), reason=f"bill {status}")
        return None
    _ready(tenant_id)
    vat = max(0, min(int(bill.get("vat_cents") or 0), total))
    vat = _claimable(tenant_id, vat)
    lines = _cost_lines(total, vat, _bill_account(tenant_id, bill), basis="accrual") + \
        [lv.line(AP, credit=total, basis="accrual")]
    return _repost(tenant_id, source_type="bill_recorded", source_id=str(bill["id"]),
                     entry_date=_d(bill.get("issue_date") or bill.get("created_at")) or _today(),
                     description=f"Supplier invoice {_number(bill)} ({bill.get('supplier') or 'supplier'})",
                     lines=lines, doc_id=bill.get("doc_id"), reason="bill changed")


def bill_payment(tenant_id: str, bill: Dict[str, Any], *, amount: int, paid_at: Optional[str],
                 source_type: str, source_id: str, method: Optional[str] = None) -> Optional[str]:
    """Money paid on a supplier bill: Dr creditors (accrual) / Dr cost + VAT input (cash) /
    Cr clearing (both)."""
    if amount <= 0:
        return None
    _ready(tenant_id)
    total = int(bill.get("total_cents") or 0)
    vat = _claimable(tenant_id, _split(amount, int(bill.get("vat_cents") or 0), total,
                                       before=int(bill.get("total_paid_cents") or 0)))
    money_out = lv.line(_received(method), credit=amount)
    from vula.commerce import ledger
    if ledger._migrated({"tenant_id": tenant_id, **bill}):
        lines = [lv.line(AP, amount), money_out]
    elif _on_accrual(tenant_id, bill):
        bill_state(tenant_id, {**bill, "status": bill.get("status") if bill.get("status") not in (None, "draft") else "sent"})
        lines = [lv.line(AP, amount, basis="accrual")] + \
            _cost_lines(amount, vat, _bill_account(tenant_id, bill), basis="cash") + [money_out]
    else:
        lines = _cost_lines(amount, vat, _bill_account(tenant_id, bill), basis=None) + [money_out]
    return _post(tenant_id, entry_date=_d(paid_at) or _today(),
                   description=f"Paid supplier invoice {_number(bill)} ({bill.get('supplier') or 'supplier'})",
                   source_type=source_type, source_id=source_id, lines=lines, doc_id=bill.get("doc_id"))


def bill_paid(tenant_id: str, bill: Dict[str, Any]) -> Optional[str]:
    left = int(bill.get("total_cents") or 0) - int(bill.get("total_paid_cents") or 0)
    return bill_payment(tenant_id, bill, amount=left, paid_at=bill.get("paid_at"), source_type="bill_paid",
                        source_id=str(bill["id"]), method=bill.get("payment_method"))


# ── orders and the card machine ──────────────────────────────────────────────

def _vat_in(total: int, registered: bool) -> int:
    """VAT inside a VAT-inclusive total (15/115), whole cents — only for a VAT-registered business."""
    return (total * 15 + 57) // 115 if registered and total > 0 else 0


def order_paid(tenant_id: str, order: Dict[str, Any]) -> Optional[str]:
    total = int(order.get("total_cents") or 0)
    if total <= 0:
        return None
    _ready(tenant_id)
    vat = _vat_in(total, _vat_registered(tenant_id))
    return _post(tenant_id, entry_date=_d(order.get("paid_at") or order.get("updated_at")) or _today(),
                   description=f"Order {order.get('display_id') or order.get('id')} paid",
                   source_type="order_paid", source_id=str(order["id"]),
                   lines=[lv.line(CLEARING, total)] + _sale_lines(total, vat, basis=None))


def order_refund(tenant_id: str, order: Dict[str, Any], amount: int, source_id: str) -> Optional[str]:
    if amount <= 0:
        return None
    _ready(tenant_id)
    vat = _vat_in(amount, _vat_registered(tenant_id))
    return _post(tenant_id, entry_date=_d(order.get("updated_at")) or _today(),
                   description=f"Order {order.get('display_id') or order.get('id')} refund",
                   source_type="order_refund", source_id=source_id,
                   lines=_sale_lines(amount, vat, basis=None, sign=-1) + [lv.line(CLEARING, credit=amount)])


def tap_payment(tenant_id: str, payment: Dict[str, Any], lines: Iterable[Any], *, bill_cents: int) -> Optional[str]:
    """A card-machine (tap to pay) payment, split the way vula/tap/core/money.allocate_payment split it
    (kind bill | tip | provider_fee | platform_fee, per party — the business is party 'merchant'):
      Dr clearing (the whole amount; the payout's bank line clears what's left after fees)
      Cr sales + VAT on the bill (VAT only for a registered business)
      a staff member's share of the bill: Dr wages / Cr tips & shares owed to staff
      a tip to staff: Cr owed to staff; a tip the business keeps: Cr other income
      a fee the business bears: Dr bank charges; one a staff member bears: Dr owed to staff
      all fees: Cr clearing.
    A test payment is never posted (the tap service doesn't call this for one)."""
    amount = int(payment.get("amount_cents") or 0)
    if amount <= 0:
        return None
    _ready(tenant_id)
    bill = max(0, min(int(bill_cents or 0), amount))
    out = [lv.line(CLEARING, amount)] + _sale_lines(bill, _vat_in(bill, _vat_registered(tenant_id)), basis=None)
    fees = 0
    for ln in lines:
        kind, party, cents = getattr(ln, "kind", None), getattr(ln, "party", None), int(getattr(ln, "cents", 0) or 0)
        if isinstance(ln, dict):
            kind, party, cents = ln.get("kind"), ln.get("party") or ln.get("party_id"), int(ln.get("cents") or 0)
        staff = party not in (None, "", "merchant")
        if kind == "bill" and staff and cents > 0:
            out += [lv.line("wages", cents, memo="staff share"), lv.line("tips_payable", credit=cents)]
        elif kind == "tip" and cents > 0:
            out.append(lv.line("tips_payable" if staff else "other_income", credit=cents))
        elif kind in ("provider_fee", "platform_fee") and cents < 0:
            out.append(lv.line("tips_payable" if staff else "bank_charges", -cents))
            fees += -cents
    if fees:
        out.append(lv.line(CLEARING, credit=fees))
    return _post(tenant_id, entry_date=_d(payment.get("created_at") or payment.get("paid_at")) or _today(),
                 description=f"Card machine payment {payment.get('provider_ref') or payment.get('id')}",
                 source_type="tap_payment", source_id=str(payment["id"]), lines=out)


# ── expenses ─────────────────────────────────────────────────────────────────

def _paid_from(expense: Dict[str, Any]) -> str:
    who = (expense.get("paid_with") or "").lower()
    if who == "personal" or (expense.get("reimbursable") and who != "company_card"):
        return STAFF
    if who in ("cash", "petty_cash"):
        return CASH
    return CLEARING


def expense(tenant_id: str, expense_row: Dict[str, Any]) -> Optional[str]:
    """A paid expense: Dr cost + VAT input / Cr where the money came from — the company card
    (clearing: its bank line clears it), cash on hand, or staff's own money (owed to them). Posting
    again after an edit (account, amount, who paid) corrects the entry."""
    amount = int(expense_row.get("amount_cents") or 0)
    if amount <= 0:
        return None
    _ready(tenant_id)
    vat = _claimable(tenant_id, max(0, min(int(expense_row.get("vat_cents") or 0), amount)))
    lines = _cost_lines(amount, vat, expense_row.get("account_code") or "other_expense", basis=None) + \
        [lv.line(_paid_from(expense_row), credit=amount)]
    return _repost(tenant_id, source_type="expense", source_id=str(expense_row["id"]),
                     entry_date=_d(expense_row.get("date")) or _today(),
                     description=f"Expense: {expense_row.get('description') or expense_row.get('supplier') or 'expense'}",
                     lines=lines, doc_id=expense_row.get("doc_id"), reason="expense changed")


def expense_removed(tenant_id: str, expense_row: Dict[str, Any], reason: str = "not the business's cost") -> Optional[str]:
    return _unpost(tenant_id, "expense", str(expense_row["id"]), reason=reason)


def expense_reimbursed(tenant_id: str, expense_row: Dict[str, Any]) -> Optional[str]:
    """Staff paid back for a claim: Dr owed to staff / Cr clearing (the bank line clears it)."""
    amount = int(expense_row.get("amount_cents") or 0)
    if amount <= 0 or _paid_from(expense_row) != STAFF:
        return None
    _ready(tenant_id)
    return _post(tenant_id, entry_date=_d(expense_row.get("reimbursed_at")) or _today(),
                   description=f"Claim paid back: {expense_row.get('description') or 'expense'}",
                   source_type="expense_reimbursed", source_id=str(expense_row["id"]),
                   lines=[lv.line(STAFF, amount), lv.line(CLEARING, credit=amount)])


# ── bank lines ───────────────────────────────────────────────────────────────

def _bank_account_ids(tenant_id: str) -> Dict[str, str]:
    """Account number as printed → its commerce_bank_accounts id."""
    try:
        rows = (_client().table("commerce_bank_accounts").select("id,account_number").eq("tenant_id", tenant_id)
                .limit(200).execute().data or [])
        return {str(r.get("account_number")): str(r["id"]) for r in rows if r.get("account_number") and r.get("id")}
    except Exception:  # noqa: BLE001
        return {}


def bank_line_lines(row: Dict[str, Any], *, bank_account_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """What one live bank line says in the books (pure: no database)."""
    amount = int(row.get("amount_cents") or 0)
    money_in = (row.get("direction") or "out") == "in"
    if (row.get("matched_invoice_id") or row.get("matched_order_id") or row.get("matched_expense_id")
            or row.get("categorized_by") == "settlement"):
        other, vat = CLEARING, 0                       # Vula recorded the payment; this clears it
    elif row.get("worker_id"):
        other, vat = row.get("account_code") or "casual_labour", 0
    elif row.get("categorized_by") in UNSURE or not row.get("account_code"):
        other, vat = SUSPENSE, 0
    else:
        other = row["account_code"]
        vat = 0 if other in BALANCE_SHEET_CODES else max(0, min(int(row.get("vat_cents") or 0), amount))
    bank = lv.line(BANK, amount, bank_account_id=bank_account_id) if money_in else \
        lv.line(BANK, credit=amount, bank_account_id=bank_account_id)
    if money_in:
        rest = [lv.line(other, credit=amount - vat)] + ([lv.line("vat_output", credit=vat)] if vat else [])
    else:
        rest = [lv.line(other, amount - vat)] + ([lv.line("vat_input", vat)] if vat else [])
    return [bank] + [ln for ln in rest if ln["debit_cents"] or ln["credit_cents"]]


# A proof of payment sent on WhatsApp is staged as a row in commerce_bank_transactions so its question can
# be asked and answered (bank_rec.stage_pop_for_review, bank_review). It is someone's screenshot, not a
# line on the bank's statement: it never counts in the books — the statement's own line does (20 Nov 2026,
# DIGG: one of the owner's personal payments counted as R2,500 of sales).
POP_SOURCE = "whatsapp_pop"


def is_pop(row: Dict[str, Any]) -> bool:
    return (row.get("source_file") or "") == POP_SOURCE


def is_live(row: Dict[str, Any]) -> bool:
    return ((row.get("match_status") or "unmatched") in LIVE_STATUSES and int(row.get("amount_cents") or 0) > 0
            and not is_pop(row))


def bank_line(tenant_id: str, row: Dict[str, Any], accounts: Dict[str, str]) -> Optional[str]:
    """Post (or correct) one live bank line's entry."""
    return _repost(tenant_id, source_type="bank_line", source_id=str(row["id"]),
                   entry_date=_d(row.get("txn_date")) or _today(),
                   description=(row.get("description") or "bank line")[:300],
                   lines=bank_line_lines(row, bank_account_id=accounts.get(str(row.get("bank_account")))),
                   bank_txn_id=str(row["id"]), reason="bank line changed")


def sync_bank_lines(tenant_id: str, ids: Iterable[str]) -> Dict[str, int]:
    """Make the books say what each bank line says now: posted, corrected (a new version), or taken
    out when the line was set aside. Re-reads the rows, so it is safe to call from anywhere."""
    ids = [str(i) for i in ids if i]
    out = {"posted": 0, "removed": 0, "failed": 0}
    if not ids or not on(tenant_id):
        return out
    _ready(tenant_id)
    accounts = _bank_account_ids(tenant_id)
    rows: List[Dict[str, Any]] = []
    try:
        for i in range(0, len(ids), 150):
            rows += (_client().table("commerce_bank_transactions").select("*").eq("tenant_id", tenant_id)
                     .in_("id", ids[i:i + 150]).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("ledger: bank lines not read for %s: %s", tenant_id, exc)
        out["failed"] = len(ids)
        return out
    for row in rows:
        try:
            if not is_live(row):
                if _live(tenant_id, "bank_line", str(row["id"])):
                    _unpost(tenant_id, "bank_line", str(row["id"]), reason=row.get("excluded_reason") or "set aside")
                    out["removed"] += 1
                continue
            got = bank_line(tenant_id, row, accounts)
            out["posted" if got else "failed"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("ledger: bank line %s not posted: %s", row.get("id"), exc)
            out["failed"] += 1
    return out


def sync_bank_lines_where(tenant_id: str, **match: Any) -> Dict[str, int]:
    """sync_bank_lines for the lines matching simple filters (e.g. source_file=…, after a statement)."""
    if not on(tenant_id):
        return {"posted": 0, "removed": 0, "failed": 0}
    try:
        from vula.db_pages import all_rows

        def q():
            x = _client().table("commerce_bank_transactions").select("id").eq("tenant_id", tenant_id)
            for k, v in match.items():
                x = x.eq(k, v)
            return x
        ids = [r["id"] for r in all_rows(q)]
    except Exception as exc:  # noqa: BLE001
        log.warning("ledger: bank lines not listed for %s: %s", tenant_id, exc)
        return {"posted": 0, "removed": 0, "failed": 1}
    return sync_bank_lines(tenant_id, ids)

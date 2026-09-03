"""
vula/commerce/ledger.py — double-entry posting for the general ledger (migration 121).

Dual-writes journal entries alongside the existing single-entry commerce_orders/
commerce_invoices/commerce_expenses tables — those are unchanged and stay the source of truth
for day-to-day operation; this is an additional layer that lets Vula produce a real trial
balance / balance sheet. Every posting call is best-effort and never raises: a failure here must
never block the actual business action (an order still gets marked paid even if journal posting
fails), matching this codebase's existing defensive style everywhere else.

Idempotency and the debit=credit invariant are enforced by the `post_journal_entry` Postgres RPC
(migration 121), not here — this module just builds the right lines and calls it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _post(tenant_id: str, *, entry_date: str, description: str, source_type: str,
          source_id: Optional[str], lines: List[Dict[str, Any]]) -> None:
    try:
        _client().rpc("post_journal_entry", {
            "p_tenant_id": tenant_id, "p_entry_date": (entry_date or _today())[:10],
            "p_description": description, "p_source_type": source_type,
            "p_source_id": str(source_id) if source_id else None, "p_lines": lines,
        }).execute()
    except Exception as exc:
        log.warning("ledger posting failed (%s/%s): %s", source_type, source_id, exc)


def post_order_paid(tenant_id: str, order: Dict[str, Any]) -> None:
    """commerce_orders has no vat_cents column — the full total posts as Sales, unsplit."""
    total = int(order.get("total_cents") or 0)
    if total <= 0:
        return
    _post(tenant_id, entry_date=order.get("updated_at") or _today(),
          description=f"Order {order.get('display_id') or order.get('id')} paid",
          source_type="order_paid", source_id=order.get("id"),
          lines=[
              {"account_code": "bank_cash", "debit_cents": total, "credit_cents": 0},
              {"account_code": "sales", "debit_cents": 0, "credit_cents": total},
          ])


def post_order_refund(tenant_id: str, order: Dict[str, Any]) -> None:
    total = int(order.get("total_cents") or 0)
    if total <= 0:
        return
    _post(tenant_id, entry_date=order.get("updated_at") or _today(),
          description=f"Order {order.get('display_id') or order.get('id')} refunded",
          source_type="order_refund", source_id=order.get("id"),
          lines=[
              {"account_code": "sales", "debit_cents": total, "credit_cents": 0},
              {"account_code": "bank_cash", "debit_cents": 0, "credit_cents": total},
          ])


def post_invoice_paid(tenant_id: str, invoice: Dict[str, Any]) -> None:
    """commerce_invoices DOES carry vat_cents — split it to vat_output when present."""
    total = int(invoice.get("total_cents") or 0)
    if total <= 0:
        return
    vat = max(0, min(int(invoice.get("vat_cents") or 0), total))
    lines = [{"account_code": "bank_cash", "debit_cents": total, "credit_cents": 0}]
    if vat > 0:
        lines.append({"account_code": "sales", "debit_cents": 0, "credit_cents": total - vat})
        lines.append({"account_code": "vat_output", "debit_cents": 0, "credit_cents": vat})
    else:
        lines.append({"account_code": "sales", "debit_cents": 0, "credit_cents": total})
    _post(tenant_id, entry_date=invoice.get("paid_at") or _today(),
          description=f"Invoice {invoice.get('invoice_number') or invoice.get('id')} paid",
          source_type="invoice_paid", source_id=invoice.get("id"), lines=lines)


def post_invoice_payment(tenant_id: str, invoice: Dict[str, Any], payment: Dict[str, Any]) -> None:
    """A single partial (or final) instalment received against an invoice (migration 130) —
    posts ONLY this payment's amount, dated when it was actually received, not lumped into one
    entry when the balance finally clears (real cash-flow timing in the trial balance). VAT is
    split proportionally using the invoice's own overall vat_cents/total_cents ratio, which
    works regardless of VAT-inclusive/exclusive pricing since that's already baked into those
    two figures. source_id is the PAYMENT's own id, not the invoice's — each instalment needs
    its own idempotency key under the post_journal_entry RPC's unique(tenant_id, source_type,
    source_id) constraint, otherwise a second payment on the same invoice would collide with
    the first and silently be dropped as "already posted"."""
    amount = int(payment.get("amount_cents") or 0)
    if amount <= 0:
        return
    inv_total = int(invoice.get("total_cents") or 0)
    inv_vat = int(invoice.get("vat_cents") or 0)
    vat = (amount * inv_vat // inv_total) if inv_total > 0 else 0
    vat = max(0, min(vat, amount))
    lines = [{"account_code": "bank_cash", "debit_cents": amount, "credit_cents": 0}]
    if vat > 0:
        lines.append({"account_code": "sales", "debit_cents": 0, "credit_cents": amount - vat})
        lines.append({"account_code": "vat_output", "debit_cents": 0, "credit_cents": vat})
    else:
        lines.append({"account_code": "sales", "debit_cents": 0, "credit_cents": amount})
    _post(tenant_id, entry_date=(payment.get("paid_at") or _today()),
          description=f"Payment on invoice {invoice.get('invoice_number') or invoice.get('id')}",
          source_type="invoice_payment", source_id=payment.get("id"), lines=lines)


def _supplier_account_code(tenant_id: Optional[str], supplier_id: Optional[str],
                           supplier_name: Optional[str]) -> str:
    """The expense account for a supplier, from their stored category — or "other_expense".

    Only an EXACT match against a real chart-of-accounts expense code is accepted. Supplier
    categories are free text ("food", "packaging"), so "packaging" maps cleanly while "food"
    deliberately does not — inventing a mapping would silently file spend under an account the
    tenant never chose, which is harder to spot than an honestly generic one.
    """
    if not tenant_id or not (supplier_id or supplier_name):
        return "other_expense"
    try:
        from vula.commerce import accounting, service
        q = service._client().table("commerce_suppliers").select("category") \
            .eq("tenant_id", tenant_id)
        q = q.eq("id", supplier_id) if supplier_id else q.eq("name", supplier_name)
        rows = q.limit(1).execute().data or []
        cat = ((rows[0].get("category") if rows else "") or "").strip().lower().replace(" ", "_")
        if not cat:
            return "other_expense"
        expense_codes = {a["code"] for a in accounting.ensure_chart(tenant_id)
                         if a.get("type") == "expense"}
        return cat if cat in expense_codes else "other_expense"
    except Exception as exc:
        log.debug("supplier account-code lookup skipped: %s", exc)
        return "other_expense"


def post_supplier_invoice_paid(tenant_id: str, invoice: Dict[str, Any]) -> None:
    """We paid a SUPPLIER's invoice — money leaving, not revenue.

    2026-09-03: the only invoice postings were sales-side (post_invoice_paid credits `sales`,
    post_invoice_payment debits `bank_cash`). update_invoice_status called post_invoice_paid
    unconditionally, so marking an INBOUND supplier bill paid would have booked money going out
    as revenue — inventing income and inflating VAT output at the same time. Nothing had marked
    an inbound bill paid yet, so no live damage, but bank reconciliation matching outgoing
    payments was about to.

    Mirrors post_expense: credit bank_cash, debit the expense account, VAT to input (reclaimable)
    rather than output (owed).
    """
    amount = int(invoice.get("total_cents") or 0)
    if amount <= 0:
        return
    vat = max(0, min(int(invoice.get("vat_cents") or 0), amount))
    # commerce_invoices carries no account_code column, so without this every supplier payment
    # would land in "other_expense" and the trial balance would show all supplier spend in one
    # undifferentiated bucket. The supplier record does carry a category; use it only when it
    # names a REAL expense account — never a guess, since a wrong account is worse than a
    # deliberately generic one.
    account_code = invoice.get("account_code") or _supplier_account_code(
        invoice.get("tenant_id"), invoice.get("supplier_id"), invoice.get("supplier"))
    lines = [{"account_code": "bank_cash", "debit_cents": 0, "credit_cents": amount}]
    if vat > 0:
        lines.append({"account_code": account_code, "debit_cents": amount - vat, "credit_cents": 0})
        lines.append({"account_code": "vat_input", "debit_cents": vat, "credit_cents": 0})
    else:
        lines.append({"account_code": account_code, "debit_cents": amount, "credit_cents": 0})
    _post(tenant_id, entry_date=(invoice.get("paid_at") or _today()),
          description=f"Paid supplier invoice "
                      f"{invoice.get('invoice_number') or invoice.get('id')}"
                      f" ({invoice.get('supplier') or 'supplier'})",
          source_type="supplier_invoice_paid", source_id=invoice.get("id"), lines=lines)


def post_expense(tenant_id: str, expense: Dict[str, Any]) -> None:
    amount = int(expense.get("amount_cents") or 0)
    if amount <= 0:
        return
    vat = max(0, min(int(expense.get("vat_cents") or 0), amount))
    account_code = expense.get("account_code") or "other_expense"
    lines = [{"account_code": "bank_cash", "debit_cents": 0, "credit_cents": amount}]
    if vat > 0:
        lines.append({"account_code": account_code, "debit_cents": amount - vat, "credit_cents": 0})
        lines.append({"account_code": "vat_input", "debit_cents": vat, "credit_cents": 0})
    else:
        lines.append({"account_code": account_code, "debit_cents": amount, "credit_cents": 0})
    _post(tenant_id, entry_date=expense.get("date") or _today(),
          description=f"Expense: {expense.get('description') or expense.get('supplier') or 'expense'}",
          source_type="expense", source_id=expense.get("id"), lines=lines)


def trial_balance(tenant_id: str, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
    """Sum debits/credits per account across posted journal entries — total debits should always
    equal total credits, since every entry is balance-checked at post time by the RPC."""
    db = _client()
    try:
        q = db.table("journal_entries").select("id").eq("tenant_id", tenant_id)
        if since:
            q = q.gte("entry_date", since)
        if until:
            q = q.lte("entry_date", until)
        entries = q.execute().data or []
    except Exception as exc:
        return {"error": f"{exc} (run migration 121?)"}

    entry_ids = [e["id"] for e in entries]
    if not entry_ids:
        return {"since": since, "until": until, "accounts": [],
                "total_debit_cents": 0, "total_credit_cents": 0}

    lines = (db.table("journal_lines").select("account_id,debit_cents,credit_cents")
             .in_("journal_entry_id", entry_ids).execute().data or [])
    accounts = {a["id"]: a for a in (db.table("commerce_accounts").select("id,code,name,type")
                .eq("tenant_id", tenant_id).execute().data or [])}

    totals: Dict[str, Dict[str, Any]] = {}
    for l in lines:
        acct = accounts.get(l["account_id"], {})
        code = acct.get("code", "unknown")
        row = totals.setdefault(code, {"code": code, "name": acct.get("name", code),
                                        "type": acct.get("type", ""),
                                        "debit_cents": 0, "credit_cents": 0})
        row["debit_cents"] += int(l.get("debit_cents") or 0)
        row["credit_cents"] += int(l.get("credit_cents") or 0)

    rows = sorted(totals.values(), key=lambda r: r["code"])
    return {
        "since": since, "until": until, "accounts": rows,
        "total_debit_cents": sum(r["debit_cents"] for r in rows),
        "total_credit_cents": sum(r["credit_cents"] for r in rows),
    }

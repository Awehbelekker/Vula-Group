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

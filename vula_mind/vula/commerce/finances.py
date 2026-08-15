"""
vula/commerce/finances.py — plain-language financial insights for a commerce tenant.

"Am I profitable this month? What's my VAT owing? Who owes me money?"

Computes real numbers from the existing tables (no new migration):
  • revenue  — paid orders + paid invoices in the period
  • expenses — commerce_expenses in the period
  • gross profit + margin
  • output VAT collected — from invoice vat_cents (SA VAT indicator; input VAT isn't tracked
    per-expense yet, so this is VAT *collected*, clearly labelled — not a filed return)
  • receivables — invoices still owed (sent) and overdue

The numbers are deterministic and instant. A short LLM narrative is generated on request
(explain=True) FROM those numbers, so it can never contradict them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

log = logging.getLogger(__name__)

_PAID_ORDER = lambda s: s not in ("pending_payment", "cancelled", "refunded")


def _client():
    from vula.commerce import service
    return service._client()


def _cents(rows: List[dict], field: str, pred=lambda r: True) -> int:
    return sum(int(r.get(field) or 0) for r in rows if pred(r))


# Standard accounts-receivable aging breakdown (2026-08-15) — for "who do I chase first", not a
# replacement for the netted `receivable` total above. Deliberately gross per bucket, not netted
# against credit notes — a credit note isn't tied to any one aging bucket, so there's no clean
# way to net it into one; the top-level `receivable` figure stays the authoritative net number.
def _aging_buckets(out_inv: List[dict], today) -> Dict[str, int]:
    buckets = {"current": 0, "days_1_30": 0, "days_31_60": 0, "days_61_90": 0, "days_90_plus": 0}
    for i in out_inv:
        if i.get("status") not in ("sent", "overdue"):
            continue
        total = int(i.get("total_cents") or 0)
        due = i.get("due_date")
        if not due:
            buckets["current"] += total
            continue
        try:
            days_over = (today - datetime.fromisoformat(str(due)[:10]).date()).days
        except Exception:
            buckets["current"] += total
            continue
        if days_over <= 0:
            buckets["current"] += total
        elif days_over <= 30:
            buckets["days_1_30"] += total
        elif days_over <= 60:
            buckets["days_31_60"] += total
        elif days_over <= 90:
            buckets["days_61_90"] += total
        else:
            buckets["days_90_plus"] += total
    return buckets


async def insights(tenant_id: str, days: int = 30) -> Dict[str, Any]:
    """Deterministic financial snapshot for the last `days`."""
    db = _client()
    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=days - 1)).isoformat()

    # Orders (revenue from the shop)
    orders = (db.table("commerce_orders").select("total_cents,status,created_at,customer_name")
              .eq("tenant_id", tenant_id).gte("created_at", since).execute().data or [])
    paid_orders = [o for o in orders if _PAID_ORDER(o.get("status"))]
    order_rev = _cents(paid_orders, "total_cents")

    # Invoices (invoiced revenue + VAT + receivables). Receivables are all-time, not period-bound.
    # commerce_invoices also stores quotes and credit notes — doc_type=="invoice" filtering and
    # netting credit notes out of receivables (they're money owed BACK to the customer, not
    # additional money owed to the tenant) were both missing here before this fix (2026-08-15),
    # same bug as admin_stats/admin_customer_detail in vula/api/commerce.py.
    inv_all = (db.table("commerce_invoices")
               .select("total_cents,vat_cents,subtotal_cents,status,created_at,customer_name,direction,doc_type,due_date")
               .eq("tenant_id", tenant_id).execute().data or [])
    out_inv = [i for i in inv_all if (i.get("direction") or "outbound") == "outbound"
              and i.get("doc_type") == "invoice"]
    out_credits = [i for i in inv_all if (i.get("direction") or "outbound") == "outbound"
                   and i.get("doc_type") == "credit_note"]
    inv_paid_period = [i for i in out_inv if i.get("status") == "paid" and (i.get("created_at") or "")[:10] >= since]
    inv_rev = _cents(inv_paid_period, "total_cents")
    vat_collected = _cents(inv_paid_period, "vat_cents")
    credited = _cents(out_credits, "total_cents")
    receivable = _cents(out_inv, "total_cents", lambda r: r.get("status") == "sent") - credited
    overdue = _cents(out_inv, "total_cents", lambda r: r.get("status") == "overdue")
    receivable_count = len([i for i in out_inv if i.get("status") in ("sent", "overdue")])
    aging = _aging_buckets(out_inv, today)

    # Expenses (costs)
    try:
        exp = (db.table("commerce_expenses").select("amount_cents,category,date")
               .eq("tenant_id", tenant_id).gte("date", since).execute().data or [])
    except Exception as exc:
        log.debug("finances: expenses read skipped: %s", exc)
        exp = []
    expenses = _cents(exp, "amount_cents")
    by_cat: Dict[str, int] = {}
    for e in exp:
        by_cat[e.get("category") or "other"] = by_cat.get(e.get("category") or "other", 0) + int(e.get("amount_cents") or 0)
    top_expenses = sorted(
        [{"category": k, "amount_cents": v} for k, v in by_cat.items()],
        key=lambda x: x["amount_cents"], reverse=True)[:5]

    revenue = order_rev + inv_rev
    gross = revenue - expenses
    margin = round(gross / revenue * 100, 1) if revenue else 0.0

    # Top customers by paid-order revenue in the period.
    cust: Dict[str, int] = {}
    for o in paid_orders:
        n = (o.get("customer_name") or "").strip() or "—"
        cust[n] = cust.get(n, 0) + int(o.get("total_cents") or 0)
    top_customers = sorted(
        [{"name": k, "revenue_cents": v} for k, v in cust.items() if k != "—"],
        key=lambda x: x["revenue_cents"], reverse=True)[:5]

    return {
        "period_days": days, "since": since, "until": today.isoformat(),
        "revenue_cents": revenue,
        "revenue_breakdown": {"orders_cents": order_rev, "invoices_cents": inv_rev},
        "expenses_cents": expenses,
        "gross_profit_cents": gross,
        "margin_pct": margin,
        "vat": {"collected_cents": vat_collected,
                "note": "Output VAT collected on paid invoices. Input VAT on expenses isn't itemised — treat as an indicator, not a filed return."},
        "receivables": {"outstanding_cents": receivable, "overdue_cents": overdue,
                        "count": receivable_count, "aging": aging},
        "orders_count": len(paid_orders),
        "top_customers": top_customers,
        "top_expense_categories": top_expenses,
    }


def _r(cents: int) -> str:
    return f"R{(cents or 0) / 100:,.2f}"


async def narrate(tenant_id: str, data: Dict[str, Any]) -> str:
    """A short, honest plain-language read of the numbers (generated FROM them)."""
    import litellm
    from core.llm_router import resolve_generation_route
    from vula.api import tenants as _tenants

    name = _tenants.get_config(tenant_id).get("display_name") or "the business"
    facts = (
        f"Period: last {data['period_days']} days ({data['since']} to {data['until']}).\n"
        f"Revenue: {_r(data['revenue_cents'])} "
        f"(orders {_r(data['revenue_breakdown']['orders_cents'])}, invoices {_r(data['revenue_breakdown']['invoices_cents'])}).\n"
        f"Expenses: {_r(data['expenses_cents'])}.\n"
        f"Gross profit: {_r(data['gross_profit_cents'])} (margin {data['margin_pct']}%).\n"
        f"VAT collected on paid invoices: {_r(data['vat']['collected_cents'])}.\n"
        f"Owed to us (unpaid invoices): {_r(data['receivables']['outstanding_cents'])}, "
        f"of which overdue: {_r(data['receivables']['overdue_cents'])}.\n"
        f"Paid orders: {data['orders_count']}.\n"
    )
    prompt = (
        f"You are a no-nonsense bookkeeper for {name}, a South African small business.\n"
        f"Here are this period's ACTUAL figures — use ONLY these, do not invent anything:\n\n{facts}\n"
        f"Write 3–5 short sentences in plain English: are they profitable, what stands out, and the "
        f"single most useful action (e.g. chase overdue invoices, watch a cost). Mention VAT only as "
        f"'collected', not as a filed amount. Be direct and encouraging. No headings, no bullet points."
    )
    model, api_key, api_base = await resolve_generation_route(task_type="finance_insight")
    try:
        import re
        resp = await litellm.acompletion(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=280, api_key=api_key, api_base=api_base)
        return re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.DOTALL).strip()
    except Exception as exc:
        log.warning("finance narrate failed: %s", exc)
        # Deterministic fallback so the panel always says something useful.
        verdict = "profitable" if data["gross_profit_cents"] > 0 else "running at a loss"
        tip = (f" You have {_r(data['receivables']['overdue_cents'])} in overdue invoices — chase those first."
               if data["receivables"]["overdue_cents"] > 0 else "")
        return (f"Over the last {data['period_days']} days you brought in {_r(data['revenue_cents'])} "
                f"against {_r(data['expenses_cents'])} in costs, so you're {verdict} "
                f"({_r(data['gross_profit_cents'])}, {data['margin_pct']}% margin).{tip}")

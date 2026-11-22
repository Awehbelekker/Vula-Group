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
from typing import Any, Dict, List, Optional

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
def _owed(inv: dict) -> int:
    """What is still owed on an invoice: its total less instalments and credit notes set against it
    (both are in total_paid_cents). 18 Nov 2026: a part-paid invoice was left out of debtors."""
    return max(0, int(inv.get("total_cents") or 0) - int(inv.get("total_paid_cents") or 0))


def _aging_buckets(out_inv: List[dict], today, owed=None,
                   statuses=("sent", "overdue", "part_paid")) -> Dict[str, int]:
    owed = owed or _owed
    buckets = {"current": 0, "days_1_30": 0, "days_31_60": 0, "days_61_90": 0, "days_90_plus": 0}
    for i in out_inv:
        if i.get("status") not in statuses:
            continue
        total = owed(i)
        if total <= 0:
            continue
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


_BUCKETS = ("current", "days_1_30", "days_31_60", "days_61_90", "days_90_plus")


def _bucket(days_over: Optional[int]) -> str:
    if days_over is None or days_over <= 0:
        return "current"
    return ("days_1_30" if days_over <= 30 else "days_31_60" if days_over <= 60
            else "days_61_90" if days_over <= 90 else "days_90_plus")


def _day(v: Any) -> Optional[str]:
    s = str(v or "")[:10]
    return s if len(s) == 10 else None


def aged_as_at(tenant_id: str, as_at: Optional[str] = None, direction: str = "outbound") -> Dict[str, Any]:
    """What was still owed on a date — by customers (`outbound`, debtors) or to suppliers (`inbound`,
    creditors) — by how far past due each document was on that date (22 Nov 2026, the year-end pack).

    A document counts when it was issued on or before the date and isn't a draft or cancelled. What was
    owed is its total less the payments (and credit notes) on record dated on or before the date; a
    document marked paid with less than its total on record counts the rest as paid on its paid date.
    So a payment made after year end leaves the document owed at year end, as it was."""
    if direction not in ("outbound", "inbound"):
        raise ValueError("direction is outbound or inbound")
    db = _client()
    day = datetime.fromisoformat(as_at[:10]).date() if as_at else datetime.now(timezone.utc).date()
    cut = day.isoformat()
    empty = {"as_at": cut, "direction": direction, "total_cents": 0, "count": 0,
             "aging": {k: 0 for k in _BUCKETS}, "parties": [], "items": []}
    try:
        from vula.commerce.ledger import _all_pages
        docs = _all_pages(lambda: db.table("commerce_invoices")
                          .select("id,invoice_number,supplier,supplier_id,customer_name,total_cents,status,"
                                  "issue_date,due_date,paid_at,created_at,direction,doc_type")
                          .eq("tenant_id", tenant_id).eq("direction", direction).order("id"))
    except Exception as exc:  # noqa: BLE001
        log.warning("aged %s skipped for %s: %s", direction, tenant_id, exc)
        return empty
    docs = [d for d in docs if (d.get("doc_type") or "invoice") == "invoice"
            and d.get("status") not in ("draft", "cancelled", "void")
            and (_day(d.get("issue_date")) or _day(d.get("created_at")) or cut) <= cut]
    paid: Dict[str, int] = {}
    ids = [d["id"] for d in docs]
    for i in range(0, len(ids), 150):
        try:
            for p in (db.table("commerce_invoice_payments").select("invoice_id,amount_cents,paid_at,created_at")
                      .eq("tenant_id", tenant_id).in_("invoice_id", ids[i:i + 150]).execute().data or []):
                if (_day(p.get("paid_at")) or _day(p.get("created_at")) or cut) <= cut:
                    paid[p["invoice_id"]] = paid.get(p["invoice_id"], 0) + int(p.get("amount_cents") or 0)
        except Exception:  # noqa: BLE001
            pass
    names: Dict[str, str] = {}
    if direction == "inbound":
        try:
            names = {x["id"]: x.get("name") for x in (db.table("commerce_suppliers").select("id,name")
                                                      .eq("tenant_id", tenant_id).execute().data or [])}
        except Exception:  # noqa: BLE001
            names = {}
    aging = {k: 0 for k in _BUCKETS}
    per: Dict[str, Dict[str, int]] = {}
    items = []
    for d in docs:
        total = int(d.get("total_cents") or 0)
        owed = total - paid.get(d["id"], 0)
        if d.get("status") == "paid" and (_day(d.get("paid_at")) or cut) <= cut:
            owed = 0                                    # marked paid by then: the rest was paid too
        if owed <= 0:
            continue
        due = _day(d.get("due_date"))
        days_over = (day - datetime.fromisoformat(due).date()).days if due else None
        name = ((names.get(d.get("supplier_id")) or d.get("supplier") or d.get("customer_name") or "Supplier not named")
                if direction == "inbound" else (d.get("customer_name") or "Customer not named"))
        aging[_bucket(days_over)] += owed
        p = per.setdefault(name, {"owed_cents": 0, "count": 0})
        p["owed_cents"] += owed
        p["count"] += 1
        items.append({"id": d["id"], "number": d.get("invoice_number") or "", "name": name,
                      "issue_date": _day(d.get("issue_date")), "due_date": due,
                      "days_over": max(days_over, 0) if days_over is not None else None,
                      "total_cents": total, "owed_cents": owed})
    items.sort(key=lambda x: (x["name"].lower(), x["issue_date"] or "", x["number"]))
    out = {"as_at": cut, "direction": direction, "total_cents": sum(x["owed_cents"] for x in items),
           "count": len(items), "aging": aging,
           "parties": [{"name": k, **v} for k, v in sorted(per.items(), key=lambda kv: -kv[1]["owed_cents"])],
           "items": items}
    from vula.commerce import ledger_v2
    if ledger_v2.is_on(tenant_id):
        from vula.commerce import books_reports
        code = "accounts_payable" if direction == "inbound" else "accounts_receivable"
        out["control_cents"] = books_reports.control(tenant_id, code, cut)
        out["ties"] = out["control_cents"] == out["total_cents"]
    return out


def aged_creditors(tenant_id: str, as_at: Optional[str] = None) -> Dict[str, Any]:
    """What the business still owes its suppliers, by how far past due each bill is (every business).

    A bill counts once it's recorded (not a draft, not cancelled); what's owed is its total less the
    payments on record against it, not a stored running total (DIGG had bills marked paid with no
    payment). For a business on the one book the total is set beside the creditors account.
    22 Nov 2026: read through aged_as_at — the old query named a column that doesn't exist
    (`number`), so it found nothing in production."""
    a = aged_as_at(tenant_id, as_at, "inbound")
    out = {"as_at": a["as_at"], "total_cents": a["total_cents"], "count": a["count"], "aging": a["aging"],
           "suppliers": [{"name": p["name"], "owed_cents": p["owed_cents"]} for p in a["parties"]][:20]}
    for k in ("control_cents", "ties"):
        if k in a:
            out[k] = a[k]
    return out


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
               .select("total_cents,total_paid_cents,vat_cents,subtotal_cents,status,created_at,customer_name,direction,doc_type,"
                       "due_date,credited_invoice_id")
               .eq("tenant_id", tenant_id).execute().data or [])
    out_inv = [i for i in inv_all if (i.get("direction") or "outbound") == "outbound"
              and i.get("doc_type") == "invoice"]
    # A credit note tied to an invoice (credit_invoice) is already in that invoice's total_paid_cents;
    # an older one written by hand isn't tied to anything, so it still comes off the total owed.
    loose_credits = sum(int(i.get("total_cents") or 0) for i in inv_all
                        if (i.get("direction") or "outbound") == "outbound" and i.get("doc_type") == "credit_note"
                        and not i.get("credited_invoice_id"))
    inv_paid_period = [i for i in out_inv if i.get("status") == "paid" and (i.get("created_at") or "")[:10] >= since]
    inv_rev = _cents(inv_paid_period, "total_cents")
    vat_collected = _cents(inv_paid_period, "vat_cents")
    # What is still owed (credit notes against an invoice are already in its total_paid_cents; a
    # credit note beyond what was owed was refunded, so it isn't owed either way).
    receivable = sum(_owed(i) for i in out_inv if i.get("status") in ("sent", "part_paid")) - loose_credits
    overdue = sum(_owed(i) for i in out_inv if i.get("status") == "overdue")
    receivable_count = len([i for i in out_inv if i.get("status") in ("sent", "overdue", "part_paid")
                            and _owed(i) > 0])
    aging = _aging_buckets(out_inv, today)
    from vula.commerce.payment_behavior import tenant_watch_list
    payment_behavior = await tenant_watch_list(tenant_id)

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

    result = {
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
        "payment_behavior": payment_behavior,
        "orders_count": len(paid_orders),
        "top_customers": top_customers,
        "top_expense_categories": top_expenses,
    }
    from vula.commerce import ledger_v2
    if ledger_v2.is_on(tenant_id):
        _from_books(tenant_id, result, since, today.isoformat())
    return result


def _from_books(tenant_id: str, result: Dict[str, Any], since: str, until: str) -> None:
    """A business on the one book: income, costs, profit and VAT from its journal (accrual view),
    and debtors and creditors set beside their control accounts. Orders, customers and the aging
    lists stay as they are (they're about documents, not the book)."""
    from vula.commerce import books_reports, reports
    p = reports.profit_and_loss(tenant_id, since, until, "accrual")
    v = books_reports.vat(tenant_id, since, until)
    revenue, expenses = p["total_income_cents"], p["total_expense_cents"]
    result.update({
        "source": books_reports.SOURCE, "basis": "accrual",
        "revenue_cents": revenue, "expenses_cents": expenses, "gross_profit_cents": revenue - expenses,
        "margin_pct": round((revenue - expenses) / revenue * 100, 1) if revenue else 0.0,
        "revenue_breakdown": {**result["revenue_breakdown"],
                              "by_account": [{"account": r["name"], "code": r["code"], "amount_cents": r["cents"]}
                                             for r in p["income"]]},
        "top_expense_categories": [{"category": r["name"], "amount_cents": r["cents"]}
                                   for r in sorted(p["expenses"], key=lambda r: -r["cents"])[:5]],
        "vat": {"collected_cents": int(v.get("output_vat_cents") or 0),
                "claimable_cents": int(v.get("input_vat_cents") or 0),
                "note": ("VAT from your books (" + ("invoice" if v.get("basis") == "accrual" else "payments")
                         + " basis) — your accountant files the return.") if v.get("vat_registered")
                else "Not VAT registered — no VAT charged or claimed."},
    })
    rec = result["receivables"]
    rec["control_cents"] = books_reports.control(tenant_id, "accounts_receivable", until)
    rec["ties"] = rec["control_cents"] == rec["outstanding_cents"] + rec["overdue_cents"]
    result["payables"] = aged_creditors(tenant_id, until)


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

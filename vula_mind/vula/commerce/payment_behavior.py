"""
vula/commerce/payment_behavior.py — "who actually pays on time" from real invoice history.

The dunning cadence (commerce.py::_process_overdue_invoices) and aging breakdown
(finances.py::_aging_buckets) both answer "who's overdue right now." This answers a related
but different question: "who is HISTORICALLY reliable" — a customer who's overdue today but
has paid every one of their last 10 invoices within a week of the due date is a very different
collections risk from one who's late for the first time. Pure integer-date arithmetic on real
paid_at/due_date pairs — no LLM involvement, same discipline as every other money computation
in this codebase.

Deliberately conservative: a customer with fewer than MIN_SAMPLE paid invoices gets no score at
all rather than a confident-sounding label built on one data point.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

MIN_SAMPLE = 2


def _parse_date(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def customer_payment_behavior(invoices: List[dict]) -> Dict[str, Any]:
    """From one customer's own invoice rows (doc_type=='invoice'), compute an on-time-payment
    summary. `invoices` needs status/due_date/paid_at/total_cents per row — callers already
    fetch this shape for other purposes (e.g. admin_customer_detail), so no new query pattern."""
    paid = []
    for i in invoices:
        if i.get("status") != "paid":
            continue
        due = _parse_date(i.get("due_date"))
        paid_at = _parse_date(i.get("paid_at"))
        if due is None or paid_at is None:
            continue
        paid.append((due, paid_at, int(i.get("total_cents") or 0)))

    if len(paid) < MIN_SAMPLE:
        return {"sample_size": len(paid), "label": "not_enough_history",
                "on_time_pct": None, "avg_days_late": None}

    days_late = [(paid_at - due).days for due, paid_at, _ in paid]
    on_time = sum(1 for d in days_late if d <= 0)
    on_time_pct = round(on_time / len(paid) * 100)
    avg_days_late = round(sum(max(0, d) for d in days_late) / len(paid), 1)

    if on_time_pct >= 90:
        label = "reliable"
    elif on_time_pct >= 70:
        label = "usually_on_time"
    elif on_time_pct >= 40:
        label = "frequently_late"
    else:
        label = "high_risk"

    return {"sample_size": len(paid), "label": label,
            "on_time_pct": on_time_pct, "avg_days_late": avg_days_late}


async def tenant_watch_list(tenant_id: str, limit: int = 10) -> Dict[str, Any]:
    """Tenant-wide collections-risk view: an overall on-time rate across every paid invoice,
    plus a ranked "watch list" of customers whose OWN history skews late — the people worth
    chasing more carefully next time, not just whoever happens to be overdue today."""
    from vula.commerce import service
    db = service._client()
    try:
        rows = (db.table("commerce_invoices")
                .select("customer_phone,customer_name,status,due_date,paid_at,total_cents,doc_type")
                .eq("tenant_id", tenant_id).eq("status", "paid").eq("doc_type", "invoice")
                .limit(5000).execute().data or [])
    except Exception:
        rows = []

    by_customer: Dict[str, List[dict]] = {}
    for r in rows:
        phone = (r.get("customer_phone") or "").strip()
        if not phone:
            continue
        by_customer.setdefault(phone, []).append(r)

    overall_paid = [r for r in rows if r.get("due_date") and r.get("paid_at")]
    overall = customer_payment_behavior(overall_paid) if overall_paid else {
        "sample_size": 0, "label": "not_enough_history", "on_time_pct": None, "avg_days_late": None}

    watch = []
    for phone, invs in by_customer.items():
        score = customer_payment_behavior(invs)
        if score["label"] in ("frequently_late", "high_risk"):
            watch.append({
                "customer_phone": phone,
                "customer_name": next((i.get("customer_name") for i in invs if i.get("customer_name")), None),
                **score,
            })
    watch.sort(key=lambda w: (w["on_time_pct"] if w["on_time_pct"] is not None else 100, -w["sample_size"]))

    return {"overall": overall, "watch_list": watch[:limit], "customers_scored": len(by_customer)}

"""
vula/commerce/recurring_bills.py — recurring bills (rent, utilities, subscriptions, supplier
accounts) that a tenant owes on a schedule, not just spends they've already made.

A recurring bill ("Rent, R15,000, due the 1st of every month") spawns a real commerce_expenses
row (status='pending', due_date set) ahead of each due date via lead_days, so it shows up in the
existing "Payments due" view (GET /admin/expenses/due) with advance warning. The owner marks it
paid via the existing PATCH /admin/expenses/{id}/pay — nothing new needed there.

Not to be confused with expense CLAIMS (vula/commerce/expenses.py): a claim records money
already spent that may need reimbursing; a recurring bill is a known future commitment.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CADENCES = ("weekly", "biweekly", "monthly")
_STEP_DAYS = {"weekly": 7, "biweekly": 14}


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _advance(d: date, cadence: str) -> date:
    if cadence in _STEP_DAYS:
        return d + timedelta(days=_STEP_DAYS[cadence])
    # monthly — same day next month (clamped to month length)
    y, m = (d.year + (1 if d.month == 12 else 0)), (1 if d.month == 12 else d.month + 1)
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def create(tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    description = (body.get("description") or "").strip()
    amount_cents = int(body.get("amount_cents") or 0)
    if not description or amount_cents <= 0:
        return {"error": "a recurring bill needs a description and a positive amount"}
    cadence = body.get("cadence") if body.get("cadence") in CADENCES else "monthly"
    try:
        nd = body.get("next_due")
        next_due = date.fromisoformat(nd) if nd else _today() + timedelta(days=_STEP_DAYS.get(cadence, 30))
    except Exception:
        next_due = _today() + timedelta(days=30)
    row = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "description": description,
        "supplier": body.get("supplier"),
        "category": body.get("category") or "other",
        "account_code": body.get("account_code"),
        "amount_cents": amount_cents,
        "cadence": cadence,
        "next_due": next_due.isoformat(),
        "lead_days": int(body.get("lead_days") or 7),
        "status": "active",
        "note": body.get("note"),
    }
    res = _client().table("commerce_recurring_bills").insert(row).execute()
    return {"bill": (res.data or [row])[0]}


async def list_bills(tenant_id: str, *, status: Optional[str] = None) -> List[dict]:
    q = _client().table("commerce_recurring_bills").select("*").eq("tenant_id", tenant_id)
    if status:
        q = q.eq("status", status)
    try:
        return q.order("next_due").execute().data or []
    except Exception as exc:
        log.debug("list recurring bills skipped (run migration 062?): %s", exc)
        return []


async def set_status(tenant_id: str, bill_id: str, status: str) -> dict:
    if status not in ("active", "paused", "cancelled"):
        raise ValueError(f"invalid status: {status}")
    res = (_client().table("commerce_recurring_bills")
           .update({"status": status, "updated_at": _now()})
           .eq("tenant_id", tenant_id).eq("id", bill_id).execute())
    return (res.data or [{}])[0]


async def update(tenant_id: str, bill_id: str, patch: Dict[str, Any]) -> dict:
    allowed = {"description", "supplier", "category", "account_code", "amount_cents",
               "cadence", "next_due", "lead_days", "note"}
    row = {k: v for k, v in patch.items() if k in allowed}
    if not row:
        return {}
    row["updated_at"] = _now()
    res = (_client().table("commerce_recurring_bills").update(row)
           .eq("tenant_id", tenant_id).eq("id", bill_id).execute())
    return (res.data or [{}])[0]


# ── Expense creation + scheduler ────────────────────────────────────────────────

async def _spawn_expense(bill: dict) -> Optional[dict]:
    """Create a pending commerce_expenses row for this bill's current due date."""
    db = _client()
    row = {
        "id": str(uuid.uuid4()),
        "tenant_id": bill["tenant_id"],
        "date": _today().isoformat(),
        "category": bill.get("category") or "other",
        "description": bill.get("description"),
        "amount_cents": int(bill.get("amount_cents") or 0),
        "supplier": bill.get("supplier"),
        "due_date": bill["next_due"],
        "status": "pending",
        "source": "recurring",
        "account_code": bill.get("account_code"),
        "recurring_bill_id": bill["id"],
    }
    try:
        res = db.table("commerce_expenses").insert(row).execute()
    except Exception as exc:
        # account_code (058) or recurring_bill_id (062) may not exist on an older schema — retry bare.
        for k in ("account_code", "recurring_bill_id"):
            row.pop(k, None)
        log.warning("recurring bill expense insert retried without account_code/recurring_bill_id: %s", exc)
        res = db.table("commerce_expenses").insert(row).execute()
    return (res.data or [row])[0]


async def process_due(tenant_id: Optional[str] = None) -> int:
    """Spawn a pending expense for each active bill within its lead window. Returns count."""
    today = _today()
    q = _client().table("commerce_recurring_bills").select("*").eq("status", "active")
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    try:
        bills = q.execute().data or []
    except Exception as exc:
        log.debug("process_due recurring bills skipped (run migration 062?): %s", exc)
        return 0

    made = 0
    for bill in bills:
        try:
            next_due = date.fromisoformat(bill["next_due"])
        except Exception:
            continue
        if today < next_due - timedelta(days=int(bill.get("lead_days") or 7)):
            continue  # not within the lead window yet
        # Belt-and-suspenders: skip if an expense for this exact cycle already exists (e.g. the
        # scheduler ran twice before the next_due advance below landed).
        try:
            existing = (_client().table("commerce_expenses").select("id")
                       .eq("recurring_bill_id", bill["id"]).eq("due_date", bill["next_due"])
                       .limit(1).execute().data or [])
        except Exception:
            existing = []
        if existing:
            continue
        try:
            await _spawn_expense(bill)
            nxt = _advance(next_due, bill.get("cadence") or "monthly")
            # If we've missed several cycles (scheduler was down a while), only ONE expense was
            # spawned above — mirrors subscriptions.process_due's same choice for the same reason:
            # silently bulk-creating retroactive bills is worse than a visible gap the owner can
            # fix by hand. This loop just fast-forwards next_due past today without spawning more.
            while nxt - timedelta(days=int(bill.get("lead_days") or 7)) <= today:
                nxt = _advance(nxt, bill.get("cadence") or "monthly")
            _client().table("commerce_recurring_bills").update(
                {"next_due": nxt.isoformat(), "updated_at": _now()}
            ).eq("id", bill["id"]).execute()
            made += 1
        except Exception as exc:
            log.warning("recurring bill %s run failed: %s", bill.get("id"), exc)
    if made:
        log.info("Recurring bills: spawned %d pending expense(s)", made)
    return made

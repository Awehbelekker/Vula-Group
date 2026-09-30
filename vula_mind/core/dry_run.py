"""core/dry_run.py — run Vula's real skills against a real tenant's data without changing anything.

The capability benchmark (evals/benchmark.py) asks every agent real questions on real tenants,
inside the live server, next to real traffic. So the guard is per request (a ContextVar), never a
module patch: only the benchmark's own task sees it.

While a dry run is active:
  • a tool on READ_ONLY runs for real (it only reads) and its call + result are recorded;
  • any other tool is NOT run — the call and its arguments are recorded, and the model is told
    it was a dry run. An allow-list, not a deny-list: a tool added later is blocked until someone
    decides it only reads;
  • WhatsApp sends (_send_reply) and email sends are recorded, not sent.
"""
from __future__ import annotations

import functools
import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional

_STATE: ContextVar[Optional[Dict[str, Any]]] = ContextVar("vula_dry_run", default=None)

# Tools that only read. Anything else — creating, updating, sending, drafting into a real
# mailbox, filing, pulling a drive file into the knowledge base — is recorded, not run.
READ_ONLY = frozenset({
    # commerce_admin
    "booking_availability", "calculate", "cash_summary", "competitor_check", "customer_lookup",
    "dynamics_lookup", "email_thread_summary", "finance_insights", "find_document", "list_bookings",
    "list_discount_codes", "list_purchase_orders", "list_quotes", "list_reminders", "list_rules",
    "list_storefront_pages", "list_subscriptions", "list_suppliers", "lookup_business_info",
    "outstanding_invoices", "price_advice", "project_profit", "recent_orders",
    "reimbursement_balance", "reorder_suggestions", "sales_summary", "stock_status",
    "view_call_sheet",
    # commerce_assistant
    "check_business_hours", "check_delivery_area", "get_daily_catch", "list_availability",
    "list_my_subscriptions", "list_products", "research_product", "review_order",
    "suggest_recipe", "track_order", "view_cart",
    # email_admin / finance_admin / calculations / clickup / drives / mail
    "email_read", "email_search", "find_contact", "list_followups", "budget_status",
    "lookup_finance_knowledge", "money_in_out", "project_spend", "supplier_lookup", "lookup_rate",
    "list_comments", "list_tasks", "drive_search", "mail_list", "mail_read",
})

NOT_RUN = ("Benchmark dry run: this action was recorded but NOT performed. Don't say it's done — "
           "say what you would do and what you'd need to confirm.")


def active() -> bool:
    return _STATE.get() is not None


def state() -> Optional[Dict[str, Any]]:
    return _STATE.get()


@contextmanager
def session() -> Iterator[Dict[str, Any]]:
    """`with dry_run.session() as st:` — everything awaited inside is a dry run; st["calls"] and
    st["sent"] hold what happened."""
    st: Dict[str, Any] = {"calls": [], "sent": []}
    token = _STATE.set(st)
    try:
        yield st
    finally:
        _STATE.reset(token)


def _short(result: Any, limit: int = 6000) -> Any:
    try:
        text = json.dumps(result, default=str)
    except Exception:
        text = str(result)
    return text[:limit]


def record_send(kind: str, to: str, body: str) -> bool:
    """Called by the send paths first thing: True means "dry run — recorded, don't send"."""
    st = _STATE.get()
    if st is None:
        return False
    st["sent"].append({"kind": kind, "to": to, "body": (body or "")[:2000]})
    return True


def guard_dispatch(fn):
    """Decorator for a skill's tool dispatcher: `async def _dispatch(self, name, args, ...)`."""
    @functools.wraps(fn)
    async def wrapper(self, name: str, args: Dict[str, Any], *a, **kw):
        st = _STATE.get()
        if st is None:
            return await fn(self, name, args, *a, **kw)
        if name in READ_ONLY:
            try:
                result = await fn(self, name, args, *a, **kw)
            except Exception as exc:
                st["calls"].append({"tool": name, "args": args, "executed": True,
                                    "error": f"{type(exc).__name__}: {exc}"[:300]})
                raise
            st["calls"].append({"tool": name, "args": args, "executed": True, "result": _short(result)})
            return result
        st["calls"].append({"tool": name, "args": args, "executed": False})
        return {"status": "dry_run_not_performed", "tool": name, "note": NOT_RUN}
    return wrapper


def calls() -> List[Dict[str, Any]]:
    st = _STATE.get()
    return list(st["calls"]) if st else []

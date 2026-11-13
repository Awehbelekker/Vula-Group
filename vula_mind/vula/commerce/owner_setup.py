"""What a new owner still has to do, and what plan they are on — for their own Home page.

A business can sign up by itself, but then nothing told the owner what was left: the go-live
checklist (vula/api/master.py::setup_checklist) was only shown to the operator, in the operator's
words ("Created from business type", "Go-live test passed"). And an owner could not see their
plan or trial, or pay: a self-serve signup never got a payment link.

`owner_view` re-reads that same checklist for the owner: the steps that are theirs to do, in plain
words, in a sensible order, with the optional ones marked. WhatsApp is one of the optional steps —
a business that only wants invoicing can finish setting up without ever connecting a number.

`plan_view` is the plan, the trial, what the plan allows and — when nothing has been paid yet —
the link to pay. Every figure is read from the records; nothing is estimated.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# (checklist id, what the owner is asked to do, why it matters, optional?)
# In the order a new business would do them. Operator-only steps (created, go-live test, history
# analysed, storefront, first order) are not the owner's to tick and are left out.
OWNER_STEPS = (
    ("branding", "Add your logo and colour", "Your invoices and your dashboard carry your brand.", False),
    ("payments", "Say how customers pay you", "Bank details or a card gateway go on every invoice.", False),
    ("vat", "Confirm your VAT status", "Invoices are worked out with or without VAT from this.", False),
    ("first_invoice", "Send your first invoice", "Create one in Invoices, or just ask Vula in the chat.", False),
    ("profile", "Tell Vula about your business", "Hours, areas, policies: what Vula answers your customers from.", False),
    ("knowledge", "Add your products or documents", "Price lists, menus and policies Vula can look things up in.", False),
    ("team", "Add your team", "Give staff their own login and say what each can see.", True),
    ("whatsapp", "Connect a WhatsApp number", "Lets customers and staff talk to Vula on WhatsApp. You can run "
                                              "everything from this dashboard without one.", True),
)


def owner_view(checklist: Dict[str, Any]) -> Dict[str, Any]:
    """The owner's steps from the operator's checklist. Only steps that apply to this business
    (the checklist already drops those its modules don't use) are included."""
    by_id = {s.get("id"): s for s in (checklist.get("steps") or []) if isinstance(s, dict)}
    steps: List[Dict[str, Any]] = []
    for sid, label, why, optional in OWNER_STEPS:
        src = by_id.get(sid)
        if not src:
            continue
        steps.append({"id": sid, "label": label, "why": why, "optional": optional,
                      "done": bool(src.get("done")), "tab": src.get("tab"), "detail": src.get("detail")})
    required = [s for s in steps if not s["optional"]]
    done = sum(1 for s in required if s["done"])
    nxt = next((s["id"] for s in required if not s["done"]), None)
    return {"steps": steps, "done": done, "total": len(required), "complete": done == len(required),
            "next": nxt, "whatsapp_connected": bool((by_id.get("whatsapp") or {}).get("done"))}


# ── the plan ─────────────────────────────────────────────────────────────────────────────────

def _client():
    from vula.commerce import service
    return service._client()


def _count(table: str, tenant_id: str, **eq) -> Optional[int]:
    try:
        q = _client().table(table).select("id", count="exact").eq("tenant_id", tenant_id)
        for k, v in eq.items():
            q = q.in_(k, v) if isinstance(v, (list, tuple)) else q.eq(k, v)
        res = q.execute()
        n = getattr(res, "count", None)
        return n if isinstance(n, int) else len(getattr(res, "data", None) or [])
    except Exception as exc:  # noqa: BLE001 — unknown, not zero
        logger.debug("plan view: %s count skipped: %s", table, exc)
        return None


def plan_view(tenant_id: str, *, with_pay_link: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """This business's plan, trial, limits and usage. `with_pay_link` adds the link to pay when
    nothing has been paid yet (only for a caller allowed to commit the business to a plan)."""
    from vula.api.tenants import get_config
    from vula.commerce import plan_limits
    now = now or datetime.now(timezone.utc)
    plan = (get_config(tenant_id).get("plan") or "starter").lower()

    billing: Dict[str, Any] = {}
    try:
        rows = (_client().table("vula_tenants").select("plan,paid,trial_ends,status,email,contact_name,company_name")
                .eq("workspace_slug", tenant_id).limit(1).execute().data or [])
        billing = rows[0] if rows and isinstance(rows[0], dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("plan view: billing row skipped: %s", exc)

    price_cents: Optional[int] = None
    label = plan.title()
    try:
        from vula.api.onboarding import TIERS
        tier = TIERS.get(plan) or {}
        price_cents = tier.get("price_cents")
        label = tier.get("label") or label
    except Exception as exc:  # noqa: BLE001
        logger.debug("plan view: tiers skipped: %s", exc)

    trial_ends = billing.get("trial_ends")
    days_left: Optional[int] = None
    if trial_ends:
        try:
            end = datetime.fromisoformat(str(trial_ends).replace("Z", "+00:00"))
            days_left = max(0, (end - now).days + (1 if (end - now).seconds > 0 and end > now else 0))
        except ValueError:
            days_left = None

    paid = bool(billing.get("paid"))
    seat_limit = plan_limits.SEAT_LIMITS.get(plan, 2)
    doc_limit = plan_limits.STARTER_DOCUMENT_LIMIT if plan == "starter" else None
    out: Dict[str, Any] = {
        "plan": plan, "label": label, "paid": paid, "has_billing_record": bool(billing),
        "price": f"R{price_cents / 100:,.2f}" if isinstance(price_cents, int) and price_cents else None,
        "trial_ends": trial_ends, "trial_days_left": days_left,
        "on_trial": bool(trial_ends) and not paid,
        "trial_over": bool(trial_ends) and not paid and days_left == 0,
        "users": {"used": _count("vula_tenant_users", tenant_id, role=["owner", "staff"]), "limit": seat_limit},
        "documents": {"used": _count("vula_filed_documents", tenant_id), "limit": doc_limit},
        "pay_url": None,
    }
    if with_pay_link and not paid and billing.get("email"):
        try:
            from vula.api.onboarding import _payfast_url
            out["pay_url"] = _payfast_url(tenant_id, plan, billing["email"],
                                          (billing.get("contact_name") or billing.get("company_name") or tenant_id))
        except Exception as exc:  # noqa: BLE001 — the link is a convenience; the plan still shows
            logger.warning("plan view: pay link not built (tenant=%s): %s", tenant_id, exc)
    return out

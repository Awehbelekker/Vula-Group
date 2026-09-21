"""vula/commerce/plan_limits.py — per-plan usage caps (go-live readiness pass, Phase 4).

Advertised limits (VulaOnboarding.jsx): Starter 25 filed documents / 1-2 users, Growth
unlimited documents / 5 users, Business unlimited both. Before this, these were advertised to
prospects but nothing enforced them — per the confirmed product decision, breach is a hard
block with an upgrade prompt, since the current unenforced state is the actual false-claim risk.

Mirrors commerce/service.py's discount-code usage_limit check (read count, compare, raise): a
transient DB blip must never block a real document/signup (fail-open on the count read itself),
but a confirmed over-cap state is a real, enforced limit (fail-closed once the count is known).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

STARTER_DOCUMENT_LIMIT = 25
# None = unlimited. Matches VulaOnboarding.jsx's "1-2 users" / "Up to 5 users" / "Unlimited
# users" copy exactly — enforced at 2/5/None.
SEAT_LIMITS = {"starter": 2, "growth": 5, "business": None}


class PlanLimitError(ValueError):
    """A real, confirmed plan limit has been reached — never raised for a metering read
    failure (those fail open, see check_document_quota/check_seat_quota below)."""


def _tenant_plan(tenant_id: str) -> str:
    from vula.api.tenants import get_config
    return (get_config(tenant_id).get("plan") or "starter").lower()


def check_document_quota(tenant_id: str) -> None:
    """Raises PlanLimitError if a Starter tenant is already at the document cap. Growth/
    Business are unlimited. Fail-open on a count-query error."""
    plan = _tenant_plan(tenant_id)
    if plan != "starter":
        return
    try:
        from vula.commerce.service import _client
        res = (_client().table("vula_filed_documents").select("id", count="exact")
               .eq("tenant_id", tenant_id).execute())
        count = res.count if res.count is not None else len(res.data or [])
    except Exception as exc:
        logger.debug("document quota check skipped (fail-open): %s", exc)
        return
    if count >= STARTER_DOCUMENT_LIMIT:
        raise PlanLimitError(
            f"You've reached the {STARTER_DOCUMENT_LIMIT}-document limit on Starter — "
            "upgrade to Growth for unlimited document intelligence."
        )


def check_seat_quota(tenant_id: str) -> None:
    """Raises PlanLimitError if adding one more owner/staff seat would exceed the tenant's
    plan cap. Fail-open on a count-query error."""
    limit = SEAT_LIMITS.get(_tenant_plan(tenant_id), 2)
    if limit is None:
        return
    try:
        from vula.commerce.service import _client
        res = (_client().table("vula_tenant_users").select("id", count="exact")
               .eq("tenant_id", tenant_id).in_("role", ["owner", "staff"]).execute())
        count = res.count if res.count is not None else len(res.data or [])
    except Exception as exc:
        logger.debug("seat quota check skipped (fail-open): %s", exc)
        return
    if count >= limit:
        raise PlanLimitError(
            f"You've reached the {limit}-user limit on your plan — upgrade for more seats."
        )

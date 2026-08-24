"""
vula/api/signup.py — self-serve tenant signup.

Lets any authenticated Supabase user create their own new tenant, in one atomic-ish step,
without a developer/master in the loop. Closes the real gap found scoping this: creating the
FIRST vula_tenant_users row for a brand-new tenant was previously master-only
(POST /v1/master/tenants/{id}/users) or required already being a member of the tenant
(POST /v1/{tenant}/users) — no path existed for "someone signs up and becomes the owner of
their own new tenant."

Reuses vula/api/master_auth.py::_verify_jwt (round-trips the bearer token to Supabase's own
/auth/v1/user) WITHOUT that module's master-role check — any real Supabase user may call this,
not just master. Reuses tenants.py's BUSINESS_TYPES module-preset logic for the tenant_config
row, matching what master's own "+ New tenant" form seeds.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, field_validator

log = logging.getLogger(__name__)
router = APIRouter(tags=["signup"])


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    slug = (text or "").lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-")[:40]


async def require_authenticated_user(authorization: str = Header(default="")) -> dict:
    """FastAPI dependency: 401 without a valid Supabase session JWT. Unlike
    master_auth.require_master, this accepts ANY real Supabase user — no role check. Returns
    {id, email} from Supabase's own /auth/v1/user response."""
    from vula.api.master_auth import _verify_jwt
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sign in required (missing bearer token).")
    user = await _verify_jwt(token)
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Invalid or expired session — sign in again.")
    return user


class SignupIn(BaseModel):
    tenant_id: str
    display_name: Optional[str] = None
    business_type: Optional[str] = "other"

    @field_validator("tenant_id")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("tenant_id is required")
        return v


@router.post("/signup")
async def signup(body: SignupIn, user: dict = Depends(require_authenticated_user)) -> dict:
    """Create a brand-new tenant + its first owner login, both tied to the verified caller.
    Create-only (never upserts, unlike master's POST /v1/tenants) — one tenant per Supabase
    account for now, the simplest abuse bound for a first pass."""
    from vula.api.tenants import BUSINESS_TYPES, _public, _CACHE as _tenant_config_cache

    db = _client()
    slug = _slugify(body.tenant_id)
    if not slug:
        raise HTTPException(status_code=400, detail="Please choose a valid business name.")

    existing_tenant = (db.table("vula_tenant_config").select("tenant_id")
                       .eq("tenant_id", slug).limit(1).execute().data or [])
    if existing_tenant:
        raise HTTPException(status_code=409, detail=f"'{slug}' is already taken — try a different name.")

    own_tenants = (db.table("vula_tenant_users").select("tenant_id")
                  .eq("user_id", user["id"]).limit(1).execute().data or [])
    if own_tenants:
        raise HTTPException(status_code=409, detail="This account already has a workspace.")

    preset = BUSINESS_TYPES.get(body.business_type or "other", BUSINESS_TYPES["other"])
    row = {
        "tenant_id": slug, "display_name": body.display_name or slug,
        "business_type": body.business_type or "other",
        "modules": preset["modules"], "plan": "starter",
        "status": "active", "updated_at": _now(),
    }
    try:
        db.table("vula_tenant_config").insert(row).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not create workspace: {exc}")

    try:
        db.table("vula_tenant_users").insert(
            {"user_id": user["id"], "tenant_id": slug, "role": "owner"}).execute()
    except Exception as exc:
        # Compensating delete — no true multi-table transaction available outside the ledger's
        # dedicated RPC (migration 121), disproportionate to build for this low-frequency,
        # non-money-moving path. Leaving an orphaned tenant_config row with no owner would be
        # worse than a clean rollback here.
        try:
            db.table("vula_tenant_config").delete().eq("tenant_id", slug).execute()
        except Exception:
            log.error("signup: failed to roll back orphaned tenant_config row for %s", slug)
        raise HTTPException(status_code=500, detail=f"Could not create your login: {exc}")

    _tenant_config_cache.pop(slug, None)
    log.info("New self-serve tenant: %s (owner=%s)", slug, user.get("email"))

    # 2026-08-24 (structured starter KB): seed a small set of business_type-appropriate
    # starter documents so this tenant's KB isn't empty on day one — reduces the "empty
    # context -> hallucination risk" failure class the same audit found across several chat
    # skills. Fire-and-forget (the same primitive the bank-statement job uses) — LLM
    # generation takes a few seconds and must never delay the signup response or block
    # account creation on failure.
    try:
        from vula.commerce.background_tasks import run_background
        from vula.commerce.starter_kb import seed_starter_kb
        run_background(slug, "starter_kb_seed", seed_starter_kb(slug, body.business_type or "other"))
    except Exception as exc:
        log.debug("starter_kb seeding skipped for %s: %s", slug, exc)

    return {"tenant": _public(row), "role": "owner"}

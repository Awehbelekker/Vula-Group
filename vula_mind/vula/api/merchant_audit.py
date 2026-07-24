"""
vula/api/merchant_audit.py — per-tenant audit trail for merchant admin actions.

The tenant-scoped counterpart to master.py's audit()/vula_admin_audit: closes the "role
changes, access-scope edits, and login management leave no trail an owner can query" gap
found in the 2026-07-24 admin-depth audit (master's own audit trail was real; team.py and
users.py had zero audit writes). Same append-only, best-effort-write shape as master's,
just scoped to vula_merchant_audit / one tenant's own view (migration 107).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service as cs
    return cs._client()


def audit(tenant_id: str, actor: Optional[dict], action: str, **detail: Any) -> None:
    """Append one merchant-admin action to vula_merchant_audit. Best-effort — auditing must
    never block the action itself, but failures are logged loudly since a silent audit gap
    defeats the point of having one."""
    try:
        _client().table("vula_merchant_audit").insert({
            "tenant_id": tenant_id, "actor_email": (actor or {}).get("email"),
            "actor_id": (actor or {}).get("user_id"), "action": action, "detail": detail or {},
        }).execute()
    except Exception as exc:
        log.error("MERCHANT AUDIT WRITE FAILED (%s tenant=%s): %s — run migration 107?",
                  action, tenant_id, exc)


def list_audit(tenant_id: str, limit: int = 100) -> list[dict]:
    try:
        q = (_client().table("vula_merchant_audit").select("*")
             .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(min(limit, 500)))
        return q.execute().data or []
    except Exception as exc:
        log.debug("merchant audit read skipped (run migration 107?): %s", exc)
        return []

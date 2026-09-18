"""vula/integrations/sync_status.py — shared "did the last background sync succeed" write-
through for per-tenant integration account tables (migration 169: vula_clickup_accounts,
vula_microsoft_accounts). Two call sites (ClickUp, OneDrive) share the exact same shape, kept
here once rather than duplicated, per this codebase's "shared implementation, thin delegator"
convention.

Before this, the dashboard's connect-status UI showed "Connected" purely from OAuth token
presence — a sync silently failing for days still looked green. Best-effort throughout: a
status-write failure must never break the actual sync it's reporting on.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def record_sync_result(table: str, tenant_id: str, *, ok: bool, error: str = "") -> None:
    try:
        from vula.commerce.service import _client
        _client().table(table).update({
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
            "last_sync_status": "ok" if ok else "error",
            "last_sync_error": None if ok else (error or "")[:500],
        }).eq("tenant_id", tenant_id).execute()
    except Exception as exc:
        logger.debug("sync status write skipped for %s/%s (run migration 169?): %s",
                     table, tenant_id, exc)

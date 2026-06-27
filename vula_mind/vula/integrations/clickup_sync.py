"""
vula/integrations/clickup_sync.py

Two-way sync between Vula field-ops and ClickUp. Outbound mirrors are best-effort
and never block the field-ops flow (guarded + logged). The vula_field_clickup_map
table links a field-ops task id to its ClickUp task id.
"""
from __future__ import annotations

import logging
from typing import Optional

from vula.clickup import service as clickup
from vula.clickup.credentials import get_tenant_clickup_creds, _client

logger = logging.getLogger(__name__)

_MAP = "vula_field_clickup_map"
# Field-ops status → ClickUp status label
_STATUS_MAP = {
    "pending": "to do",
    "in_progress": "in progress",
    "awaiting_sign_off": "in progress",
    "complete": "complete",
    "rejected": "to do",
}


def _get_clickup_id(field_task_id: str) -> Optional[str]:
    try:
        res = (_client().table(_MAP).select("clickup_task_id")
               .eq("field_task_id", field_task_id).limit(1).execute())
        rows = res.data or []
        return rows[0]["clickup_task_id"] if rows else None
    except Exception as exc:
        logger.debug("clickup map lookup failed for %s: %s", field_task_id, exc)
        return None


def _save_map(tenant_id: str, field_task_id: str, clickup_task_id: str) -> None:
    try:
        _client().table(_MAP).upsert(
            {"tenant_id": tenant_id, "field_task_id": field_task_id,
             "clickup_task_id": clickup_task_id},
            on_conflict="field_task_id",
        ).execute()
    except Exception as exc:
        logger.debug("clickup map save failed: %s", exc)


async def mirror_task_create(tenant_id: str, field_task_id: str, title: str,
                            description: str = "", due_date: str = "") -> None:
    """Mirror a new field-ops task into ClickUp and record the mapping."""
    if not get_tenant_clickup_creds(tenant_id):
        return
    try:
        res = await clickup.create_task(tenant_id, title=title, description=description,
                                       due_date=due_date or None)
        cid = res.get("id")
        if cid:
            _save_map(tenant_id, field_task_id, cid)
            logger.info("Mirrored field task %s → ClickUp %s", field_task_id, cid)
    except Exception as exc:
        logger.warning("ClickUp mirror_task_create failed (%s): %s", field_task_id, exc)


async def mirror_status(tenant_id: str, field_task_id: str, status: str) -> None:
    """Mirror a field-ops status change to the linked ClickUp task."""
    if not get_tenant_clickup_creds(tenant_id):
        return
    cid = _get_clickup_id(field_task_id)
    if not cid:
        return
    try:
        await clickup.update_task_status(tenant_id, cid, _STATUS_MAP.get(status, status))
    except Exception as exc:
        logger.warning("ClickUp mirror_status failed (%s): %s", field_task_id, exc)


async def mirror_signoff(tenant_id: str, field_task_id: str, decision: str, notes: str = "") -> None:
    """Mirror an approve/reject sign-off to the linked ClickUp task."""
    if not get_tenant_clickup_creds(tenant_id):
        return
    cid = _get_clickup_id(field_task_id)
    if not cid:
        return
    try:
        status = "complete" if decision == "approved" else "to do"
        await clickup.update_task_status(tenant_id, cid, status, notes=notes)
    except Exception as exc:
        logger.warning("ClickUp mirror_signoff failed (%s): %s", field_task_id, exc)


def field_task_for_clickup(clickup_task_id: str) -> Optional[dict]:
    """Reverse lookup: ClickUp task id → {tenant_id, field_task_id}."""
    try:
        res = (_client().table(_MAP).select("tenant_id,field_task_id")
               .eq("clickup_task_id", clickup_task_id).limit(1).execute())
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as exc:
        logger.debug("clickup reverse map failed: %s", exc)
        return None

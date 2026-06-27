"""
vula/api/clickup.py — ClickUp connect + inbound webhook.

    POST /v1/clickup/connect          — store a tenant's ClickUp token + list, register webhook
    GET  /v1/clickup/status/{tenant}  — connection status
    POST /v1/clickup/webhook          — ClickUp → Vula (task status changes mirror back to field-ops)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from config import settings
from vula.clickup.credentials import _client, invalidate

log = logging.getLogger(__name__)
router = APIRouter(tags=["clickup"])


class ConnectIn(BaseModel):
    tenant_id: str
    api_token: str
    team_id: Optional[str] = None
    default_list_id: str
    space_id: Optional[str] = None
    connected_by: Optional[str] = None


@router.post("/connect")
async def connect(body: ConnectIn) -> dict:
    """Store a tenant's ClickUp credentials and register the status webhook."""
    record = {
        "tenant_id": body.tenant_id,
        "api_token": body.api_token,
        "team_id": body.team_id,
        "list_ids": {"default": body.default_list_id},
        "space_id": body.space_id,
        "status": "connected",
        "connected_by": body.connected_by,
        "connected_at": "now()",
    }
    _client().table("vula_clickup_accounts").upsert(record, on_conflict="tenant_id").execute()
    invalidate(body.tenant_id)

    webhook_ok = False
    try:
        from vula.clickup import service
        base = settings.public_base_url if hasattr(settings, "public_base_url") else \
            "https://vula-group-production.up.railway.app"
        res = await service.register_webhook(body.tenant_id, f"{base}/v1/clickup/webhook")
        webhook_ok = "id" in res
    except Exception as exc:
        log.warning("ClickUp webhook registration failed for %s: %s", body.tenant_id, exc)

    return {"tenant_id": body.tenant_id, "status": "connected", "webhook_registered": webhook_ok}


@router.get("/status/{tenant_id}")
async def status(tenant_id: str) -> dict:
    res = (_client().table("vula_clickup_accounts")
           .select("tenant_id,team_id,list_ids,status,connected_at")
           .eq("tenant_id", tenant_id).limit(1).execute())
    rows = res.data or []
    return rows[0] if rows else {"tenant_id": tenant_id, "status": "not_connected"}


@router.post("/webhook")
async def webhook(request: Request) -> dict:
    """ClickUp task updates → mirror status back onto the linked field-ops task."""
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored"}

    clickup_task_id = body.get("task_id")
    if not clickup_task_id:
        return {"status": "ignored"}

    from vula.integrations.clickup_sync import field_task_for_clickup
    link = field_task_for_clickup(clickup_task_id)
    if not link:
        return {"status": "unmapped"}

    # Pull the new status from the history_items payload if present.
    new_status = None
    for h in body.get("history_items", []) or []:
        after = h.get("after")
        if isinstance(after, dict) and after.get("status"):
            new_status = after["status"]
    if not new_status:
        return {"status": "no_status_change"}

    # Map ClickUp status back to a field-ops status.
    fo_status = {
        "complete": "complete", "closed": "complete", "done": "complete",
        "in progress": "in_progress", "to do": "pending", "open": "pending",
    }.get(str(new_status).lower(), None)
    if not fo_status:
        return {"status": "unmapped_status"}

    try:
        from vula.models.field_ops import get_field_ops_db
        get_field_ops_db().update_task_status(link["field_task_id"], fo_status)
    except Exception as exc:
        log.warning("Field-ops update from ClickUp webhook failed: %s", exc)
        return {"status": "error"}
    return {"status": "ok", "field_task": link["field_task_id"], "new_status": fo_status}

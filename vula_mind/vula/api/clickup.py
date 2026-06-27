"""
vula/api/clickup.py — ClickUp one-click OAuth connect + inbound webhook.

One-click flow (mirrors WhatsApp connect):
    GET  /v1/clickup/authorize-url?tenant_id=   → ClickUp consent URL (state=tenant)
    GET  /v1/clickup/oauth/callback?code=&state= → exchange + auto-discover + store + close popup
    GET  /v1/clickup/status/{tenant_id}          → connection status
    GET  /v1/clickup/lists/{tenant_id}           → discovered lists (for default picker)
    POST /v1/clickup/default-list                 → set the default list
    POST /v1/clickup/connect                      → manual token fallback
    POST /v1/clickup/webhook                      → ClickUp → Vula status mirror
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import settings
from vula.clickup import service
from vula.clickup.credentials import _client, invalidate, get_tenant_clickup_creds

log = logging.getLogger(__name__)
router = APIRouter(tags=["clickup"])

_AUTHORIZE = "https://app.clickup.com/api"


def _store_connection(tenant_id: str, token: str, team_id: Optional[str],
                     lists: Optional[list], default: Optional[str],
                     team_name: str = "", connected_by: str = "") -> None:
    """Upsert a tenant's ClickUp connection (mirrors whatsapp_connect upsert)."""
    list_ids: dict = {"default": default} if default else {}
    if lists:
        for l in lists:
            if l.get("id"):
                list_ids[l["id"]] = l.get("name")
    _client().table("vula_clickup_accounts").upsert({
        "tenant_id": tenant_id,
        "api_token": token,
        "team_id": team_id,
        "list_ids": list_ids,
        "status": "connected",
        "connected_by": connected_by,
        "connected_at": "now()",
    }, on_conflict="tenant_id").execute()
    invalidate(tenant_id)


async def _register_webhook(tenant_id: str) -> bool:
    try:
        res = await service.register_webhook(
            tenant_id, f"{settings.public_base_url}/v1/clickup/webhook")
        return "id" in res
    except Exception as exc:
        log.warning("ClickUp webhook registration failed for %s: %s", tenant_id, exc)
        return False


# ── One-click OAuth ───────────────────────────────────────────────────────────

@router.get("/authorize-url")
async def authorize_url(tenant_id: str) -> dict:
    """Return the ClickUp consent URL for this tenant (opened in a popup)."""
    if not settings.clickup_client_id:
        return {"error": "ClickUp app not configured (CLICKUP_CLIENT_ID missing)."}
    params = {
        "client_id": settings.clickup_client_id,
        "redirect_uri": f"{settings.public_base_url}/v1/clickup/oauth/callback",
        "state": tenant_id,
    }
    return {"url": f"{_AUTHORIZE}?{urlencode(params)}"}


def _popup_close_html(message: str, ok: bool = True) -> HTMLResponse:
    colour = "#2C5545" if ok else "#b91c1c"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>ClickUp</title></head>
        <body style="font-family:system-ui;text-align:center;padding:48px;color:{colour}">
        <h2>{message}</h2><p>You can close this window.</p>
        <script>try{{window.opener&&window.opener.postMessage('clickup-connected','*');}}catch(e){{}}
        setTimeout(function(){{window.close();}}, 1200);</script>
        </body></html>"""
    )


@router.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "") -> HTMLResponse:
    """ClickUp redirects here. Exchange code → token, auto-discover, store, close popup."""
    tenant_id = state
    if not code or not tenant_id:
        return _popup_close_html("Connection cancelled.", ok=False)
    try:
        token = await service.exchange_code(code)
        if not token:
            return _popup_close_html("Couldn't get a ClickUp token.", ok=False)
        info = await service.discover_team_and_lists(token)
        _store_connection(tenant_id, token, info.get("team_id"), info.get("lists"),
                         info.get("default"), team_name=info.get("team_name") or "")
        await _register_webhook(tenant_id)
        return _popup_close_html(f"ClickUp connected — {info.get('team_name') or 'workspace'} ✅")
    except Exception as exc:
        log.error("ClickUp OAuth callback failed for %s: %s", tenant_id, exc)
        return _popup_close_html("ClickUp connection failed. Please try again.", ok=False)


# ── Status / lists / default ──────────────────────────────────────────────────

@router.get("/status/{tenant_id}")
async def status(tenant_id: str) -> dict:
    try:
        res = (_client().table("vula_clickup_accounts")
               .select("tenant_id,team_id,list_ids,status,connected_at")
               .eq("tenant_id", tenant_id).limit(1).execute())
        rows = res.data or []
    except Exception:
        rows = []
    if not rows:
        return {"tenant_id": tenant_id, "status": "not_connected"}
    row = rows[0]
    lists = row.get("list_ids") or {}
    row["default_list_id"] = lists.get("default") if isinstance(lists, dict) else None
    return row


@router.get("/lists/{tenant_id}")
async def lists(tenant_id: str) -> dict:
    """Return the tenant's discovered lists for the default-list picker."""
    res = (_client().table("vula_clickup_accounts").select("list_ids")
           .eq("tenant_id", tenant_id).limit(1).execute())
    rows = res.data or []
    raw = (rows[0].get("list_ids") if rows else {}) or {}
    items = [{"id": k, "name": v} for k, v in raw.items() if k != "default"]
    return {"lists": items, "default": raw.get("default")}


class DefaultListIn(BaseModel):
    tenant_id: str
    list_id: str


@router.post("/default-list")
async def set_default_list(body: DefaultListIn) -> dict:
    res = (_client().table("vula_clickup_accounts").select("list_ids")
           .eq("tenant_id", body.tenant_id).limit(1).execute())
    rows = res.data or []
    raw = (rows[0].get("list_ids") if rows else {}) or {}
    raw["default"] = body.list_id
    _client().table("vula_clickup_accounts").update({"list_ids": raw}) \
        .eq("tenant_id", body.tenant_id).execute()
    invalidate(body.tenant_id)
    return {"tenant_id": body.tenant_id, "default_list_id": body.list_id}


# ── Manual token fallback ─────────────────────────────────────────────────────

class ConnectIn(BaseModel):
    tenant_id: str
    api_token: str
    team_id: Optional[str] = None
    default_list_id: str
    connected_by: Optional[str] = None


@router.post("/connect")
async def connect(body: ConnectIn) -> dict:
    _store_connection(body.tenant_id, body.api_token, body.team_id, None,
                     body.default_list_id, connected_by=body.connected_by or "")
    ok = await _register_webhook(body.tenant_id)
    return {"tenant_id": body.tenant_id, "status": "connected", "webhook_registered": ok}


# ── Sync ClickUp projects into the tenant's knowledge base ────────────────────

@router.post("/sync-kb/{tenant_id}")
async def sync_kb(tenant_id: str) -> dict:
    """Pull the tenant's ClickUp lists + their tasks into the RAG knowledge base
    so the AI can answer questions about live projects (e.g. 'what's happening on
    HPC Bokaap?'). Re-runnable — re-ingests each list under a stable doc id.
    """
    creds = get_tenant_clickup_creds(tenant_id)
    if not creds:
        return {"error": "ClickUp not connected for this tenant."}
    list_ids = creds.get("list_ids") or {}
    if not isinstance(list_ids, dict):
        return {"error": "No lists stored for this tenant."}

    from vula.ingestion.pipeline import VulaIngestionPipeline
    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)

    synced, chunks = 0, 0
    for lid, lname in list_ids.items():
        if lid == "default":
            continue
        try:
            tasks = await service.list_tasks(tenant_id, list_id=lid, limit=100)
        except Exception:
            continue
        if not isinstance(tasks, list) or not tasks:
            continue
        lines = [f"ClickUp project / list: {lname}", "Current tasks:"]
        for t in tasks:
            due = f" — due {t['due_date']}" if t.get("due_date") else ""
            who = f" — {', '.join(t['assignees'])}" if t.get("assignees") else ""
            lines.append(f"- {t.get('title')} [{t.get('status') or 'open'}]{due}{who}")
        try:
            res = await pipeline.ingest_text(
                content="\n".join(lines),
                filename=f"{lname}.txt".replace("/", "-"),
                doc_id=f"clickup_{lid}",
            )
            synced += 1
            chunks += getattr(res, "chunks_stored", 0) or 0
        except Exception as exc:
            log.warning("KB sync ingest failed for list %s: %s", lid, exc)
    return {"tenant_id": tenant_id, "synced_lists": synced, "chunks_added": chunks}


# ── Inbound webhook (ClickUp → Vula) ──────────────────────────────────────────

@router.post("/webhook")
async def webhook(request: Request) -> dict:
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

    new_status = None
    for h in body.get("history_items", []) or []:
        after = h.get("after")
        if isinstance(after, dict) and after.get("status"):
            new_status = after["status"]
    if not new_status:
        return {"status": "no_status_change"}

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

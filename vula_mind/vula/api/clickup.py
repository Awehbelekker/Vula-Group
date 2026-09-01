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

import hashlib
import hmac
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
    from vula.email_imap.credentials import encrypt_secret
    _client().table("vula_clickup_accounts").upsert({
        "tenant_id": tenant_id,
        "api_token": encrypt_secret(token),
        "team_id": team_id,
        "list_ids": list_ids,
        "status": "connected",
        "connected_by": connected_by,
        "connected_at": "now()",
    }, on_conflict="tenant_id").execute()
    invalidate(tenant_id)


async def _register_webhook(tenant_id: str) -> bool:
    """Register the webhook AND keep the signing secret it returns.

    2026-09-01: the secret was previously discarded (only "id" was checked), leaving the inbound
    endpoint with nothing to authenticate against — see migration 151 and _verify_signature.
    """
    try:
        res = await service.register_webhook(
            tenant_id, f"{settings.public_base_url}/v1/clickup/webhook")
        if "id" not in res:
            return False
        secret = res.get("secret") or (res.get("webhook") or {}).get("secret")
        wid = res.get("id") or (res.get("webhook") or {}).get("id")
        if secret:
            try:
                from vula.email_imap.credentials import encrypt_secret
                _client().table("vula_clickup_accounts").update({
                    "webhook_secret": encrypt_secret(secret), "webhook_id": wid,
                }).eq("tenant_id", tenant_id).execute()
                invalidate(tenant_id)
            except Exception as exc:
                log.warning("ClickUp webhook secret not stored (run migration 151?): %s", exc)
        else:
            log.warning("ClickUp webhook registered for %s but returned no secret", tenant_id)
        return True
    except Exception as exc:
        log.warning("ClickUp webhook registration failed for %s: %s", tenant_id, exc)
        return False


def _webhook_secret_for(tenant_id: str) -> Optional[str]:
    try:
        rows = (_client().table("vula_clickup_accounts").select("webhook_secret")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        if rows and rows[0].get("webhook_secret"):
            from vula.email_imap.credentials import decrypt_secret
            return decrypt_secret(rows[0]["webhook_secret"])
    except Exception as exc:
        log.debug("ClickUp webhook secret lookup failed for %s: %s", tenant_id, exc)
    return None


def _verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """ClickUp signs every delivery: X-Signature = HMAC-SHA256(raw body, webhook secret), hex.
    Verified against ClickUp's own docs (developer.clickup.com/docs/webhooksignature)."""
    if not (signature and secret):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.strip(), expected)


def _any_stored_webhook_secret() -> list[tuple[str, str]]:
    """(tenant_id, secret) for every tenant with a stored webhook secret.

    ClickUp's payload carries no tenant id, so the signature is what identifies the sender:
    whichever tenant's secret validates the body IS the tenant. That makes verification and
    tenant resolution the same step, and removes the old token-probing guesswork.
    """
    out: list[tuple[str, str]] = []
    try:
        rows = (_client().table("vula_clickup_accounts").select("tenant_id,webhook_secret")
                .eq("status", "connected").execute().data or [])
    except Exception as exc:
        log.debug("ClickUp webhook secret sweep failed: %s", exc)
        return out
    from vula.email_imap.credentials import decrypt_secret
    for r in rows:
        if r.get("webhook_secret"):
            try:
                out.append((r["tenant_id"], decrypt_secret(r["webhook_secret"])))
            except Exception:
                continue
    return out


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
    # 2026-09-01 SECURITY: this endpoint mutates real state — it updates field-ops task status
    # and can trigger procurement posting — and until now accepted ANY unauthenticated POST.
    # Same class of hole as the unauthenticated Yoco webhook. ClickUp signs every delivery
    # (X-Signature = HMAC-SHA256 of the raw body, hex), and since the payload carries no tenant
    # id, whichever tenant's stored secret validates the body IS the sender — verification and
    # tenant resolution in one step.
    raw = await request.body()
    signature = request.headers.get("X-Signature") or request.headers.get("x-signature") or ""
    verified_tenant: Optional[str] = None
    secrets = _any_stored_webhook_secret()
    for tid, secret in secrets:
        if _verify_signature(raw, signature, secret):
            verified_tenant = tid
            break
    if not verified_tenant:
        if not secrets:
            # No secret stored for anyone yet (pre-migration-151, or a webhook registered before
            # secrets were kept). Refuse rather than fall back to trusting the caller: an
            # unverifiable request is exactly what this check exists to stop. Re-connect ClickUp
            # to register a fresh webhook and store its secret.
            log.warning("ClickUp webhook rejected — no signing secret stored for any tenant "
                        "(run migration 151 and reconnect ClickUp)")
            return {"status": "unverified", "reason": "no_secret_stored"}
        log.warning("ClickUp webhook rejected — bad or missing X-Signature")
        return {"status": "unverified"}

    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored"}

    event = body.get("event") or ""
    clickup_task_id = body.get("task_id")
    if not clickup_task_id:
        return {"status": "ignored"}

    new_status = None
    for h in body.get("history_items", []) or []:
        after = h.get("after")
        if isinstance(after, dict) and after.get("status"):
            new_status = after["status"]
    if not new_status:
        # Not a status change — but the team still cares about assignments, due-date moves,
        # priority changes and comments, all of which used to be dropped silently here even
        # though taskUpdated was already subscribed.
        return await _handle_non_status_event(verified_tenant, event, clickup_task_id, body)

    from vula.integrations.clickup_sync import field_task_for_clickup
    link = field_task_for_clickup(clickup_task_id)
    if link:
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

    # Not a field-ops-mirrored task — try procurement (a native ClickUp task tagged
    # procurement/stock). The tenant is already known: it's whichever tenant's signing secret
    # validated this delivery, which is stronger than the old token-probing guess.
    tenant_id = verified_tenant
    if not tenant_id:
        return {"status": "unmapped"}
    try:
        from vula.clickup.service import get_task
        from vula.integrations.procurement import handle_task_status_change
        task = await get_task(tenant_id, clickup_task_id)
        if not task:
            return {"status": "unmapped"}
        posted = await handle_task_status_change(tenant_id, task)
        return {"status": "ok", "tenant_id": tenant_id, "procurement_logged": bool(posted)}
    except Exception as exc:
        log.warning("Procurement webhook handling failed for %s: %s", clickup_task_id, exc)
        return {"status": "error"}


def _history_change(body: dict, field: str) -> Optional[str]:
    """Human-readable 'after' value for a changed field in the webhook's history_items."""
    for h in body.get("history_items", []) or []:
        if h.get("field") != field:
            continue
        after = h.get("after")
        if isinstance(after, dict):
            return (after.get("username") or after.get("status") or after.get("priority")
                    or after.get("date") or after.get("name"))
        if isinstance(after, list) and after:
            first = after[0]
            return first.get("username") if isinstance(first, dict) else str(first)
        if after not in (None, ""):
            return str(after)
    return None


async def _handle_non_status_event(tenant_id: str, event: str, task_id: str,
                                   body: dict) -> dict:
    """Assignments, due-date moves, priority changes and comments.

    2026-09-01: taskUpdated was already subscribed but the handler returned early on anything
    that wasn't a status change, so none of this ever reached the team. Now the people who work
    on WhatsApp hear about ClickUp changes without having to sit in ClickUp.
    """
    from vula.clickup.service import get_task

    try:
        task = await get_task(tenant_id, task_id)
    except Exception as exc:
        log.debug("ClickUp task fetch failed for %s: %s", task_id, exc)
        task = None
    title = (task or {}).get("name") or "a task"
    url = (task or {}).get("url") or ""

    msg = None
    if event == "taskCommentPosted":
        # The comment text isn't always in the payload; read it back so the team sees the words,
        # not just "someone commented".
        text, who = None, None
        for h in body.get("history_items", []) or []:
            c = h.get("comment") or {}
            if isinstance(c, dict) and c.get("text_content"):
                text = c["text_content"].strip()
            who = ((h.get("user") or {}).get("username")) or who
        if not text:
            try:
                comments = await service.list_comments(tenant_id, task_id, limit=1)
                if comments:
                    text, who = comments[0].get("text"), comments[0].get("by") or who
            except Exception:
                pass
        if text:
            msg = (f"💬 {who or 'Someone'} commented on *{title}*:\n\n\"{text[:400]}\""
                   + (f"\n\n{url}" if url else ""))
    elif event == "taskAssigneeUpdated":
        who = _history_change(body, "assignee_add") or _history_change(body, "assignees")
        if who:
            msg = f"👤 *{title}* is now assigned to {who}." + (f"\n{url}" if url else "")
    elif event == "taskDueDateUpdated":
        due = (task or {}).get("due_date")
        when = ""
        if due:
            try:
                from datetime import datetime, timezone
                when = datetime.fromtimestamp(int(due) / 1000, timezone.utc).strftime("%a %d %b")
            except Exception:
                when = ""
        msg = (f"📅 Due date changed on *{title}*" + (f" — now {when}." if when else ".")
               + (f"\n{url}" if url else ""))
    elif event == "taskPriorityUpdated":
        pri = _history_change(body, "priority")
        if pri:
            msg = f"⚡ *{title}* is now {pri} priority." + (f"\n{url}" if url else "")
    elif event == "taskCreated":
        msg = f"🆕 New ClickUp task: *{title}*" + (f"\n{url}" if url else "")
    elif event == "taskDeleted":
        msg = f"🗑️ A ClickUp task was deleted ({task_id})."
    else:
        return {"status": "ignored_event", "event": event}

    if not msg:
        return {"status": "no_detail", "event": event}
    try:
        from vula.integrations.notify import notify_team
        await notify_team(tenant_id, "clickup_update", msg)
    except Exception as exc:
        log.warning("ClickUp notify failed for %s: %s", tenant_id, exc)
        return {"status": "error", "event": event}
    return {"status": "ok", "event": event, "notified": True}


async def _resolve_tenant_for_task(clickup_task_id: str) -> Optional[str]:
    """Find which connected tenant's ClickUp token can see this task id."""
    from vula.clickup.service import get_task
    try:
        rows = (_client().table("vula_clickup_accounts").select("tenant_id")
                .eq("status", "connected").execute().data or [])
    except Exception:
        return None
    for row in rows:
        tid = row.get("tenant_id")
        if not tid:
            continue
        try:
            if await get_task(tid, clickup_task_id):
                return tid
        except Exception:
            continue
    return None

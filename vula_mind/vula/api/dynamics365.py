"""
vula/api/dynamics365.py — one-click Dynamics 365 (Dataverse) connect.

    GET /v1/dynamics365/authorize-url?tenant_id=&org_url= → Microsoft consent URL
    GET /v1/dynamics365/oauth/callback?code=&state=       → exchange + store + close popup
    GET /v1/dynamics365/status/{tenant_id}                → connection status

Same shape as vula/api/microsoft.py, reusing the same Azure app (settings.microsoft_client_id/
secret) — Dataverse just needs its API permission added to that app registration. Unlike
Graph (one universal resource), Dataverse tokens are org-specific, so org_url has to travel
through the OAuth round-trip in `state` alongside tenant_id.
"""
from __future__ import annotations

import base64
import json
import logging
from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from config import settings
from vula.dynamics365 import client
from vula.dynamics365.credentials import store_connection, _client

log = logging.getLogger(__name__)
router = APIRouter(tags=["dynamics365"])


def _redirect_uri() -> str:
    return f"{settings.public_base_url}/v1/dynamics365/oauth/callback"


def _encode_state(tenant_id: str, org_url: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"t": tenant_id, "org": org_url}).encode()).decode()


def _decode_state(state: str) -> tuple[str, str]:
    d = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
    return d["t"], d["org"]


@router.get("/authorize-url")
async def authorize_url(tenant_id: str, org_url: str) -> dict:
    if not settings.microsoft_client_id:
        return {"error": "Microsoft app not configured (MICROSOFT_CLIENT_ID missing)."}
    if not org_url:
        return {"error": "org_url is required, e.g. https://yourorg.crm4.dynamics.com"}
    org_url = org_url.rstrip("/")
    scope = f"offline_access {org_url}{client.SCOPES_SUFFIX}"
    params = {
        "client_id": settings.microsoft_client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(),
        "response_mode": "query",
        "scope": scope,
        "state": _encode_state(tenant_id, org_url),
    }
    return {"url": f"{client._auth_url()}?{urlencode(params)}"}


def _popup(message: str, ok: bool = True) -> HTMLResponse:
    colour = "#2C5545" if ok else "#b91c1c"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Dynamics 365</title></head>
        <body style="font-family:system-ui;text-align:center;padding:48px;color:{colour}">
        <h2>{message}</h2><p>You can close this window.</p>
        <script>try{{window.opener&&window.opener.postMessage('dynamics365-connected','*');}}catch(e){{}}
        setTimeout(function(){{window.close();}}, 1200);</script></body></html>""")


@router.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "") -> HTMLResponse:
    if not code or not state:
        return _popup("Connection cancelled.", ok=False)
    try:
        tenant_id, org_url = _decode_state(state)
    except Exception:
        return _popup("Invalid connection state.", ok=False)
    try:
        tok = await client.exchange_code(code, _redirect_uri(), org_url)
        if not tok.get("access_token"):
            return _popup("Couldn't get a Dynamics 365 token.", ok=False)
        store_connection(
            tenant_id, org_url=org_url, access_token=tok["access_token"],
            refresh_token=tok.get("refresh_token"), expires_in=tok.get("expires_in", 3600),
            email=tok.get("email", ""), scopes=tok.get("scope", ""))
        return _popup(f"Dynamics 365 connected — {tok.get('email') or org_url} ✅")
    except Exception as exc:
        log.error("Dynamics365 OAuth callback failed for %s: %s", tenant_id, exc)
        return _popup("Dynamics 365 connection failed. Please try again.", ok=False)


@router.get("/{tenant_id}/search")
async def search(tenant_id: str, query: str = "", kind: str = "contact", limit: int = 8) -> dict:
    """Thin lookup endpoint for the dashboard's rep CRM screen — wraps the same client functions
    the WhatsApp `dynamics_lookup` tool already uses (core/skills/commerce_admin.py)."""
    if kind not in ("account", "contact", "opportunity"):
        return {"error": "kind must be account, contact, or opportunity"}
    try:
        if kind == "account":
            results = await client.search_accounts(tenant_id, query, limit)
        elif kind == "contact":
            results = await client.search_contacts(tenant_id, query, limit)
        else:
            results = await client.list_opportunities(tenant_id, query, limit)
        return {"results": results}
    except Exception as exc:
        log.warning("Dynamics365 search failed for %s: %s", tenant_id, exc)
        return {"error": str(exc)[:200]}


@router.get("/status/{tenant_id}")
async def status(tenant_id: str) -> dict:
    try:
        rows = (_client().table("vula_dynamics365_accounts")
                .select("tenant_id,org_url,email,status,connected_at")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception:
        rows = []
    return rows[0] if rows else {"tenant_id": tenant_id, "status": "not_connected"}

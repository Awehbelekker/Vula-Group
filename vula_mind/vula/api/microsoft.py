"""
vula/api/microsoft.py — one-click Microsoft connect (OneDrive + Outlook).

    GET /v1/microsoft/authorize-url?tenant_id=    → Microsoft consent URL
    GET /v1/microsoft/oauth/callback?code=&state= → exchange + store + close popup
    GET /v1/microsoft/status/{tenant_id}          → connection status
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from config import settings
from vula import oauth_state
from vula.api.master_auth import require_auth
from vula.microsoft import service
from vula.microsoft.credentials import store_connection, _client

log = logging.getLogger(__name__)
router = APIRouter(tags=["microsoft"])


def _redirect_uri() -> str:
    return f"{settings.public_base_url}/v1/microsoft/oauth/callback"


@router.get("/authorize-url", dependencies=[Depends(require_auth)])  # a member of tenant_id (audit H2)
async def authorize_url(tenant_id: str) -> dict:
    from vula import connections
    if not connections.one_tap("microsoft"):
        return await connections.not_available("microsoft")
    params = {
        "client_id": settings.microsoft_client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(),
        "response_mode": "query",
        "scope": " ".join(service.SCOPES),
        "state": oauth_state.sign("microsoft", tenant_id),
    }
    return {"url": f"{service._auth_url()}?{urlencode(params)}"}


def _popup(message: str, ok: bool = True) -> HTMLResponse:
    colour = "#2C5545" if ok else "#b91c1c"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Microsoft</title></head>
        <body style="font-family:system-ui;text-align:center;padding:48px;color:{colour}">
        <h2>{message}</h2><p>You can close this window.</p>
        <script>try{{window.opener&&window.opener.postMessage('microsoft-connected','*');}}catch(e){{}}
        setTimeout(function(){{window.close();}}, 1200);</script></body></html>""")


@router.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "") -> HTMLResponse:
    # Signed state only (9 Nov 2026 audit H2): a raw business id here let anyone link their own
    # account into another business.
    if not code or not state:
        return _popup("Connection cancelled.", ok=False)
    signed = oauth_state.verify("microsoft", state)
    if not signed:
        return _popup("That connection link has expired. Start again from Vula.", ok=False)
    tenant_id = signed["tenant_id"]
    try:
        tok = await service.exchange_code(code, _redirect_uri())
        if not tok.get("access_token"):
            return _popup("Couldn't get a Microsoft token.", ok=False)
        store_connection(
            tenant_id, access_token=tok["access_token"], refresh_token=tok.get("refresh_token"),
            expires_in=tok.get("expires_in", 3600), email=tok.get("email", ""),
            scopes=tok.get("scope", ""))
        return _popup(f"Microsoft connected — {tok.get('email') or 'account'} ✅")
    except Exception as exc:
        log.error("Microsoft OAuth callback failed for %s: %s", tenant_id, exc)
        return _popup("Microsoft connection failed. Please try again.", ok=False)


@router.get("/status/{tenant_id}")
async def status(tenant_id: str) -> dict:
    try:
        rows = (_client().table("vula_microsoft_accounts")
                .select("tenant_id,email,status,connected_at,"
                        "last_synced_at,last_sync_status,last_sync_error,scopes")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception:
        rows = []
    from vula import connections
    out = rows[0] if rows else {"tenant_id": tenant_id, "status": "not_connected"}
    out["one_tap"] = connections.one_tap("microsoft")   # its Connect button can work
    # 12 Nov 2026: a connection made before the calendar permission needs a reconnect for it.
    from vula.outlook_calendar import has_calendar
    out["has_calendar"] = has_calendar(out.pop("scopes", None))
    return out

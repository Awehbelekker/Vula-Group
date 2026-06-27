"""
vula/api/google.py — one-click Google connect (Drive + Gmail).

    GET /v1/google/authorize-url?tenant_id=    → Google consent URL
    GET /v1/google/oauth/callback?code=&state= → exchange + store + close popup
    GET /v1/google/status/{tenant_id}          → connection status
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from config import settings
from vula.google import service
from vula.google.credentials import store_connection, _client

log = logging.getLogger(__name__)
router = APIRouter(tags=["google"])

_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"


def _redirect_uri() -> str:
    return f"{settings.public_base_url}/v1/google/oauth/callback"


@router.get("/authorize-url")
async def authorize_url(tenant_id: str) -> dict:
    if not settings.google_client_id:
        return {"error": "Google app not configured (GOOGLE_CLIENT_ID missing)."}
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(service.SCOPES),
        "access_type": "offline",      # get a refresh token
        "prompt": "consent",            # ensure refresh token is returned
        "include_granted_scopes": "true",
        "state": tenant_id,
    }
    return {"url": f"{_AUTH}?{urlencode(params)}"}


def _popup(message: str, ok: bool = True) -> HTMLResponse:
    colour = "#2C5545" if ok else "#b91c1c"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Google</title></head>
        <body style="font-family:system-ui;text-align:center;padding:48px;color:{colour}">
        <h2>{message}</h2><p>You can close this window.</p>
        <script>try{{window.opener&&window.opener.postMessage('google-connected','*');}}catch(e){{}}
        setTimeout(function(){{window.close();}}, 1200);</script></body></html>""")


@router.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "") -> HTMLResponse:
    tenant_id = state
    if not code or not tenant_id:
        return _popup("Connection cancelled.", ok=False)
    try:
        tok = await service.exchange_code(code, _redirect_uri())
        if not tok.get("access_token"):
            return _popup("Couldn't get a Google token.", ok=False)
        store_connection(
            tenant_id, access_token=tok["access_token"], refresh_token=tok.get("refresh_token"),
            expires_in=tok.get("expires_in", 3600), email=tok.get("email", ""),
            scopes=tok.get("scope", ""))
        return _popup(f"Google connected — {tok.get('email') or 'account'} ✅")
    except Exception as exc:
        log.error("Google OAuth callback failed for %s: %s", tenant_id, exc)
        return _popup("Google connection failed. Please try again.", ok=False)


@router.get("/status/{tenant_id}")
async def status(tenant_id: str) -> dict:
    try:
        rows = (_client().table("vula_google_accounts")
                .select("tenant_id,email,status,connected_at")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception:
        rows = []
    return rows[0] if rows else {"tenant_id": tenant_id, "status": "not_connected"}

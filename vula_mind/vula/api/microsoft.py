"""
vula/api/microsoft.py — one-click Microsoft connect (OneDrive + Outlook).

    GET /v1/microsoft/authorize-url?tenant_id=    → Microsoft consent URL
    GET /v1/microsoft/oauth/callback?code=&state= → exchange + store + close popup
    GET /v1/microsoft/status/{tenant_id}          → connection status

  A team member's OWN calendar (13 Nov 2026, vula/microsoft/member_credentials.py):
    GET    /v1/microsoft/my-calendar/start?t=          the 15-minute link → Microsoft sign-in
    GET    /v1/microsoft/my-calendar?tenant_id=        the signed-in person's calendar status
    POST   /v1/microsoft/my-calendar/link?tenant_id=   a link for the signed-in person
    DELETE /v1/microsoft/my-calendar?tenant_id=        disconnect it
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

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
        from vula.microsoft import member_credentials as mc
        personal = oauth_state.verify(mc.STATE_KIND, state)
        if personal and personal["extra"].get("m"):
            return await _personal_callback(code, personal["tenant_id"], str(personal["extra"]["m"]))
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


# ── A team member's own calendar (13 Nov 2026) ───────────────────────────────────────────

async def _personal_callback(code: str, tenant_id: str, member_id: str) -> HTMLResponse:
    from vula.microsoft import member_credentials as mc
    try:
        tok = await service.exchange_code(code, _redirect_uri(), scopes=mc.PERSONAL_SCOPES)
        if not tok.get("access_token"):
            return _popup("Couldn't get a Microsoft token.", ok=False)
        row = mc.store(tenant_id, member_id, tok)
        if row.get("status") != "connected":
            return _popup("Your calendar couldn't be saved. Please try again.", ok=False)
    except Exception as exc:
        log.error("personal calendar connect failed for %s/%s: %s", tenant_id, member_id, exc)
        return _popup("Connecting your calendar failed. Please try again.", ok=False)
    try:                                       # bring their meetings in straight away
        from vula import outlook_calendar
        await outlook_calendar.sync_member(tenant_id, member_id)
    except Exception as exc:
        log.info("first calendar sync deferred for %s/%s: %s", tenant_id, member_id, exc)
    return _popup(f"Your calendar is connected — {tok.get('email') or 'Outlook'} ✅")


@router.get("/my-calendar/start")
async def my_calendar_start(t: str = ""):
    """The link Vula gave the person: valid for 15 minutes, then straight to Microsoft's sign-in
    asking for their calendar only."""
    from vula.microsoft import member_credentials as mc
    signed = oauth_state.verify(mc.LINK_KIND, t)
    if not signed or not signed["extra"].get("m"):
        return _popup("This link has expired. Ask Vula on WhatsApp for a new one: *connect my calendar*.", ok=False)
    params = {
        "client_id": settings.microsoft_client_id, "response_type": "code", "redirect_uri": _redirect_uri(),
        "response_mode": "query", "scope": " ".join(mc.PERSONAL_SCOPES), "prompt": "select_account",
        "state": oauth_state.sign(mc.STATE_KIND, signed["tenant_id"], {"m": str(signed["extra"]["m"])}),
    }
    return RedirectResponse(f"{service._auth_url()}?{urlencode(params)}", status_code=302)


async def _me(tenant_id: str, authorization: str) -> dict:
    """The signed-in person's own team row — every my-calendar call acts on them only."""
    from vula.api.tenant_auth import member_scope
    if not (authorization or "").removeprefix("Bearer ").strip():
        raise HTTPException(status_code=401, detail="Sign in required.")
    scope = await member_scope(authorization, tenant_id)
    if scope is None:
        raise HTTPException(status_code=403, detail="You don't have access to this workspace.")
    member = scope.get("member") or {}
    if not member.get("id"):
        raise HTTPException(status_code=400, detail="Add yourself to the team (with your WhatsApp number) first.")
    return member


@router.get("/my-calendar", dependencies=[Depends(require_auth)])   # a member of tenant_id; then only themselves
async def my_calendar(tenant_id: str, authorization: str = Header(default="")) -> dict:
    from vula.microsoft import member_credentials as mc
    me = await _me(tenant_id, authorization)
    row = mc.get_row(tenant_id, me["id"]) or {}
    return {"connected": row.get("status") == "connected", "email": row.get("email"),
            "last_synced_at": row.get("last_synced_at"), "last_sync_error": row.get("last_sync_error")}


@router.post("/my-calendar/link", dependencies=[Depends(require_auth)])
async def my_calendar_link(tenant_id: str, authorization: str = Header(default="")) -> dict:
    from vula.microsoft import member_credentials as mc
    me = await _me(tenant_id, authorization)
    return {"url": mc.link_for(tenant_id, me["id"]), "expires_in_minutes": 15}


@router.delete("/my-calendar", dependencies=[Depends(require_auth)])
async def my_calendar_disconnect(tenant_id: str, authorization: str = Header(default="")) -> dict:
    from vula import outlook_calendar
    me = await _me(tenant_id, authorization)
    return {"disconnected": outlook_calendar.disconnect_member(tenant_id, me["id"])}

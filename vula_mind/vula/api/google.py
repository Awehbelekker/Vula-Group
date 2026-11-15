"""
vula/api/google.py — one-click Google connect (Drive + Gmail).

    GET  /v1/google/authorize-url?tenant_id=    → Google consent URL
    GET  /v1/google/oauth/callback?code=&state= → exchange + store + close popup
    GET  /v1/google/status/{tenant_id}          → connection status
    GET  /v1/google/{tenant_id}/drive/search?q= → search files Vula can see (drive.file scope)
    POST /v1/google/{tenant_id}/drive/import    → pull a Drive file into vula_filed_documents

Drive import is search-based, not a folder browser: the drive.file OAuth scope (a deliberate
least-privilege choice, see vula/google/service.py) means Vula can only see files it created
itself or ones the tenant has explicitly opened through it before — a generic "browse my whole
Drive" UI would show nothing for most tenants under this scope. A live folder-browser needs
Google's Picker widget, which needs its own client-side OAuth flow and Google Cloud config beyond
what this connect flow does — a real follow-up, not built here.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from config import settings
from vula import oauth_state
from vula.api.master_auth import require_auth
from vula.google import service
from vula.google.credentials import store_connection, _client

log = logging.getLogger(__name__)
router = APIRouter(tags=["google"])

_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"


def _redirect_uri() -> str:
    return f"{settings.public_base_url}/v1/google/oauth/callback"


@router.get("/authorize-url", dependencies=[Depends(require_auth)])  # a member of tenant_id (audit H2)
async def authorize_url(tenant_id: str) -> dict:
    from vula import connections
    if not connections.one_tap("google"):
        return await connections.not_available("google")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(service.SCOPES),
        "access_type": "offline",      # get a refresh token
        "prompt": "consent",            # ensure refresh token is returned
        "include_granted_scopes": "true",
        "state": oauth_state.sign("google", tenant_id),
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
    # Signed state only (9 Nov 2026 audit H2): a raw business id here let anyone link their own
    # account into another business.
    if not code or not state:
        return _popup("Connection cancelled.", ok=False)
    signed = oauth_state.verify("google", state)
    if not signed:
        from vula.google import member_credentials as gc
        personal = oauth_state.verify(gc.STATE_KIND, state)
        if personal and personal["extra"].get("m"):
            return await _personal_callback(code, personal["tenant_id"], str(personal["extra"]["m"]))
        return _popup("That connection link has expired. Start again from Vula.", ok=False)
    tenant_id = signed["tenant_id"]
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
    from vula import connections
    out = rows[0] if rows else {"tenant_id": tenant_id, "status": "not_connected"}
    out["one_tap"] = connections.one_tap("google")   # its Connect button can work
    return out


@router.get("/{tenant_id}/drive/search")
async def drive_search_route(tenant_id: str, q: str = "") -> dict:
    try:
        files = await service.drive_search(tenant_id, q, limit=25)
    except service.GoogleNotConnected:
        return {"error": "Google Drive isn't connected for this tenant.", "files": []}
    except Exception as exc:
        log.warning("Drive search failed for %s: %s", tenant_id, exc)
        return {"error": str(exc), "files": []}
    return {"files": files}


@router.post("/{tenant_id}/drive/import")
async def drive_import_route(tenant_id: str, body: dict) -> dict:
    """Pull one Drive file (by id, from a prior /drive/search result) into the tenant's own
    documents library — category='media' by default, no OCR/KB-extraction attempted, same as
    the dashboard's "Just store this" upload. Optionally tag it to a customer."""
    file_id = body.get("file_id")
    if not file_id:
        return {"error": "file_id is required"}
    try:
        dl = await service.drive_download(tenant_id, file_id)
    except service.GoogleNotConnected:
        return {"error": "Google Drive isn't connected for this tenant."}
    except Exception as exc:
        log.warning("Drive download failed for %s/%s: %s", tenant_id, file_id, exc)
        return {"error": str(exc)}

    try:
        from vula.integrations.doc_filing import file_document
        row = await file_document(
            tenant_id, filename=dl.get("name") or file_id, data=dl.get("data"),
            content_type=dl.get("mime") or "application/octet-stream",
            category="media", source="dashboard", filed_by=f"dashboard:{tenant_id}:drive-import",
            customer_phone=body.get("customer_phone") or None,
        )
        return {"document": row}
    except Exception as exc:
        log.warning("Drive import file_document failed for %s/%s: %s", tenant_id, file_id, exc)
        return {"error": str(exc)}


# ── A team member's own Google Calendar (15 Nov 2026, vula/google/member_credentials.py) ──
#   GET    /v1/google/my-calendar/start?t=          the 15-minute link → Google sign-in (calendar only)
#   GET    /v1/google/my-calendar?tenant_id=        the signed-in person's status
#   POST   /v1/google/my-calendar/link?tenant_id=   a link for them
#   DELETE /v1/google/my-calendar?tenant_id=        disconnect

async def _personal_callback(code: str, tenant_id: str, member_id: str) -> HTMLResponse:
    from vula.google import member_credentials as gc
    try:
        tok = await service.exchange_code(code, _redirect_uri())
        if not tok.get("access_token"):
            return _popup("Couldn't get a Google token.", ok=False)
        if not gc.has_calendar(tok.get("scope")):
            return _popup("Google didn't give access to your calendar — tick the calendar box and try again.", ok=False)
        row = gc.store(tenant_id, member_id, tok)
        if row.get("status") != "connected":
            return _popup("Your calendar couldn't be saved. Please try again.", ok=False)
    except Exception as exc:
        log.error("Google calendar connect failed for %s/%s: %s", tenant_id, member_id, exc)
        return _popup("Connecting your calendar failed. Please try again.", ok=False)
    try:
        from vula import google_calendar
        await google_calendar.sync_member(tenant_id, member_id)
    except Exception as exc:
        log.info("first Google calendar sync deferred for %s/%s: %s", tenant_id, member_id, exc)
    return _popup(f"Your calendar is connected — {tok.get('email') or 'Google'} ✅")


@router.get("/my-calendar/start")
async def my_calendar_start(t: str = ""):
    from vula.google import member_credentials as gc
    signed = oauth_state.verify(gc.LINK_KIND, t)
    if not signed or not signed["extra"].get("m"):
        return _popup("This link has expired. Ask Vula on WhatsApp for a new one: *connect my Google calendar*.", ok=False)
    if not gc.available():
        return _popup("Google Calendar isn't set up on Vula yet.", ok=False)
    return RedirectResponse(gc.consent_url(signed["tenant_id"], str(signed["extra"]["m"]), _redirect_uri()),
                            status_code=302)


async def _me(tenant_id: str, authorization: str) -> dict:
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
    from vula.google import member_credentials as gc
    me = await _me(tenant_id, authorization)
    row = gc.get_row(tenant_id, me["id"]) or {}
    return {"available": gc.available(), "connected": row.get("status") == "connected", "email": row.get("email"),
            "last_synced_at": row.get("last_synced_at"), "last_sync_error": row.get("last_sync_error")}


@router.post("/my-calendar/link", dependencies=[Depends(require_auth)])
async def my_calendar_link(tenant_id: str, authorization: str = Header(default="")) -> dict:
    from vula.google import member_credentials as gc
    me = await _me(tenant_id, authorization)
    if not gc.available():
        raise HTTPException(status_code=400, detail="Google Calendar isn't set up on Vula yet.")
    return {"url": gc.link_for(tenant_id, me["id"]), "expires_in_minutes": 15}


@router.delete("/my-calendar", dependencies=[Depends(require_auth)])
async def my_calendar_disconnect(tenant_id: str, authorization: str = Header(default="")) -> dict:
    from vula import outlook_calendar
    me = await _me(tenant_id, authorization)
    return {"disconnected": outlook_calendar.disconnect_member(tenant_id, me["id"], provider="google")}

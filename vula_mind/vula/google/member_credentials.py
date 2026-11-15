"""
vula/google/member_credentials.py — one team member's own Google Calendar connection.

15 Nov 2026: the Google twin of vula/microsoft/member_credentials.py. Stored in the same table
(vula_member_calendars, provider 'google'); the storage helpers are shared with the Microsoft one.
Calendar only: openid, email and calendar.events.readonly — never Gmail or Drive. Google keeps a
refresh token only with access_type=offline + prompt=consent, so the link asks for both.

Until Google verifies Vula's app (see docs: Google Cloud steps), only the app's Test users can
connect, and their connection lasts 7 days.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

import httpx

from config import settings
from vula.microsoft import member_credentials as _shared

logger = logging.getLogger(__name__)

PROVIDER = "google"
PERSONAL_SCOPES = ["openid", "email", "https://www.googleapis.com/auth/calendar.events.readonly"]
LINK_KIND = "gcal-link"
STATE_KIND = "google-calendar"
_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


def available() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def has_calendar(scopes: Optional[str]) -> bool:
    return "calendar" in (scopes or "")


def link_for(tenant_id: str, member_id: str) -> str:
    from vula import oauth_state
    token = oauth_state.sign(LINK_KIND, tenant_id, {"m": str(member_id)})
    return f"{settings.public_base_url}/v1/google/my-calendar/start?t={quote(token)}"


def consent_url(tenant_id: str, member_id: str, redirect_uri: str) -> str:
    from vula import oauth_state
    params = {"client_id": settings.google_client_id, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": " ".join(PERSONAL_SCOPES), "access_type": "offline", "prompt": "consent",
              "state": oauth_state.sign(STATE_KIND, tenant_id, {"m": str(member_id)})}
    return f"{_AUTH}?{urlencode(params)}"


def store(tenant_id: str, member_id: str, tok: Dict[str, Any]) -> Dict[str, Any]:
    return _shared.store(tenant_id, member_id, tok, provider=PROVIDER)


def get_row(tenant_id: str, member_id: str) -> Optional[Dict[str, Any]]:
    return _shared.get_row(tenant_id, member_id, PROVIDER)


def connections(tenant_id: Optional[str] = None):
    return _shared.connections(tenant_id, PROVIDER)


def mark(tenant_id: str, member_id: str, error: Optional[str]) -> None:
    _shared.mark(tenant_id, member_id, error, PROVIDER)


async def _refresh(tenant_id: str, member_id: str, refresh_token: str) -> Optional[str]:
    from vula.email_imap.credentials import encrypt_secret
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(_TOKEN_URL, data={
            "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token"})
    if r.status_code != 200:
        logger.warning("Google calendar refresh failed for %s/%s: %s", tenant_id, member_id, r.text[:200])
        return None
    d = r.json()
    update = {"access_token": encrypt_secret(d.get("access_token")),
              "token_expiry": (datetime.now(timezone.utc) + timedelta(seconds=int(d.get("expires_in", 3600)))).isoformat(),
              "updated_at": datetime.now(timezone.utc).isoformat()}
    if d.get("scope"):
        update["scopes"] = d["scope"]
    try:
        _shared._client().table(_shared.TABLE).update(update).eq("tenant_id", tenant_id) \
            .eq("member_id", str(member_id)).eq("provider", PROVIDER).execute()
    except Exception:
        pass
    return d.get("access_token")


async def get_token(tenant_id: str, member_id: str) -> Optional[Dict[str, Any]]:
    from vula.email_imap.credentials import decrypt_secret
    try:
        rows = (_shared._client().table(_shared.TABLE).select("access_token,refresh_token,token_expiry,email,scopes")
                .eq("tenant_id", tenant_id).eq("member_id", str(member_id)).eq("provider", PROVIDER)
                .eq("status", "connected").limit(1).execute().data or [])
    except Exception as exc:
        logger.debug("google calendar lookup failed: %s", exc)
        return None
    if not rows:
        return None
    r = rows[0]
    token = decrypt_secret(r.get("access_token") or "") or None
    refresh = decrypt_secret(r.get("refresh_token") or "") or None
    needs = not token
    if r.get("token_expiry") and not needs:
        try:
            needs = datetime.fromisoformat(str(r["token_expiry"]).replace("Z", "+00:00")) <= \
                datetime.now(timezone.utc) + timedelta(seconds=60)
        except Exception:
            needs = True
    if needs and refresh:
        token = await _refresh(tenant_id, member_id, refresh)
    return {"access_token": token, "email": r.get("email"), "scopes": r.get("scopes") or ""} if token else None

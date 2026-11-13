"""
vula/microsoft/member_credentials.py — one team member's own Outlook calendar connection.

13 Nov 2026. The business's Microsoft account (vula/microsoft/credentials.py) is one per business;
each person can also connect their own calendar (vula_member_calendars, migration 265) so their
meetings come into their own Vula diary (vula/outlook_calendar.py). Calendar only: the token asks
for offline_access, User.Read and Calendars.Read — never mail or files. Tokens are encrypted like
the business account's; refresh asks for the personal scopes, then (if refused) what this person
granted.

Connecting: link_for() makes a 15-minute link (WhatsApp "connect my calendar" or the dashboard);
it opens Microsoft's sign-in (vula/api/microsoft.py my_calendar_start), and the callback stores
the token on that member. Only the person themselves is ever given their link.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from config import settings

logger = logging.getLogger(__name__)

TABLE = "vula_member_calendars"
PERSONAL_SCOPES = ["offline_access", "User.Read", "Calendars.Read"]
LINK_KIND = "mscal-link"           # the link Vula gives the person (oauth_state, 15 minutes)
STATE_KIND = "microsoft-calendar"  # the OAuth state Microsoft sends back


def _client():
    from vula.commerce import service
    return service._client()


def link_for(tenant_id: str, member_id: str) -> str:
    from vula import oauth_state
    token = oauth_state.sign(LINK_KIND, tenant_id, {"m": str(member_id)})
    return f"{settings.public_base_url}/v1/microsoft/my-calendar/start?t={quote(token)}"


def store(tenant_id: str, member_id: str, tok: Dict[str, Any]) -> Dict[str, Any]:
    """Save a fresh token on this member and read the row back."""
    from vula.email_imap.credentials import encrypt_secret
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in") or 3600))).isoformat()
    row = {"tenant_id": tenant_id, "member_id": str(member_id), "provider": "microsoft",
           "email": tok.get("email") or "", "access_token": encrypt_secret(tok["access_token"]),
           "token_expiry": expiry, "scopes": tok.get("scope") or "", "status": "connected",
           "last_sync_error": None, "connected_at": datetime.now(timezone.utc).isoformat(),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    if tok.get("refresh_token"):
        row["refresh_token"] = encrypt_secret(tok["refresh_token"])
    _client().table(TABLE).upsert(row, on_conflict="tenant_id,member_id,provider").execute()
    return get_row(tenant_id, member_id) or {}


def get_row(tenant_id: str, member_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table(TABLE).select("id,tenant_id,member_id,email,status,scopes,token_expiry,"
                                          "last_synced_at,last_sync_error,connected_at")
            .eq("tenant_id", tenant_id).eq("member_id", str(member_id)).eq("provider", "microsoft")
            .limit(1).execute().data or [])
    return rows[0] if rows else None


def connections(tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    q = (_client().table(TABLE).select("tenant_id,member_id,email,status,scopes")
         .eq("provider", "microsoft").eq("status", "connected"))
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    return q.execute().data or []


def disconnect(tenant_id: str, member_id: str) -> bool:
    _client().table(TABLE).delete().eq("tenant_id", tenant_id).eq("member_id", str(member_id)) \
        .eq("provider", "microsoft").execute()
    return get_row(tenant_id, member_id) is None


def mark(tenant_id: str, member_id: str, error: Optional[str]) -> None:
    """Record how the last sync went (shown on the dashboard card)."""
    try:
        _client().table(TABLE).update({"last_synced_at": datetime.now(timezone.utc).isoformat(),
                                       "last_sync_error": (error or None) and str(error)[:300]}) \
            .eq("tenant_id", tenant_id).eq("member_id", str(member_id)).execute()
    except Exception:
        pass


async def _refresh(tenant_id: str, member_id: str, refresh_token: str, granted: Optional[str]) -> Optional[str]:
    from vula.email_imap.credentials import encrypt_secret
    from vula.microsoft.service import _token_url
    tries = [" ".join(PERSONAL_SCOPES)]
    if granted and granted.strip():
        g = granted.strip() if "offline_access" in granted else granted.strip() + " offline_access"
        if g != tries[0]:
            tries.append(g)
    async with httpx.AsyncClient(timeout=20.0) as client:
        for scope in tries:
            r = await client.post(_token_url(), data={
                "client_id": settings.microsoft_client_id, "client_secret": settings.microsoft_client_secret,
                "refresh_token": refresh_token, "grant_type": "refresh_token", "scope": scope})
            if r.status_code == 200:
                break
        if r.status_code != 200:
            logger.warning("calendar token refresh failed for %s/%s: %s", tenant_id, member_id, r.text[:200])
            return None
        d = r.json()
    update = {"access_token": encrypt_secret(d.get("access_token")),
              "token_expiry": (datetime.now(timezone.utc) + timedelta(seconds=int(d.get("expires_in", 3600)))).isoformat(),
              "updated_at": datetime.now(timezone.utc).isoformat()}
    if d.get("scope"):
        update["scopes"] = d["scope"]
    if d.get("refresh_token"):
        update["refresh_token"] = encrypt_secret(d["refresh_token"])
    try:
        _client().table(TABLE).update(update).eq("tenant_id", tenant_id).eq("member_id", str(member_id)).execute()
    except Exception:
        pass
    return d.get("access_token")


async def get_token(tenant_id: str, member_id: str) -> Optional[Dict[str, Any]]:
    """{access_token, email, scopes} for this person's connected calendar, refreshed if needed."""
    from vula.email_imap.credentials import decrypt_secret
    try:
        rows = (_client().table(TABLE).select("access_token,refresh_token,token_expiry,email,scopes")
                .eq("tenant_id", tenant_id).eq("member_id", str(member_id)).eq("provider", "microsoft")
                .eq("status", "connected").limit(1).execute().data or [])
    except Exception as exc:
        logger.debug("member calendar lookup failed: %s", exc)
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
        token = await _refresh(tenant_id, member_id, refresh, r.get("scopes"))
    return {"access_token": token, "email": r.get("email"), "scopes": r.get("scopes") or ""} if token else None

"""vula/xero/credentials.py — a business's Xero connection: signed sign-in state, encrypted tokens,
refresh, disconnect (migration 247, vula_xero_accounts).

One Xero app (settings.xero_client_id/secret) serves every business. Xero access tokens last 30
minutes; the refresh token rotates on every use, so the new one is always saved. The OAuth `state`
is signed (HMAC, 15 minutes) — the callback is public, and an unsigned tenant id would let anyone
attach their own Xero to another business's account.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from config import settings

log = logging.getLogger(__name__)

TABLE = "vula_xero_accounts"
AUTH_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
REVOKE_URL = "https://identity.xero.com/connect/revocation"
CONNECTIONS_URL = "https://api.xero.com/connections"
STATE_MAX_AGE = 15 * 60


def _client():
    from vula.commerce.service import _client as c
    return c()


def redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/v1/xero/oauth/callback"


# ── signed state ─────────────────────────────────────────────────────────────────────────────

def _state_key() -> bytes:
    seed = (getattr(settings, "secrets_key", "") or settings.supabase_service_role_key
            or settings.supabase_service_key or settings.api_key or "vula-xero")
    return hashlib.sha256(("xero-oauth-state:" + seed).encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_state(tenant_id: str, by: str = "", now: Optional[float] = None) -> str:
    body = _b64(json.dumps({"t": tenant_id, "b": (by or "")[:80], "i": int(now or time.time()),
                            "n": secrets.token_hex(8)}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_state_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_state(state: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The signed state's {tenant, by}, or None when it's forged, altered or older than 15 minutes."""
    try:
        body, sig = (state or "").split(".", 1)
        want = _b64(hmac.new(_state_key(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        data = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    age = (now or time.time()) - int(data.get("i") or 0)
    if age < -60 or age > STATE_MAX_AGE or not data.get("t"):
        return None
    return {"tenant_id": data["t"], "by": data.get("b") or ""}


def authorize_url(tenant_id: str, by: str = "") -> str:
    from urllib.parse import urlencode
    return f"{AUTH_URL}?" + urlencode({
        "response_type": "code", "client_id": settings.xero_client_id, "redirect_uri": redirect_uri(),
        "scope": settings.xero_scopes, "state": sign_state(tenant_id, by)})


# ── tokens ───────────────────────────────────────────────────────────────────────────────────

def _basic() -> str:
    raw = f"{settings.xero_client_id}:{settings.xero_client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def _token_request(data: Dict[str, str]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(TOKEN_URL, data=data, headers={"Authorization": _basic()})
    if r.status_code != 200:
        raise RuntimeError(f"Xero sign-in refused ({r.status_code})")
    return r.json()


def _auth_event(access_token: str) -> str:
    """The sign-in's authentication_event_id (in the access token's JWT payload), to pick the
    organisation the owner just approved when their login has several."""
    try:
        return json.loads(_unb64(access_token.split(".")[1])).get("authentication_event_id") or ""
    except (ValueError, IndexError, TypeError):
        return ""


async def _organisations(access_token: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(CONNECTIONS_URL, headers={"Authorization": f"Bearer {access_token}",
                                                       "Accept": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"Xero organisations not read ({r.status_code})")
    return [c for c in (r.json() or []) if (c.get("tenantType") or "ORGANISATION") == "ORGANISATION"]


async def exchange_code(code: str) -> Dict[str, Any]:
    tok = await _token_request({"grant_type": "authorization_code", "code": code,
                                "redirect_uri": redirect_uri()})
    orgs = await _organisations(tok["access_token"])
    if not orgs:
        raise RuntimeError("No Xero organisation was chosen")
    event = _auth_event(tok["access_token"])
    org = next((o for o in orgs if event and o.get("authEventId") == event), orgs[0])
    return {**tok, "xero_tenant_id": org.get("tenantId"), "org_name": org.get("tenantName") or ""}


def store_connection(tenant_id: str, tok: Dict[str, Any], by: str = "") -> Dict[str, Any]:
    from vula.email_imap.credentials import encrypt_secret
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in") or 1800))
    row = {"tenant_id": tenant_id, "xero_tenant_id": tok.get("xero_tenant_id"), "org_name": tok.get("org_name"),
           "access_token": encrypt_secret(tok["access_token"]),
           "refresh_token": encrypt_secret(tok.get("refresh_token") or ""),
           "token_expiry": expiry.isoformat(), "scopes": tok.get("scope") or settings.xero_scopes,
           "status": "connected", "connected_by": (by or "")[:80] or None, "last_error": None,
           "connected_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    _client().table(TABLE).upsert(row, on_conflict="tenant_id").execute()
    back = connection(tenant_id) or {}
    if back.get("status") != "connected" or back.get("xero_tenant_id") != tok.get("xero_tenant_id"):
        raise RuntimeError("The Xero connection didn't read back as saved")
    return back


def connection(tenant_id: str) -> Optional[Dict[str, Any]]:
    """The saved connection, without its tokens."""
    try:
        rows = (_client().table(TABLE).select("tenant_id,xero_tenant_id,org_name,status,connected_by,connected_at,last_error")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — no migration 247 yet
        log.debug("xero connection read skipped: %s", exc)
        return None
    return rows[0] if rows else None


def _mark(tenant_id: str, status: str, error: str = "") -> None:
    _client().table(TABLE).update({"status": status, "last_error": (error or None),
                                   "updated_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("tenant_id", tenant_id).execute()


async def access(tenant_id: str) -> Dict[str, str]:
    """{access_token, xero_tenant_id} for API calls, refreshed 60 s before it expires. A refresh
    Xero refuses marks the connection 'needs_reconnect' (the daily connection check tells the owner)."""
    from vula.email_imap.credentials import decrypt_secret, encrypt_secret
    rows = (_client().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("status", "connected")
            .limit(1).execute().data or [])
    if not rows:
        raise LookupError("Xero isn't connected")
    r = rows[0]
    token, refresh = decrypt_secret(r.get("access_token") or ""), decrypt_secret(r.get("refresh_token") or "")
    try:
        expiry = datetime.fromisoformat(str(r.get("token_expiry")).replace("Z", "+00:00"))
    except ValueError:
        expiry = datetime.now(timezone.utc)
    if token and expiry > datetime.now(timezone.utc) + timedelta(seconds=60):
        return {"access_token": token, "xero_tenant_id": r["xero_tenant_id"]}
    if not refresh:
        _mark(tenant_id, "needs_reconnect", "no refresh token")
        raise LookupError("Xero needs connecting again")
    try:
        tok = await _token_request({"grant_type": "refresh_token", "refresh_token": refresh})
    except RuntimeError as exc:
        _mark(tenant_id, "needs_reconnect", str(exc))
        raise LookupError("Xero needs connecting again") from exc
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in") or 1800))
    _client().table(TABLE).update({
        "access_token": encrypt_secret(tok["access_token"]),
        "refresh_token": encrypt_secret(tok.get("refresh_token") or refresh),      # Xero rotates it
        "token_expiry": expiry.isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("tenant_id", tenant_id).execute()
    return {"access_token": tok["access_token"], "xero_tenant_id": r["xero_tenant_id"]}


async def disconnect(tenant_id: str) -> None:
    """Revoke Vula's access at Xero (frees the connection slot) and forget the tokens."""
    from vula.email_imap.credentials import decrypt_secret
    rows = (_client().table(TABLE).select("refresh_token").eq("tenant_id", tenant_id).limit(1).execute().data or [])
    refresh = decrypt_secret((rows[0] if rows else {}).get("refresh_token") or "")
    if refresh:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(REVOKE_URL, data={"token": refresh}, headers={"Authorization": _basic()})
        except httpx.HTTPError as exc:
            log.info("xero revoke for %s not confirmed: %s", tenant_id, type(exc).__name__)
    _client().table(TABLE).update({"status": "disconnected", "access_token": None, "refresh_token": None,
                                   "updated_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("tenant_id", tenant_id).execute()

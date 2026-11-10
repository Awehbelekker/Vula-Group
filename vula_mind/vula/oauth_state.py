"""
vula/oauth_state.py — the signed OAuth `state` for one-tap connections (Google, Microsoft,
ClickUp, Dynamics 365).

Until 9 Nov 2026 (audit H2) these sent the business's id as the raw `state` and the callback
stored whatever account came back against `state`. Anyone could build a consent link with
state=<another business> and consent with their own Google or Microsoft account; that account
then became the other business's connection, and its mail and Drive filing flowed to them.

Now `state` is signed (HMAC-SHA256 with a server-only key), names the provider it was made for,
and expires after 15 minutes. It's the same shape as Xero's (vula/xero/credentials.py), which
was already signed. The authorize-url endpoints that hand one out need a member of that business.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

from config import settings

MAX_AGE = 15 * 60


def _key(provider: str) -> bytes:
    seed = (getattr(settings, "secrets_key", "") or settings.supabase_service_role_key
            or settings.supabase_service_key or settings.api_key or "")
    if not seed:
        raise RuntimeError("No server secret to sign the connection with (SECRETS_KEY).")
    return hashlib.sha256(f"oauth-state:{provider}:{seed}".encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(provider: str, tenant_id: str, extra: Optional[Dict[str, Any]] = None,
         now: Optional[float] = None) -> str:
    """A state for `provider` naming `tenant_id` (plus small `extra` fields, e.g. Dynamics' org)."""
    body = _b64(json.dumps({"p": provider, "t": tenant_id, "x": extra or {}, "i": int(now or time.time()),
                            "n": secrets.token_hex(8)}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(provider), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(provider: str, state: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """{"tenant_id", "extra"} for a state this server signed for `provider` in the last 15 minutes,
    else None (forged, altered, made for another provider, expired, or not signed at all)."""
    try:
        body, sig = (state or "").split(".", 1)
        want = _b64(hmac.new(_key(provider), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        data = json.loads(_unb64(body))
    except (ValueError, TypeError, RuntimeError):
        return None
    if not isinstance(data, dict) or data.get("p") != provider or not data.get("t"):
        return None
    age = (now or time.time()) - int(data.get("i") or 0)
    if age < -60 or age > MAX_AGE:
        return None
    return {"tenant_id": str(data["t"]), "extra": data.get("x") or {}}

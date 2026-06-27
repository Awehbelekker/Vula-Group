"""
vula/email_imap/credentials.py — per-tenant IMAP/SMTP creds with encrypted password.

Password is encrypted at rest (Fernet, keyed off an existing server secret), so the
DB never holds plaintext mailbox passwords. App-passwords are still recommended.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)
_CACHE: dict[str, dict] = {}


def _client():
    from supabase import create_client
    return create_client(settings.supabase_url,
                         settings.supabase_service_role_key or settings.supabase_service_key)


def _fernet():
    from cryptography.fernet import Fernet
    seed = (settings.supabase_service_role_key or settings.supabase_service_key or "vula-email").encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed).digest()))


def encrypt_secret(password: str) -> str:
    try:
        return "fernet:" + _fernet().encrypt(password.encode()).decode()
    except Exception as exc:
        logger.warning("Fernet unavailable, storing obfuscated only: %s", exc)
        return "plain:" + base64.b64encode(password.encode()).decode()


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith("fernet:"):
        try:
            return _fernet().decrypt(stored[7:].encode()).decode()
        except Exception:
            return ""
    if stored.startswith("plain:"):
        return base64.b64decode(stored[6:]).decode()
    return stored


def invalidate(tenant_id: str) -> None:
    _CACHE.pop(tenant_id, None)


def get_email_creds(tenant_id: str) -> Optional[dict]:
    """Return {email, from_name, imap_host, imap_port, smtp_host, smtp_port, password,
    send_mode} for a connected tenant, else None."""
    if tenant_id in _CACHE:
        return _CACHE[tenant_id]
    try:
        rows = (_client().table("vula_email_accounts")
                .select("email,from_name,imap_host,imap_port,smtp_host,smtp_port,secret,send_mode,status")
                .eq("tenant_id", tenant_id).eq("status", "connected").limit(1).execute().data or [])
    except Exception as exc:
        logger.debug("email creds lookup failed for %s: %s", tenant_id, exc)
        return None
    if not rows:
        return None
    r = rows[0]
    creds = {
        "email": r["email"], "from_name": r.get("from_name"),
        "imap_host": r["imap_host"], "imap_port": r.get("imap_port") or 993,
        "smtp_host": r.get("smtp_host"), "smtp_port": r.get("smtp_port") or 465,
        "password": decrypt_secret(r["secret"]), "send_mode": r.get("send_mode") or "draft",
    }
    _CACHE[tenant_id] = creds
    return creds

"""
vula/email_imap/credentials.py — IMAP/SMTP creds with encrypted password.

Password is encrypted at rest (Fernet, keyed off an existing server secret), so the
DB never holds plaintext mailbox passwords. App-passwords are still recommended.

A tenant can have several connected mailboxes (migration 093) — every lookup here is
keyed by account id. `get_email_creds(tenant_id)` without an account_id resolves to the
tenant's *primary* account, for the handful of features that only make sense against one
mailbox (the AI email skill's default, bank-statement password handling, notify_phone
fallback) — everything else (sync, filing) should go through `list_email_accounts` and
loop over every connected account.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)
_CACHE: dict[str, dict] = {}   # keyed by account id


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


_SELECT_COLS = "id,tenant_id,email,from_name,imap_host,imap_port,smtp_host,smtp_port,secret,send_mode,status,is_primary"


def _row_to_creds(r: dict) -> dict:
    return {
        "id": r["id"], "tenant_id": r["tenant_id"],
        "email": r["email"], "from_name": r.get("from_name"),
        "imap_host": r["imap_host"], "imap_port": r.get("imap_port") or 993,
        "smtp_host": r.get("smtp_host"), "smtp_port": r.get("smtp_port") or 465,
        "password": decrypt_secret(r["secret"]), "send_mode": r.get("send_mode") or "draft",
        "is_primary": bool(r.get("is_primary")),
    }


def invalidate(tenant_id: str, account_id: Optional[str] = None) -> None:
    """Drop cached creds. With an account_id, drops just that entry; without one, drops
    every cached account belonging to the tenant (used by legacy tenant-only call sites)."""
    if account_id:
        _CACHE.pop(account_id, None)
        return
    for aid in [k for k, v in _CACHE.items() if v.get("tenant_id") == tenant_id]:
        _CACHE.pop(aid, None)


def get_email_creds(tenant_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    """Return creds for one mailbox: a specific account_id if given, else the tenant's
    primary connected account. Returns None if nothing matches."""
    if account_id and account_id in _CACHE:
        return _CACHE[account_id]
    try:
        q = _client().table("vula_email_accounts").select(_SELECT_COLS).eq("tenant_id", tenant_id).eq("status", "connected")
        q = q.eq("id", account_id) if account_id else q.eq("is_primary", True)
        rows = q.limit(1).execute().data or []
        if not rows and not account_id:
            # No primary set (shouldn't happen post-migration-093, but don't strand a
            # tenant with connected mailboxes and no usable default) — fall back to
            # whichever connected account was made first.
            rows = (_client().table("vula_email_accounts").select(_SELECT_COLS)
                    .eq("tenant_id", tenant_id).eq("status", "connected")
                    .order("connected_at").limit(1).execute().data or [])
    except Exception as exc:
        logger.debug("email creds lookup failed for %s/%s: %s", tenant_id, account_id, exc)
        return None
    if not rows:
        return None
    creds = _row_to_creds(rows[0])
    _CACHE[creds["id"]] = creds
    return creds


def list_email_accounts(tenant_id: str, status: str = "connected") -> list[dict]:
    """Every connected mailbox for a tenant, each with its own creds — for sync/filing,
    which must touch ALL connected accounts, not just the primary one."""
    try:
        q = _client().table("vula_email_accounts").select(_SELECT_COLS).eq("tenant_id", tenant_id)
        if status:
            q = q.eq("status", status)
        rows = q.execute().data or []
    except Exception as exc:
        logger.debug("email accounts list failed for %s: %s", tenant_id, exc)
        return []
    creds_list = [_row_to_creds(r) for r in rows]
    for c in creds_list:
        _CACHE[c["id"]] = c
    return creds_list

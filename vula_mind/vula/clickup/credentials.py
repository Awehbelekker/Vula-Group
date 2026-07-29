"""
vula/clickup/credentials.py

Per-tenant ClickUp credentials, fetched from Supabase (vula_clickup_accounts)
with an in-process cache — mirrors `_get_tenant_wa_creds` in vula/api/whatsapp.py.
"""
from __future__ import annotations

import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

_CACHE: dict[str, dict] = {}


def _client():
    from supabase import create_client
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key or settings.supabase_service_key,
    )


def get_tenant_clickup_creds(tenant_id: str) -> Optional[dict]:
    """Return {token, team_id, list_ids, space_id} for a connected tenant, else None."""
    if tenant_id in _CACHE:
        return _CACHE[tenant_id]
    try:
        res = (_client().table("vula_clickup_accounts")
               .select("api_token,team_id,list_ids,space_id,status")
               .eq("tenant_id", tenant_id).eq("status", "connected")
               .limit(1).execute())
        rows = res.data or []
        if rows and rows[0].get("api_token"):
            from vula.email_imap.credentials import decrypt_secret
            r = rows[0]
            creds = {
                "token": decrypt_secret(r["api_token"]),
                "team_id": r.get("team_id"),
                "list_ids": r.get("list_ids") or {},
                "space_id": r.get("space_id"),
            }
            _CACHE[tenant_id] = creds
            return creds
    except Exception as exc:
        logger.debug("ClickUp creds lookup failed for %s: %s", tenant_id, exc)
    return None


def default_list_id(creds: dict) -> Optional[str]:
    """Pick the tenant's default ClickUp list id from their stored list_ids."""
    lists = creds.get("list_ids") or {}
    if isinstance(lists, dict):
        return lists.get("default") or next(iter(lists.values()), None)
    if isinstance(lists, list) and lists:
        return lists[0]
    return None


def invalidate(tenant_id: str) -> None:
    _CACHE.pop(tenant_id, None)

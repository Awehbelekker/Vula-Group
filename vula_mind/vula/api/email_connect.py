"""
vula/api/email_connect.py — connect a generic IMAP/SMTP mailbox (GoDaddy, cPanel, …).

    POST   /v1/email/connect            test + store an IMAP/SMTP mailbox
    GET    /v1/email/status/{tenant}    connection status (no secret)
    DELETE /v1/email/disconnect/{tenant}
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from vula.email_imap import service
from vula.email_imap.credentials import _client, encrypt_secret, invalidate

log = logging.getLogger(__name__)
router = APIRouter(tags=["email"])


class ConnectIn(BaseModel):
    tenant_id: str
    email: str
    password: str
    imap_host: str
    imap_port: int = 993
    smtp_host: Optional[str] = None
    smtp_port: int = 465
    from_name: Optional[str] = None
    send_mode: str = "draft"          # draft | send
    connected_by: Optional[str] = None


@router.post("/connect")
async def connect(body: ConnectIn) -> dict:
    creds = {"email": body.email, "password": body.password,
             "imap_host": body.imap_host, "imap_port": body.imap_port}
    test = await service.test_connection(creds)
    if not test.get("ok"):
        return {"status": "error", "error": test.get("error") or "IMAP login failed"}
    try:
        _client().table("vula_email_accounts").upsert({
            "tenant_id": body.tenant_id, "email": body.email, "from_name": body.from_name,
            "imap_host": body.imap_host, "imap_port": body.imap_port,
            "smtp_host": body.smtp_host, "smtp_port": body.smtp_port,
            "secret": encrypt_secret(body.password), "send_mode": body.send_mode,
            "status": "connected", "connected_by": body.connected_by,
            "connected_at": "now()", "updated_at": "now()",
        }, on_conflict="tenant_id").execute()
    except Exception as exc:
        return {"status": "error", "error": f"store failed (run migration 023?): {exc}"}
    invalidate(body.tenant_id)
    return {"status": "connected", "email": body.email, "send_mode": body.send_mode}


@router.get("/status/{tenant_id}")
async def status(tenant_id: str) -> dict:
    try:
        rows = (_client().table("vula_email_accounts")
                .select("tenant_id,email,from_name,send_mode,status,connected_at")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception:
        rows = []
    return rows[0] if rows else {"tenant_id": tenant_id, "status": "not_connected"}


@router.delete("/disconnect/{tenant_id}")
async def disconnect(tenant_id: str) -> dict:
    try:
        _client().table("vula_email_accounts").delete().eq("tenant_id", tenant_id).execute()
    except Exception:
        pass
    invalidate(tenant_id)
    return {"tenant_id": tenant_id, "status": "not_connected"}

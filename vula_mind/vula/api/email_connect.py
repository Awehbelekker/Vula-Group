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
    notify_phone: Optional[str] = None  # WhatsApp to ask when a doc can't be project-matched
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
            "notify_phone": body.notify_phone,
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


@router.post("/sync/{tenant_id}")
async def sync_now(tenant_id: str) -> dict:
    """Manually run a mailbox sync (build contacts + file new attachments)."""
    from vula.email_imap.sync import process_email_sync
    return await process_email_sync(tenant_id)


@router.get("/contacts/{tenant_id}")
async def list_contacts(tenant_id: str, kind: Optional[str] = None) -> dict:
    """The contact / supplier / co-worker directory built from email."""
    try:
        q = (_client().table("vula_email_contacts").select("*")
             .eq("tenant_id", tenant_id).order("message_count", desc=True).limit(500))
        if kind:
            q = q.eq("kind", kind)
        rows = q.execute().data or []
    except Exception as exc:
        log.debug("contacts list skipped (run migration 024?): %s", exc)
        rows = []
    internal = [c for c in rows if c["kind"] == "internal"]
    external = [c for c in rows if c["kind"] != "internal"]
    return {"tenant_id": tenant_id, "contacts": rows, "count": len(rows),
            "co_workers": internal, "external": external}


class ContactKindIn(BaseModel):
    kind: str


@router.patch("/contacts/{tenant_id}/{contact_id}")
async def set_contact_kind(tenant_id: str, contact_id: str, body: ContactKindIn) -> dict:
    """Re-tag a contact (e.g. mark as 'supplier' or 'client')."""
    try:
        _client().table("vula_email_contacts").update({"kind": body.kind}).eq("id", contact_id).execute()
    except Exception as exc:
        return {"error": str(exc)}
    return {"id": contact_id, "kind": body.kind}


@router.delete("/disconnect/{tenant_id}")
async def disconnect(tenant_id: str) -> dict:
    try:
        _client().table("vula_email_accounts").delete().eq("tenant_id", tenant_id).execute()
    except Exception:
        pass
    invalidate(tenant_id)
    return {"tenant_id": tenant_id, "status": "not_connected"}

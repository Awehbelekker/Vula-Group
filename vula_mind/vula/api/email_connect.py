"""
vula/api/email_connect.py — connect one or more IMAP/SMTP mailboxes (GoDaddy, cPanel, Gmail…).

A tenant can connect several mailboxes (migration 093) — e.g. admin@, sales@, and a
personal Gmail all feeding the same KB/contact-directory/document-filing. Exactly one is
marked "primary" at a time, used as the default mailbox for single-mailbox-only features
(the AI email skill, bank-statement password handling, the notify_phone fallback).

    POST   /v1/email/connect                     connect a mailbox (adds, doesn't replace)
    GET    /v1/email/status/{tenant}              every connected account (no secrets)
    POST   /v1/email/set-primary/{tenant}/{id}     make one account the primary
    DELETE /v1/email/disconnect/{tenant}/{id}      disconnect one account
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
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

    db = _client()
    # First mailbox a tenant connects becomes primary automatically; later ones don't touch
    # the existing primary — use /set-primary explicitly to change it.
    try:
        existing = db.table("vula_email_accounts").select("id").eq("tenant_id", body.tenant_id).limit(1).execute().data
    except Exception as exc:
        return {"status": "error", "error": f"lookup failed (run migration 093?): {exc}"}
    is_first = not existing

    try:
        row = db.table("vula_email_accounts").upsert({
            "tenant_id": body.tenant_id, "email": body.email, "from_name": body.from_name,
            "imap_host": body.imap_host, "imap_port": body.imap_port,
            "smtp_host": body.smtp_host, "smtp_port": body.smtp_port,
            "secret": encrypt_secret(body.password), "send_mode": body.send_mode,
            "notify_phone": body.notify_phone, "is_primary": is_first,
            "status": "connected", "connected_by": body.connected_by,
            "connected_at": "now()", "updated_at": "now()",
        }, on_conflict="tenant_id,email").execute()
    except Exception as exc:
        return {"status": "error", "error": f"store failed (run migration 093?): {exc}"}
    account_id = (row.data or [{}])[0].get("id")
    invalidate(body.tenant_id, account_id)
    return {"status": "connected", "account_id": account_id, "email": body.email,
            "is_primary": is_first, "send_mode": body.send_mode}


@router.get("/status/{tenant_id}")
async def status(tenant_id: str) -> dict:
    try:
        rows = (_client().table("vula_email_accounts")
                .select("id,email,from_name,send_mode,status,is_primary,connected_at,last_sync_at")
                .eq("tenant_id", tenant_id).order("is_primary", desc=True).execute().data or [])
    except Exception:
        rows = []
    return {"tenant_id": tenant_id, "accounts": rows, "count": len(rows)}


@router.post("/set-primary/{tenant_id}/{account_id}")
async def set_primary(tenant_id: str, account_id: str) -> dict:
    """Make one connected account the tenant's primary mailbox. The partial unique index
    on (tenant_id) WHERE is_primary means only one row can hold it — flip the old one off
    first so the new one's update never collides with it."""
    db = _client()
    try:
        db.table("vula_email_accounts").update({"is_primary": False}).eq("tenant_id", tenant_id).execute()
        res = db.table("vula_email_accounts").update({"is_primary": True}) \
            .eq("tenant_id", tenant_id).eq("id", account_id).execute()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    if not res.data:
        raise HTTPException(status_code=404, detail="account not found")
    invalidate(tenant_id, account_id)
    return {"status": "ok", "account_id": account_id, "is_primary": True}


@router.post("/sync/{tenant_id}")
async def sync_now(tenant_id: str) -> dict:
    """Manually run a sync across every connected mailbox for this tenant."""
    from vula.email_imap.sync import process_tenant_email_sync
    return await process_tenant_email_sync(tenant_id)


@router.post("/backfill/{tenant_id}")
async def backfill(tenant_id: str, count: int = 200, account_id: Optional[str] = None) -> dict:
    """Catch Vula up on a mailbox's back-history: pull the most-recent `count` emails (regardless
    of the sync cursor) so historical invoices, proofs-of-payment and statements get filed. Runs
    in the BACKGROUND (attachment OCR/ingest is slow ~3s/email) and returns immediately; poll
    /status to see contacts grow. Scoped to one account if given, else every connected mailbox
    for the tenant — pass account_id when backfilling a mailbox you just connected, so you're
    not re-scanning mailboxes that are already caught up."""
    import asyncio
    from vula.email_imap.credentials import list_email_accounts
    from vula.email_imap.sync import process_email_sync
    n = max(1, min(count, 500))

    async def _run():
        accounts = [a for a in list_email_accounts(tenant_id) if not account_id or a["id"] == account_id]
        for acct in accounts:
            try:
                res = await process_email_sync(tenant_id, acct["id"], max_emails=n, from_uid=0)
                log.info("email backfill for %s/%s done: %s", tenant_id, acct["email"], res)
            except Exception as exc:
                log.warning("email backfill for %s/%s failed: %s", tenant_id, acct["email"], exc)

    asyncio.create_task(_run())
    return {"status": "started", "count": n, "account_id": account_id,
            "note": "Backfill running in the background — filing contacts + attachments. Check /status."}


@router.get("/contacts/{tenant_id}")
async def list_contacts(tenant_id: str, kind: Optional[str] = None) -> dict:
    """The contact / supplier / co-worker directory built from email — pooled across every
    connected mailbox for the tenant (a contact is identified by address, not by which
    mailbox first saw them)."""
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


@router.post("/contacts/{tenant_id}/import")
async def import_contacts(tenant_id: str, body: dict) -> dict:
    """Bulk CSV import (P2.5) — rows: [{email, name?, kind?}]. Upserts on (tenant_id, email),
    same table email sync writes to, so imported + synced contacts live side by side."""
    rows = (body or {}).get("contacts") or []
    imported, skipped = 0, 0
    db = _client()
    for r in rows:
        email = (r.get("email") or "").strip().lower()
        if not email or "@" not in email:
            skipped += 1
            continue
        try:
            existing = (db.table("vula_email_contacts").select("id")
                        .eq("tenant_id", tenant_id).eq("email", email).limit(1).execute().data or [])
            row = {"tenant_id": tenant_id, "email": email, "name": (r.get("name") or "").strip() or None,
                   "kind": r.get("kind") or "external", "domain": email.split("@")[-1]}
            if existing:
                db.table("vula_email_contacts").update(row).eq("id", existing[0]["id"]).execute()
            else:
                db.table("vula_email_contacts").insert(row).execute()
            imported += 1
        except Exception as exc:
            log.warning("contact import row failed (%s): %s", email, exc)
            skipped += 1
    return {"imported": imported, "skipped": skipped}


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


@router.get("/followups/{tenant_id}")
async def list_followups(tenant_id: str, status: str = "open") -> dict:
    """Emails awaiting a reply, across every connected mailbox."""
    try:
        rows = (_client().table("vula_email_followups").select("*")
                .eq("tenant_id", tenant_id).eq("status", status)
                .order("received_at", desc=True).limit(200).execute().data or [])
    except Exception as exc:
        log.debug("followups list skipped (run migration 028?): %s", exc)
        rows = []
    return {"tenant_id": tenant_id, "followups": rows, "count": len(rows)}


class FollowupStatusIn(BaseModel):
    status: str          # done | snoozed | open | spam (spam = flagged as a false-positive
                          # by the user, distinct from snoozed/"later" — kept for periodic
                          # review of what's slipping through _needs_reply's filter)


@router.patch("/followups/{tenant_id}/{followup_id}")
async def set_followup_status(tenant_id: str, followup_id: str, body: FollowupStatusIn) -> dict:
    try:
        _client().table("vula_email_followups").update({"status": body.status}).eq("id", followup_id).execute()
    except Exception as exc:
        return {"error": str(exc)}
    return {"id": followup_id, "status": body.status}


@router.delete("/disconnect/{tenant_id}/{account_id}")
async def disconnect(tenant_id: str, account_id: str) -> dict:
    """Disconnect one mailbox. If it was the primary, hand primary status to whichever
    other connected account was made first — a tenant with any connected mailbox always
    has exactly one primary, never zero, so single-mailbox features keep working."""
    db = _client()
    try:
        row = (db.table("vula_email_accounts").select("is_primary")
               .eq("tenant_id", tenant_id).eq("id", account_id).limit(1).execute().data or [])
        was_primary = bool(row and row[0].get("is_primary"))
        db.table("vula_email_accounts").delete().eq("tenant_id", tenant_id).eq("id", account_id).execute()
        if was_primary:
            nxt = (db.table("vula_email_accounts").select("id").eq("tenant_id", tenant_id)
                   .order("connected_at").limit(1).execute().data or [])
            if nxt:
                db.table("vula_email_accounts").update({"is_primary": True}).eq("id", nxt[0]["id"]).execute()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    invalidate(tenant_id, account_id)
    return {"tenant_id": tenant_id, "account_id": account_id, "status": "disconnected"}

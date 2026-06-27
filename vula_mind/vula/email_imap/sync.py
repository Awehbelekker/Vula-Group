"""
vula/email_imap/sync.py — email auto-sync (contacts library + work-attachment filing).

A background loop pulls NEW mail per connected tenant and, work-relevant-only:
  • builds a contact directory (internal co-workers vs external contacts/suppliers),
  • auto-files genuine attachments into the KB + Documents library (project-matched),
    skipping inline signature images and bulk/no-reply senders.
Sync position is the IMAP UID stored on the account row, so nothing is processed twice.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from typing import Optional

from vula.email_imap.credentials import get_email_creds, _client
from vula.email_imap.service import _hdr, _imap_login, _is_real_attachment

logger = logging.getLogger(__name__)

import asyncio as _asyncio
_sync_locks: dict = {}

def _lock_for(tenant_id: str) -> "_asyncio.Lock":
    lk = _sync_locks.get(tenant_id)
    if lk is None:
        lk = _sync_locks[tenant_id] = _asyncio.Lock()
    return lk

_NOISE = re.compile(r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|notification|mailer|newsletter|"
                    r"bounce|postmaster|marketing|updates?@|alerts?@)", re.IGNORECASE)
_MAX_ATTACH_BYTES = 15 * 1024 * 1024


def _fetch_new(creds: dict, last_uid: int, max_emails: int) -> dict:
    """[blocking] Fetch the most-recent NEW emails (UID > last_uid). Returns
    {emails: [...], max_uid: int}. Each email carries parsed contacts + real attachments."""
    m = _imap_login(creds)
    try:
        m.select("INBOX")
        typ, data = m.uid("search", None, "ALL")
        uids = [int(u) for u in (data[0].split() if data and data[0] else [])]
        new = sorted(u for u in uids if u > last_uid)
        batch = new[-max_emails:]  # most-recent new ones
        out = []
        for u in batch:
            typ, md = m.uid("fetch", str(u).encode(), "(RFC822)")
            if not md or not md[0]:
                continue
            msg = email.message_from_bytes(md[0][1])
            when = None
            try:
                when = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
            except Exception:
                when = datetime.now(timezone.utc).isoformat()
            people = [(_hdr(n), a) for n, a in
                      getaddresses([msg.get("From", ""), msg.get("To", ""), msg.get("Cc", "")])]
            body, attachments = "", []
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not body and "attachment" not in str(part.get("Content-Disposition") or "").lower():
                    try:
                        body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                    except Exception:
                        body = ""
                elif _is_real_attachment(part):
                    payload = part.get_payload(decode=True)
                    if payload and len(payload) <= _MAX_ATTACH_BYTES:
                        attachments.append({"name": _hdr(part.get_filename()) or f"attachment-{u}",
                                            "data": payload,
                                            "mime": part.get_content_type() or "application/octet-stream"})
            out.append({"uid": u, "when": when, "subject": _hdr(msg.get("Subject")) or "(no subject)",
                        "from": _hdr(msg.get("From")), "people": people, "body": body[:2000],
                        "attachments": attachments})
        return {"emails": out, "max_uid": max(batch) if batch else last_uid}
    finally:
        try: m.logout()
        except Exception: pass


def _upsert_contact(db, tenant_id: str, addr: str, name: str, kind: str, when: str) -> None:
    addr = (addr or "").strip().lower()
    if not addr or "@" not in addr or _NOISE.search(addr):
        return
    try:
        existing = (db.table("vula_email_contacts").select("id,message_count")
                    .eq("tenant_id", tenant_id).eq("email", addr).limit(1).execute().data or [])
        if existing:
            e = existing[0]
            db.table("vula_email_contacts").update({
                "message_count": (e.get("message_count") or 0) + 1, "last_seen": when,
                **({"name": name} if name else {})}).eq("id", e["id"]).execute()
        else:
            db.table("vula_email_contacts").insert({
                "tenant_id": tenant_id, "email": addr, "name": name or None,
                "domain": addr.split("@")[-1], "kind": kind, "message_count": 1,
                "first_seen": when, "last_seen": when}).execute()
    except Exception as exc:
        logger.debug("contact upsert skipped (run migration 024?): %s", exc)


async def process_email_sync(tenant_id: str, max_emails: int = 20) -> dict:
    """Sync one tenant: build contacts + file work attachments from new mail."""
    import asyncio
    lock = _lock_for(tenant_id)
    if lock.locked():
        return {"synced": 0, "skipped": "sync already running"}
    async with lock:
        return await _do_email_sync(tenant_id, max_emails)


async def _do_email_sync(tenant_id: str, max_emails: int) -> dict:
    import asyncio
    creds = get_email_creds(tenant_id)
    if not creds:
        return {"synced": 0}
    db = _client()
    try:
        row = (db.table("vula_email_accounts").select("last_sync_uid,auto_sync,notify_phone")
               .eq("tenant_id", tenant_id).limit(1).execute().data or [{}])[0]
    except Exception:
        return {"synced": 0}
    if row.get("auto_sync") is False:
        return {"synced": 0}
    last_uid = int(row.get("last_sync_uid") or 0)
    notify_phone = row.get("notify_phone")
    own_domain = creds["email"].split("@")[-1].lower()

    try:
        result = await asyncio.to_thread(_fetch_new, creds, last_uid, max_emails)
    except Exception as exc:
        logger.warning("email sync fetch failed for %s: %s", tenant_id, exc)
        return {"synced": 0, "error": str(exc)[:120]}

    emails = result["emails"]
    contacts_seen, filed = set(), 0
    for em in emails:
        for name, addr in em["people"]:
            kind = "internal" if addr.lower().endswith("@" + own_domain) else "external"
            _upsert_contact(db, tenant_id, addr, name, kind, em["when"])
            contacts_seen.add(addr.lower())
        for att in em["attachments"]:
            try:
                await _file_attachment(tenant_id, em, att, notify_phone)
                filed += 1
            except Exception as exc:
                logger.debug("attachment file failed: %s", exc)

    try:
        db.table("vula_email_accounts").update({
            "last_sync_uid": result["max_uid"], "last_sync_at": "now()"}).eq("tenant_id", tenant_id).execute()
    except Exception:
        pass
    return {"synced": len(emails), "contacts": len(contacts_seen), "filed_attachments": filed,
            "last_uid": result["max_uid"]}


async def _file_attachment(tenant_id: str, em: dict, att: dict, notify_phone: str = None) -> None:
    """Ingest an attachment into the KB + record it in the Documents library. The AI
    analyses the document (category/summary/fields) and project-matches on its CONTENT —
    so e.g. a 'payment notification' that is actually a wage payment for a project gets
    classified and filed correctly rather than dropped as noise."""
    from config import settings
    from vula.ingestion.pipeline import VulaIngestionPipeline

    db = _client()
    # Dedup: don't re-file the same attachment if it's already in the library.
    try:
        dup = (db.table("vula_filed_documents").select("id")
               .eq("tenant_id", tenant_id).eq("source", "email")
               .eq("filename", att["name"]).limit(1).execute().data or [])
        if dup:
            return
    except Exception:
        pass

    d = settings.upload_dir / tenant_id
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", att["name"])
    p = d / safe
    p.write_bytes(att["data"])
    res = await VulaIngestionPipeline(tenant_id=tenant_id).ingest_file(p, source_type="document")

    # Let the AI read it → category + summary + structured fields.
    category, summary, fields = "Email attachment", em.get("subject"), {}
    try:
        from vula.api.whatsapp import _analyze_document
        analysis = await _analyze_document(tenant_id, att["name"], p)
        if analysis:
            category = analysis.get("category") or category
            summary = analysis.get("summary") or summary
            fields = analysis.get("fields") or {}
    except Exception as exc:
        logger.debug("attachment analysis skipped: %s", exc)

    try:
        from vula.integrations.doc_filing import (match_project, project_examples,
                                                  lookup_learned_project)
        field_text = " ".join(str(v) for v in fields.values() if isinstance(v, (str, int, float)))
        hint = f"{em.get('subject','')} {em.get('from','')} {summary or ''} {field_text} {em.get('body','')}"
        # Learned rules (from past corrections) win — they're high-confidence by definition.
        match = lookup_learned_project(tenant_id, fields) or match_project(tenant_id, hint)
        confident = bool(match and match.get("confidence") == "high")

        # Only auto-file on a CONFIDENT match. Anything weaker (no match, or a single
        # coincidental token) → ask the human on WhatsApp rather than risk mis-filing.
        ask = bool(not confident and notify_phone)
        if not confident:
            match = None
        db.table("vula_filed_documents").insert({
            "tenant_id": tenant_id, "project": match["project"] if match else None,
            "category": category, "summary": summary, "fields": fields, "filename": att["name"],
            "mime": att.get("mime"), "doc_id": getattr(res, "doc_id", None),
            "source": "email", "status": "pending_project" if ask else "filed",
            "filed_by": notify_phone if ask else em.get("from"),
        }).execute()

        # Post to the project finance ledger if it carries an amount.
        if confident and match:
            try:
                from vula.integrations.finances import post_finance_from_doc
                post_finance_from_doc(tenant_id, match["project"], fields,
                                      getattr(res, "doc_id", None), att["name"],
                                      summary or "", category, owner_names=[em.get("from", "")])
            except Exception as exc:
                logger.debug("finance post skipped: %s", exc)

        if ask:
            try:
                from vula.api.whatsapp import _send_reply
                ex = project_examples(tenant_id)
                hint_txt = f" (e.g. {', '.join(ex)})" if ex else ""
                await _send_reply(notify_phone, (
                    f"📎 A document came in by email — *{att['name']}* "
                    f"from {em.get('from','')}. Which project should I file it under?{hint_txt} "
                    f"Reply with the project name, or 'skip'."), tenant_id=tenant_id)
            except Exception as exc:
                logger.debug("notify ask failed: %s", exc)
    except Exception as exc:
        logger.debug("filed_documents record skipped: %s", exc)


async def process_all_email_sync() -> int:
    """Sync every connected mailbox (called by the background loop)."""
    try:
        rows = (_client().table("vula_email_accounts").select("tenant_id")
                .eq("status", "connected").execute().data or [])
    except Exception:
        return 0
    total = 0
    for r in rows:
        try:
            res = await process_email_sync(r["tenant_id"])
            total += res.get("synced", 0)
        except Exception as exc:
            logger.warning("email sync failed for %s: %s", r.get("tenant_id"), exc)
    return total

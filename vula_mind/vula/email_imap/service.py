"""
vula/email_imap/service.py — IMAP read/search + attachments + SMTP/Drafts.

imaplib/smtplib are blocking, so every public function runs the work in a thread
via asyncio.to_thread. Read + search + attachment download + draft-to-Drafts (and
optional SMTP send).
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)


class EmailNotConnected(Exception):
    pass


def _hdr(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _is_real_attachment(part) -> bool:
    """True for genuine attachments; skips inline signature images (image00X.jpg logos)."""
    cd = str(part.get("Content-Disposition") or "").lower()
    if "attachment" in cd:
        return True
    fn = part.get_filename()
    if not fn:
        return False
    ctype = (part.get_content_type() or "").lower()
    # Inline images (email-signature logos) carry 'inline' and/or a Content-ID — skip them.
    if ctype.startswith("image/") and ("inline" in cd or part.get("Content-ID")):
        return False
    return True


def _imap_login(creds: dict) -> imaplib.IMAP4_SSL:
    m = imaplib.IMAP4_SSL(creds["imap_host"], int(creds.get("imap_port") or 993))
    m.login(creds["email"], creds["password"])
    return m


# ── Connection test ───────────────────────────────────────────────────────────

def _test(creds: dict) -> dict:
    try:
        m = _imap_login(creds)
        m.select("INBOX")
        m.logout()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


async def test_connection(creds: dict) -> dict:
    return await asyncio.to_thread(_test, creds)


# ── Search / list ─────────────────────────────────────────────────────────────

def _search(creds: dict, query: str, limit: int) -> list[dict]:
    m = _imap_login(creds)
    try:
        m.select("INBOX")
        # IMAP search can't carry non-ASCII; use the ASCII part of the query
        # (e.g. "Anli Kotzé" → "Anli Kotz"), then fall back to ALL.
        safe = (query or "").encode("ascii", "ignore").decode().strip()
        ids = []
        if safe:
            try:
                typ, data = m.search(None, "TEXT", f'"{safe}"')
                ids = (data[0].split() if data and data[0] else [])
            except Exception:
                ids = []
        if not ids:
            typ, data = m.search(None, "ALL")
            ids = (data[0].split() if data and data[0] else [])
        ids = ids[-max(1, min(limit, 25)):][::-1]  # newest first
        out = []
        for i in ids:
            typ, msg_data = m.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            out.append({"uid": i.decode(), "from": _hdr(msg.get("From")),
                        "subject": _hdr(msg.get("Subject")) or "(no subject)",
                        "date": _hdr(msg.get("Date"))})
        return out
    finally:
        try: m.logout()
        except Exception: pass


async def search(creds: dict, query: str = "", limit: int = 10) -> list[dict]:
    return await asyncio.to_thread(_search, creds, query, limit)


# ── Read full message + attachment list ───────────────────────────────────────

def _read(creds: dict, uid: str) -> dict:
    uid = str(uid or "").strip()
    if not uid.isdigit():
        return {"error": "Need a numeric message id (uid) from email_search first."}
    m = _imap_login(creds)
    try:
        m.select("INBOX")
        # BODY.PEEK[] — never silently mark the owner's own mailbox as read on their behalf
        # (see sync.py's identical fix; this fetch used RFC822/BODY[] which implicitly sets \Seen).
        typ, data = m.fetch(uid.encode(), "(BODY.PEEK[])")
        if not data or not data[0]:
            return {"error": "message not found"}
        msg = email.message_from_bytes(data[0][1])
        body, attachments = "", []
        for part in msg.walk():
            cd = str(part.get("Content-Disposition") or "")
            fn = part.get_filename()
            if part.get_content_type() == "text/plain" and "attachment" not in cd and not body:
                try:
                    body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                except Exception:
                    body = ""
            elif _is_real_attachment(part):
                attachments.append(_hdr(fn) or "attachment")
        return {"uid": uid, "from": _hdr(msg.get("From")), "to": _hdr(msg.get("To")),
                "subject": _hdr(msg.get("Subject")) or "(no subject)", "date": _hdr(msg.get("Date")),
                "message_id": msg.get("Message-ID", ""), "body": body[:6000], "attachments": attachments}
    finally:
        try: m.logout()
        except Exception: pass


async def read(creds: dict, uid: str) -> dict:
    return await asyncio.to_thread(_read, creds, uid)


def _download_attachment(creds: dict, uid: str, filename: str) -> Optional[dict]:
    m = _imap_login(creds)
    try:
        m.select("INBOX")
        # BODY.PEEK[] — never silently mark the owner's own mailbox as read on their behalf
        # (see sync.py's identical fix; this fetch used RFC822/BODY[] which implicitly sets \Seen).
        typ, data = m.fetch(uid.encode(), "(BODY.PEEK[])")
        if not data or not data[0]:
            return None
        msg = email.message_from_bytes(data[0][1])
        for part in msg.walk():
            if not _is_real_attachment(part):
                continue
            fn = _hdr(part.get_filename())
            if fn and (not filename or fn == filename or filename.lower() in fn.lower()):
                payload = part.get_payload(decode=True)
                if payload:
                    return {"name": fn, "data": payload,
                            "mime": part.get_content_type() or "application/octet-stream"}
        return None
    finally:
        try: m.logout()
        except Exception: pass


async def download_attachment(creds: dict, uid: str, filename: str = "") -> Optional[dict]:
    return await asyncio.to_thread(_download_attachment, creds, uid, filename)


# ── Draft (IMAP APPEND) / send (SMTP) ─────────────────────────────────────────

def _build(creds: dict, to: str, subject: str, body: str,
           attachments: Optional[list[dict]] = None) -> EmailMessage:
    msg = EmailMessage()
    frm = creds["email"]
    if creds.get("from_name"):
        frm = f'{creds["from_name"]} <{creds["email"]}>'
    msg["From"], msg["To"], msg["Subject"] = frm, to, subject
    # Marks every AI-composed message (auto-sent or a draft later sent as-is) so
    # vula/email_imap/sync.py's Sent-folder voice-profile capture can exclude it — learning tone
    # from Vula's own writing would just reinforce whatever it already does, not the owner's real
    # voice. Deliberately conservative: most mail clients preserve custom headers even after a
    # human edits a draft before sending, so an edited draft is excluded too rather than risking
    # AI-originated text polluting the sample.
    msg["X-Vula-Sent"] = "1"
    msg.set_content(body)
    for att in (attachments or []):
        mime = att.get("mimetype") or "application/octet-stream"
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(att["content"], maintype=maintype or "application",
                            subtype=subtype or "octet-stream", filename=att["filename"])
    return msg


def _save_draft(creds: dict, to: str, subject: str, body: str) -> dict:
    m = _imap_login(creds)
    try:
        raw = _build(creds, to, subject, body).as_bytes()
        for folder in ("Drafts", "INBOX.Drafts", "[Gmail]/Drafts"):
            try:
                typ, _ = m.append(folder, "\\Draft", None, raw)
                if typ == "OK":
                    return {"saved_to": folder, "to": to, "subject": subject}
            except Exception:
                continue
        return {"error": "could not find a Drafts folder"}
    finally:
        try: m.logout()
        except Exception: pass


async def save_draft(creds: dict, to: str, subject: str, body: str) -> dict:
    return await asyncio.to_thread(_save_draft, creds, to, subject, body)


def _send(creds: dict, to: str, subject: str, body: str,
          attachments: Optional[list[dict]] = None) -> dict:
    host, port = creds.get("smtp_host"), int(creds.get("smtp_port") or 465)
    if not host:
        return {"error": "no SMTP host configured"}
    msg = _build(creds, to, subject, body, attachments=attachments)
    ctx = ssl.create_default_context()
    if port == 587:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(creds["email"], creds["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as s:
            s.login(creds["email"], creds["password"])
            s.send_message(msg)
    return {"sent": True, "to": to, "subject": subject}


async def send(creds: dict, to: str, subject: str, body: str,
                attachments: Optional[list[dict]] = None) -> dict:
    return await asyncio.to_thread(_send, creds, to, subject, body, attachments)


def _send_batch(creds: dict, messages: list[dict]) -> list[dict]:
    """[blocking] Send several emails over ONE SMTP connection/login — a campaign send
    reconnecting per-recipient would be slow and risk the provider treating rapid repeat
    logins as abuse. Returns [{to, sent, error}] in the same order as `messages`."""
    host, port = creds.get("smtp_host"), int(creds.get("smtp_port") or 465)
    if not host:
        return [{"to": m["to"], "sent": False, "error": "no SMTP host configured"} for m in messages]
    ctx = ssl.create_default_context()
    try:
        if port == 587:
            s = smtplib.SMTP(host, port, timeout=20)
            s.starttls(context=ctx)
        else:
            s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    except Exception as exc:
        err = str(exc)[:200]
        return [{"to": m["to"], "sent": False, "error": err} for m in messages]

    results = []
    try:
        s.login(creds["email"], creds["password"])
    except Exception as exc:
        err = str(exc)[:200]
        try: s.quit()
        except Exception: pass
        return [{"to": m["to"], "sent": False, "error": err} for m in messages]

    try:
        for m in messages:
            try:
                msg = _build(creds, m["to"], m["subject"], m["body"])
                s.send_message(msg)
                results.append({"to": m["to"], "sent": True})
            except Exception as exc:
                results.append({"to": m["to"], "sent": False, "error": str(exc)[:200]})
    finally:
        try: s.quit()
        except Exception: pass
    return results


async def send_batch(creds: dict, messages: list[dict]) -> list[dict]:
    return await asyncio.to_thread(_send_batch, creds, messages)

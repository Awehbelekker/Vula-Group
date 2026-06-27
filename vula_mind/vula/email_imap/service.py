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
from email.utils import parsedate_to_datetime
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
        typ, data = m.fetch(uid.encode(), "(RFC822)")
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
            elif fn or "attachment" in cd:
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
        typ, data = m.fetch(uid.encode(), "(RFC822)")
        if not data or not data[0]:
            return None
        msg = email.message_from_bytes(data[0][1])
        for part in msg.walk():
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

def _build(creds: dict, to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    frm = creds["email"]
    if creds.get("from_name"):
        frm = f'{creds["from_name"]} <{creds["email"]}>'
    msg["From"], msg["To"], msg["Subject"] = frm, to, subject
    msg.set_content(body)
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


def _send(creds: dict, to: str, subject: str, body: str) -> dict:
    host, port = creds.get("smtp_host"), int(creds.get("smtp_port") or 465)
    if not host:
        return {"error": "no SMTP host configured"}
    msg = _build(creds, to, subject, body)
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


async def send(creds: dict, to: str, subject: str, body: str) -> dict:
    return await asyncio.to_thread(_send, creds, to, subject, body)

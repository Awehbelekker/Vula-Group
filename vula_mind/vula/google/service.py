"""
vula/google/service.py — Google Drive + Gmail operations (per tenant).

OAuth one-click connect (settings.google_client_id/secret). Drive: search + pull
files (pulled files are ingested into the KB). Gmail: list/read + create DRAFTS only
(Vula never sends without explicit approval).
"""
from __future__ import annotations

import base64
import logging
from email.mime.text import MIMEText
from typing import Optional

import httpx

from config import settings
from vula.google.credentials import get_access_token

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE = "https://www.googleapis.com/drive/v3"
_GMAIL = "https://gmail.googleapis.com/gmail/v1"
_USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",  # create drafts (not send)
    "openid", "email",
]


class GoogleNotConnected(Exception):
    pass


async def _token(tenant_id: str) -> str:
    creds = await get_access_token(tenant_id)
    if not creds or not creds.get("access_token"):
        raise GoogleNotConnected(tenant_id)
    return creds["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── OAuth ─────────────────────────────────────────────────────────────────────

async def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange an auth code for tokens + the user's email. Returns a dict for storage."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(_TOKEN_URL, data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code": code, "grant_type": "authorization_code",
            "redirect_uri": redirect_uri})
        r.raise_for_status()
        tok = r.json()
        email = ""
        try:
            ui = await client.get(_USERINFO, headers=_hdr(tok["access_token"]))
            email = ui.json().get("email", "")
        except Exception:
            pass
    return {"access_token": tok.get("access_token"), "refresh_token": tok.get("refresh_token"),
            "expires_in": tok.get("expires_in", 3600), "scope": tok.get("scope", ""), "email": email}


# ── Drive ─────────────────────────────────────────────────────────────────────

async def drive_search(tenant_id: str, query: str, limit: int = 10) -> list[dict]:
    token = await _token(tenant_id)
    q = f"name contains '{query}' and trashed = false" if query else "trashed = false"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{_DRIVE}/files", headers=_hdr(token), params={
            "q": q, "pageSize": max(1, min(limit, 25)),
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
            "orderBy": "modifiedTime desc"})
        r.raise_for_status()
        return r.json().get("files", [])


_EXPORT = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}


async def drive_download(tenant_id: str, file_id: str) -> dict:
    """Return {name, mime, data} for a Drive file (Google-native files exported to PDF)."""
    token = await _token(tenant_id)
    async with httpx.AsyncClient(timeout=60.0) as client:
        meta = (await client.get(f"{_DRIVE}/files/{file_id}", headers=_hdr(token),
                                 params={"fields": "name,mimeType"})).json()
        name, mime = meta.get("name", file_id), meta.get("mimeType", "")
        if mime in _EXPORT:
            exp_mime, ext = _EXPORT[mime]
            dl = await client.get(f"{_DRIVE}/files/{file_id}/export", headers=_hdr(token),
                                  params={"mimeType": exp_mime})
            if not name.endswith(ext):
                name += ext
            mime = exp_mime
        else:
            dl = await client.get(f"{_DRIVE}/files/{file_id}", headers=_hdr(token),
                                  params={"alt": "media"})
        dl.raise_for_status()
        return {"name": name, "mime": mime, "data": dl.content}


# ── Gmail (read + draft only) ─────────────────────────────────────────────────

async def gmail_list(tenant_id: str, query: str = "", limit: int = 10) -> list[dict]:
    token = await _token(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{_GMAIL}/users/me/messages", headers=_hdr(token),
                             params={"q": query or "in:inbox", "maxResults": max(1, min(limit, 20))})
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("messages", [])]
        out = []
        for mid in ids:
            md = (await client.get(f"{_GMAIL}/users/me/messages/{mid}", headers=_hdr(token),
                                   params={"format": "metadata",
                                           "metadataHeaders": ["From", "Subject", "Date"]})).json()
            hdrs = {h["name"]: h["value"] for h in md.get("payload", {}).get("headers", [])}
            out.append({"id": mid, "threadId": md.get("threadId"), "from": hdrs.get("From", ""),
                        "subject": hdrs.get("Subject", "(no subject)"), "date": hdrs.get("Date", ""),
                        "snippet": md.get("snippet", "")})
        return out


def _decode_body(payload: dict) -> str:
    def walk(p):
        if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(p["body"]["data"]).decode("utf-8", "ignore")
        for part in p.get("parts", []) or []:
            t = walk(part)
            if t:
                return t
        return ""
    return walk(payload)[:4000]


async def gmail_read(tenant_id: str, message_id: str) -> dict:
    token = await _token(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        md = (await client.get(f"{_GMAIL}/users/me/messages/{message_id}", headers=_hdr(token),
                               params={"format": "full"})).json()
    hdrs = {h["name"]: h["value"] for h in md.get("payload", {}).get("headers", [])}
    return {"id": message_id, "threadId": md.get("threadId"), "from": hdrs.get("From", ""),
            "to": hdrs.get("To", ""), "subject": hdrs.get("Subject", "(no subject)"),
            "date": hdrs.get("Date", ""), "body": _decode_body(md.get("payload", {}))}


async def gmail_create_draft(tenant_id: str, to: str, subject: str, body: str,
                             thread_id: Optional[str] = None) -> dict:
    """Create a Gmail DRAFT (never sends). Returns {draft_id} or {error}."""
    token = await _token(tenant_id)
    msg = MIMEText(body)
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = {"message": {"raw": raw}}
    if thread_id:
        payload["message"]["threadId"] = thread_id
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(f"{_GMAIL}/users/me/drafts", headers=_hdr(token), json=payload)
        r.raise_for_status()
        d = r.json()
    return {"draft_id": d.get("id"), "to": to, "subject": subject}

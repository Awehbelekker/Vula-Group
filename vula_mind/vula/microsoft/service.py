"""
vula/microsoft/service.py — OneDrive + Outlook via Microsoft Graph (per tenant).

OAuth one-click connect. OneDrive: search + pull files (ingested into the KB).
Outlook mail: list/read + create DRAFTS only (Vula never sends without approval —
no Mail.Send scope is requested).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from config import settings
from vula.microsoft.credentials import get_access_token

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"

SCOPES = ["offline_access", "User.Read", "Files.Read", "Mail.Read", "Mail.ReadWrite"]


class MicrosoftNotConnected(Exception):
    pass


async def _token(tenant_id: str) -> str:
    creds = await get_access_token(tenant_id)
    if not creds or not creds.get("access_token"):
        raise MicrosoftNotConnected(tenant_id)
    return creds["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _auth_url() -> str:
    return f"{settings.microsoft_authority}/oauth2/v2.0/authorize"


def _token_url() -> str:
    return f"{settings.microsoft_authority}/oauth2/v2.0/token"


# ── OAuth ─────────────────────────────────────────────────────────────────────

async def exchange_code(code: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(_token_url(), data={
            "client_id": settings.microsoft_client_id,
            "client_secret": settings.microsoft_client_secret,
            "code": code, "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES)})
        r.raise_for_status()
        tok = r.json()
        email = ""
        try:
            me = await client.get(f"{_GRAPH}/me", headers=_hdr(tok["access_token"]))
            j = me.json()
            email = j.get("mail") or j.get("userPrincipalName") or ""
        except Exception:
            pass
    return {"access_token": tok.get("access_token"), "refresh_token": tok.get("refresh_token"),
            "expires_in": tok.get("expires_in", 3600), "scope": tok.get("scope", ""), "email": email}


# ── OneDrive ──────────────────────────────────────────────────────────────────

async def drive_search(tenant_id: str, query: str, limit: int = 10) -> list[dict]:
    token = await _token(tenant_id)
    path = f"/me/drive/root/search(q='{query}')" if query else "/me/drive/root/children"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{_GRAPH}{path}", headers=_hdr(token),
                             params={"$top": max(1, min(limit, 25)),
                                     "$select": "id,name,file,webUrl,lastModifiedDateTime"})
        r.raise_for_status()
        items = r.json().get("value", [])
    return [{"id": i.get("id"), "name": i.get("name"),
             "mimeType": (i.get("file") or {}).get("mimeType", ""),
             "webViewLink": i.get("webUrl"), "modifiedTime": i.get("lastModifiedDateTime")}
            for i in items if i.get("file")]


async def drive_download(tenant_id: str, file_id: str) -> dict:
    token = await _token(tenant_id)
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        meta = (await client.get(f"{_GRAPH}/me/drive/items/{file_id}", headers=_hdr(token),
                                 params={"$select": "name,file"})).json()
        name = meta.get("name", file_id)
        mime = (meta.get("file") or {}).get("mimeType", "application/octet-stream")
        dl = await client.get(f"{_GRAPH}/me/drive/items/{file_id}/content", headers=_hdr(token))
        dl.raise_for_status()
        return {"name": name, "mime": mime, "data": dl.content}


# ── Outlook mail (read + draft only) ──────────────────────────────────────────

async def mail_list(tenant_id: str, query: str = "", limit: int = 10) -> list[dict]:
    token = await _token(tenant_id)
    params = {"$top": max(1, min(limit, 20)), "$select": "id,subject,from,receivedDateTime,bodyPreview",
              "$orderby": "receivedDateTime desc"}
    if query:
        params["$search"] = f'"{query}"'
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{_GRAPH}/me/messages", headers=_hdr(token), params=params)
        r.raise_for_status()
        out = []
        for m in r.json().get("value", []):
            frm = (m.get("from") or {}).get("emailAddress", {})
            out.append({"id": m.get("id"), "subject": m.get("subject") or "(no subject)",
                        "from": f"{frm.get('name','')} <{frm.get('address','')}>",
                        "date": m.get("receivedDateTime", ""), "snippet": m.get("bodyPreview", "")})
        return out


async def mail_read(tenant_id: str, message_id: str) -> dict:
    token = await _token(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        m = (await client.get(f"{_GRAPH}/me/messages/{message_id}", headers=_hdr(token),
                              params={"$select": "subject,from,toRecipients,receivedDateTime,body"})).json()
    frm = (m.get("from") or {}).get("emailAddress", {})
    return {"id": message_id, "subject": m.get("subject") or "(no subject)",
            "from": f"{frm.get('name','')} <{frm.get('address','')}>",
            "date": m.get("receivedDateTime", ""),
            "body": (m.get("body") or {}).get("content", "")[:4000]}


async def mail_create_draft(tenant_id: str, to: str, subject: str, body: str,
                            reply_to_id: Optional[str] = None) -> dict:
    """Create an Outlook DRAFT (never sends). Returns {draft_id} or {error}."""
    token = await _token(tenant_id)
    msg = {"subject": subject, "body": {"contentType": "Text", "content": body},
           "toRecipients": [{"emailAddress": {"address": to}}]}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(f"{_GRAPH}/me/messages",
                             headers={**_hdr(token), "Content-Type": "application/json"}, json=msg)
        r.raise_for_status()
        d = r.json()
    return {"draft_id": d.get("id"), "to": to, "subject": subject}

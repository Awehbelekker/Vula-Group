"""
vula/dynamics365/client.py — Dynamics 365 (Dataverse) via its Web API, per tenant.

OAuth one-click connect, same shape as vula/microsoft/service.py. Dataverse's Web API is
plain OData/REST over HTTPS with a bearer token — no SDK needed, matching this codebase's
existing httpx-direct style for every other external API. Read-only lookups only (accounts,
contacts, opportunities) — nothing here writes back to Dynamics.
"""
from __future__ import annotations

import logging

import httpx

from config import settings
from vula.dynamics365.credentials import get_access_token

logger = logging.getLogger(__name__)

SCOPES_SUFFIX = "/.default"  # appended to org_url to build the resource-specific scope


class Dynamics365NotConnected(Exception):
    pass


async def _auth(tenant_id: str) -> tuple[str, str]:
    creds = await get_access_token(tenant_id)
    if not creds or not creds.get("access_token"):
        raise Dynamics365NotConnected(tenant_id)
    return creds["access_token"], creds["org_url"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json",
            "OData-MaxVersion": "4.0", "OData-Version": "4.0"}


def _auth_url() -> str:
    return f"{settings.microsoft_authority}/oauth2/v2.0/authorize"


def _token_url() -> str:
    return f"{settings.microsoft_authority}/oauth2/v2.0/token"


# ── OAuth ─────────────────────────────────────────────────────────────────────

async def exchange_code(code: str, redirect_uri: str, org_url: str) -> dict:
    scope = f"offline_access {org_url.rstrip('/')}{SCOPES_SUFFIX}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(_token_url(), data={
            "client_id": settings.microsoft_client_id,
            "client_secret": settings.microsoft_client_secret,
            "code": code, "grant_type": "authorization_code",
            "redirect_uri": redirect_uri, "scope": scope})
        r.raise_for_status()
        tok = r.json()
        email = ""
        try:
            who = await client.get(f"{org_url.rstrip('/')}/api/data/v9.2/WhoAmI",
                                   headers=_hdr(tok["access_token"]))
            uid = who.json().get("UserId")
            if uid:
                me = await client.get(
                    f"{org_url.rstrip('/')}/api/data/v9.2/systemusers({uid})",
                    headers=_hdr(tok["access_token"]), params={"$select": "internalemailaddress"})
                email = me.json().get("internalemailaddress") or ""
        except Exception:
            pass
    return {"access_token": tok.get("access_token"), "refresh_token": tok.get("refresh_token"),
            "expires_in": tok.get("expires_in", 3600), "scope": tok.get("scope", ""), "email": email}


# ── Dataverse lookups (read-only) ──────────────────────────────────────────────

async def search_accounts(tenant_id: str, query: str, limit: int = 5) -> list[dict]:
    token, org_url = await _auth(tenant_id)
    params = {"$select": "name,telephone1,emailaddress1,address1_city",
             "$top": max(1, min(limit, 20))}
    if query:
        safe = query.replace("'", "''")
        params["$filter"] = f"contains(name,'{safe}')"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{org_url}/api/data/v9.2/accounts", headers=_hdr(token), params=params)
        r.raise_for_status()
        rows = r.json().get("value", [])
    return [{"name": a.get("name"), "phone": a.get("telephone1"), "email": a.get("emailaddress1"),
             "city": a.get("address1_city")} for a in rows]


async def search_contacts(tenant_id: str, query: str, limit: int = 5) -> list[dict]:
    token, org_url = await _auth(tenant_id)
    params = {"$select": "fullname,telephone1,emailaddress1,jobtitle",
             "$top": max(1, min(limit, 20))}
    if query:
        safe = query.replace("'", "''")
        params["$filter"] = f"contains(fullname,'{safe}')"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{org_url}/api/data/v9.2/contacts", headers=_hdr(token), params=params)
        r.raise_for_status()
        rows = r.json().get("value", [])
    return [{"name": c.get("fullname"), "phone": c.get("telephone1"), "email": c.get("emailaddress1"),
             "title": c.get("jobtitle")} for c in rows]


async def list_opportunities(tenant_id: str, query: str = "", limit: int = 5) -> list[dict]:
    token, org_url = await _auth(tenant_id)
    params = {"$select": "name,estimatedvalue,estimatedclosedate,statuscode",
             "$top": max(1, min(limit, 20)), "$orderby": "estimatedclosedate asc"}
    if query:
        safe = query.replace("'", "''")
        params["$filter"] = f"contains(name,'{safe}')"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{org_url}/api/data/v9.2/opportunities", headers=_hdr(token), params=params)
        r.raise_for_status()
        rows = r.json().get("value", [])
    return [{"name": o.get("name"), "value": o.get("estimatedvalue"),
             "close_date": o.get("estimatedclosedate"), "status": o.get("statuscode")} for o in rows]

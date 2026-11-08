"""vula/xero/client.py — read a business's Xero books (read-only), page by page, within Xero's limits.

Xero allows 60 calls a minute per organisation: a 429 waits the Retry-After it gives (once per
call, up to 60 s) and tries again. Paged endpoints return 100 records a page; reading stops at the
first short page. Money is converted to integer cents at the edge (`cents`), never kept as floats.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

API = "https://api.xero.com/api.xro/2.0"
PAGE_SIZE = 100
MAX_PAGES = 500          # 50,000 records of one kind — far beyond a small business
MAX_WAIT_S = 60


def cents(value: Any) -> int:
    """Xero's decimal amounts → integer cents, rounded half away from zero."""
    if value in (None, ""):
        return 0
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return 0


_MS_DATE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")


def xdate(value: Any) -> Optional[date]:
    """Xero dates: '/Date(1764547200000+0000)/' or '2026-12-01T00:00:00' → date."""
    if not value:
        return None
    s = str(value)
    m = _MS_DATE.match(s)
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


class XeroClient:
    """One business's Xero organisation. `get` and `pages` are the only network calls."""

    def __init__(self, tenant_id: str, *, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.tenant_id = tenant_id
        self._transport = transport
        self.calls = 0

    async def _headers(self) -> Dict[str, str]:
        from vula.xero import credentials
        a = await credentials.access(self.tenant_id)
        return {"Authorization": f"Bearer {a['access_token']}", "xero-tenant-id": a["xero_tenant_id"],
                "Accept": "application/json"}

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return (await self._request(path, params)).json() or {}

    async def get_bytes(self, path: str, accept: str, max_bytes: int) -> bytes:
        """A file (an attachment). Refused when it is bigger than max_bytes."""
        r = await self._request(path, None, accept=accept or "application/octet-stream")
        if len(r.content) > max_bytes:
            raise ValueError(f"bigger than {max_bytes // (1024 * 1024)} MB")
        return r.content

    async def _request(self, path: str, params: Optional[Dict[str, Any]] = None,
                       accept: Optional[str] = None) -> httpx.Response:
        for attempt in range(3):
            headers = await self._headers()
            if accept:
                headers["Accept"] = accept
            async with httpx.AsyncClient(timeout=60.0, transport=self._transport) as client:
                r = await client.get(f"{API}/{path}", params=params or {}, headers=headers)
            self.calls += 1
            if r.status_code == 429 and attempt < 2:
                wait = min(int(r.headers.get("Retry-After") or 5), MAX_WAIT_S)
                log.info("xero rate limit for %s: waiting %ss", self.tenant_id, wait)
                await asyncio.sleep(wait)
                continue
            if r.status_code == 401:
                raise LookupError("Xero refused Vula's sign-in — connect Xero again")
            if r.status_code == 403:
                raise PermissionError(f"Xero didn't allow reading {path.split('/')[0]} (missing permission)")
            if r.status_code >= 400:
                raise RuntimeError(f"Xero {path.split('/')[0]}: error {r.status_code}")
            return r
        raise RuntimeError("Xero kept asking Vula to slow down — try again in a few minutes")

    async def pages(self, path: str, key: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            batch = (await self.get(path, {**(params or {}), "page": page})).get(key) or []
            out += batch
            if len(batch) < PAGE_SIZE:
                return out
        raise RuntimeError(f"More than {MAX_PAGES * PAGE_SIZE} {key} in Xero — ask Vula's team")

    # ── what the move needs ──────────────────────────────────────────────────────────────────
    async def organisation(self) -> Dict[str, Any]:
        return ((await self.get("Organisation")).get("Organisations") or [{}])[0]

    async def accounts(self) -> List[Dict[str, Any]]:
        return (await self.get("Accounts")).get("Accounts") or []

    async def contacts(self) -> List[Dict[str, Any]]:
        return await self.pages("Contacts", "Contacts", {"includeArchived": "true"})

    async def items(self) -> List[Dict[str, Any]]:
        return (await self.get("Items")).get("Items") or []

    async def invoices(self) -> List[Dict[str, Any]]:
        """Customer invoices (ACCREC) and supplier bills (ACCPAY), drafts/voided/deleted left out."""
        return await self.pages("Invoices", "Invoices",
                                {"where": 'Status!="DELETED"&&Status!="VOIDED"&&Status!="DRAFT"', "order": "Date"})

    async def credit_notes(self) -> List[Dict[str, Any]]:
        return await self.pages("CreditNotes", "CreditNotes",
                                {"where": 'Status!="DELETED"&&Status!="VOIDED"&&Status!="DRAFT"'})

    async def payments(self) -> List[Dict[str, Any]]:
        return await self.pages("Payments", "Payments", {"where": 'Status!="DELETED"'})

    async def bank_transactions(self) -> List[Dict[str, Any]]:
        return await self.pages("BankTransactions", "BankTransactions",
                                {"where": 'Status!="DELETED"&&Status!="VOIDED"'})

    async def manual_journals(self) -> List[Dict[str, Any]]:
        return await self.pages("ManualJournals", "ManualJournals")

    async def attachments(self, endpoint: str, record_id: str) -> List[Dict[str, Any]]:
        """The files attached to one invoice/bill ("Invoices") or credit note ("CreditNotes")."""
        return (await self.get(f"{endpoint}/{record_id}/Attachments")).get("Attachments") or []

    async def attachment(self, endpoint: str, record_id: str, att: Dict[str, Any], max_bytes: int) -> bytes:
        from urllib.parse import quote
        return await self.get_bytes(f"{endpoint}/{record_id}/Attachments/{quote(att.get('FileName') or '', safe='')}",
                                    att.get("MimeType") or "", max_bytes)

    async def trial_balance(self, on: date) -> Dict[str, Any]:
        return ((await self.get("Reports/TrialBalance", {"date": on.isoformat()})).get("Reports") or [{}])[0]

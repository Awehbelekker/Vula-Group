"""vula/accountant_activity.py — what an accountant looked at and took away, on the business's record.

19 Nov 2026 (Phase 3 of Xero-quality books). Every read an accountant's login makes on a business
(server.py's guard lets through only those, vula/api/tenant_auth.ACCOUNTANT) is written to that
business's vula_merchant_audit, which the owner reads as "Accountant activity" on the Team screen:

  accountant_downloaded   a file (.csv, .zip, .pdf, .xlsx) — every time
  accountant_opened       a document (/v1/documents/{t}/filed/{id}/open) — every time
  accountant_viewed       a report, the bank lines, invoices, folders… — once per 30 minutes per page

What's kept is the page and its query (dates, the view), never the content. Writing never blocks or
fails the request.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_SEEN: Dict[Tuple[str, str, str], float] = {}
_WINDOW = 1800
_FILE = re.compile(r"\.(?:csv|zip|pdf|xlsx)/?$|/pdf/?$", re.I)
_ID = re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|/\d{3,}")

PAGES = {
    "reports/pnl": "Profit and loss", "reports/vat": "VAT", "reports/ledger": "General ledger",
    "reports/ledger.csv": "General ledger (CSV)", "reports/statements": "Financial statements",
    "reports/trial-balance": "Trial balance", "reports/aged-creditors": "Aged creditors",
    "reports/vat-scenario": "VAT check", "accounts": "Chart of accounts",
    "bank/transactions": "Bank lines", "bank/reconciliation": "Bank reconciliation",
    "bank/accounts": "Bank accounts", "invoices": "Invoices", "invoices/:id/pdf": "Invoice PDF",
    "expenses": "Expenses", "finances/insights": "Finances", "finance-folders": "Folders for the accountant",
    "finance-folders.zip": "Folders (zip)", "finance-folders/year-end-pack.xlsx": "Year-end workbook",
    "finance-folders/year-end-pack.zip": "Year-end pack (zip)", "bank-statements/coverage": "Bank statements on file",
    "books-audit/latest": "Books check", "books-audit/review": "Books review",
    "filed": "Documents", "filed/:id/open": "A document",
}


def page_of(path: str) -> str:
    """'/v1/commerce/digg/admin/invoices/<uuid>/pdf' → 'invoices/:id/pdf'."""
    p = urlsplit(path).path
    p = re.sub(r"^/v1/commerce/[^/]+/admin/", "", p)
    p = re.sub(r"^/v1/documents/[^/]+/", "", p)
    p = re.sub(r"^/v1/(?:team|tenants)/[^/]+/?", "", p) or "workspace"
    return _ID.sub("/:id", "/" + p.strip("/")).strip("/") or "workspace"


def action_for(path: str) -> str:
    if path.rstrip("/").endswith("/open"):
        return "accountant_opened"
    return "accountant_downloaded" if _FILE.search(path) else "accountant_viewed"


def _write(tenant_id: str, actor: Dict[str, Any], action: str, page: str, query: str,
           target: str = "") -> None:
    from vula.api import merchant_audit
    detail = {"page": page, "what": PAGES.get(page, page)}
    if query:
        detail["query"] = query[:300]
    if target:
        detail["document"] = target
    merchant_audit.audit(tenant_id, {"email": actor.get("email"), "user_id": actor.get("user_id")},
                         action, **detail)


def record(tenant_id: str, acc: Dict[str, Any], path: str, query: str = "", target: str = "") -> bool:
    """Note one read. Returns whether a row is written (views are kept once per 30 minutes)."""
    page = page_of(path)
    if page in ("me", "workspace", "brand"):
        return False                                # the workspace loading itself, not a read of the books
    action = action_for(path)
    if action == "accountant_viewed":
        key, now = (tenant_id, acc.get("email") or "", page), time.monotonic()
        if _SEEN.get(key, 0) > now:
            return False
        if len(_SEEN) > 5000:
            _SEEN.clear()
        _SEEN[key] = now + _WINDOW
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _write, tenant_id, acc, action, page, query, target)
    except RuntimeError:
        _write(tenant_id, acc, action, page, query, target)
    return True


def activity(tenant_id: str, limit: int = 100) -> list:
    """The latest accountant reads on this business, newest first."""
    from vula.api import merchant_audit
    try:
        rows = (merchant_audit._client().table("vula_merchant_audit").select("*")
                .eq("tenant_id", tenant_id).like("action", "accountant_%")
                .order("created_at", desc=True).limit(min(limit, 500)).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("accountant activity read skipped: %s", exc)
        return []
    return rows

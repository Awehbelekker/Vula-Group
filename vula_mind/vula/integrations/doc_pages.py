"""A later page of a document, sent on its own, joins the first page (2026-10-26).

Ian sent Bathroom Bizarre's two-page tax invoice 1013/00037437 as two photos. Vula filed page 1
alone (5 of the 9 lines, a total it couldn't find on the page) and would have filed page 2 as a
second invoice. A page is taken as the continuation of an earlier document only when all of
this holds:

- same supplier and the same document number, filed in the last few days;
- the earlier document is incomplete: no total, no lines, a figure that wasn't on the page, or
  lines that fall short of its total;
- the new page brings lines the earlier one doesn't have (a re-sent or re-issued copy of the
  same page repeats them, and is left to the duplicate and re-issue paths).

Joining appends the new lines and takes the totals from the later page (invoices print them on
the last page). Nothing here writes to the database; the WhatsApp handler does that.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

JOIN_WINDOW_DAYS = 3
_NUMBER_KEYS = ("document_number", "invoice_number")
_TOTAL_KEYS = ("total_cents", "vat_cents", "subtotal_cents", "discount_cents")
_NAME_NOISE = re.compile(r"\b(pty|ltd|cc|inc|limited|proprietary|the)\b")


def _alnum(s: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(s or "").lower())


def _supplier(s: Any) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower())
    return re.sub(r"\s+", " ", _NAME_NOISE.sub(" ", s)).strip()


def doc_number(fields: Optional[dict]) -> str:
    for k in _NUMBER_KEYS:
        n = _alnum((fields or {}).get(k))
        if len(n) >= 3:
            return n
    return ""


def _same_supplier(a: Any, b: Any) -> bool:
    a, b = _supplier(a), _supplier(b)
    return bool(a and b and (a == b or a.startswith(b) or b.startswith(a)))


def _lines(fields: Optional[dict]) -> List[dict]:
    return [li for li in ((fields or {}).get("line_items") or []) if isinstance(li, dict)]


def _line_key(li: dict) -> tuple:
    desc = re.sub(r"\s+", " ", str(li.get("description") or "").lower()).strip()[:40]
    return desc, li.get("total_cents")


def is_incomplete(fields: Optional[dict]) -> bool:
    """The page on file is missing something a later page could supply."""
    from vula.commerce.extraction_quality import lines_short_cents, scan_quality_ok
    f = fields or {}
    if f.get("_unverified_figures") or not f.get("total_cents") or not _lines(f):
        return True
    return bool(lines_short_cents(f)) or not scan_quality_ok(f)


def continues(first: Optional[dict], page: Optional[dict]) -> bool:
    """`page` (just read) is a later page of `first` (already filed)."""
    if not first or not page:
        return False
    if not doc_number(page) or doc_number(page) != doc_number(first):
        return False
    if not _same_supplier(first.get("supplier"), page.get("supplier")):
        return False
    if not is_incomplete(first):
        return False
    new = _lines(page)
    if not new:
        return bool(page.get("total_cents"))          # a last page holding only the totals
    seen = {_line_key(li) for li in _lines(first)}
    repeated = sum(1 for li in new if _line_key(li) in seen)
    return repeated * 2 <= len(new)


def merge(first: dict, page: dict) -> dict:
    """The first page's fields with the later page's lines added and its totals taken."""
    out = dict(first)
    seen = {_line_key(li) for li in _lines(first)}
    out["line_items"] = _lines(first) + [li for li in _lines(page) if _line_key(li) not in seen]
    if page.get("total_cents"):
        for k in _TOTAL_KEYS:
            if page.get(k) is not None:
                out[k] = page[k]
        if "amounts_exclude_vat" in page:
            out["amounts_exclude_vat"] = page["amounts_exclude_vat"]
        # The earlier page's "figure not on the page" was its guess at a total the later page
        # now gives; only the later page's own doubts carry over.
        out.pop("_unverified_figures", None)
        if page.get("_unverified_figures"):
            out["_unverified_figures"] = page["_unverified_figures"]
    for k, v in page.items():
        if not k.startswith("_") and v not in (None, "", [], {}) and out.get(k) in (None, "", [], {}):
            out[k] = v
    out["pages"] = int(first.get("pages") or 1) + 1
    return out


def find_first_page(tenant_id: str, fields: Optional[dict], db=None) -> Optional[dict]:
    """The filed document `fields` continues, or None. Fails closed: any error → None, and the
    page is filed on its own as before."""
    if not doc_number(fields) or not (fields or {}).get("supplier"):
        return None
    try:
        if db is None:
            from vula.commerce.service import _client
            db = _client()
        since = (datetime.now(timezone.utc) - timedelta(days=JOIN_WINDOW_DAYS)).isoformat()
        rows = (db.table("vula_filed_documents")
                .select("id,filename,category,project,status,fields,file_url,commerce_invoice_id,summary")
                .eq("tenant_id", tenant_id).gte("created_at", since)
                .order("created_at", desc=True).limit(80).execute().data or [])
    except Exception as exc:
        logger.debug("first-page lookup skipped: %s", exc)
        return None
    for row in rows:
        f = row.get("fields") or {}
        if f.get("page_of") or row.get("status") == "cancelled":
            continue
        if continues(f, fields):
            return row
    return None

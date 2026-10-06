"""
vula/commerce/reread.py — re-read filed documents whose first analysis came back empty.

2026-09-27: production had ~50 documents stuck as "Email attachment" (the email sync's
fallback when _analyze_document failed — Off the Hook's daily card settlement summaries among
them) and ~120 invoices/quotes with no amount or supplier. The durable copy of every filed
document is in Supabase Storage (file_url), so each can be analysed again with today's
pipeline (fitz → pdfplumber → OCR text, cheap → cloud → Docling). PDFs only; other formats
(xml, tot, ppsx) are left as they are.

Only a read that adds something replaces what's filed: a real category for an "Email
attachment", or an amount/supplier for a money document. Nothing is booked into the books —
the fields fill in supplier history, bank reconciliation (settlements) and search. A stock
sheet found on the way is stored as rows (stock_sheet.persist_if_stock_sheet).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from vula.commerce import service

log = logging.getLogger(__name__)

_MONEY = ("Invoice", "Quote / Estimate")
_STATUS: Dict[str, Dict[str, Any]] = {}      # tenant → last/ongoing run, for the dashboard


def _missing_money(row: dict) -> bool:
    f = row.get("fields") or {}
    return row.get("category") in _MONEY and (f.get("total_cents") in (None, "", 0) or not f.get("supplier"))


def _missing(row: dict) -> list:
    """Required details this document lacks (doc_quality.REQUIRED — 2026-10-02: every kind of
    document with details worth finding, not just invoices and quotes)."""
    from vula.commerce.doc_quality import missing_details
    return missing_details(row.get("category"), row.get("fields"))


_LINE_CHECKED = ("Invoice", "Quote / Estimate")


def _lines_short(row: dict) -> int:
    """Cents an invoice/quote's extracted line items fall short of its total (0 = complete)."""
    if row.get("category") not in _LINE_CHECKED:
        return 0
    from vula.commerce.extraction_quality import lines_short_cents
    return lines_short_cents(row.get("fields") or {})


def candidates(tenant_id: str, limit: int = 200, fresh_only: bool = False) -> List[dict]:
    """Filed PDFs that are uncategorised, missing a required detail, or (invoices and quotes)
    whose line items fall short of the total — a missed line (6 Oct, Gardens Handiman). fresh_only
    skips one already re-read (fields._reread_at) — the daily automatic pass tries each once."""
    from vula.commerce.doc_quality import REQUIRED
    rows = (service._client().table("vula_filed_documents")
            .select("id,category,filename,file_url,fields,summary,doc_id,project,created_at")
            .eq("tenant_id", tenant_id).in_("category", ["Email attachment", *REQUIRED])
            .order("created_at", desc=True).limit(2000).execute().data or [])
    out = [r for r in rows
           if r.get("file_url") and (r.get("filename") or "").lower().endswith(".pdf")
           and (r.get("category") == "Email attachment" or _missing(r) or _lines_short(r))
           and not (fresh_only and (r.get("fields") or {}).get("_reread_at"))]
    return out[:limit]


def _improves(row: dict, analysis: dict) -> bool:
    cat = analysis.get("category")
    fields = analysis.get("fields") or {}
    if not cat or (cat == "General Document" and not fields):
        return False
    if row.get("category") == "Email attachment":
        return True
    if _lines_only(row, analysis):
        return True
    # Only if the re-read found something that was missing (and kept the document's kind).
    from vula.commerce.doc_quality import missing_details
    before = set(missing_details(row.get("category"), row.get("fields")))
    after = set(missing_details(row.get("category"), fields))
    return cat == row.get("category") and len(after) < len(before)


def _lines_only(row: dict, analysis: dict) -> bool:
    """A complete document whose line items were short, re-read as the same document (same
    category and total) with fewer cents missing — only its line items are taken."""
    if _missing(row) or not _lines_short(row) or analysis.get("category") != row.get("category"):
        return False
    from vula.commerce.extraction_quality import lines_short_cents
    new, old = analysis.get("fields") or {}, row.get("fields") or {}
    try:
        same_total = int(new.get("total_cents")) == int(old.get("total_cents"))
    except (TypeError, ValueError):
        return False
    return same_total and bool(new.get("line_items")) and lines_short_cents(new) < _lines_short(row)


async def _download(url: str) -> bytes:
    from vula.storage_links import fetch
    return await fetch(url)


async def reread_missing(tenant_id: str, limit: int = 60, fresh_only: bool = False) -> Dict[str, Any]:
    from vula.api.whatsapp import _analyze_document
    rows = candidates(tenant_id, limit, fresh_only=fresh_only)
    status = _STATUS[tenant_id] = {"running": True, "total": len(rows), "done": 0, "fixed": 0,
                                   "stock_sheets": 0, "failed": 0, "categories": {}}
    for row in rows:
        try:
            data = await _download(row["file_url"])
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / Path(row["filename"]).name
                path.write_bytes(data)
                text = ""
                try:
                    from vula.ingestion.pipeline import VulaIngestionPipeline
                    pages = await VulaIngestionPipeline(tenant_id=tenant_id).parser.parse(path)
                    text = "\n".join(t for _, t in (pages or [])).strip()
                except Exception as exc:
                    log.debug("reread parse failed for %s: %s", row["id"], exc)
                analysis = await _analyze_document(tenant_id, row["filename"], path, text=text or None)
                if text:
                    try:
                        from vula.commerce import stock_sheet
                        if stock_sheet.persist_if_stock_sheet(tenant_id, row.get("doc_id") or row["id"],
                                                              row["filename"], text):
                            status["stock_sheets"] += 1
                    except Exception as exc:
                        log.debug("reread stock sheet check skipped: %s", exc)
            from datetime import datetime, timezone
            stamp = datetime.now(timezone.utc).isoformat()
            if analysis and _lines_only(row, analysis):
                # Only the missing lines were wrong: take the new line items, keep every other
                # detail (supplier, date, total, VAT) exactly as first read and booked.
                merged = {**(row.get("fields") or {}),
                          "line_items": analysis["fields"]["line_items"], "_reread_at": stamp}
                analysis = {**analysis, "category": row["category"],
                            "summary": row.get("summary"), "fields": merged}
            elif analysis and _improves(row, analysis):
                # Keep what the first read found when the re-read has nothing for that key.
                merged = {**(row.get("fields") or {}),
                          **{k: v for k, v in (analysis.get("fields") or {}).items() if v not in (None, "")},
                          "_reread_at": stamp}
            else:
                merged = None
            if merged is not None:
                (service._client().table("vula_filed_documents")
                 .update({"category": analysis["category"],
                          "summary": analysis.get("summary") or row.get("summary"),
                          "fields": merged})
                 .eq("tenant_id", tenant_id).eq("id", row["id"]).execute())
                try:
                    from vula.commerce.price_book import record_from_document
                    record_from_document(tenant_id, {**row, "category": analysis["category"],
                                                     "fields": analysis.get("fields") or {}})
                except Exception as exc:
                    log.debug("reread price book skipped: %s", exc)
                status["fixed"] += 1
                cats = status["categories"]
                cats[analysis["category"]] = cats.get(analysis["category"], 0) + 1
            else:
                # Tried once; still missing → the owner's list (doc_quality.health), not retried daily.
                (service._client().table("vula_filed_documents")
                 .update({"fields": {**(row.get("fields") or {}), "_reread_at": stamp}})
                 .eq("tenant_id", tenant_id).eq("id", row["id"]).execute())
        except Exception as exc:
            log.warning("reread of document %s failed: %s", row.get("id"), exc)
            status["failed"] += 1
        status["done"] += 1
    status["running"] = False
    return dict(status)


def status_for(tenant_id: str) -> Dict[str, Any]:
    return dict(_STATUS.get(tenant_id) or {"running": False})


# ── Learn from history (2026-09-28) ───────────────────────────────────────────
# Ian: "a lot of data is given and so little used — concerning if we onboard a client and pull
# in old data and it's not analysed." One run over everything already filed: re-read what's
# missing (above), read every BOQ in full (spreadsheets row by row, long PDFs past the first
# page of text), put every priced line into the price book, add the casual workers' day rates,
# and report what was learned. Nothing is booked into the books.

_LEARN: Dict[str, Dict[str, Any]] = {}
_BOQ = "Bill of Quantities (BOQ)"
_PRICE_LIST = "Menu / Price List"


def learn_status(tenant_id: str) -> Dict[str, Any]:
    return dict(_LEARN.get(tenant_id) or {"running": False})


def _money_rows(tenant_id: str) -> List[dict]:
    from vula.commerce.ledger import _all_pages
    from vula.commerce.price_book import SOURCE_KIND

    def make():
        return (service._client().table("vula_filed_documents")
                .select("id,tenant_id,category,filename,file_url,fields,summary,project,created_at")
                .eq("tenant_id", tenant_id).in_("category", list(SOURCE_KIND))
                .order("created_at", desc=True))
    return _all_pages(make)


async def _reread_boq(tenant_id: str, row: dict) -> bool:
    """Read one filed BOQ in full. True when it now has more lines than before."""
    from vula.ingestion import boq_sheet
    name = (row.get("filename") or "").lower()
    if not row.get("file_url") or not name.endswith(boq_sheet.SHEET_SUFFIXES + (".pdf",)):
        return False
    fields = row.get("fields") or {}
    before = len(fields.get("line_items") or [])
    data = await _download(row["file_url"])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(row["filename"]).name
        path.write_bytes(data)
        if name.endswith(boq_sheet.SHEET_SUFFIXES):
            new_fields = boq_sheet.apply_to_fields(fields, boq_sheet.parse(path))
        else:
            from vula.api.whatsapp import _complete_boq_lines
            from vula.ingestion.pipeline import VulaIngestionPipeline
            pages = await VulaIngestionPipeline(tenant_id=tenant_id).parser.parse(path)
            text = "\n".join(t for _, t in (pages or [])).strip()
            done = await _complete_boq_lines({"category": _BOQ, "fields": fields}, path, text,
                                             row["filename"])
            new_fields = done.get("fields") or fields
    if len(new_fields.get("line_items") or []) <= before:
        return False
    (service._client().table("vula_filed_documents").update({"fields": new_fields})
     .eq("tenant_id", tenant_id).eq("id", row["id"]).execute())
    row["fields"] = new_fields
    if row.get("project") and new_fields.get("sections"):
        service.upsert_project_boq(tenant_id, row["project"], int(new_fields.get("total_cents") or 0),
                                   sections=new_fields["sections"])
    return True


async def _file_text(tenant_id: str, row: dict, path: Path) -> str:
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pages = await VulaIngestionPipeline(tenant_id=tenant_id).parser.parse(path)
        return "\n".join(t for _, t in (pages or [])).strip()
    except Exception as exc:
        log.debug("parse failed for %s: %s", row.get("id"), exc)
        return ""


async def _read_price_list(tenant_id: str, row: dict) -> bool:
    """Read a filed price list's priced lines (it was filed with a summary only). True when
    it now has them."""
    from vula.api.whatsapp import _complete_price_list, _has_priced_lines
    from vula.ingestion import boq_sheet
    name = (row.get("filename") or "").lower()
    fields = row.get("fields") or {}
    if (not row.get("file_url") or _has_priced_lines(fields)
            or not name.endswith(boq_sheet.SHEET_SUFFIXES + (".pdf",))):
        return False
    data = await _download(row["file_url"])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(row["filename"]).name
        path.write_bytes(data)
        text = "" if name.endswith(boq_sheet.SHEET_SUFFIXES) else await _file_text(tenant_id, row, path)
        done = await _complete_price_list({"category": _PRICE_LIST, "fields": fields}, path, text,
                                          row["filename"])
    new_fields = done.get("fields") or fields
    if not _has_priced_lines(new_fields):
        return False
    (service._client().table("vula_filed_documents").update({"fields": new_fields})
     .eq("tenant_id", tenant_id).eq("id", row["id"]).execute())
    row["fields"] = new_fields
    return True


def _stock_sheet_candidates(tenant_id: str) -> List[dict]:
    """Filed PDFs that say they hold stock on hand but aren't stored as a stock sheet
    (2026-09-29, Gerflor: "DT SOH and Planning" was filed as a Programme / Schedule before
    stock sheets were stored as rows, so "how's stock on Creation?" had nothing to answer from)."""
    db = service._client()
    rows = []
    for col, pat in (("summary", "%stock on hand%"), ("summary", "%SOH%"), ("filename", "%SOH%")):
        try:
            rows += (db.table("vula_filed_documents")
                     .select("id,filename,file_url,doc_id,summary")
                     .eq("tenant_id", tenant_id).ilike(col, pat).limit(200).execute().data or [])
        except Exception as exc:
            log.debug("stock sheet candidates skipped: %s", exc)
    try:
        have = {r.get("doc_id") for r in (db.table("vula_stock_sheets").select("doc_id")
                                          .eq("tenant_id", tenant_id).limit(1000).execute().data or [])}
    except Exception:
        have = set()
    out, seen = [], set()
    for r in rows:
        key = r.get("doc_id") or r.get("id")
        if (key in seen or key in have or not r.get("file_url")
                or not (r.get("filename") or "").lower().endswith(".pdf")):
            continue
        seen.add(key)
        out.append(r)
    return out


async def _store_stock_sheet(tenant_id: str, row: dict) -> bool:
    from vula.commerce import stock_sheet
    data = await _download(row["file_url"])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(row["filename"]).name
        path.write_bytes(data)
        text = await _file_text(tenant_id, row, path)
    return bool(text and stock_sheet.persist_if_stock_sheet(
        tenant_id, row.get("doc_id") or row["id"], row["filename"], text))


async def learn_from_history(tenant_id: str, reread_first: bool = True) -> Dict[str, Any]:
    from vula.commerce import price_book
    st = _LEARN[tenant_id] = {"running": True, "step": "re-reading documents missing data",
                              "reread_fixed": 0, "boqs_completed": 0, "documents": 0,
                              "priced_lines": 0, "labour_rates": 0, "failed": 0}
    try:
        if reread_first and candidates(tenant_id):
            r = await reread_missing(tenant_id)
            st["reread_fixed"] = r.get("fixed", 0)
        st["step"] = "reading BOQs in full"
        rows = _money_rows(tenant_id)
        for row in rows:
            if row.get("category") == _BOQ:
                try:
                    if await _reread_boq(tenant_id, row):
                        st["boqs_completed"] += 1
                except Exception as exc:
                    log.warning("BOQ re-read of %s failed: %s", row.get("id"), exc)
                    st["failed"] += 1
        st["step"] = "reading price lists and stock sheets"
        st["price_lists_read"] = st["stock_sheets"] = 0
        for row in rows:
            if row.get("category") == _PRICE_LIST:
                try:
                    if await _read_price_list(tenant_id, row):
                        st["price_lists_read"] += 1
                except Exception as exc:
                    log.warning("price list read of %s failed: %s", row.get("id"), exc)
                    st["failed"] += 1
        for row in _stock_sheet_candidates(tenant_id):
            try:
                if await _store_stock_sheet(tenant_id, row):
                    st["stock_sheets"] += 1
            except Exception as exc:
                log.warning("stock sheet read of %s failed: %s", row.get("id"), exc)
                st["failed"] += 1
        st["step"] = "labelling variations"
        st["labelled"] = 0
        from vula.integrations.doc_labels import labels_for
        for row in rows:
            f = row.get("fields") or {}
            labels = labels_for(row.get("category") or "", row.get("summary") or "", f, row.get("filename") or "")
            if labels and labels != (f.get("labels") or []):
                f = {**f, "labels": labels}
                row["fields"] = f
                try:
                    (service._client().table("vula_filed_documents").update({"fields": f})
                     .eq("tenant_id", tenant_id).eq("id", row["id"]).execute())
                    st["labelled"] += 1
                except Exception as exc:
                    log.debug("label update failed for %s: %s", row.get("id"), exc)
        st["step"] = "building the price book"
        for row in rows:
            st["priced_lines"] += price_book.record_from_document(tenant_id, row)
            st["documents"] += 1
        st["labour_rates"] = price_book.record_worker_rates(tenant_id)
        st["summary"] = price_book.summary(tenant_id)
        st["still_unread"] = len(candidates(tenant_id))
        st["step"] = "done"
    except Exception as exc:
        log.warning("learn from history failed for %s: %s", tenant_id, exc)
        st["error"] = str(exc)
    st["running"] = False
    return dict(st)

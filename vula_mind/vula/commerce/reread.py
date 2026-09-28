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

import httpx

from vula.commerce import service

log = logging.getLogger(__name__)

_MONEY = ("Invoice", "Quote / Estimate")
_STATUS: Dict[str, Dict[str, Any]] = {}      # tenant → last/ongoing run, for the dashboard


def _missing_money(row: dict) -> bool:
    f = row.get("fields") or {}
    return row.get("category") in _MONEY and (f.get("total_cents") in (None, "", 0) or not f.get("supplier"))


def candidates(tenant_id: str, limit: int = 200) -> List[dict]:
    """Filed PDFs that are uncategorised or a money document missing its amount/supplier."""
    rows = (service._client().table("vula_filed_documents")
            .select("id,category,filename,file_url,fields,summary,doc_id")
            .eq("tenant_id", tenant_id).in_("category", ["Email attachment", *_MONEY])
            .order("created_at", desc=True).limit(2000).execute().data or [])
    out = [r for r in rows
           if r.get("file_url") and (r.get("filename") or "").lower().endswith(".pdf")
           and (r.get("category") == "Email attachment" or _missing_money(r))]
    return out[:limit]


def _improves(row: dict, analysis: dict) -> bool:
    cat = analysis.get("category")
    fields = analysis.get("fields") or {}
    if not cat or (cat == "General Document" and not fields):
        return False
    if row.get("category") == "Email attachment":
        return True
    # A money document: only if the re-read found what was missing.
    return bool(fields.get("total_cents") or fields.get("supplier"))


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def reread_missing(tenant_id: str, limit: int = 60) -> Dict[str, Any]:
    from vula.api.whatsapp import _analyze_document
    rows = candidates(tenant_id, limit)
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
            if analysis and _improves(row, analysis):
                (service._client().table("vula_filed_documents")
                 .update({"category": analysis["category"],
                          "summary": analysis.get("summary") or row.get("summary"),
                          "fields": analysis.get("fields") or {}})
                 .eq("tenant_id", tenant_id).eq("id", row["id"]).execute())
                status["fixed"] += 1
                cats = status["categories"]
                cats[analysis["category"]] = cats.get(analysis["category"], 0) + 1
        except Exception as exc:
            log.warning("reread of document %s failed: %s", row.get("id"), exc)
            status["failed"] += 1
        status["done"] += 1
    status["running"] = False
    return dict(status)


def status_for(tenant_id: str) -> Dict[str, Any]:
    return dict(_STATUS.get(tenant_id) or {"running": False})

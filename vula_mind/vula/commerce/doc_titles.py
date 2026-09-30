"""Human names for knowledge-base documents.

A knowledge-base hit carries the pipeline's doc_id and the raw filename it was ingested under.
For a WhatsApp upload the filed name used to be just "<category> <timestamp>" — Gerflor's
Mipolam Affinity slip-resistance test was filed as "Report 20260827-1001.pdf", the SAME name as
an unrelated chemical-resistance report uploaded that minute. A rep asking for "the Affinity slip
certificate" by name could never find it, and an answer couldn't say which data sheet a figure
came from.

- titles_for(): the filed name for each knowledge-base hit (joined on doc_id, not filename).
- generic_name() / title_from_summary(): a short content title for a document whose name says
  nothing, taken from its own summary and checked against it (never an invented word).
- retitle_tenant(): preview / apply that for the documents already filed.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def titles_for(tenant_id: str, doc_ids: Iterable) -> Dict[str, str]:
    """{doc_id: filename of the filed document} for the ids that have one. Fail-open ({})."""
    ids = sorted({str(d) for d in doc_ids if d})
    if not tenant_id or not ids:
        return {}
    try:
        from vula.commerce import service
        rows = (service._client().table("vula_filed_documents").select("doc_id,filename")
                .eq("tenant_id", tenant_id).in_("doc_id", ids).execute().data or [])
    except Exception as exc:
        logger.debug("titles_for skipped: %s", exc)
        return {}
    return {str(r["doc_id"]): r["filename"] for r in rows if r.get("doc_id") and r.get("filename")}


# "Report 20260827-1001.pdf", "General Document 20260827-1001.pdf", "Other 20260901-0800.jpg":
# a category label and a timestamp, nothing identifying.
# ("Invoice - Solid Cape 2026…" names its party: " - " marks an identified document.)
_GENERIC_RE = re.compile(r"^(?!.* - )[A-Za-z /()&]{2,40}? \d{8}-\d{4}\.\w{2,5}$")

# Money documents are identified by their party and amount, and are handled by the extraction
# path — only name the knowledge documents (reports, data sheets, brochures, letters…).
_MONEY_CATEGORIES = ("invoice", "receipt", "proof of payment", "bank statement", "quote",
                     "credit note", "statement", "settlement", "payslip", "purchase order")


def generic_name(filename: str) -> bool:
    return bool(_GENERIC_RE.match(filename or ""))


def is_money_category(category: str) -> bool:
    c = (category or "").lower()
    return any(m in c for m in _MONEY_CATEGORIES)


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9®™+'.\-]*")
_TITLE_FILLER = {"a", "an", "the", "of", "for", "and", "on", "in", "to", "with", "by", "-", "&",
                 "test", "results", "result", "report", "sheet", "data", "product", "card",
                 "brochure", "certificate", "letter", "flooring"}


def grounded_title(title: str, summary: str) -> bool:
    """Every content word of the title appears in the summary (case-insensitive). A title is a
    label a person will search by — it must never name a product or figure the document
    doesn't."""
    src = (summary or "").lower()
    words = _WORD.findall(title or "")
    if not words or len(words) > 10:
        return False
    for w in words:
        lw = w.lower().strip(".'")
        if lw in _TITLE_FILLER or len(lw) < 2:
            continue
        if lw not in src:
            return False
    return True


def _clean(title: str) -> str:
    t = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", title or "")
    t = re.sub(r"\s+", " ", t).strip(" .-")
    return t[:80]


async def title_from_summary(summary: str, category: str) -> Optional[str]:
    """A 3–8 word title naming the product/subject and what the document is, e.g. "Mipolam
    Affinity – slip resistance test (R10)". None when there's no summary, the model is
    unavailable, or the title isn't grounded in the summary."""
    summary = (summary or "").strip()
    if len(summary) < 20:
        return None
    try:
        import litellm
        from core.llm_router import resolve_generation_route
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route(task_type="doc_title")
        resp = await litellm.acompletion(
            model=model, api_key=api_key, api_base=api_base, temperature=0, max_tokens=40,
            messages=[
                {"role": "system", "content":
                    "Give a short file title (3-8 words) for this document: the product or "
                    "subject it is about, then what kind of document it is, plus a key result "
                    "if the summary states one (e.g. 'Mipolam Affinity – slip resistance test "
                    "(R10)'). Use ONLY words from the summary. Reply with the title only."},
                {"role": "user", "content": f"Category: {category}\nSummary: {summary[:900]}"}])
        title = _clean((resp.choices[0].message.content or "").strip().strip('"'))
    except Exception as exc:
        logger.debug("title_from_summary skipped: %s", exc)
        return None
    return title if title and grounded_title(title, summary) else None


def with_title(title: str, old_filename: str, created_at: Optional[str] = None) -> str:
    """New filename: the title, the original date, the original extension. The date keeps two
    versions of the same data sheet apart."""
    ext = Path(old_filename or "").suffix or ".pdf"
    m = re.search(r"(\d{4})(\d{2})(\d{2})-\d{4}", old_filename or "")
    date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else (created_at or "")[:10]
    return f"{_clean(title)} {date}{ext}".replace("  ", " ").strip()


async def retitle_tenant(tenant_id: str, apply: bool = False, limit: int = 300) -> Dict[str, Any]:
    """Propose (and with apply=True, write) content titles for this tenant's filed documents
    whose name is only a category and a timestamp. Money documents are skipped. Only the
    filename changes — the document, its knowledge-base chunks and its doc_id are untouched."""
    from vula.commerce import service
    rows = (service._client().table("vula_filed_documents")
            .select("id,filename,category,summary,created_at")
            .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(2000)
            .execute().data or [])
    todo = [r for r in rows if generic_name(r.get("filename") or "")
            and not is_money_category(r.get("category") or "")][:limit]
    proposals: List[Dict[str, Any]] = []
    skipped = 0
    for r in todo:
        title = await title_from_summary(r.get("summary") or "", r.get("category") or "")
        if not title:
            skipped += 1
            continue
        new = with_title(title, r["filename"], r.get("created_at"))
        proposals.append({"id": r["id"], "old": r["filename"], "new": new})
    written = 0
    if apply:
        for p in proposals:
            try:
                service._client().table("vula_filed_documents").update({"filename": p["new"]}) \
                    .eq("tenant_id", tenant_id).eq("id", p["id"]).execute()
                written += 1
            except Exception as exc:
                logger.warning("retitle %s failed: %s", p["id"], exc)
    return {"tenant_id": tenant_id, "generic": len(todo), "proposed": len(proposals),
            "skipped": skipped, "written": written, "applied": apply, "items": proposals}

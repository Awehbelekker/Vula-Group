"""Set a business up from its own documents — the company profile, terms, price list, menu, FAQ.

12 Nov 2026 (Ian): "in onboarding can a client upload documents for processing to eliminate
manual input … Vula needs to showcase its capabilities from the beginning."

The website already did this (profile_from_site.py): Vula reads the site and DRAFTS the business
profile answers — hours, areas, payment terms, returns, FAQ — for the owner to confirm. Plenty of
businesses have no website worth reading but do have a company profile PDF, a terms sheet or a
price list. A document they uploaded was filed and could be searched, but never used to fill
anything in: the owner still typed every profile answer by hand.

For each uploaded document this:
  1. files it in the knowledge base exactly like any other upload (so Vula can answer from it), and
  2. reads its text and drafts the profile answers still open — with the SAME rule as the website:
     an answer is kept only when the sentence it quotes is really in the document. A figure or a
     policy the model made up has no sentence to point at, and is dropped.

A draft is never an answer. The owner sees each one with the document it came from and keeps it
or throws it away (Settings › Business profile, the Home setup card, or the WhatsApp interview).
A question that already has a draft — from the website or an earlier document — keeps it.

Progress is kept in vula_background_jobs (kind "profile_docs") so either worker can report it.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

JOB = "profile_docs"
MAX_FILES = 10
ALLOWED = {".pdf", ".docx", ".doc", ".txt", ".md", ".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg", ".webp"}
_CHUNK = 3500            # profile_from_site reads this much per "page"
_MAX_CHUNKS = 4          # …and about this many in one go


def allowed(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in ALLOWED


def as_pages(name: str, text: str) -> List[Tuple[str, str, str]]:
    """A document's text as the (where, title, text) pieces profile_from_site reads. Every piece
    carries the same "document: <name>" address, so a kept answer points at the file."""
    from vula.commerce.business_profile import DOCUMENT_PREFIX
    clean = (text or "").strip()
    where = DOCUMENT_PREFIX + name
    return [(where, name, clean[i:i + _CHUNK]) for i in range(0, min(len(clean), _CHUNK * _MAX_CHUNKS), _CHUNK)]


async def read_text(tenant_id: str, path: Path) -> str:
    """The document's text, by the same parser the knowledge base uses (PDF, Word, spreadsheet,
    photo of a page)."""
    from vula.ingestion.pipeline import VulaIngestionPipeline
    pages = await VulaIngestionPipeline(tenant_id=tenant_id).parser.parse(path)
    return "\n\n".join(str(t) for _, t in (pages or []) if t)


async def draft_from_text(tenant_id: str, name: str, text: str) -> int:
    """Draft the open profile answers from one document's text and store the new ones. Returns
    how many drafts were added."""
    from vula.commerce import business_profile as bp
    from vula.commerce.profile_from_site import draft_from_pages
    pages = as_pages(name, text)
    if not pages:
        return 0
    drafts = await draft_from_pages(tenant_id, pages, source="documents")
    already = set(bp.get_drafts(tenant_id))          # the first source to answer a question keeps it
    fresh = {k: d for k, d in drafts.items() if k not in already}
    return bp.save_drafts(tenant_id, fresh) if fresh else 0


def status(tenant_id: str) -> Dict[str, Any]:
    from vula.commerce import site_import
    return site_import.status(tenant_id, JOB)


def _save(tenant_id: str, st: Dict[str, Any]) -> None:
    from vula.commerce import site_import
    site_import._save(tenant_id, st, JOB)


async def run(tenant_id: str, files: List[Dict[str, Any]], file_it=None) -> Dict[str, Any]:
    """Read the uploaded documents one at a time: file each in the knowledge base (`file_it`,
    the same step any upload gets), then draft profile answers from it. One document failing
    never stops the rest; each one's outcome is recorded.

    `files`: [{"name", "path", "mime"}]. Returns the final status."""
    from vula.commerce import site_import
    st: Dict[str, Any] = {"running": True, "started_at": site_import._now(), "step": "reading",
                          "files": [{"name": f["name"], "status": "waiting", "drafts": 0} for f in files],
                          "drafts": 0}
    _save(tenant_id, st)
    for i, f in enumerate(files):
        row = st["files"][i]
        row["status"] = "reading"
        _save(tenant_id, st)
        path = Path(f["path"])
        try:
            if file_it is not None:
                try:
                    await file_it(tenant_id, path, f.get("mime"))
                    row["filed"] = True
                except Exception as exc:  # noqa: BLE001 — still try to read it for the profile
                    log.warning("setup from documents: %s not filed for %s: %s", f["name"], tenant_id, exc)
                    row["filed"] = False
            text = await read_text(tenant_id, path)
            if not text.strip():
                row["status"] = "no_text"
            else:
                row["drafts"] = await draft_from_text(tenant_id, f["name"], text)
                row["status"] = "done"
                st["drafts"] += row["drafts"]
        except Exception as exc:  # noqa: BLE001
            log.warning("setup from documents: %s failed for %s: %s", f["name"], tenant_id, exc)
            row["status"] = "failed"
        _save(tenant_id, st)
    st.update({"running": False, "step": "done", "finished_at": site_import._now()})
    _save(tenant_id, st)
    return st


def already_running(tenant_id: str) -> bool:
    return bool(status(tenant_id).get("running"))


def reject_reason(names: List[str]) -> Optional[str]:
    """Why this batch can't be taken, or None."""
    if not names:
        return "Choose at least one document."
    if len(names) > MAX_FILES:
        return f"Upload up to {MAX_FILES} documents at a time."
    bad = [n for n in names if not allowed(n)]
    if bad:
        return (f"I can't read {', '.join(bad[:3])}. Use PDF, Word, Excel, a text file or a photo of the page.")
    return None

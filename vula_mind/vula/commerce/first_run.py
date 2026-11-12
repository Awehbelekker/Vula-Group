"""The first-run welcome: set a new business up from its website (and documents, if it wants),
then show the owner what Vula learned and let them try it.

12 Nov 2026 (Ian: Vula should "showcase its capabilities and strength from the beginning" —
but "not all clients would want to do this in trial", so it can be skipped, or be "just my
website"). Everything here reads what the existing set-up jobs produced; nothing new is stored
apart from whether the welcome was closed.

  summary                  what the website import and the document reading found so far, the
                           drafted answers, the brand, whether the welcome should show, and up to
                           three questions to try
  preview_customer_answer  how Vula would answer a customer, right now, from what the owner has
                           kept — customer-visible documents only, no tools, nothing saved
  close                    skipped or done, for the whole business
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

JOB = "first_run"
SHOW_FOR_DAYS = 14
PREVIEWS_PER_HOUR = 10
MAX_QUESTION = 300

# A question a customer would ask, per profile answer — fixed wording, never written by a model.
_TRY: Dict[str, str] = {
    "hours": "What are your opening hours?",
    "area": "Do you deliver to {place}?",
    "contact": "What's your phone number?",
    "payment": "How can I pay?",
    "cancellations": "Can I cancel my order?",
    "delivery_fee": "How much is delivery?",
    "lead_times": "How soon can you deliver?",
    "bookings": "How do I book?",
}
_previews: Dict[str, List[float]] = {}


def _first_place(text: str) -> Optional[str]:
    """The first place named in an areas answer ("Sea Point, Green Point and Gardens" → "Sea Point")."""
    part = re.split(r"[,;/]| and | or |\n", text or "", maxsplit=1)[0].strip(" .:-")
    part = re.sub(r"(?i)^(we )?(deliver|work|operate)( to| in)?\s+", "", part).strip()
    return part if 2 <= len(part) <= 40 and not re.search(r"\d{3,}", part) else None


def try_asking(answers: Dict[str, str], drafts: Dict[str, Any], limit: int = 3) -> List[Dict[str, Any]]:
    """Up to `limit` questions a customer would ask that the owner's answers (or drafts) cover.
    `ready` is true when the answer is kept — only kept answers are used to answer customers."""
    out: List[Dict[str, Any]] = []
    for key, wording in _TRY.items():
        source = (answers or {}).get(key) or ((drafts or {}).get(key) or {}).get("answer")
        if not (source or "").strip():
            continue
        if "{place}" in wording:
            place = _first_place(source)
            wording = wording.format(place=place) if place else "Where do you deliver?"
        out.append({"key": key, "question": wording, "ready": bool((answers or {}).get(key))})
        if len(out) >= limit:
            break
    return out


def _status(tenant_id: str) -> Dict[str, Any]:
    from vula.commerce import site_import
    return site_import.status(tenant_id, JOB)


def close(tenant_id: str, reason: str) -> Dict[str, Any]:
    """The welcome is skipped or done — for the whole business, not one device."""
    from vula.commerce import site_import
    if reason not in ("skipped", "done"):
        raise ValueError("reason must be 'skipped' or 'done'")
    st = {"running": False, "closed": reason, "closed_at": site_import._now()}
    site_import._save(tenant_id, st, JOB)
    return st


def _created_recently(tenant_id: str, now: datetime) -> bool:
    try:
        from vula.commerce import service
        rows = (service._client().table("vula_tenant_config").select("created_at")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        created = datetime.fromisoformat(str(rows[0]["created_at"]).replace("Z", "+00:00"))
    except Exception as exc:  # noqa: BLE001 — can't tell: don't push a welcome on anyone
        log.debug("first run: creation date unreadable for %s: %s", tenant_id, exc)
        return False
    return now - created <= timedelta(days=SHOW_FOR_DAYS)


def _read(label: str, fn, missing: List[str]):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — a part that can't be read is reported, not zero
        log.warning("first run summary: %s unreadable: %s", label, exc)
        missing.append(label)
        return None


def summary(tenant_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    from vula.commerce import business_profile as bp
    from vula.commerce import profile_from_docs, site_import
    now = now or datetime.now(timezone.utc)
    missing: List[str] = []
    closed = (_read("welcome", lambda: _status(tenant_id), missing) or {}).get("closed")
    website = _read("website", lambda: site_import.status(tenant_id), missing) or {}
    documents = _read("documents", lambda: profile_from_docs.status(tenant_id), missing) or {}
    profile = _read("profile", lambda: bp.status(tenant_id), missing) or {}
    brand = _read("brand", lambda: _brand(tenant_id), missing) or {}
    setup_done = bool(_read("checklist", lambda: _setup_complete(tenant_id), missing))
    running = bool(website.get("running") or documents.get("running"))
    keep = ("running", "step", "url", "platform", "products_found", "products_created", "products_updated",
            "pages_read", "kb_documents", "profile_drafts", "profile_facts", "images_kept", "coverage",
            "note", "error", "started_at", "finished_at")
    return {
        "show": not closed and not setup_done and _created_recently(tenant_id, now),
        "closed": closed, "running": running,
        "website": {k: website[k] for k in keep if k in website},
        "documents": {k: documents[k] for k in ("running", "files", "drafts") if k in documents},
        "brand": brand,
        "profile": {"answered": profile.get("answered"), "total": profile.get("total"),
                    "drafts": len(profile.get("drafts") or {})},
        "try_asking": try_asking(profile.get("answers") or {}, profile.get("drafts") or {}),
        **({"not_read": missing} if missing else {}),
    }


def _brand(tenant_id: str) -> Dict[str, Any]:
    from vula.commerce import website_setup
    from vula.commerce.service import _client
    rows = (_client().table("commerce_invoice_settings").select("logo_url,accent_color")
            .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    s = rows[0] if rows else {}
    return {"logo_url": s.get("logo_url"), "accent_color": s.get("accent_color"),
            "set": not website_setup.is_default_brand(s)}


def _setup_complete(tenant_id: str) -> bool:
    from vula.api.master import setup_checklist
    from vula.commerce.owner_setup import owner_view
    return bool(owner_view(setup_checklist(tenant_id)).get("complete"))


def _allow_preview(tenant_id: str, now: float) -> bool:
    recent = [t for t in _previews.get(tenant_id, []) if now - t < 3600]
    if len(recent) >= PREVIEWS_PER_HOUR:
        _previews[tenant_id] = recent
        return False
    _previews[tenant_id] = recent + [now]
    return True


async def preview_customer_answer(tenant_id: str, question: str) -> Dict[str, Any]:
    """How Vula would answer a customer, right now. The general answering skill (no tools, so
    nothing can be ordered or sent), limited to customer-visible documents, in the customer
    voice, with a business fact it has no document for declined rather than guessed. Not a
    customer, not a session: nothing is saved or recorded."""
    from core import voice
    from core.skills.base import SkillInput, grounding_of, source_names
    from core.skills.loader import get_skill
    from vula.ingestion.pipeline import kb_audience
    q = re.sub(r"\s+", " ", question or "").strip()
    if not q:
        raise ValueError("Ask a question.")
    if len(q) > MAX_QUESTION:
        raise ValueError(f"Keep the question under {MAX_QUESTION} characters.")
    if not _allow_preview(tenant_id, time.time()):
        raise PermissionError("That's the limit for previews this hour. Try again later.")
    with kb_audience("public"), voice.use(tenant_id, "customer", "whatsapp"):
        out = await get_skill("reasoning")(SkillInput(
            question=q, tenant_id=tenant_id, top_k=5,
            metadata={"strict_grounding": True, "channel": "preview"}))
    answer = out.answer if (out.success and out.answer) else "I couldn't answer that just now."
    return {"answer": answer, "sources": source_names(out.sources) or None,
            "grounded": grounding_of(out.sources) in ("kb", "records")}

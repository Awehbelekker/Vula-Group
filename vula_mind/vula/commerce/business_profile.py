"""The business profile interview (2026-09-30).

A new tenant has no documents of its own, and the starter KB is model-drafted with
[placeholders]. Instead of letting Vula guess at delivery areas, hours or payment terms, the owner
answers ~10 short questions — on WhatsApp ("set up my profile") or in the dashboard — and the
answers become one knowledge-base document, "Business profile (from the owner)", that every
skill retrieves like any other document. Until a question is answered, Vula has nothing to state
and says it will check (the starter KB's placeholder lines are no longer retrievable either —
see pipeline._without_placeholders).

Storage: vula_business_profile (migration 189), fail-open when the table isn't there yet.

2026-10-17 (KB retrieval 1b): written as TWO documents — the answers customers may see (hours,
delivery areas, payment methods, returns…) as a public one the customer assistant can use, and
the rest (who does what in the team, tax regime, payroll, pricing approach) as a team-only one.
Which answers are public is the owner's list (public_keys, migration 214); until they set it,
each question's `public` default below applies. A question added later is team-only unless it
says otherwise.
"""
from __future__ import annotations

import hashlib
import logging
import re as _re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_COMMON: List[Dict[str, str]] = [
    {"key": "what_we_do", "public": True, "q": "In a sentence or two: what does the business sell or do, and for whom?"},
    {"key": "area", "public": True, "q": "Where do you work or deliver? (areas, towns, or 'collection only')"},
    {"key": "hours", "public": True, "q": "What are your working hours, and when are you closed?"},
    {"key": "payment", "public": True, "q": "How do customers pay you (EFT, card, cash, payment link), and what are your payment terms?"},
    {"key": "pricing", "q": "How do you price — fixed price list, quote per job, or per hour/m²? Anything you always add (call-out, delivery)?"},
    {"key": "lead_times", "public": True, "q": "How quickly can you usually deliver or start a job?"},
    {"key": "cancellations", "public": True, "q": "What's your policy on cancellations, returns or refunds?"},
    {"key": "team", "q": "Who does what in the team (e.g. 'Sipho quotes, Lindi does accounts')?"},
    {"key": "faq", "public": True, "q": "What do customers ask you most often — and what's the answer?"},
    # 6 Oct: tax questions need this (vula/commerce/tax.py) — Turnover Tax, company, sole prop.
    {"key": "tax_regime", "q": "How is the business taxed: Turnover Tax, a (Pty) Ltd company, or a sole proprietor (your own name)?"},
    {"key": "payroll", "q": "Do you employ staff on payroll (PAYE/UIF), or are workers paid through subcontractors?"},
    {"key": "vat_category", "q": "If you're VAT registered: do you submit VAT every two months (category A or B) or monthly (C)?"},
]

_EXTRA: Dict[str, List[Dict[str, str]]] = {
    "food": [{"key": "delivery_fee", "public": True, "q": "What's your delivery fee and minimum order, and which days do you deliver?"}],
    "retail": [{"key": "stock", "public": True, "q": "Which products do you always keep in stock, and which are order-in only?"}],
    "trades": [{"key": "registration", "public": True, "q": "Are you registered with CIDB/NHBRC or any trade body? Anything customers should know about guarantees?"}],
    "services": [{"key": "fees", "q": "How are your fees structured (fixed, % of project, hourly) and when do you invoice?"}],
    "health": [{"key": "bookings", "public": True, "q": "How do patients book, and what's your cancellation / no-show policy? Which medical aids do you work with?"}],
    "rep": [{"key": "principal", "q": "Which brands or principals do you represent, and who are your distributors?"},
            {"key": "territory", "q": "Which areas and customer types (architects, contractors, retailers) do you cover?"}],
}

PROFILE_FILENAME = "Business profile (from the owner).md"
PUBLIC_PROFILE_FILENAME = "Business profile — for customers (from the owner).md"


def questions(business_type: str) -> List[Dict[str, str]]:
    return _COMMON + _EXTRA.get((business_type or "").lower(), [])


def _client():
    from vula.commerce import service
    return service._client()


def _business_type(tenant_id: str) -> str:
    try:
        from vula.api.tenants import tenant_profile
        return (tenant_profile(tenant_id) or {}).get("business_type") or "other"
    except Exception:
        return "other"


def default_public_keys(business_type: str) -> List[str]:
    return [q["key"] for q in questions(business_type) if q.get("public")]


def get_public_keys(tenant_id: str) -> List[str]:
    """The owner's list of answers customers may see; the defaults until they set one (or before
    migration 214 is applied)."""
    try:
        rows = (_client().table("vula_business_profile").select("public_keys")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        keys = rows[0].get("public_keys") if rows else None
        if isinstance(keys, list):
            return [str(k) for k in keys]
    except Exception as exc:
        log.debug("business profile public_keys read skipped (migration 214?): %s", exc)
    return default_public_keys(_business_type(tenant_id))


def get_answers(tenant_id: str) -> Dict[str, str]:
    try:
        rows = (_client().table("vula_business_profile").select("answers")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        return dict((rows[0].get("answers") if rows else None) or {})
    except Exception as exc:
        log.debug("business profile read skipped (migration 189?): %s", exc)
        return {}


def structured_hours(tenant_id: str) -> str:
    """The opening hours set in Settings (commerce_order_settings.business_hours) as one line,
    or "" when they aren't set."""
    try:
        from vula.commerce.hours import describe
        from vula.commerce.order_workflow import get_order_settings
        return describe(get_order_settings(tenant_id).get("business_hours"))
    except Exception:
        return ""


def effective_answers(tenant_id: str, answers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The answers with the hours taken from Settings when they're set there. 2026-10-22: the
    free-text "hours" answer and the structured hours check_business_hours reads could disagree,
    so a customer got one answer from the KB and another from the hours check. One source now."""
    answers = dict(get_answers(tenant_id) if answers is None else answers)
    hours = structured_hours(tenant_id)
    if hours:
        answers["hours"] = hours
    return answers


def status(tenant_id: str) -> Dict[str, Any]:
    qs = questions(_business_type(tenant_id))
    answers = effective_answers(tenant_id)
    missing = [q for q in qs if not (answers.get(q["key"]) or "").strip()]
    return {"questions": qs, "answers": answers, "missing": [q["key"] for q in missing],
            "answered": len(qs) - len(missing), "total": len(qs),
            "public_keys": get_public_keys(tenant_id)}


def next_question(tenant_id: str, skipped: Optional[set] = None) -> Optional[Dict[str, str]]:
    skipped = skipped or set()
    answers = effective_answers(tenant_id)
    for q in questions(_business_type(tenant_id)):
        if not (answers.get(q["key"]) or "").strip() and q["key"] not in skipped:
            return q
    return None


def render_document(tenant_id: str, answers: Dict[str, str], keys: Optional[set] = None,
                    for_customers: bool = False) -> str:
    """The profile as a KB document — all answers, or only `keys`. "" when none are answered."""
    try:
        from vula.api.tenants import get_config
        name = (get_config(tenant_id) or {}).get("display_name") or tenant_id
    except Exception:
        name = tenant_id
    title = "what customers can know" if for_customers else "for the team"
    body: List[str] = []
    for q in questions(_business_type(tenant_id)):
        a = (answers.get(q["key"]) or "").strip()
        if a and (keys is None or q["key"] in keys):
            body += [f"## {q['q']}", a, ""]
    if not body:
        return ""
    return "\n".join([f"# {name} — business profile, {title} (answered by the owner)", ""] + body).strip()


async def save_answers(tenant_id: str, updates: Dict[str, str], by: str = "",
                       public_keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """Merge answers (and, if given, the owner's list of answers customers may see), store them,
    and re-write the two profile documents in the tenant's KB."""
    valid = {q["key"] for q in questions(_business_type(tenant_id))}
    clean = {k: str(v or "").strip()[:2000] for k, v in (updates or {}).items() if k in valid}
    answers = {**get_answers(tenant_id), **clean}
    answers = {k: v for k, v in answers.items() if v}
    row = {"tenant_id": tenant_id, "answers": answers, "updated_by": by or None,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    warning = None
    if public_keys is not None:
        row["public_keys"] = [k for k in public_keys if k in valid]
    try:
        _client().table("vula_business_profile").upsert(row).execute()
    except Exception as exc:
        if "public_keys" not in row:
            log.warning("business profile save failed for %s: %s", tenant_id, exc)
            return {"saved": False, "error": "Couldn't save — has migration 189 been applied?"}
        # The answers still save without the column; the customer list needs migration 214.
        row.pop("public_keys")
        try:
            _client().table("vula_business_profile").upsert(row).execute()
            warning = "Answers saved, but which ones customers see couldn't be — run migration 214."
        except Exception as exc2:
            log.warning("business profile save failed for %s: %s", tenant_id, exc2)
            return {"saved": False, "error": "Couldn't save — has migration 189 been applied?"}
    keys = set(row.get("public_keys") if "public_keys" in row else get_public_keys(tenant_id))
    ingested = await _write_documents(tenant_id, effective_answers(tenant_id, answers), keys)
    out = {"saved": True, "ingested": ingested, **status(tenant_id)}
    if warning:
        out["warning"] = warning
    return out


async def refresh_documents(tenant_id: str) -> bool:
    """Re-write the profile documents from the stored answers — e.g. after the opening hours
    change in Settings, so the customer KB never keeps the old hours."""
    answers = effective_answers(tenant_id)
    if not answers:
        return True
    return await _write_documents(tenant_id, answers, set(get_public_keys(tenant_id)))


async def _write_documents(tenant_id: str, answers: Dict[str, str], public: set) -> bool:
    """The customer part (audience public) and the team part (staff). Each is cleared before it
    is re-written, so an answer moved from one side to the other leaves nothing behind."""
    from vula.ingestion.pipeline import VulaIngestionPipeline
    pipe = VulaIngestionPipeline(tenant_id=tenant_id)
    base = hashlib.md5(tenant_id.encode()).hexdigest()[:16]
    team_keys = {k for k in answers if k not in public}
    ok = True
    for doc_id, filename, keys, audience in (
            ("profile_" + base, PROFILE_FILENAME, team_keys, "staff"),
            ("profile_public_" + base, PUBLIC_PROFILE_FILENAME, public, "public")):
        doc = render_document(tenant_id, answers, keys, for_customers=audience == "public")
        try:
            await pipe.store.delete_document(tenant_id, doc_id)
            if doc:
                res = await pipe.ingest_text(content=doc, filename=filename, doc_id=doc_id,
                                             audience=audience)
                ok = ok and res.status == "success"
        except Exception as exc:
            log.warning("business profile KB write failed for %s (%s): %s", tenant_id, audience, exc)
            ok = False
    return ok


# ── WhatsApp interview ──────────────────────────────────────────────────────────
# "set up my profile" starts it; each reply answers the question just asked; "skip" moves on;
# "stop" ends it. In-memory state with a 30-minute TTL — like the sticky follow-up.
_ACTIVE: Dict[Tuple[str, str], Tuple[str, float, set]] = {}
_TTL = 1800

_START_RE = _re.compile(r"^\s*(?:set ?up|start|do|fill in|update|finish)\s+(?:my|our|the)\s+"
                        r"(?:business )?profile\b|^\s*business profile\s*$|^\s*profile\s*$", _re.I)
_SKIP_RE = _re.compile(r"^\s*(?:skip|next|pass|later|not sure|n/?a)\s*[.!]?\s*$", _re.I)
_STOP_RE = _re.compile(r"^\s*(?:stop|cancel|done|finish|that'?s all|enough)\s*[.!]?\s*$", _re.I)


def _ask(q: Dict[str, str], st: Dict[str, Any]) -> str:
    return (f"Question {st['answered'] + 1} of {st['total']}: {q['q']}\n\n"
            "(Reply with the answer — or *skip*, or *stop* to finish later.)")


async def handle_interview(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """The reply for this message if it's part of the profile interview, else None."""
    key = (tenant_id, phone)
    now = time.time()
    active = _ACTIVE.get(key)
    if active and now - active[1] > _TTL:
        _ACTIVE.pop(key, None)
        active = None
    if not active:
        if not _START_RE.search(text or ""):
            return None
        q = next_question(tenant_id)
        if not q:
            return ("Your business profile is complete ✅ — I answer from it. To change an answer, "
                    "edit it in Settings › Business profile.")
        _ACTIVE[key] = (q["key"], now, set())
        return ("Let's set up your business profile, so I answer customers and your team from what "
                "YOU tell me, not guesses.\n\n" + _ask(q, status(tenant_id)))
    asked, _, skipped = active
    if _STOP_RE.search(text or ""):
        _ACTIVE.pop(key, None)
        st = status(tenant_id)
        return (f"Saved — {st['answered']} of {st['total']} answered. Send *set up my profile* any "
                "time to carry on.")
    if _SKIP_RE.search(text or ""):
        skipped = skipped | {asked}
    else:
        res = await save_answers(tenant_id, {asked: text}, by=phone)
        if not res.get("saved"):
            _ACTIVE.pop(key, None)
            return f"I couldn't save that answer — {res.get('error')}"
    q = next_question(tenant_id, skipped)
    if not q:
        _ACTIVE.pop(key, None)
        st = status(tenant_id)
        return (f"Thanks — that's the profile done ({st['answered']} of {st['total']} answered) ✅. "
                "I'll answer from it from now on; anything you skipped I'll say I need to check.")
    _ACTIVE[key] = (q["key"], now, skipped)
    return "Got it ✅\n\n" + _ask(q, status(tenant_id))

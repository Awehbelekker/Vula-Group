"""
vula/escalation.py — agent escalate-and-learn.

When the agent isn't confident, escalate the client's question to a designated human helper
on WhatsApp. When the helper answers, relay it to the client and store it as a learned answer
so the agent can answer the same question itself next time.

Helper selection: a team member who opted into the "help_request" notify event, else an
owner/manager with a WhatsApp number.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# Phrases that mean "the agent couldn't really answer" (escalate even if confidence looks ok).
# Covers Afrikaans too — the assistant replies in the customer's language, so an Afrikaans
# "ek weet nie" previously sailed past this English-only check and never escalated.
_NO_ANSWER = re.compile(
    r"(i (don'?t|do not) (know|have)|not sure|couldn'?t find|can'?t help|no (info|information|record)|unable to|i'?m not able"
    r"|ek (weet|het) nie|nie seker nie|kan nie help nie|ek sal (met die span|eers) (kyk|vra)|laat ek uitvind)",
    re.IGNORECASE,
)

# Explicit complaint/frustration words (English + Afrikaans) — a signal about the CUSTOMER's
# message, independent of how confident the bot's own reply looked. Before this, staff only
# got pulled in when the bot doubted itself; an upset customer getting a calm, confidently
# wrong or unhelpful reply never escalated at all.
_FRUSTRATION_WORDS = re.compile(
    r"(ridiculous|terrible|useless|pathetic|waste of (my )?(time|money)|not happy|"
    r"unacceptable|disgusted|disgusting|(this is |so )?annoying|angry|furious|fed up|"
    r"sick of|worst (service|experience)|scam|rip.?off|shocking service|"
    r"belaglik|omgekrap|woedend|totale mors|swak diens|verskriklike diens)",
    re.IGNORECASE,
)


def customer_seems_frustrated(text: str) -> bool:
    """Cheap heuristic on the customer's own message — no LLM call needed. Catches explicit
    complaint words, shouting (mostly-caps with real content, not a short "OK"/order number),
    and repeated ?!/!! punctuation, the common typed signals of frustration."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _FRUSTRATION_WORDS.search(stripped):
        return True
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) >= 8 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.8:
        return True
    if re.search(r"[!?]{3,}", stripped):
        return True
    return False


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


def should_escalate(answer: str, confidence: float, threshold: float = 0.4,
                    customer_text: str = "") -> bool:
    if not answer or not answer.strip():
        return True
    if _NO_ANSWER.search(answer):
        return True
    if customer_text and customer_seems_frustrated(customer_text):
        return True
    return confidence is not None and confidence < threshold


def find_learned_answer(tenant_id: str, question: str) -> Optional[str]:
    """Best stored answer for a similar past question (token-overlap match)."""
    try:
        rows = (_client().table("vula_learned_answers").select("question,answer")
                .eq("tenant_id", tenant_id).order("created_at", desc=True)
                .limit(200).execute().data or [])
    except Exception as exc:
        log.debug("learned-answer lookup skipped (run migration 042?): %s", exc)
        return None
    qt = _tokens(question)
    if not qt:
        return None
    best, best_score = None, 0.0
    for r in rows:
        lt = _tokens(r.get("question", ""))
        if not lt:
            continue
        score = len(qt & lt) / len(qt | lt)   # Jaccard
        if score > best_score:
            best, best_score = r.get("answer"), score
    return best if best_score >= 0.5 else None


def _pick_helper(tenant_id: str) -> Optional[dict]:
    try:
        rows = (_client().table("vula_team_members").select("name,whatsapp,role,notify,active")
                .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])
    except Exception:
        rows = []
    helpers = [r for r in rows if (r.get("whatsapp") or "").strip()
               and "help_request" in (r.get("notify") or [])]
    if not helpers:
        helpers = [r for r in rows if (r.get("whatsapp") or "").strip()
                   and r.get("role") in ("owner", "manager")]
    return helpers[0] if helpers else None


def open_escalation_for_helper(helper_phone: str) -> Optional[dict]:
    """The oldest FRESH open escalation assigned to this helper (their next text is the answer).

    Escalations older than 48h are expired on sight instead of returned — confirmed live
    2026-07-16: a 2-week-old open escalation swallowed the helper's unrelated "Hi" (sent for a
    completely different reason), relayed it to the customer as "the answer", and stored it as
    a learned answer. A helper who hasn't replied within 2 days is not going to — the customer
    conversation has long moved on."""
    from datetime import timedelta
    digits = re.sub(r"\D", "", helper_phone or "")
    if not digits:
        return None
    try:
        rows = (_client().table("vula_escalations").select("*")
                .eq("helper_phone", digits).eq("status", "open")
                .order("created_at", desc=False).limit(5).execute().data or [])
    except Exception:
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    fresh = []
    for r in rows:
        if (r.get("created_at") or "") < cutoff:
            try:
                _client().table("vula_escalations").update(
                    {"status": "expired"}).eq("id", r["id"]).eq("status", "open").execute()
            except Exception:
                pass
        else:
            fresh.append(r)
    return fresh[0] if fresh else None


def create_escalation(tenant_id: str, customer_phone: str, question: str) -> Optional[dict]:
    """Record an escalation and pick a helper. Returns the row (+ helper) or None if no helper.

    Dedup (2026-07-17): one OPEN escalation per customer at a time — the ask_team tool AND the
    phrase-based detector both fired on the same message, creating two escalations and pinging
    the helper twice. If the customer already has an open one, return None (no new row, no
    second ping) — the helper's single reply resolves it."""
    digits = re.sub(r"\D", "", customer_phone or "")
    try:
        existing = (_client().table("vula_escalations").select("id")
                    .eq("tenant_id", tenant_id).eq("customer_phone", digits)
                    .eq("status", "open").limit(1).execute().data or [])
        if existing:
            log.info("escalation dedup: %s already has an open escalation", digits)
            return None
    except Exception:
        pass
    helper = _pick_helper(tenant_id)
    if not helper:
        return None
    import uuid
    helper_digits = re.sub(r"\D", "", helper["whatsapp"])
    row = {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id,
        "customer_phone": re.sub(r"\D", "", customer_phone or ""), "question": question,
        "status": "open", "helper_phone": helper_digits, "helper_name": helper.get("name"),
        "created_at": _now(),
    }
    try:
        _client().table("vula_escalations").insert(row).execute()
    except Exception as exc:
        log.warning("escalation insert failed (run migration 042?): %s", exc)
        return None
    return row


def find_abandoned_escalations(tenant_id: str, hours: float = 48.0) -> list[dict]:
    """Open escalations past the give-up window that the CUSTOMER was never told about.

    2026-09-01, ahead of off-the-hook going live: the helper-side of this loop works — every
    escalation since the nudge shipped (2026-07-28) was chased, confirmed against real data.
    The customer side was never closed. A real off-the-hook customer asked on 2026-08-25
    whether they could collect 100kg of hake instead of having it delivered; Staci was nudged
    the next day, never answered, and the customer has heard nothing since. Silence is the
    worst possible answer to a real buying question — worse than "I couldn't find out".

    Uses answered_at IS NULL as the "customer never heard back" test, so a question that WAS
    answered can never be apologised for a second time.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        return (_client().table("vula_escalations").select("*")
                .eq("tenant_id", tenant_id).in_("status", ["open", "expired"])
                .lt("created_at", cutoff)
                .is_("answered_at", "null")
                .is_("customer_notified_at", "null")
                .limit(50).execute().data or [])
    except Exception as exc:
        log.debug("abandoned escalation lookup skipped (run migration 149?): %s", exc)
        return []


def mark_customer_notified(escalation_id: str) -> None:
    """Stamped only after the apology actually reaches the customer, so a send failure means
    we try again next tick rather than silently dropping them."""
    try:
        _client().table("vula_escalations").update(
            {"customer_notified_at": _now(), "status": "expired"}
        ).eq("id", escalation_id).execute()
    except Exception as exc:
        log.debug("customer-notified stamp failed for %s: %s", escalation_id, exc)


def find_stale_open_escalations(tenant_id: str, hours: float = 6.0) -> list[dict]:
    """This tenant's open escalations older than `hours` that haven't been nudged yet —
    part of generalizing proactive re-engagement past commerce-only tenants (2026-07-28).
    Excludes rows already past the 48h auto-expire window: open_escalation_for_helper
    retires those on sight instead, so the two paths never double-handle the same row."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat()
    expiry_cutoff = (now - timedelta(hours=48)).isoformat()
    try:
        rows = (_client().table("vula_escalations").select("*")
                .eq("tenant_id", tenant_id).eq("status", "open")
                .lt("created_at", cutoff).gte("created_at", expiry_cutoff)
                .is_("stale_notified_at", "null")
                .limit(50).execute().data or [])
    except Exception as exc:
        log.debug("stale escalation lookup skipped (run migration 111?): %s", exc)
        return []
    return rows


def mark_stale_notified(escalation_id: str) -> None:
    try:
        _client().table("vula_escalations").update(
            {"stale_notified_at": _now()}).eq("id", escalation_id).execute()
    except Exception as exc:
        log.debug("stale-notified stamp failed for %s: %s", escalation_id, exc)


def answer_escalation(escalation: dict, answer: str) -> Optional[dict]:
    """Mark answered, store the learned answer, return who to reply to.

    The status update is a CONDITIONAL claim (only wins while still 'open') — returns None if
    another worker/webhook-retry already answered it. Confirmed live 2026-07-16: Meta redelivered
    the helper's message after a container restart wiped the in-memory msg-id dedup, and both
    deliveries answered the same escalation → the customer got the relay twice."""
    db = _client()
    try:
        claim = (db.table("vula_escalations").update(
            {"status": "answered", "answer": answer, "answered_at": _now()}
        ).eq("id", escalation["id"]).eq("status", "open").execute())
        if not claim.data:
            return None  # someone else already answered it — don't relay again
    except Exception as exc:
        log.warning("escalation update failed: %s", exc)
        return None
    try:
        import uuid
        db.table("vula_learned_answers").insert({
            "id": str(uuid.uuid4()), "tenant_id": escalation["tenant_id"],
            "question": escalation["question"], "answer": answer,
            "source": "escalation", "created_at": _now(),
        }).execute()
    except Exception as exc:
        log.debug("learned-answer store skipped: %s", exc)
    return {
        "tenant_id": escalation["tenant_id"],
        "customer_phone": escalation["customer_phone"],
        "question": escalation["question"],
    }

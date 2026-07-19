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


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


def should_escalate(answer: str, confidence: float, threshold: float = 0.4) -> bool:
    if not answer or not answer.strip():
        return True
    if _NO_ANSWER.search(answer):
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

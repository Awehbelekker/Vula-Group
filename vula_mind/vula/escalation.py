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
# 2026-09-11: added isiZulu/isiXhosa/Sesotho for the same reason — the persona prompt promises
# these languages (commerce_assistant.py's conversation rules), but this gate was silently
# English/Afrikaans-only, so a confidently-wrong reply to an isiZulu/isiXhosa/Sesotho speaker
# never escalated. Deliberately a SHORT, high-confidence starter list (not exhaustive) — these
# are basic, unambiguous first-person phrases, not idiomatic translations, chosen to minimise
# the risk of a wrong/awkward phrase either misfiring or missing real cases; worth a native-
# speaker review to extend, same as the brief that raised this recommends.
_NO_ANSWER = re.compile(
    r"(i (don'?t|do not) (know|have)|not sure|couldn'?t find|can'?t help|no (info|information|record)|unable to|i'?m not able"
    r"|ek (weet|het) nie|nie seker nie|kan nie help nie|ek sal (met die span|eers) (kyk|vra)|laat ek uitvind"
    r"|angazi|angikwazi ukusiza|ngizobuza (ithimba|abantu)"                    # isiZulu
    r"|andazi|andikwazi ukunceda|ndiza kubuza (iqela|abantu)"                 # isiXhosa
    r"|ha ke tsebe|nka se o thuse|ke tla botsa (sehlopha|batho))",             # Sesotho
    re.IGNORECASE,
)

# Explicit complaint/frustration words (English + Afrikaans) — a signal about the CUSTOMER's
# message, independent of how confident the bot's own reply looked. Before this, staff only
# got pulled in when the bot doubted itself; an upset customer getting a calm, confidently
# wrong or unhelpful reply never escalated at all.
# A helper's reply that is an INSTRUCTION TO VULA, not an answer to the customer. Real incident
# (2026-09-01): Staci replied "Respond to Richard Downing via WhatsApp business and say delivery
# will be on Monday between 10:00 - 12:00" to a delivery question. That was her telling Vula what
# to do — it got stored verbatim as the canonical customer-facing answer, naming a real customer.
# Same class of bug as the bare-greeting and helper-counter-question guards already in
# vula/api/whatsapp.py; this is the third variant.
_INSTRUCTION_TO_VULA = re.compile(
    r"^\s*(please\s+)?(respond|reply|answer|tell|let|inform|send|forward|advise|ask)\b"
    r"[^.?!]{0,60}\b(him|her|them|the customer|the client|to\s+\w+)\b"
    r"|^\s*(please\s+)?(say|state|mention|confirm)\s+(that|to)\b"
    r"|\b(sê vir|antwoord vir|laat weet)\b",
    re.IGNORECASE,
)


def reply_is_instruction_to_vula(text: str) -> bool:
    """True when the helper is directing Vula rather than answering the customer.

    Such a reply is still RELAYED (the helper did intend the customer to hear something) but is
    never LEARNED — storing a directive as the canonical answer is what produced the worst of the
    two bad rows found in production.
    """
    return bool(_INSTRUCTION_TO_VULA.search((text or "").strip()))


# isiZulu/isiXhosa/Sesotho additions, 2026-09-11 — same starter-list caveat as _NO_ANSWER
# above: a short, high-confidence list (anger/complaint being the common thread), not a full
# idiomatic translation of the English/Afrikaans list. Extend with a native speaker's review.
_FRUSTRATION_WORDS = re.compile(
    r"(ridiculous|terrible|useless|pathetic|waste of (my )?(time|money)|not happy|"
    r"unacceptable|disgusted|disgusting|(this is |so )?annoying|angry|furious|fed up|"
    r"sick of|worst (service|experience)|scam|rip.?off|shocking service|"
    r"belaglik|omgekrap|woedend|totale mors|swak diens|verskriklike diens|"
    r"ngicasukile|ngikhathele yi|umsebenzi ombi|"                             # isiZulu
    r"ndicaphukile|ndidiniwe|inkonzo embi|"                                   # isiXhosa
    r"ke halefile|ke tenegile|tshebeletso e mpe)",                            # Sesotho
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


def _redact_contacts(text: str) -> str:
    """A stored answer is replayed to OTHER customers, so it must never carry the contact
    details of the one who prompted it. Reuses vula/commerce/voice_profile.py's own redaction
    (2026-09-08: this used to be a verbatim copy of the same two regexes — a future fix to the
    pattern applied to one copy alone would leave the other redacting with the stale, possibly
    still-leaky pattern)."""
    from vula.commerce.voice_profile import _redact
    return _redact(text or "")


def approve_learned_answer(learned_id: str, approved_by: str = "") -> bool:
    """Owner tapped Keep — this answer may now be reused for similar questions."""
    try:
        _client().table("vula_learned_answers").update({
            "status": "approved", "approved_at": _now(), "approved_by": approved_by or None,
        }).eq("id", learned_id).execute()
        return True
    except Exception as exc:
        log.warning("learned-answer approve failed for %s: %s", learned_id, exc)
        return False


def reject_learned_answer(learned_id: str) -> bool:
    """Owner tapped Bin — never reuse this one."""
    try:
        _client().table("vula_learned_answers").update(
            {"status": "rejected"}).eq("id", learned_id).execute()
        return True
    except Exception as exc:
        log.warning("learned-answer reject failed for %s: %s", learned_id, exc)
        return False


def get_learned_answer(learned_id: str) -> Optional[dict]:
    try:
        rows = (_client().table("vula_learned_answers").select("*")
                .eq("id", learned_id).limit(1).execute().data or [])
        return rows[0] if rows else None
    except Exception:
        return None


def should_escalate(answer: str, confidence: float, threshold: float = 0.4,
                    customer_text: str = "") -> bool:
    if not answer or not answer.strip():
        return True
    if _NO_ANSWER.search(answer):
        return True
    if customer_text and customer_seems_frustrated(customer_text):
        return True
    return confidence is not None and confidence < threshold


# Words that carry no distinguishing meaning — a difference in these is fine, a difference in
# anything else (a place, a product, a quantity) means it is a DIFFERENT question.
_COMMON_WORDS = {
    "the", "and", "for", "you", "your", "yours", "our", "ours", "are", "can", "could", "would",
    "will", "does", "did", "have", "has", "had", "was", "were", "with", "what", "whats", "when",
    "where", "which", "who", "why", "how", "there", "they", "them", "this", "that", "these",
    "those", "from", "about", "any", "all", "get", "got", "please", "thanks", "thank", "hello",
    "hi", "still", "just", "some", "much", "many", "make", "made", "take", "want", "need",
    "order", "orders", "buy", "know", "tell", "say", "not", "but", "yes", "day", "days",
}

# The real protection is _distinguishing_tokens below, not this number: two questions can only
# reach the threshold at all if they differ ONLY in common words. Raising it further starts
# rejecting genuine paraphrase ("what is in the box" vs "whats in the box") without adding safety.
MATCH_THRESHOLD = 0.6


def _distinguishing_tokens(a: set, b: set) -> set:
    """Tokens present in exactly one question that actually change its meaning.

    2026-09-01, real incident: "do you deliver to Milnerton" matched the stored answer for
    "do you deliver to Timbuktu" — Jaccard 3/5 = 0.6, comfortably over the old 0.5 bar. The ONLY
    differing token was the place name, i.e. the single word that decides the answer. A customer
    in Milnerton would have been sent internal instructions naming a different customer.

    Numbers and any non-trivial word outside the common-word list count as distinguishing, so a
    difference in place, product or quantity blocks the match outright regardless of score. This
    is deliberately deterministic — the same lesson as unverified_prices() in core/skills/base.py:
    on this platform the structural check is what holds, not a tuned threshold.
    """
    return {t for t in (a ^ b) if t not in _COMMON_WORDS and (len(t) > 3 or t.isdigit())}


# 2026-09-15 (Tenant Mind Phase 2): the Qdrant doc_id prefix used to index an approved
# answer's question for semantic search — see embed_learned_answer / _find_learned_answer_semantic.
_QDRANT_DOC_PREFIX = "learned_answer_"
_SEMANTIC_SCORE_THRESHOLD = 0.55  # not load-bearing on its own — see the docstring below


async def embed_learned_answer(tenant_id: str, learned_id: str, question: str) -> None:
    """Index an approved answer's QUESTION (not the answer text — matching on question
    similarity, not letting a similarly-worded answer distort the match) for semantic
    retrieval. Call once, right after approve_learned_answer() succeeds. Best-effort and fully
    decoupled from the approval itself: the approval already landed in Postgres (the source of
    truth) by the time this runs, an indexing failure here must never undo it, and
    find_learned_answer() always falls back to the plain keyword search below regardless."""
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        doc_id = _QDRANT_DOC_PREFIX + learned_id
        await pipeline.ingest_text(
            content=question, filename=f"{doc_id}.txt", doc_id=doc_id,
            source_type="learned_answer_approved",
        )
    except Exception as exc:
        log.debug("learned-answer embed skipped for %s: %s", learned_id, exc)


async def _find_learned_answer_semantic(tenant_id: str, question: str, qt: set) -> Optional[str]:
    """Semantic candidates via Qdrant — finds real paraphrases the plain word-overlap match
    below completely misses (e.g. "what's your policy on late deliveries" vs "if my order
    arrives late what happens"). Every candidate still goes through the EXACT SAME
    _distinguishing_tokens guard as the keyword path before it can ever be returned: semantic
    closeness is not a safety property by itself — the guard is what actually prevented the
    2026-09-01 "Milnerton answered with a Timbuktu answer" incident, and a smarter retrieval
    method doesn't make that check any less necessary. The similarity threshold below is
    deliberately not fine-tuned for the same reason: getting it roughly right is enough,
    because the guard — not the score — is the real backstop.

    Returns None (never an empty-result marker vs. a failure marker — same value either way) on
    any infrastructure failure so the caller falls back to keyword search rather than concluding
    there's genuinely no learned answer."""
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        query_embedding = await pipeline.embedder.embed(question)
        hits = await pipeline.store.search(
            tenant_id, query_embedding, limit=5, score_threshold=_SEMANTIC_SCORE_THRESHOLD,
            source_type="learned_answer_approved",
        )
    except Exception as exc:
        log.debug("semantic learned-answer search skipped for %s: %s", tenant_id, exc)
        return None

    for hit in hits:
        doc_id = hit.get("doc_id") or ""
        if not doc_id.startswith(_QDRANT_DOC_PREFIX):
            continue
        learned_id = doc_id[len(_QDRANT_DOC_PREFIX):]
        # Always re-read the real row from Postgres — never trust Qdrant's cached payload for
        # status or answer text. Same "source of truth" discipline as everywhere else on this
        # platform: Qdrant is an index into Postgres, never a second copy of the fact itself.
        row = get_learned_answer(learned_id)
        if not row or row.get("tenant_id") != tenant_id or row.get("status") != "approved":
            continue
        lt = _tokens(row.get("question", ""))
        if not lt or _distinguishing_tokens(qt, lt):
            continue  # different place/product/quantity — not the same question
        return row.get("answer")
    return None


def _find_learned_answer_keyword(tenant_id: str, question: str, qt: set) -> Optional[str]:
    """The original plain word-overlap match — kept as-is as the fallback for a tenant with no
    embedded answers yet, or when Qdrant/embedding is unavailable, so the mechanism degrades
    gracefully instead of going fully dark."""
    try:
        q = (_client().table("vula_learned_answers").select("question,answer,status")
             .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(200))
        try:
            rows = q.eq("status", "approved").execute().data or []
        except Exception:
            # Before migration 150 there is no status column. Fail CLOSED: serving unreviewed
            # answers is exactly the defect this guard exists to stop.
            log.debug("learned-answer status filter unavailable (run migration 150?)")
            return None
    except Exception as exc:
        log.debug("learned-answer lookup skipped (run migration 042?): %s", exc)
        return None
    best, best_score = None, 0.0
    for r in rows:
        lt = _tokens(r.get("question", ""))
        if not lt:
            continue
        if _distinguishing_tokens(qt, lt):
            continue  # different place/product/quantity — not the same question
        score = len(qt & lt) / len(qt | lt)   # Jaccard
        if score > best_score:
            best, best_score = r.get("answer"), score
    return best if best_score >= MATCH_THRESHOLD else None


async def find_learned_answer(tenant_id: str, question: str) -> Optional[str]:
    """Best stored answer for a similar past question.

    Only APPROVED answers are ever returned (migration 150): both learned answers that existed
    in production on 2026-09-01 were wrong — one was the helper replying about something else
    entirely, the other was the helper instructing Vula rather than answering the customer — so
    an unreviewed answer must never reach a customer.

    2026-09-15 (Tenant Mind Phase 2): tries semantic search first (finds real paraphrases the
    old plain word-overlap match missed entirely), falling back to the original keyword match
    if semantic search finds nothing or isn't available. See _find_learned_answer_semantic's
    docstring for why the distinguishing-token safety guard applies identically either way.
    """
    qt = _tokens(question)
    if not qt:
        return None
    semantic = await _find_learned_answer_semantic(tenant_id, question, qt)
    if semantic is not None:
        return semantic
    return _find_learned_answer_keyword(tenant_id, question, qt)


def _pick_helper(tenant_id: str, exclude_phone: str = "") -> Optional[dict]:
    """Pick a human helper to relay the question to.

    Excludes `exclude_phone` (the asker) from the candidate pool — confirmed live
    2026-09-17: DIGG's owner Judy is also its only registered team member with an
    owner/manager role, so an admin question from her own number picked *her* as the
    helper, sent her "let me check with the team", then pinged her own WhatsApp asking
    her to answer her own question. A tenant with no OTHER helper simply gets no
    escalation (falls back to the agent's own reply) rather than this self-loop.
    """
    try:
        rows = (_client().table("vula_team_members").select("name,whatsapp,role,notify,active")
                .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])
    except Exception:
        rows = []
    exclude_digits = re.sub(r"\D", "", exclude_phone or "")
    if exclude_digits:
        rows = [r for r in rows if re.sub(r"\D", "", r.get("whatsapp") or "") != exclude_digits]
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
    helper = _pick_helper(tenant_id, exclude_phone=customer_phone)
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
    # Relay always happens (below) — but LEARNING is gated. Both learned answers that existed in
    # production on 2026-09-01 were wrong, and both would have been caught here.
    learned_id = None
    if reply_is_instruction_to_vula(answer):
        log.info("not learning escalation %s — helper reply is an instruction, not an answer",
                 escalation.get("id"))
    else:
        try:
            import uuid
            learned_id = str(uuid.uuid4())
            db.table("vula_learned_answers").insert({
                "id": learned_id, "tenant_id": escalation["tenant_id"],
                "question": escalation["question"], "answer": _redact_contacts(answer),
                "source": "escalation", "status": "pending", "created_at": _now(),
            }).execute()
        except Exception as exc:
            learned_id = None
            log.debug("learned-answer store skipped: %s", exc)
    return {
        "tenant_id": escalation["tenant_id"],
        "customer_phone": escalation["customer_phone"],
        "question": escalation["question"],
        "learned_id": learned_id,
    }


def capture_owner_correction(tenant_id: str, question: str, correction: str) -> Optional[str]:
    """An admin corrected or supplied the real answer to something Vula got wrong or wasn't
    sure about (see core.verification.is_uncertain_reply — vula/api/whatsapp.py's
    _maybe_capture_owner_correction is the caller/detector). Stores a new PENDING
    vula_learned_answers row keyed to the ORIGINAL question, source='owner_correction' — same
    review gate as an escalation answer (Keep/Bin on WhatsApp via _handle_learn_review_reply,
    which needs zero changes since it keys purely on learned_id regardless of source).

    Unlike answer_escalation(), this never touches vula_escalations — no escalation ticket
    exists for this case; the owner corrected Vula unprompted, not in reply to a helper ping.

    2026-09-17: real DIGG incident — "can I colour a cast iron fireplace?" got a confidently
    wrong answer, and the owner's follow-up correction (real SA paint brands she'd researched
    herself) was relayed back once and then forgotten. Nothing captured it, so the same wrong
    answer would ship again to the next person who asks something similar.

    Returns the new learned_id, or None if skipped: the correction reads as an instruction
    rather than a factual answer (same guard answer_escalation() uses), a pending row for the
    same question already exists (two rapid corrections / a webhook retry), or the insert
    failed."""
    if reply_is_instruction_to_vula(correction):
        log.info("not capturing owner correction for %s — reply reads as an instruction, not "
                 "an answer", tenant_id)
        return None
    db = _client()
    try:
        existing = (db.table("vula_learned_answers").select("id")
                    .eq("tenant_id", tenant_id).eq("question", question)
                    .eq("status", "pending").limit(1).execute().data or [])
        if existing:
            return None
    except Exception:
        pass  # dedup is best-effort; fall through to insert rather than lose a real correction
    try:
        import uuid
        learned_id = str(uuid.uuid4())
        db.table("vula_learned_answers").insert({
            "id": learned_id, "tenant_id": tenant_id, "question": question,
            "answer": _redact_contacts(correction),
            "source": "owner_correction", "status": "pending", "created_at": _now(),
        }).execute()
        return learned_id
    except Exception as exc:
        log.debug("owner-correction learned-answer store skipped: %s", exc)
        return None


def queue_research_candidate(tenant_id: str, question: str, answer: str) -> Optional[str]:
    """Vula's own web-research synthesis (core/skills/reasoning.py's web-search fallback),
    NOT tenant-originated, queued as a PENDING vula_learned_answers row with
    source='web_research' — same table/review gate as owner corrections and escalation
    answers, but reached only after core.verification.apply()'s two-signal accuracy gate
    (adversarial verdict == 'pass' AND the web result's own confidence >= 0.7) already cleared
    it. Kept as a distinct source label so the master-admin promotion queue
    (vula/api/master.py) shows a curator which kind of row they're looking at — this one is
    Vula's own content and carries no tenant-specific risk, unlike an owner correction.

    Unlike answer_escalation()/capture_owner_correction(), there's no human "reply" here to
    misread as an instruction to Vula, so no reply_is_instruction_to_vula guard is needed.
    """
    db = _client()
    try:
        existing = (db.table("vula_learned_answers").select("id")
                    .eq("tenant_id", tenant_id).eq("question", question)
                    .eq("status", "pending").limit(1).execute().data or [])
        if existing:
            return None
    except Exception:
        pass  # dedup is best-effort; fall through to insert rather than lose a real candidate
    try:
        import uuid
        learned_id = str(uuid.uuid4())
        db.table("vula_learned_answers").insert({
            "id": learned_id, "tenant_id": tenant_id, "question": question,
            "answer": _redact_contacts(answer),
            "source": "web_research", "status": "pending", "created_at": _now(),
        }).execute()
        return learned_id
    except Exception as exc:
        log.debug("research-candidate learned-answer store skipped: %s", exc)
        return None

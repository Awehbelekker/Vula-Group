"""How well Vula is answering, as four numbers an owner can read.

There was no live measure of answer quality: the evals run on fixtures, the checker's verdicts
are its own, and nothing told an owner whether Vula's answers this week rested on their records
or were guesses. This counts, from the turn record (vula/turns.py) and the feedback tables, for
one business over the last N days:

  backed     answers that rested on the business's own documents, a tool or the database
  unsure     answers Vula itself marked low-confidence or worded as "I couldn't / don't have"
  corrected  replies someone marked wrong (a 👎 in the inbox or chat, or "that's wrong")
  handed_on  questions passed to a person

Counts only. No message, reply or question text is read into the result, so a staff member's
own thread with Vula is counted without anything they wrote being shown to anyone.

`backed` is known only for answers recorded since grounding was added to the turn record; older
turns are left out of that rate rather than counted as unbacked.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BACKED = ("records", "kb")
_UNSURE_CONFIDENCE = 0.4


def _client():
    from vula.commerce import service
    return service._client()


def _pct(part: int, whole: int) -> Optional[int]:
    return round(100 * part / whole) if whole else None


def _count(table: str, tenant_id: str, column: str, since: str, **eq) -> Optional[int]:
    try:
        q = _client().table(table).select("id").eq("tenant_id", tenant_id).gte(column, since)
        for k, v in eq.items():
            q = q.eq(k, v)
        return len(q.limit(5000).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — a table this database doesn't have yet
        logger.debug("quality: %s skipped: %s", table, exc)
        return None


def summarise(turns: List[Dict[str, Any]]) -> Dict[str, int]:
    """Counts from turn rows (steps + replies). Pure, so it can be tested without a database."""
    from vula.conversation_check import _CANT_RE
    out = {"answers": 0, "with_grounding_known": 0, "backed": 0, "unsure": 0}
    for t in turns:
        skill_steps = [s for s in (t.get("steps") or []) if isinstance(s, dict) and s.get("step") == "skill"
                       and not s.get("error")]
        if not skill_steps:
            continue                       # a button tap, a template, a handler with no answer
        last = skill_steps[-1]
        out["answers"] += 1
        if last.get("grounding"):
            out["with_grounding_known"] += 1
            if last["grounding"] in _BACKED:
                out["backed"] += 1
        low = isinstance(last.get("confidence"), (int, float)) and last["confidence"] < _UNSURE_CONFIDENCE
        said_so = any(isinstance(r, dict) and _CANT_RE.search(str(r.get("text") or ""))
                      for r in (t.get("replies") or []))
        if low or said_so:
            out["unsure"] += 1
    return out


def weekly(tenant_id: str, days: int = 7, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The four rates for this business. Every figure is a count or a whole-number percentage;
    a part that couldn't be read is None, never 0."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    try:
        rows = (_client().table("vula_turns").select("steps,replies")
                .eq("tenant_id", tenant_id).gte("started_at", since)
                .order("started_at", desc=True).limit(3000).execute().data or [])
        counts = summarise([r for r in rows if isinstance(r, dict)])
    except Exception as exc:  # noqa: BLE001
        logger.debug("quality: turns skipped (run migration 195?): %s", exc)
        counts = None

    wrong_ratings = _count("vula_reply_feedback", tenant_id, "created_at", since, rating="down")
    wrong_said = _count("vula_kb_turns", tenant_id, "feedback_at", since, feedback="wrong")
    corrected = None if wrong_ratings is None and wrong_said is None else (wrong_ratings or 0) + (wrong_said or 0)
    handed_on = _count("vula_escalations", tenant_id, "created_at", since)

    answers = counts["answers"] if counts else None
    return {
        "days": days,
        "answers": answers,
        "backed": {"count": counts["backed"], "of": counts["with_grounding_known"],
                   "pct": _pct(counts["backed"], counts["with_grounding_known"])} if counts else None,
        "unsure": {"count": counts["unsure"], "pct": _pct(counts["unsure"], answers)} if counts else None,
        "corrected": {"count": corrected, "pct": _pct(corrected, answers) if answers else None}
        if corrected is not None else None,
        "handed_on": {"count": handed_on} if handed_on is not None else None,
    }

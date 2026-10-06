"""
vula/open_questions.py — the questions Vula is waiting on, per person (migration 194).

Every question Vula asks someone on WhatsApp that a short reply can answer ("Approve", "yes",
"Atlantis") is recorded here with what it is about. A reply is matched to that person's NEWEST
open question first; the old handlers that each guessed "is this short message mine?" only run
when there is no open question (or the table isn't there yet — everything here fails open).

2026-10-05/06 (digg-demo) is why: "Approve" to the STE Scaffolding supplier question approved a
June test invoice, and "Atlantis Paarden Eiland" — the answer to the project question about a
proof of payment just sent — was applied to an expense claim from 28 August.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# How long an unanswered question can still claim a reply.
TTL = {
    "approval": timedelta(days=14),
    "doc_project": timedelta(days=3),
    "pop_match": timedelta(days=3),
}
DEFAULT_TTL = timedelta(days=3)


def _client():
    from vula.commerce import service
    return service._client()


def _digits(phone: str) -> str:
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    return "27" + d[1:] if d.startswith("0") and len(d) == 10 else d


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ask(tenant_id: str, phone: str, kind: str, ref_id: str, prompt: str = "") -> Optional[str]:
    """Record that `phone` was just asked a `kind` question about `ref_id`. Returns its id, or
    None when it couldn't be stored (callers carry on — the old matching still works)."""
    if not (tenant_id and phone and ref_id):
        return None
    try:
        now = _now()
        row = {"tenant_id": tenant_id, "phone": _digits(phone), "kind": kind, "ref_id": str(ref_id),
               "prompt": (prompt or "")[:200], "status": "open", "asked_at": now.isoformat(),
               "expires_at": (now + TTL.get(kind, DEFAULT_TTL)).isoformat()}
        data = _client().table("vula_open_questions").insert(row).execute().data or []
        return data[0].get("id") if data else None
    except Exception as exc:
        logger.debug("open question not recorded (run migration 194?): %s", exc)
        return None


def current(tenant_id: str, phone: str) -> Optional[dict]:
    """The newest open, unexpired question for this person, or None."""
    try:
        rows = (_client().table("vula_open_questions").select("*")
                .eq("tenant_id", tenant_id).eq("phone", _digits(phone)).eq("status", "open")
                .gte("expires_at", _now().isoformat())
                .order("asked_at", desc=True).limit(1).execute().data or [])
        return rows[0] if rows else None
    except Exception as exc:
        logger.debug("open question lookup skipped: %s", exc)
        return None


def open_for(tenant_id: str, phone: str, limit: int = 5) -> list:
    """This person's open, unexpired questions, newest first."""
    try:
        return (_client().table("vula_open_questions").select("*")
                .eq("tenant_id", tenant_id).eq("phone", _digits(phone)).eq("status", "open")
                .gte("expires_at", _now().isoformat())
                .order("asked_at", desc=True).limit(limit).execute().data or [])
    except Exception as exc:
        logger.debug("open questions lookup skipped: %s", exc)
        return []


def close_mine(tenant_id: str, phone: str, ref_id: str, answer: str = "") -> None:
    """This person answered about `ref_id` (by whatever path) — their question about it is done."""
    try:
        (_client().table("vula_open_questions")
         .update({"status": "answered", "answer": (answer or "")[:200], "answered_at": _now().isoformat()})
         .eq("tenant_id", tenant_id).eq("phone", _digits(phone)).eq("ref_id", str(ref_id))
         .eq("status", "open").execute())
    except Exception as exc:
        logger.debug("open question close_mine skipped: %s", exc)


def close(question_id: str, answer: str = "", status: str = "answered") -> None:
    try:
        (_client().table("vula_open_questions")
         .update({"status": status, "answer": (answer or "")[:200], "answered_at": _now().isoformat()})
         .eq("id", question_id).execute())
    except Exception as exc:
        logger.debug("open question close skipped: %s", exc)


def close_for(tenant_id: str, ref_id: str, status: str = "closed") -> None:
    """The thing a question was about got settled some other way (the dashboard, another
    approver, the old reply path) — nobody should be asked about it any more."""
    if not ref_id:
        return
    try:
        (_client().table("vula_open_questions")
         .update({"status": status, "answered_at": _now().isoformat()})
         .eq("tenant_id", tenant_id).eq("ref_id", str(ref_id)).eq("status", "open").execute())
    except Exception as exc:
        logger.debug("open question close_for skipped: %s", exc)

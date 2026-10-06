"""
vula/open_questions.py — the questions Vula is waiting on, per person (migration 194).

Every question Vula asks someone on WhatsApp that a short reply can answer ("Approve", "yes",
"Atlantis") is recorded here with what it is about. A reply is matched to that person's NEWEST
open question first; the old handlers that each guessed "is this short message mine?" only run
when there is no open question (or the table isn't there yet — everything here fails open).

2026-10-05/06 (digg-demo) is why: "Approve" to the STE Scaffolding supplier question approved a
June test invoice, and "Atlantis Paarden Eiland" — the answer to the project question about a
proof of payment just sent — was applied to an expense claim from 28 August.

Questions nobody prompted (a document that came in by email, an approval someone else asked
for) don't interrupt: while the person has a question under QUIET_FOR old still open, the new one
is queued (migration 197) and sent when they answer it, or after QUIET_FOR without an answer.
Questions that reply to something the person just sent use `ask` and go straight out.
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
# An open question this fresh means the person is still on it — don't stack another on top.
QUIET_FOR = timedelta(hours=2)


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


def _busy(tenant_id: str, phone: str) -> bool:
    """True while this person has an open question asked less than QUIET_FOR ago."""
    since = (_now() - QUIET_FOR).isoformat()
    return any((q.get("asked_at") or "") >= since for q in open_for(tenant_id, phone))


async def ask_or_queue(tenant_id: str, phone: str, kind: str, ref_id: str, prompt: str,
                       message: str) -> str:
    """Send `message` (a question nobody prompted) now if the person is free, otherwise queue it.
    Returns "sent", "queued" or "failed"."""
    from vula.api.whatsapp import _send_reply
    if tenant_id and phone and ref_id and _busy(tenant_id, phone):
        try:
            now = _now()
            row = {"tenant_id": tenant_id, "phone": _digits(phone), "kind": kind,
                   "ref_id": str(ref_id), "prompt": (prompt or "")[:200], "message": message,
                   "status": "queued", "asked_at": now.isoformat(),
                   "expires_at": (now + TTL.get(kind, DEFAULT_TTL)).isoformat()}
            _client().table("vula_open_questions").insert(row).execute()
            return "queued"
        except Exception as exc:   # no column/table yet — ask now, as before
            logger.debug("question not queued (run migration 197?): %s", exc)
    if await _send_reply(phone, message, tenant_id=tenant_id) is False:
        return "failed"
    ask(tenant_id, phone, kind, ref_id, prompt)
    return "sent"


def _queued(tenant_id: str, phone: str) -> list:
    try:
        return (_client().table("vula_open_questions").select("*")
                .eq("tenant_id", tenant_id).eq("phone", _digits(phone)).eq("status", "queued")
                .gte("expires_at", _now().isoformat())
                .order("asked_at", desc=False).limit(20).execute().data or [])
    except Exception as exc:
        logger.debug("queued questions lookup skipped: %s", exc)
        return []


async def release_next(tenant_id: str, phone: str) -> bool:
    """Send this person's oldest queued question, if they're free. True when one went out."""
    if _busy(tenant_id, phone):
        return False
    waiting = _queued(tenant_id, phone)
    if not waiting:
        return False
    q, more = waiting[0], len(waiting) - 1
    msg = q.get("message") or q.get("prompt") or ""
    if more:
        msg += f"\n\n_({more} more question{'s' if more != 1 else ''} after this one.)_"
    from vula.api.whatsapp import _send_reply
    if await _send_reply(phone, msg, tenant_id=tenant_id) is False:
        return False
    now = _now()
    try:
        (_client().table("vula_open_questions")
         .update({"status": "open", "asked_at": now.isoformat(),
                  "expires_at": (now + TTL.get(q.get("kind"), DEFAULT_TTL)).isoformat()})
         .eq("id", q["id"]).execute())
    except Exception as exc:
        logger.debug("released question not reopened: %s", exc)
    if q.get("kind") == "doc_project":
        try:
            from vula.integrations.doc_filing import mark_asked
            mark_asked(tenant_id, {"id": q.get("ref_id"), "fields": _doc_fields(tenant_id, q)}, phone)
        except Exception as exc:
            logger.debug("mark_asked on release skipped: %s", exc)
    return True


def _doc_fields(tenant_id: str, q: dict) -> dict:
    rows = (_client().table("vula_filed_documents").select("fields")
            .eq("tenant_id", tenant_id).eq("id", q.get("ref_id")).limit(1).execute().data or [])
    return (rows[0].get("fields") or {}) if rows else {}


async def release_due() -> int:
    """Background sweep: anyone with queued questions whose open one was answered some other way
    (the dashboard) or has gone QUIET_FOR without an answer gets the next one."""
    try:
        rows = (_client().table("vula_open_questions").select("tenant_id,phone")
                .eq("status", "queued").gte("expires_at", _now().isoformat())
                .limit(500).execute().data or [])
    except Exception as exc:
        logger.debug("question queue sweep skipped: %s", exc)
        return 0
    sent = 0
    for tid, ph in {(r["tenant_id"], r["phone"]) for r in rows if r.get("tenant_id") and r.get("phone")}:
        try:
            sent += bool(await release_next(tid, ph))
        except Exception as exc:
            logger.debug("release for %s failed: %s", ph, exc)
    return sent


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
         .in_("status", ["open", "queued"]).execute())
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
         .eq("tenant_id", tenant_id).eq("ref_id", str(ref_id)).in_("status", ["open", "queued"])
         .execute())
    except Exception as exc:
        logger.debug("open question close_for skipped: %s", exc)

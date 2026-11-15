"""Which skill answered a person's last question, so a follow-up goes to the right place.

Routing looks at one message at a time (core/hrm/orchestrator.py). "And for next week?" names no
skill, so on its own it falls through to the general one and loses the thread. vula/api/whatsapp.py
kept the last skill per person in a dict — which a restart empties and the other worker never
sees — and the model that classifies a keyword miss was shown the bare message.

This keeps, per (tenant, phone), for 30 minutes:
  sticky   the last skill a follow-up is allowed to return to (the caller's own list)
  latest   whichever skill answered last, and the question it answered — given to the
           classifier as context for the next message

Both live in memory. With settings.follow_up_routing_enabled the latest skill is also written to
the conversation's session row (commerce_conversation_sessions.last_skill / last_skill_at,
migration 265) and read from there on a miss, so it survives a restart and is shared between
workers. Only the skill NAME is stored — never the question.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

TTL_SECONDS = 30 * 60
_MISS_TTL = 60.0

_STICKY: Dict[Tuple[str, str], Tuple[str, float]] = {}
_LATEST: Dict[Tuple[str, str], Tuple[str, str, float]] = {}
_DB_MISS: Dict[Tuple[str, str], float] = {}
_LOCK = threading.Lock()


def _durable(tenant_id: Optional[str] = None) -> bool:
    try:
        from vula import features
        return features.on(tenant_id, "follow_up_routing")
    except Exception:  # noqa: BLE001
        return False


def _session_keys(phone: str) -> list:
    return [phone, f"admin:{phone}"]


def _write(tenant_id: str, phone: str, skill: str) -> None:
    try:
        from vula.commerce import service
        (service._client().table("commerce_conversation_sessions")
         .update({"last_skill": skill, "last_skill_at": datetime.now(timezone.utc).isoformat()})
         .eq("tenant_id", tenant_id).in_("session_key", _session_keys(phone)).execute())
    except Exception as exc:  # noqa: BLE001 — routing memory is a convenience, never a failure
        logger.debug("route memory not saved (run migration 265?): %s", exc)


def _read(tenant_id: str, phone: str) -> Optional[str]:
    try:
        from vula.commerce import service
        rows = (service._client().table("commerce_conversation_sessions")
                .select("last_skill,last_skill_at").eq("tenant_id", tenant_id)
                .in_("session_key", _session_keys(phone)).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("route memory not read (run migration 265?): %s", exc)
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=TTL_SECONDS)).isoformat()
    fresh = [r for r in rows if isinstance(r, dict) and r.get("last_skill")
             and str(r.get("last_skill_at") or "") >= cutoff]
    fresh.sort(key=lambda r: str(r.get("last_skill_at")), reverse=True)
    return fresh[0]["last_skill"] if fresh else None


def remember(tenant_id: str, phone: str, skill: Optional[str], question: str = "",
             sticky: bool = False) -> None:
    """Note that `skill` just answered this person. `sticky=True` when a follow-up may return
    to it."""
    if not (tenant_id and phone and skill):
        return
    key, now = (tenant_id, phone), time.time()
    with _LOCK:
        _LATEST[key] = (skill, (question or "")[:200], now)
        if sticky:
            _STICKY[key] = (skill, now)
        _DB_MISS.pop(key, None)
    if _durable(tenant_id):
        threading.Thread(target=_write, args=(tenant_id, phone, skill), daemon=True).start()


def sticky(tenant_id: str, phone: str, allowed: Optional[set] = None) -> Optional[str]:
    """The skill a follow-up from this person should go back to, or None. `allowed` limits a
    skill recovered from the database to the caller's own list."""
    key, now = (tenant_id, phone), time.time()
    with _LOCK:
        hit = _STICKY.get(key)
        if hit and now - hit[1] < TTL_SECONDS:
            return hit[0]
        if not _durable(tenant_id) or now - _DB_MISS.get(key, 0) < _MISS_TTL:
            return None
    skill = _read(tenant_id, phone)
    with _LOCK:
        if skill and (allowed is None or skill in allowed):
            _STICKY[key] = (skill, now)
            return skill
        _DB_MISS[key] = now
    return None


def latest(tenant_id: str, phone: str) -> Optional[Dict[str, Any]]:
    """{"skill", "question"} for whatever answered this person last, within the window."""
    with _LOCK:
        hit = _LATEST.get((tenant_id, phone))
    if hit and time.time() - hit[2] < TTL_SECONDS:
        return {"skill": hit[0], "question": hit[1]}
    return None


def clear() -> None:
    """Forget everything held in memory (tests)."""
    with _LOCK:
        _STICKY.clear()
        _LATEST.clear()
        _DB_MISS.clear()

"""
vula/turns.py — a record of every WhatsApp message Vula handled (migration 195).

One row per inbound message: what came in, the steps Vula took (the open question it answered,
the skill that ran, each tool call, the model route, errors) and every reply it sent, in full.
"Why did Vula say that?" becomes one query instead of an hour in the Railway logs — on 5–6 Oct a
reply about a document wasn't in the chat history at all, and outbound messages kept only a
200-character preview.

The current turn lives in a context variable, so any code running for that message (including
tasks it spawns) can add a step with `note()` without being handed anything. Everything here is
best-effort: no turn, no table, or a failed write never touches the reply itself.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CURRENT: ContextVar[Optional[dict]] = ContextVar("vula_turn", default=None)
_MAX_STEPS = 60
_MAX_VALUE = 300


def _short(v: Any) -> Any:
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    s = str(v)
    return s if len(s) <= _MAX_VALUE else s[:_MAX_VALUE] + "…"


def current() -> Optional[dict]:
    return _CURRENT.get()


def note(step: str, **detail: Any) -> None:
    """Add a step to the turn being handled (no-op outside one)."""
    turn = _CURRENT.get()
    if turn is None or len(turn["steps"]) >= _MAX_STEPS:
        return
    entry = {"step": step, "ms": int((time.monotonic() - turn["_t0"]) * 1000)}
    entry.update({k: _short(v) for k, v in detail.items() if v is not None})
    turn["steps"].append(entry)


def reply(text: str, kind: str = "text", to: Optional[str] = None) -> None:
    """Record a message Vula sent during this turn — in full, with who it went to when that
    isn't the sender (a team ping, a relay)."""
    turn = _CURRENT.get()
    if turn is not None and text:
        entry = {"kind": kind, "text": text, "ms": int((time.monotonic() - turn["_t0"]) * 1000)}
        digits = "".join(ch for ch in (to or "") if ch.isdigit())
        if digits and not turn["phone"].endswith(digits[-9:]):
            entry["to"] = digits
        turn["replies"].append(entry)


def _client():
    from vula.commerce import service
    return service._client()


def _save(turn: dict) -> None:
    row = {k: v for k, v in turn.items() if not k.startswith("_")}
    try:
        _client().table("vula_turns").insert(row).execute()
    except Exception as exc:
        logger.debug("turn not recorded (run migration 195?): %s", exc)


async def run(coro, *, tenant_id: Optional[str], phone: str, kind: str,
              text: str = "", wamid: Optional[str] = None) -> Any:
    """Await `coro` as one recorded turn."""
    turn = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "phone": phone, "wamid": wamid,
            "kind": kind, "text": text, "steps": [], "replies": [], "outcome": "done",
            "started_at": datetime.now(timezone.utc).isoformat(), "_t0": time.monotonic()}
    token = _CURRENT.set(turn)
    try:
        return await coro
    except Exception as exc:
        turn["outcome"] = "failed"
        note("error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        _CURRENT.reset(token)
        turn["duration_ms"] = int((time.monotonic() - turn["_t0"]) * 1000)
        await asyncio.to_thread(_save, turn)

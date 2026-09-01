"""
vula/voice_retry.py — park a voice note whose transcription failed, and retry it locally.

Real telemetry (2026-09-01): 26% of every voice note Vula has ever received (6 of 23) was lost
to a bare 530 from the local Whisper tunnel — the SA GPU unreachable, never once an actual
transcription failure. The customer was told "please type it out instead", which for a food
order often just means the order doesn't happen.

The deliberate choice here is to retry LOCALLY rather than fall back to a third-party cloud
transcriber: the customer's audio never leaves Vula's own infrastructure (POPIA), and an
outage costs a delay instead of an order. core/transcribe.py's cloud fallback remains available
for anyone who configures a key, but this path means it isn't required.

Give-up policy is time-based, not just attempt-based: after MAX_AGE_HOURS we stop and tell the
customer honestly, rather than resurrecting a stale order hours after they've moved on.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
MAX_AGE_HOURS = 6
_MAX_BYTES = 8 * 1024 * 1024  # a WhatsApp voice note is ~15-175KB; this is a sanity ceiling


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(tenant_id: str, customer_phone: str, audio: bytes, *, mime_type: str = "audio/ogg",
            msg_id: Optional[str] = None, route_mode: str = "commerce") -> bool:
    """Park a failed voice note for a later local retry. Returns False if it couldn't be
    queued — the caller must then fall back to asking the customer to type."""
    if not audio or not tenant_id or not customer_phone:
        return False
    if len(audio) > _MAX_BYTES:
        log.warning("voice note too large to queue (%d bytes) — not retrying", len(audio))
        return False
    row = {
        "tenant_id": tenant_id,
        "customer_phone": customer_phone,
        "msg_id": msg_id,
        "mime_type": mime_type or "audio/ogg",
        "audio_b64": base64.b64encode(audio).decode("ascii"),
        "route_mode": route_mode or "commerce",
        "status": "pending",
        "attempts": 0,
        "created_at": _now(),
    }
    try:
        _client().table("vula_voice_retry_queue").insert(row).execute()
        log.info("queued voice note for retry (tenant=%s, %d bytes)", tenant_id, len(audio))
        return True
    except Exception as exc:
        # A duplicate msg_id means Meta redelivered the same webhook — already queued, which is
        # a success from the caller's point of view, not a failure.
        s = str(exc)
        if "23505" in s or "duplicate" in s.lower():
            log.info("voice note already queued (msg_id=%s) — not queuing twice", msg_id)
            return True
        log.warning("voice retry enqueue failed (run migration 148?): %s", exc)
        return False


def pending(limit: int = 20) -> List[Dict[str, Any]]:
    """Oldest pending entries first."""
    try:
        return (_client().table("vula_voice_retry_queue").select("*")
                .eq("status", "pending").order("created_at", desc=False)
                .limit(limit).execute().data or [])
    except Exception as exc:
        log.debug("voice retry queue read skipped (run migration 148?): %s", exc)
        return []


def too_old(row: Dict[str, Any]) -> bool:
    created = str(row.get("created_at") or "").replace("Z", "+00:00")
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(created)
    except Exception:
        return False
    return age > timedelta(hours=MAX_AGE_HOURS)


def mark_done(row_id: str) -> None:
    """Transcribed and routed — drop it. The queue is transient, not an audio archive, and
    keeping customer voice recordings around longer than needed is not ours to do."""
    try:
        _client().table("vula_voice_retry_queue").delete().eq("id", row_id).execute()
    except Exception as exc:
        log.debug("voice retry cleanup failed for %s: %s", row_id, exc)


def mark_failed_attempt(row_id: str, attempts: int, error: str) -> None:
    try:
        _client().table("vula_voice_retry_queue").update({
            "attempts": attempts + 1,
            "last_error": (error or "")[:300],
            "last_attempt_at": _now(),
        }).eq("id", row_id).execute()
    except Exception as exc:
        log.debug("voice retry attempt stamp failed for %s: %s", row_id, exc)


def give_up(row_id: str) -> None:
    """Stop retrying, but keep the row briefly so it's visible that a note was dropped —
    the audio is cleared, since there's no longer any reason to hold a customer's recording."""
    try:
        _client().table("vula_voice_retry_queue").update({
            "status": "gave_up", "audio_b64": "", "last_attempt_at": _now(),
        }).eq("id", row_id).execute()
    except Exception as exc:
        log.debug("voice retry give-up stamp failed for %s: %s", row_id, exc)


def audio_of(row: Dict[str, Any]) -> bytes:
    try:
        return base64.b64decode(row.get("audio_b64") or "")
    except Exception:
        return b""

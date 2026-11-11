"""vula/api/doc_batch.py — one reply for a batch of documents, not two per document.

10 Nov 2026 (DIGG): Judy sent ~20 skip invoices, proofs of payment and labour sheets in a row. Each
got its own "Filed: …" reply as it finished (the "Got it" was already once per burst), and because
documents are read one after another for a minute each, the replies kept arriving for ten minutes
after she stopped — "WhatsApp is feeling like it's in a loop".

Now, while a sender's documents are being worked through:
  - a single document still gets its reply as soon as it's done (nothing changes for one file);
  - from the second document in the burst, each document's reply is held, and when the last one is
    done (and nothing new has come in for GRACE_SECONDS) they go out as ONE numbered summary.

How it hooks in (vula/api/whatsapp.py): _handle_document_ingest runs inside enter()/leave(), and
_send_reply asks capture() first. A ContextVar marks the task (and the tasks it starts) as working
for that sender, so only document replies are held — a message the sender types meanwhile is answered
as normal. Sends with an idem_key (the "Got it") are never held. Per worker process: with two workers
a batch split across both gets two summaries, still far fewer than one or two per document.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

GRACE_SECONDS = 90.0
# 11 Nov 2026 (DIGG): ~30 FNB statements forwarded one at a time, 30-90 s apart. Each was read and
# answered before the next arrived, so every one was "the first of a new burst" and got its own
# reply (plus a "Got it" every two minutes). A document within PACE_SECONDS of the sender's previous
# one now joins that batch, and the summary waits GRACE_SECONDS after the last one is done.
PACE_SECONDS = 180.0
_last_doc: Dict[str, float] = {}


def _clock() -> float:
    import time
    return time.monotonic()
_current: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("doc_batch_sender", default=None)


class _Burst:
    __slots__ = ("active", "seen", "items", "flush", "project")

    def __init__(self) -> None:
        self.active = 0                      # documents of this burst still being worked on
        self.seen = 0                        # documents seen in this burst
        self.items: List[Tuple[str, str]] = []   # (tenant_id, reply) held for the summary
        self.flush: Optional[asyncio.Task] = None
        self.project: Optional[str] = None   # the project a caption in this burst named


_bursts: Dict[str, _Burst] = {}


def _key(phone: str) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def enter(phone: str) -> contextvars.Token:
    """A document from `phone` starts being worked on."""
    k = _key(phone)
    now = _clock()
    recent = now - _last_doc.get(k, float("-inf")) <= PACE_SECONDS
    _last_doc[k] = now
    for other in [p for p, t in _last_doc.items() if now - t > PACE_SECONDS]:
        _last_doc.pop(other, None)
    b = _bursts.get(k)
    if b is None:
        b = _bursts[k] = _Burst()
        if recent:
            b.seen = 1                       # the previous document came moments ago: same batch
    b.active += 1
    b.seen += 1
    if b.flush and not b.flush.done():
        b.flush.cancel()                     # more is coming: hold the summary
    b.flush = None
    return _current.set(k)


def continuing(phone: str) -> bool:
    """This document is not the first of its batch (so the sender already had a "Got it")."""
    b = _bursts.get(_key(phone))
    return bool(b and b.seen >= 2)


def capture(to: str, message: str, tenant_id: str) -> bool:
    """True when this reply is held for the batch summary instead of sent now: it's sent from a
    document's work for the same sender, and this burst has more than one document."""
    k = _current.get()
    if not k or k != _key(to) or not (message or "").strip():
        return False
    b = _bursts.get(k)
    if not b or b.seen < 2:
        return False
    b.items.append((tenant_id, message.strip()))
    return True


CAPTION_MINUTES = 10.0
_recent_project: Dict[str, Tuple[str, float]] = {}


def note_project(phone: str, project: str) -> None:
    """A caption named `project`: this sender's documents without a caption of their own, in this
    burst or within CAPTION_MINUTES of it, are filed there too (10 Nov 2026, DIGG: "Belladonna" on
    the first of 20 skip slips, sent a few minutes apart — the rest were asked about one by one)."""
    if not project:
        return
    k = _key(phone)
    b = _bursts.get(k)
    if b:
        b.project = project
    import time
    _recent_project[k] = (project, time.monotonic())


def project(phone: str) -> Optional[str]:
    """The project a recent caption from this sender named, or None."""
    k = _key(phone)
    b = _bursts.get(k)
    if b and b.project:
        return b.project
    import time
    got = _recent_project.get(k)
    if got and time.monotonic() - got[1] <= CAPTION_MINUTES * 60:
        return got[0]
    _recent_project.pop(k, None)
    return None


def pause() -> contextvars.Token:
    """Stop holding replies in this task for a moment (a keyed send such as the "Got it")."""
    return _current.set(None)


def resume(token: contextvars.Token) -> None:
    _current.reset(token)


def summary(items: List[Tuple[str, str]]) -> str:
    n = len(items)
    head = f"📄 Done — {n} document{'s' if n != 1 else ''} worked through:"
    return "\n\n".join([head] + [f"*{i}.* {text}" for i, (_t, text) in enumerate(items, 1)])


async def leave(phone: str, token: contextvars.Token,
                send: Callable[[str, str, str], Awaitable[bool]], grace: Optional[float] = None) -> None:
    """A document from `phone` is done. When it was the last one in the burst, the held replies are
    sent as one summary after a short wait (unless another document arrives first)."""
    try:
        _current.reset(token)
    except ValueError:                       # reset from another context: harmless
        pass
    k = _key(phone)
    b = _bursts.get(k)
    if not b:
        return
    b.active = max(0, b.active - 1)
    if b.active:
        return
    if not b.items:                          # nothing held (one document, replied to already): done
        if _bursts.get(k) is b:
            del _bursts[k]
        return

    async def _flush() -> None:
        try:
            await asyncio.sleep(GRACE_SECONDS if grace is None else grace)
        except asyncio.CancelledError:
            return
        if b.active:                         # a new document started meanwhile
            return
        if _bursts.get(k) is b:
            del _bursts[k]
        if not b.items:
            return
        tenant_id = next((t for t, _m in reversed(b.items) if t), "")
        try:
            await send(phone, summary(b.items), tenant_id)
        except Exception as exc:  # noqa: BLE001 — a failed summary must not lose silently
            log.warning("document batch summary to %s failed: %s", k[-4:], exc)

    b.flush = asyncio.create_task(_flush())

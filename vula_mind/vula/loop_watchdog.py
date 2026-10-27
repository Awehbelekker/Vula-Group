"""Alert when the event loop stops turning, i.e. when /status (and every WhatsApp reply) stops
answering although the process is still up.

Gap audit 1.3. The 26 Oct freeze (fixed in #220) was a blocking call on the event loop: every
asyncio timeout, holding message and health check runs ON that loop, so none of them fired and
nobody was told. This watchdog lives on its own thread. A small task on the loop records a
heartbeat every few seconds; the thread checks it, and when it is older than
LOOP_WATCHDOG_STALL_S it logs an ERROR with the loop's current stack (what is blocking it) and
alerts the operator once. It logs again when the loop recovers.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_BEAT_EVERY_S = 5


class Watchdog:
    def __init__(self, stall_s: float, alert: Callable[[str], None], loop_thread_id: Optional[int] = None):
        self.stall_s = stall_s
        self.alert = alert
        self.loop_thread_id = loop_thread_id
        self.last_beat = time.monotonic()
        self.stalled = False

    def beat(self, now: Optional[float] = None) -> None:
        self.last_beat = time.monotonic() if now is None else now

    def check(self, now: Optional[float] = None) -> Optional[str]:
        """'stalled' the first time the heartbeat is too old, 'recovered' when it is fresh again,
        else None."""
        age = (time.monotonic() if now is None else now) - self.last_beat
        if age > self.stall_s and not self.stalled:
            self.stalled = True
            logger.error("event loop stalled for %ds: /status and replies are not being answered. "
                         "The loop is at:\n%s", int(age), self._loop_stack())
            try:
                self.alert(f"⚠️ Vula's server has stopped answering for {int(age)}s (event loop "
                           f"blocked). Replies are waiting. The Railway logs show where it is stuck.")
            except Exception as exc:  # noqa: BLE001
                logger.warning("loop stall alert not sent: %s", type(exc).__name__)
            return "stalled"
        if age <= self.stall_s and self.stalled:
            self.stalled = False
            logger.warning("event loop recovered after a stall")
            return "recovered"
        return None

    def _loop_stack(self) -> str:
        frame = sys._current_frames().get(self.loop_thread_id) if self.loop_thread_id else None
        return "".join(traceback.format_stack(frame)[-15:]) if frame else "(stack not available)"


def _send_alert(text: str) -> None:
    """From the watchdog thread: the main loop is blocked, so the alert runs on a loop of its own."""
    from core.mass_mind.health import should_alert
    if not should_alert("event_loop_stalled"):
        return
    from vula.integrations.platform_support import alert_operator
    asyncio.run(asyncio.wait_for(alert_operator(text), timeout=30))


_dog: Optional[Watchdog] = None


async def _heartbeat(dog: Watchdog) -> None:
    while True:
        dog.beat()
        await asyncio.sleep(_BEAT_EVERY_S)


def start(stall_s: float) -> Optional[Watchdog]:
    """Call once from inside the running loop (the app's lifespan). stall_s <= 0 disables it."""
    global _dog
    if stall_s <= 0 or _dog is not None:
        return _dog
    dog = Watchdog(stall_s, _send_alert, threading.get_ident())
    asyncio.get_running_loop().create_task(_heartbeat(dog))

    def _watch() -> None:
        while True:
            time.sleep(min(10.0, stall_s / 3))
            try:
                dog.check()
            except Exception as exc:  # noqa: BLE001
                logger.warning("loop watchdog check failed: %s", type(exc).__name__)

    threading.Thread(target=_watch, name="loop-watchdog", daemon=True).start()
    _dog = dog
    return dog

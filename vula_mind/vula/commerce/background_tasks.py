"""Generic "do this in the background, notify on completion" primitive.

Extracted from vula/api/commerce.py's `_statement_job` (bank-statement reconciliation was
the first, and until now only, consumer of this shape). HTTP handlers that kick off
longer-than-request work should return a `{"processing": true}` response immediately and
call `run_background()` rather than awaiting the work inline or hand-rolling their own
`asyncio.create_task` + try/except.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("vula.commerce.background_tasks")

NotifyBuilder = Callable[[Any], Awaitable[None]]


def run_background(
    tenant_id: str,
    label: str,
    coro: Awaitable[Any],
    notify_builder: Optional[NotifyBuilder] = None,
    notify_on_failure: Optional[Callable[[Exception], Awaitable[None]]] = None,
) -> None:
    """Fire-and-forget `coro`, logging + best-effort notifying on both success and failure.

    `notify_builder(result)` is awaited with the coroutine's return value once it completes
    successfully — use it to WhatsApp/email the tenant a summary. `notify_on_failure(exc)` is
    awaited if the coroutine raises — best-effort, since a background job failing silently is
    worse than a failed job the tenant never hears about. Both are optional; a task with
    neither just runs and logs.
    """

    async def _run():
        try:
            result = await coro
        except Exception as exc:
            log.warning("background job %r failed for %s: %s", label, tenant_id, exc)
            if notify_on_failure:
                try:
                    await notify_on_failure(exc)
                except Exception as notify_exc:
                    log.debug("background job %r failure-notify skipped: %s", label, notify_exc)
            return
        if notify_builder:
            try:
                await notify_builder(result)
            except Exception as exc:
                log.debug("background job %r completion-notify skipped: %s", label, exc)

    asyncio.create_task(_run())

"""
core/reasoning_telemetry.py — persistent sink for the shared reasoning-telemetry envelope.

The LLM router and the VRL verify events emit the same envelope (schema/system/run_id/task/
outcome/escalated + verifier/reason/extra). Stdout logging is fine for debugging but Railway's
filesystem is ephemeral, so nothing accumulates for measurement. This writes each event to the
vula_reasoning_telemetry table (migration 045) so live runs pile up and the verified-reasoning
report can read real production data — not just the test harness.

Design constraints:
  • Never block the request: the insert runs in a thread executor (fire-and-forget) when there's
    a running loop, so it doesn't add latency to a generation.
  • Never break the caller: every failure is swallowed. Telemetry must not affect routing.
  • POPIA: `task` must already be a label/hash upstream — this module does not see raw prompts.
Gated by VULA_TELEMETRY_DB (default on); set to a falsey value to disable the DB writes entirely.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.environ.get("VULA_TELEMETRY_DB", "true").lower() in ("1", "true", "yes", "on")


def _insert(entry: dict) -> None:
    try:
        from vula.commerce import service as cs
        cs._client().table("vula_reasoning_telemetry").insert(entry).execute()
    except Exception as exc:  # missing table / no client / network — telemetry is best-effort
        logger.debug("reasoning telemetry insert skipped: %s", exc)


def emit(*, system: str, run_id: str | None = None, task: str | None = None,
         outcome: str | None = None, escalated: bool = False, verifier: str | None = None,
         reason: str | None = None, tenant_id: str | None = None, extra: dict | None = None) -> None:
    """Best-effort, non-blocking write of one telemetry envelope to the DB sink."""
    if not _enabled():
        return
    entry = {
        "schema": 1, "system": system, "run_id": run_id, "task": task,
        "outcome": outcome, "escalated": bool(escalated), "verifier": verifier,
        "reason": reason, "tenant_id": tenant_id, "extra": extra or {},
    }
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _insert, entry)   # off the request path
    except RuntimeError:
        _insert(entry)                                 # no loop (sync context) — inline

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
    # 2026-09-01: the test suite was writing real rows into the PRODUCTION telemetry sink.
    # Confirmed while investigating a degenerate-output incident: every "!!!!" event in
    # vula_reasoning_telemetry belonged to tenant "test-tenant", and 198 of ~300 verification
    # events over ten days were test runs, not traffic. That distorts exactly the numbers this
    # sink exists to inform. pytest sets PYTEST_CURRENT_TEST for every test it runs, so this is
    # a reliable signal and needs no per-test opt-out; a test that wants to assert on emitted
    # envelopes patches emit() directly (see tests/test_verification.py::emits) rather than
    # reading them back from the database.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
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


_PII_KEYS = {"customer_phone", "phone", "email", "customer_email", "customer_address", "address"}


def log_tool_call(tenant_id: str, skill: str, tool: str, args: dict | None = None) -> None:
    """Record that an agent called a tool (for the Agent Activity view). POPIA-safe: drops
    contact fields and truncates values, so we log WHAT the agent did, not customer PII."""
    safe = {}
    for k, v in (args or {}).items():
        if k in _PII_KEYS:
            continue
        s = str(v)
        safe[k] = (s[:60] + "…") if len(s) > 60 else s
    emit(system="vula-agent-tool", task=tool, outcome=skill, tenant_id=tenant_id, extra={"args": safe})

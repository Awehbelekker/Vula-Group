"""core/sentry_utils.py — thin, safe wrapper for tagging Sentry events with tenant_id.

No-op whenever Sentry isn't configured (SENTRY_DSN unset) or the SDK isn't importable — callers
never need to guard this themselves. Only the tenant_id is tagged; never a phone number or any
message content (see server.py's Sentry init for the fuller PII-scrubbing rationale).
"""
from __future__ import annotations


def tag_tenant(tenant_id: str | None) -> None:
    if not tenant_id:
        return
    try:
        import sentry_sdk
        sentry_sdk.set_tag("tenant_id", tenant_id)
    except Exception:
        pass


def note_scheduler_lock_flap(holder: str, prev_expires_at: str, requested_expires_at: str) -> None:
    """Breadcrumb for the still-undiagnosed scheduler-lock self-renew flap (server.py's
    _try_acquire_or_renew_scheduler_lock docstring has the history — a confirmed past duplicate-
    send bug, symptom fixed, root cause not yet nailed down). No PII here — holder is a
    hostname-pid string, timestamps are lease bookkeeping, never customer content."""
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            scope.set_extra("holder", holder)
            scope.set_extra("prev_expires_at", prev_expires_at)
            scope.set_extra("requested_expires_at", requested_expires_at)
            sentry_sdk.capture_message("scheduler lock self-renew lost own lease", level="warning")
    except Exception:
        pass

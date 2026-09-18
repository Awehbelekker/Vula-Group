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

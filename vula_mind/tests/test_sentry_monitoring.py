"""Tests for core/sentry_utils.py — added alongside server.py's Sentry init (go-live
readiness pass, Phase 0.1). Error monitoring must never be able to break the app: tag_tenant
has to no-op safely whether or not SENTRY_DSN is configured, and whether or not the sentry_sdk
package can actually be imported."""
import sys
from unittest.mock import MagicMock, patch

from core.sentry_utils import note_scheduler_lock_flap, tag_tenant


def test_tag_tenant_noop_on_empty_tenant_id():
    tag_tenant(None)
    tag_tenant("")


def test_tag_tenant_calls_sentry_set_tag_when_available():
    fake_sentry = MagicMock()
    with patch.dict(sys.modules, {"sentry_sdk": fake_sentry}):
        tag_tenant("digg-demo")
    fake_sentry.set_tag.assert_called_once_with("tenant_id", "digg-demo")


def test_tag_tenant_swallows_import_error():
    with patch.dict(sys.modules, {"sentry_sdk": None}):
        tag_tenant("digg-demo")  # must not raise


def test_tag_tenant_swallows_set_tag_exception():
    fake_sentry = MagicMock()
    fake_sentry.set_tag.side_effect = RuntimeError("sentry down")
    with patch.dict(sys.modules, {"sentry_sdk": fake_sentry}):
        tag_tenant("digg-demo")  # must not raise


def test_note_scheduler_lock_flap_captures_message_with_context():
    fake_sentry = MagicMock()
    fake_scope = MagicMock()
    fake_sentry.new_scope.return_value.__enter__ = MagicMock(return_value=fake_scope)
    fake_sentry.new_scope.return_value.__exit__ = MagicMock(return_value=False)
    with patch.dict(sys.modules, {"sentry_sdk": fake_sentry}):
        note_scheduler_lock_flap("host-123", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")
    fake_scope.set_extra.assert_any_call("holder", "host-123")
    fake_sentry.capture_message.assert_called_once_with(
        "scheduler lock self-renew lost own lease", level="warning"
    )


def test_note_scheduler_lock_flap_swallows_import_error():
    with patch.dict(sys.modules, {"sentry_sdk": None}):
        note_scheduler_lock_flap("host-123", "a", "b")  # must not raise

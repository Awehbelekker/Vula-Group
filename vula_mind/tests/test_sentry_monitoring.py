"""Tests for core/sentry_utils.py — added alongside server.py's Sentry init (go-live
readiness pass, Phase 0.1). Error monitoring must never be able to break the app: tag_tenant
has to no-op safely whether or not SENTRY_DSN is configured, and whether or not the sentry_sdk
package can actually be imported."""
import sys
from unittest.mock import MagicMock, patch

from core.sentry_utils import tag_tenant


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

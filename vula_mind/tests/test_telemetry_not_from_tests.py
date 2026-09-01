"""The test suite must not write into the production telemetry sink.

Found 2026-09-01 while investigating a degenerate-output incident: every "!!!!" event in
vula_reasoning_telemetry belonged to tenant "test-tenant", and 198 of roughly 300 verification
events over ten days were test runs rather than real traffic. That distorts precisely the
numbers this sink exists to inform — the checker_error rate, the defect rate, the degenerate
count — all of which were being read this session to make decisions.

pytest sets PYTEST_CURRENT_TEST for every test it runs, so the gate needs no per-test opt-out.
A test that wants to assert on emitted envelopes patches emit() directly (the `emits` fixture in
tests/test_verification.py) rather than reading rows back from the database.
"""
import os
from unittest.mock import patch

import core.reasoning_telemetry as rt


def test_telemetry_is_disabled_while_running_under_pytest():
    """This assertion runs inside pytest, so the variable is necessarily set."""
    assert os.environ.get("PYTEST_CURRENT_TEST"), "pytest should set this for every test"
    assert rt._enabled() is False


def test_no_database_write_happens_from_a_test():
    with patch.object(rt, "_insert") as insert:
        rt.emit(system="vula-degenerate-output", task="commerce_admin",
                outcome="substituted", tenant_id="test-tenant", extra={"snippet": "!" * 50})
    insert.assert_not_called(), "a test must never reach the production sink"


def test_log_tool_call_is_also_silenced():
    with patch.object(rt, "_insert") as insert:
        rt.log_tool_call("test-tenant", "commerce_admin", "create_quote", {"total": 100})
    insert.assert_not_called()


def test_it_would_be_enabled_outside_pytest():
    """Guard the gate itself: remove the marker and normal behaviour returns."""
    with patch.dict(os.environ, {"VULA_TELEMETRY_DB": "true"}, clear=False):
        saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            assert rt._enabled() is True
        finally:
            if saved is not None:
                os.environ["PYTEST_CURRENT_TEST"] = saved


def test_the_explicit_env_switch_still_wins_outside_pytest():
    with patch.dict(os.environ, {"VULA_TELEMETRY_DB": "false"}, clear=False):
        saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            assert rt._enabled() is False
        finally:
            if saved is not None:
                os.environ["PYTEST_CURRENT_TEST"] = saved

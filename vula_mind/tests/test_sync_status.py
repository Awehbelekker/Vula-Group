"""Tests for vula/integrations/sync_status.py (migration 169) — go-live readiness pass, Phase
3.2. Shared write-through for "did the last background sync succeed", used by both the ClickUp
and OneDrive sync loops. Best-effort throughout: a status-write failure must never break the
actual sync it's reporting on."""
from unittest.mock import MagicMock, patch

from vula.integrations.sync_status import record_sync_result


def test_record_sync_result_ok_writes_status_and_timestamp():
    mock_db = MagicMock()
    with patch("vula.commerce.service._client", return_value=mock_db):
        record_sync_result("vula_clickup_accounts", "digg-demo", ok=True)

    mock_db.table.assert_called_once_with("vula_clickup_accounts")
    patch_arg = mock_db.table.return_value.update.call_args[0][0]
    assert patch_arg["last_sync_status"] == "ok"
    assert patch_arg["last_sync_error"] is None
    assert patch_arg["last_synced_at"]
    mock_db.table.return_value.update.return_value.eq.assert_called_once_with("tenant_id", "digg-demo")


def test_record_sync_result_error_writes_truncated_error_message():
    mock_db = MagicMock()
    long_error = "x" * 1000
    with patch("vula.commerce.service._client", return_value=mock_db):
        record_sync_result("vula_microsoft_accounts", "digg-demo", ok=False, error=long_error)

    patch_arg = mock_db.table.return_value.update.call_args[0][0]
    assert patch_arg["last_sync_status"] == "error"
    assert len(patch_arg["last_sync_error"]) == 500


def test_record_sync_result_never_raises_on_db_error():
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        record_sync_result("vula_clickup_accounts", "digg-demo", ok=True)  # must not raise

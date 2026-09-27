"""OneDrive sync only re-ingests files modified since the last successful sweep (2026-09-25)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.microsoft import service


@pytest.mark.asyncio
async def test_unchanged_files_are_skipped(tmp_path, monkeypatch):
    accounts = MagicMock()
    accounts.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"tenant_id": "t1", "last_synced_at": "2026-09-20T00:00:00+00:00"}])
    files = [{"id": "old", "name": "old.pdf", "modifiedTime": "2026-09-01T00:00:00Z"},
             {"id": "new", "name": "new.pdf", "modifiedTime": "2026-09-24T00:00:00Z"}]
    download = AsyncMock(return_value={"name": "new.pdf", "data": b"x"})
    ingest = AsyncMock()
    monkeypatch.setattr("config.settings.upload_dir", tmp_path)
    with patch("vula.microsoft.credentials._client", return_value=accounts), \
         patch.object(service, "list_recent_files", AsyncMock(return_value=files)), \
         patch.object(service, "drive_download", download), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as P, \
         patch("vula.integrations.sync_status.record_sync_result"):
        P.return_value.ingest_file = ingest
        total = await service.process_all_onedrive_sync()
    assert total == 1
    assert [c.args[1] for c in download.await_args_list] == ["new"]

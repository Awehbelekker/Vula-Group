"""Tests for proactive OneDrive->KB sync (2026-09-18, E3) — drive_search/drive_download were
on-demand only (a file reached the KB only after the user explicitly asked Vula to pull that
specific one in-conversation). Mirrors process_all_email_sync/process_all_clickup_sync's shape.

No equivalent exists for Google Drive: drive.file is a deliberately restrictive OAuth scope
(Vula only sees files it created or the user explicitly picked — see vula/google/service.py's
own SCOPES comment), so an equivalent sweep would return nothing useful without a scope change
this session doesn't make unilaterally. Not tested here since it isn't built.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.microsoft.service import MicrosoftNotConnected

TID = "digg-demo"


# ── list_recent_files ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_recent_files_returns_parsed_files():
    from vula.microsoft.service import list_recent_files
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"value": [
        {"id": "f1", "name": "Site Report.pdf", "file": {"mimeType": "application/pdf"},
         "webUrl": "https://...", "lastModifiedDateTime": "2026-09-17T10:00:00Z"},
    ]}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=resp)

    with (
        patch("vula.microsoft.service._token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.httpx.AsyncClient", return_value=mock_client),
    ):
        files = await list_recent_files(TID)

    assert files == [{"id": "f1", "name": "Site Report.pdf", "mimeType": "application/pdf",
                      "modifiedTime": "2026-09-17T10:00:00Z"}]
    assert mock_client.get.call_args.args[0].endswith("/me/drive/recent")


@pytest.mark.asyncio
async def test_list_recent_files_filters_out_folders():
    """Graph's /recent can include folders too — only items with a `file` facet are real files."""
    from vula.microsoft.service import list_recent_files
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"value": [
        {"id": "folder1", "name": "Projects"},  # no "file" key — a folder
        {"id": "f1", "name": "Report.pdf", "file": {"mimeType": "application/pdf"}},
    ]}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=resp)

    with (
        patch("vula.microsoft.service._token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.httpx.AsyncClient", return_value=mock_client),
    ):
        files = await list_recent_files(TID)

    assert len(files) == 1 and files[0]["id"] == "f1"


@pytest.mark.asyncio
async def test_list_recent_files_not_connected_returns_empty():
    from vula.microsoft.service import list_recent_files
    with patch("vula.microsoft.service._token", new=AsyncMock(side_effect=MicrosoftNotConnected())):
        files = await list_recent_files(TID)
    assert files == []


@pytest.mark.asyncio
async def test_list_recent_files_api_failure_returns_empty_not_raise():
    from vula.microsoft.service import list_recent_files
    with (
        patch("vula.microsoft.service._token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.httpx.AsyncClient", side_effect=RuntimeError("network down")),
    ):
        files = await list_recent_files(TID)
    assert files == []


# ── process_all_onedrive_sync ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_ingests_every_tenants_recent_files(tmp_path, monkeypatch):
    from vula.microsoft.service import process_all_onedrive_sync
    from config import settings
    monkeypatch.setattr(settings, "upload_dir", tmp_path)

    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "tenant-a"}])
    files = [{"id": "f1", "name": "Report.pdf", "mimeType": "application/pdf"}]
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_file = AsyncMock()

    with (
        patch("vula.microsoft.credentials._client", return_value=mock_client),
        patch("vula.microsoft.service.list_recent_files", new=AsyncMock(return_value=files)),
        patch("vula.microsoft.service.drive_download",
              new=AsyncMock(return_value={"name": "Report.pdf", "mime": "application/pdf", "data": b"pdf-bytes"})),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        total = await process_all_onedrive_sync()

    assert total == 1
    mock_pipeline.ingest_file.assert_awaited_once()
    saved_path = mock_pipeline.ingest_file.call_args.args[0]
    assert isinstance(saved_path, Path) and saved_path.name == "Report.pdf"
    assert saved_path.read_bytes() == b"pdf-bytes"


@pytest.mark.asyncio
async def test_sync_no_files_is_a_no_op_not_an_error():
    from vula.microsoft.service import process_all_onedrive_sync
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "tenant-a"}])

    with (
        patch("vula.microsoft.credentials._client", return_value=mock_client),
        patch("vula.microsoft.service.list_recent_files", new=AsyncMock(return_value=[])),
    ):
        total = await process_all_onedrive_sync()

    assert total == 0


@pytest.mark.asyncio
async def test_sync_one_tenant_failure_does_not_stop_others(tmp_path, monkeypatch):
    from vula.microsoft.service import process_all_onedrive_sync
    from config import settings
    monkeypatch.setattr(settings, "upload_dir", tmp_path)

    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "broken"}, {"tenant_id": "fine"}])

    async def _fake_list(tenant_id):
        if tenant_id == "broken":
            raise RuntimeError("graph api down")
        return [{"id": "f1", "name": "Report.pdf", "mimeType": "application/pdf"}]

    mock_pipeline = MagicMock()
    mock_pipeline.ingest_file = AsyncMock()

    with (
        patch("vula.microsoft.credentials._client", return_value=mock_client),
        patch("vula.microsoft.service.list_recent_files", side_effect=_fake_list),
        patch("vula.microsoft.service.drive_download",
              new=AsyncMock(return_value={"name": "Report.pdf", "mime": "application/pdf", "data": b"bytes"})),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        total = await process_all_onedrive_sync()

    assert total == 1


@pytest.mark.asyncio
async def test_sync_db_failure_returns_zero_not_raise():
    from vula.microsoft.service import process_all_onedrive_sync
    with patch("vula.microsoft.credentials._client", side_effect=RuntimeError("db down")):
        total = await process_all_onedrive_sync()
    assert total == 0


@pytest.mark.asyncio
async def test_sync_one_file_download_failure_does_not_stop_the_rest(tmp_path, monkeypatch):
    from vula.microsoft.service import process_all_onedrive_sync
    from config import settings
    monkeypatch.setattr(settings, "upload_dir", tmp_path)

    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "tenant-a"}])
    files = [{"id": "bad", "name": "Broken.pdf", "mimeType": "application/pdf"},
             {"id": "good", "name": "Good.pdf", "mimeType": "application/pdf"}]

    async def _fake_download(tenant_id, file_id):
        if file_id == "bad":
            raise RuntimeError("download failed")
        return {"name": "Good.pdf", "mime": "application/pdf", "data": b"bytes"}

    mock_pipeline = MagicMock()
    mock_pipeline.ingest_file = AsyncMock()

    with (
        patch("vula.microsoft.credentials._client", return_value=mock_client),
        patch("vula.microsoft.service.list_recent_files", new=AsyncMock(return_value=files)),
        patch("vula.microsoft.service.drive_download", side_effect=_fake_download),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        total = await process_all_onedrive_sync()

    assert total == 1
    mock_pipeline.ingest_file.assert_awaited_once()

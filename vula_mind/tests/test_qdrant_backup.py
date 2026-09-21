"""Tests for vula/integrations/qdrant_backup.py — the periodic per-tenant Qdrant collection
backup job (docs/dr.md's previously-flagged "no Qdrant backup at all" gap, migration 171)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.integrations import qdrant_backup


def test_list_tenant_collections_filters_prefix_and_training():
    body = {"result": {"collections": [
        {"name": "vula_tenant_a"},
        {"name": "vula_tenant_b"},
        {"name": "vula_vula_training"},   # excluded: shared training data, not a tenant
        {"name": "some_other_collection"},  # excluded: not a vula_ collection at all
    ]}}
    out = qdrant_backup._list_tenant_collections(body)
    assert out == [("vula_tenant_a", "tenant-a"), ("vula_tenant_b", "tenant-b")]


@pytest.mark.asyncio
async def test_snapshot_deletes_qdrant_copy_even_when_download_fails():
    """The finally block must fire Qdrant-side cleanup even if the download itself raised, or a
    flaky download leaves an orphaned snapshot on Qdrant's own disk forever."""
    create_resp = MagicMock()
    create_resp.raise_for_status = MagicMock()
    create_resp.json.return_value = {"result": {"name": "snap-1"}}

    client = MagicMock()
    client.post = AsyncMock(return_value=create_resp)
    client.get = AsyncMock(side_effect=RuntimeError("download failed"))
    client.delete = AsyncMock()

    with pytest.raises(RuntimeError):
        await qdrant_backup._snapshot_one_collection(client, "http://qdrant", {}, "vula_tenant_a")

    client.delete.assert_awaited_once_with(
        "http://qdrant/collections/vula_tenant_a/snapshots/snap-1", headers={}
    )


@pytest.mark.asyncio
async def test_snapshot_happy_path_returns_bytes_and_cleans_up():
    create_resp = MagicMock()
    create_resp.raise_for_status = MagicMock()
    create_resp.json.return_value = {"result": {"name": "snap-1"}}
    dl_resp = MagicMock()
    dl_resp.raise_for_status = MagicMock()
    dl_resp.content = b"snapshot-bytes"

    client = MagicMock()
    client.post = AsyncMock(return_value=create_resp)
    client.get = AsyncMock(return_value=dl_resp)
    client.delete = AsyncMock()

    data = await qdrant_backup._snapshot_one_collection(client, "http://qdrant", {}, "vula_tenant_a")

    assert data == b"snapshot-bytes"
    client.delete.assert_awaited_once()


def test_upload_and_prune_keeps_only_newest_n():
    existing = [{"name": f"2026010{i}.snapshot", "created_at": f"2026-01-0{i}T00:00:00Z"}
                for i in range(1, 10)]  # 9 objects, cap is 7

    mock_bucket = MagicMock()
    mock_bucket.upload = MagicMock()
    mock_bucket.list = MagicMock(return_value=existing)
    mock_bucket.remove = MagicMock()

    mock_sb = MagicMock()
    mock_sb.storage.from_.return_value = mock_bucket

    with patch.object(qdrant_backup, "_client", return_value=mock_sb):
        path = qdrant_backup._upload_and_prune("tenant-a", b"data")

    assert path.startswith("tenant-a/")
    mock_bucket.upload.assert_called_once()
    mock_bucket.remove.assert_called_once()
    removed_paths = mock_bucket.remove.call_args[0][0]
    assert len(removed_paths) == 2  # 9 - 7 kept = 2 pruned
    assert all(p.startswith("tenant-a/") for p in removed_paths)


def test_upload_and_prune_no_op_when_under_cap():
    mock_bucket = MagicMock()
    mock_bucket.upload = MagicMock()
    mock_bucket.list = MagicMock(return_value=[{"name": "a.snapshot", "created_at": "2026-01-01"}])
    mock_bucket.remove = MagicMock()

    mock_sb = MagicMock()
    mock_sb.storage.from_.return_value = mock_bucket

    with patch.object(qdrant_backup, "_client", return_value=mock_sb):
        qdrant_backup._upload_and_prune("tenant-a", b"data")

    mock_bucket.remove.assert_not_called()


def test_record_status_upserts_with_on_conflict_tenant_id():
    mock_table = MagicMock()
    mock_sb = MagicMock()
    mock_sb.table.return_value = mock_table

    with patch.object(qdrant_backup, "_client", return_value=mock_sb):
        qdrant_backup._record_status("tenant-a", ok=True, path="tenant-a/x.snapshot")

    mock_sb.table.assert_called_once_with("vula_qdrant_backup_status")
    _, kwargs = mock_table.upsert.call_args
    assert kwargs.get("on_conflict") == "tenant_id"
    payload = mock_table.upsert.call_args[0][0]
    assert payload["tenant_id"] == "tenant-a"
    assert payload["last_backup_status"] == "ok"
    assert payload["last_snapshot_path"] == "tenant-a/x.snapshot"


def test_record_status_never_raises_on_db_error():
    mock_sb = MagicMock()
    mock_sb.table.side_effect = RuntimeError("db down")
    with patch.object(qdrant_backup, "_client", return_value=mock_sb):
        qdrant_backup._record_status("tenant-a", ok=False, error="boom")  # must not raise


@pytest.mark.asyncio
async def test_backup_all_tenants_no_qdrant_base_returns_zero(monkeypatch):
    monkeypatch.delenv("QDRANT_BASE", raising=False)
    with patch("httpx.AsyncClient") as mock_cls:
        n = await qdrant_backup.backup_all_tenants()
    assert n == 0
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_backup_all_tenants_one_tenant_failure_does_not_stop_others(monkeypatch):
    monkeypatch.setenv("QDRANT_BASE", "http://qdrant:6333")

    collections_resp = MagicMock()
    collections_resp.raise_for_status = MagicMock()
    collections_resp.json.return_value = {"result": {"collections": [
        {"name": "vula_broken"}, {"name": "vula_fine"},
    ]}}

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=collections_resp)

    async def _fake_snapshot(client, qbase, headers, name):
        if name == "vula_broken":
            raise RuntimeError("qdrant snapshot failed")
        return b"data"

    statuses = []

    def _fake_record_status(tenant_id, *, ok, error="", path=""):
        statuses.append((tenant_id, ok))

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(qdrant_backup, "_snapshot_one_collection", side_effect=_fake_snapshot),
        patch.object(qdrant_backup, "_upload_and_prune", return_value="fine/x.snapshot"),
        patch.object(qdrant_backup, "_record_status", side_effect=_fake_record_status),
    ):
        n = await qdrant_backup.backup_all_tenants()

    assert n == 1  # only "fine" succeeded
    assert ("broken", False) in statuses
    assert ("fine", True) in statuses


@pytest.mark.asyncio
async def test_backup_all_tenants_listing_failure_is_fail_open(monkeypatch):
    monkeypatch.setenv("QDRANT_BASE", "http://qdrant:6333")
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=RuntimeError("qdrant unreachable"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        n = await qdrant_backup.backup_all_tenants()  # must not raise

    assert n == 0

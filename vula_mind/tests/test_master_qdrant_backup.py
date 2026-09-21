"""Tests for /v1/master/qdrant-backup* (vula/api/master.py) — a manual trigger + status view
for the daily Qdrant snapshot job (vula/integrations/qdrant_backup.py, migration 171, see
docs/dr.md). Added so an operator confirming a fix (e.g. the bucket size limit in migration
172) doesn't have to wait up to 24h for the next scheduled run or restart the service."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api import master


@pytest.mark.asyncio
async def test_master_qdrant_backup_status_returns_rows():
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.order.return_value.execute.return_value = (
        MagicMock(data=[{"tenant_id": "digg-demo", "last_backup_status": "error"}])
    )
    with patch("vula.api.master._client", return_value=mock_db):
        result = await master.master_qdrant_backup_status()

    mock_db.table.assert_called_once_with("vula_qdrant_backup_status")
    assert result == {"statuses": [{"tenant_id": "digg-demo", "last_backup_status": "error"}]}


@pytest.mark.asyncio
async def test_master_run_qdrant_backup_fires_job_and_audits():
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.order.return_value.execute.return_value = (
        MagicMock(data=[{"tenant_id": "digg-demo", "last_backup_status": "ok"}])
    )
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.integrations.qdrant_backup.backup_all_tenants", new=AsyncMock(return_value=6)),
        patch("vula.api.master.audit") as mock_audit,
    ):
        result = await master.master_run_qdrant_backup(identity={"user_id": "master-1", "email": "ian@vula.ai"})

    assert result["ok_count"] == 6
    assert result["statuses"] == [{"tenant_id": "digg-demo", "last_backup_status": "ok"}]
    mock_audit.assert_called_once_with(
        {"user_id": "master-1", "email": "ian@vula.ai"}, "qdrant_backup.run", ok_count=6
    )


@pytest.mark.asyncio
async def test_master_run_qdrant_backup_requires_master_dependency():
    """The endpoint must stay behind require_master like every other /v1/master/* route —
    read the router registration rather than re-deriving it, so this fails loudly if the
    dependency is ever dropped by accident."""
    route = next(r for r in master.router.routes if r.path == "/qdrant-backup/run")
    assert route.methods == {"POST"}
    # require_master is applied router-wide via APIRouter(dependencies=[Depends(require_master)])
    assert any(
        getattr(dep.dependency, "__name__", "") == "require_master"
        for dep in master.router.dependencies
    )

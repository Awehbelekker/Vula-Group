"""Tests for the 2026-09-18 fix wiring ClickUp into the tenant knowledge base.

Real gap: sync_tenant_clickup_kb (formerly the inline body of the /sync-kb HTTP route) did the
right thing — pulled tasks per list, ingested them into the tenant's Qdrant collection under a
stable doc_id — but was never actually called from anywhere: no cron job, no onboarding hook, no
UI trigger. ClickUp content never reached the knowledge base in practice, so a question
answerable from a ClickUp task only worked if the model happened to route to clickup_admin's
live-API tools instead of the shared KB search every other skill already uses.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TID = "digg-demo"


# ── sync_tenant_clickup_kb (the extracted, callable-from-anywhere function) ───────

@pytest.mark.asyncio
async def test_sync_tenant_clickup_kb_ingests_each_list():
    from vula.api.clickup import sync_tenant_clickup_kb

    creds = {"list_ids": {"default": "list1", "list1": "HPC Bokaap", "list2": "Site B"}}
    tasks = [{"title": "Pour foundations", "status": "in progress", "due_date": None, "assignees": ["Judy"]}]
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=MagicMock(chunks_stored=3))

    with (
        patch("vula.api.clickup.get_tenant_clickup_creds", return_value=creds),
        patch("vula.clickup.service.list_tasks", new=AsyncMock(return_value=tasks)),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        res = await sync_tenant_clickup_kb(TID)

    assert res["synced_lists"] == 2
    assert res["chunks_added"] == 6
    doc_ids = [c.kwargs["doc_id"] for c in mock_pipeline.ingest_text.call_args_list]
    assert set(doc_ids) == {"clickup_list1", "clickup_list2"}
    # "default" is a pointer to the default list id, not a real list — never ingested itself.
    assert "clickup_default" not in doc_ids


@pytest.mark.asyncio
async def test_sync_tenant_clickup_kb_not_connected():
    from vula.api.clickup import sync_tenant_clickup_kb
    with patch("vula.api.clickup.get_tenant_clickup_creds", return_value=None):
        res = await sync_tenant_clickup_kb(TID)
    assert "error" in res


@pytest.mark.asyncio
async def test_sync_kb_route_delegates_to_the_shared_function():
    from vula.api.clickup import sync_kb
    expected = {"tenant_id": TID, "synced_lists": 1, "chunks_added": 2}
    with patch("vula.api.clickup.sync_tenant_clickup_kb", new=AsyncMock(return_value=expected)) as mock_sync:
        res = await sync_kb(TID)
    mock_sync.assert_awaited_once_with(TID)
    assert res is expected


# ── process_all_clickup_sync (the new scheduled-loop entry point) ────────────────

@pytest.mark.asyncio
async def test_process_all_clickup_sync_iterates_connected_tenants():
    from vula.clickup.service import process_all_clickup_sync

    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "tenant-a"}, {"tenant_id": "tenant-b"}])

    async def _fake_sync(tid):
        return {"tenant_id": tid, "synced_lists": 1, "chunks_added": 1}

    with (
        patch("vula.clickup.service._client", return_value=mock_client),
        patch("vula.api.clickup.sync_tenant_clickup_kb", new=AsyncMock(side_effect=_fake_sync)) as mock_sync,
    ):
        total = await process_all_clickup_sync()

    assert total == 2
    assert mock_sync.await_count == 2
    mock_client.table.return_value.select.return_value.eq.assert_called_once_with("status", "connected")


@pytest.mark.asyncio
async def test_process_all_clickup_sync_one_tenant_failure_does_not_stop_others():
    from vula.clickup.service import process_all_clickup_sync

    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "broken"}, {"tenant_id": "fine"}])

    async def _fake_sync(tid):
        if tid == "broken":
            raise RuntimeError("clickup API down")
        return {"tenant_id": tid, "synced_lists": 1, "chunks_added": 1}

    with (
        patch("vula.clickup.service._client", return_value=mock_client),
        patch("vula.api.clickup.sync_tenant_clickup_kb", new=AsyncMock(side_effect=_fake_sync)),
    ):
        total = await process_all_clickup_sync()

    assert total == 1


@pytest.mark.asyncio
async def test_process_all_clickup_sync_db_failure_returns_zero_not_raise():
    from vula.clickup.service import process_all_clickup_sync
    with patch("vula.clickup.service._client", side_effect=RuntimeError("db down")):
        total = await process_all_clickup_sync()
    assert total == 0


@pytest.mark.asyncio
async def test_process_all_clickup_sync_records_status_per_tenant():
    """Go-live readiness pass Phase 3.2: each tenant's sync outcome must be persisted (migration
    169), not just logged — the dashboard's connect-status UI reads this to stop showing
    "Connected" for a sync that's actually been failing."""
    from vula.clickup.service import process_all_clickup_sync

    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "broken"}, {"tenant_id": "fine"}])

    async def _fake_sync(tid):
        if tid == "broken":
            raise RuntimeError("clickup API down")
        return {"tenant_id": tid, "synced_lists": 1, "chunks_added": 1}

    with (
        patch("vula.clickup.service._client", return_value=mock_client),
        patch("vula.api.clickup.sync_tenant_clickup_kb", new=AsyncMock(side_effect=_fake_sync)),
        patch("vula.integrations.sync_status.record_sync_result") as mock_record,
    ):
        await process_all_clickup_sync()

    calls = {c.args[1]: c.kwargs for c in mock_record.call_args_list}
    assert calls["broken"]["ok"] is False
    assert "clickup API down" in calls["broken"]["error"]
    assert calls["fine"]["ok"] is True


# ── incremental webhook ingest (taskCreated / taskCommentPosted) ─────────────────

@pytest.mark.asyncio
async def test_new_task_event_ingests_into_the_kb():
    import vula.api.clickup as cu

    task = {"name": "Site inspection", "url": "http://cu/t1", "status": "open",
            "assignees": ["Judy"], "description": "Walk the site before pour"}
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=MagicMock(chunks_stored=1))
    with (
        patch("vula.clickup.service.get_task", new=AsyncMock(return_value=task)),
        patch("vula.integrations.notify.notify_team", new=AsyncMock()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        await cu._handle_non_status_event(TID, "taskCreated", "t1", {"event": "taskCreated"})

    mock_pipeline.ingest_text.assert_awaited_once()
    assert mock_pipeline.ingest_text.call_args.kwargs["doc_id"] == "clickup_task_t1"
    content = mock_pipeline.ingest_text.call_args.kwargs["content"]
    assert "Site inspection" in content and "Walk the site before pour" in content


@pytest.mark.asyncio
async def test_comment_event_ingests_the_comment_text():
    import vula.api.clickup as cu

    task = {"name": "Site inspection", "url": "", "status": "open", "assignees": [], "description": ""}
    body = {"event": "taskCommentPosted", "task_id": "t1", "history_items": [
        {"user": {"username": "Judy"}, "comment": {"text_content": "Client moved it to Thursday"}}]}
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=MagicMock(chunks_stored=1))
    with (
        patch("vula.clickup.service.get_task", new=AsyncMock(return_value=task)),
        patch("vula.integrations.notify.notify_team", new=AsyncMock()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        await cu._handle_non_status_event(TID, "taskCommentPosted", "t1", body)

    mock_pipeline.ingest_text.assert_awaited_once()
    assert "Client moved it to Thursday" in mock_pipeline.ingest_text.call_args.kwargs["content"]


@pytest.mark.asyncio
async def test_assignee_change_event_does_not_ingest():
    """Only content-changing events (a new task, a new comment) re-ingest — metadata-only
    changes (assignee/priority/due-date) are cheap notifications, not KB-worthy on their own."""
    import vula.api.clickup as cu

    task = {"name": "Site inspection", "url": "", "status": "open", "assignees": ["Judy"], "description": ""}
    body = {"event": "taskAssigneeUpdated", "task_id": "t1",
            "history_items": [{"field": "assignee_add", "after": {"username": "Judy"}}]}
    with (
        patch("vula.clickup.service.get_task", new=AsyncMock(return_value=task)),
        patch("vula.integrations.notify.notify_team", new=AsyncMock()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
    ):
        await cu._handle_non_status_event(TID, "taskAssigneeUpdated", "t1", body)

    mock_pipeline_cls.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_failure_never_breaks_the_notification():
    import vula.api.clickup as cu

    task = {"name": "Site inspection", "url": "", "status": "open", "assignees": [], "description": ""}
    body = {"event": "taskCreated", "task_id": "t1"}
    with (
        patch("vula.clickup.service.get_task", new=AsyncMock(return_value=task)),
        patch("vula.integrations.notify.notify_team", new=AsyncMock()) as notify,
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", side_effect=RuntimeError("qdrant down")),
    ):
        out = await cu._handle_non_status_event(TID, "taskCreated", "t1", body)

    assert out["status"] == "ok" and out["notified"] is True
    notify.assert_awaited_once()

"""Tests for ClickUp Docs ingestion (2026-09-18, E2) — a structurally separate API surface (v3)
from everything else in vula/clickup/service.py (v2: tasks/comments/lists/webhooks). Extends
sync_tenant_clickup_kb (already wired into the scheduled KB sync loop, see
test_clickup_kb_sync.py) to also sweep every Doc's every page.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TID = "digg-demo"
CREDS = {"token": "tok123", "team_id": "team1", "list_ids": {}}


def _mock_response(json_data, status_ok=True):
    resp = MagicMock()
    if status_ok:
        resp.raise_for_status = MagicMock()
    else:
        resp.raise_for_status = MagicMock(side_effect=Exception("HTTP error"))
    resp.json.return_value = json_data
    return resp


def _mock_async_client(get_return):
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=get_return)
    return mock_client


# ── list_docs ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_docs_returns_parsed_docs():
    from vula.clickup.service import list_docs
    resp = _mock_response({"docs": [{"id": "d1", "name": "Project Handbook"}, {"id": "d2", "name": "Onboarding"}]})
    with (
        patch("vula.clickup.service._creds_or_raise", return_value=CREDS),
        patch("vula.clickup.service.httpx.AsyncClient", return_value=_mock_async_client(resp)),
    ):
        docs = await list_docs(TID)
    assert docs == [{"id": "d1", "name": "Project Handbook"}, {"id": "d2", "name": "Onboarding"}]


@pytest.mark.asyncio
async def test_list_docs_handles_bare_list_response_shape():
    """ClickUp v3 responses sometimes wrap in a key, sometimes return a bare list — handle both."""
    from vula.clickup.service import list_docs
    resp = _mock_response([{"id": "d1", "name": "Handbook"}])
    with (
        patch("vula.clickup.service._creds_or_raise", return_value=CREDS),
        patch("vula.clickup.service.httpx.AsyncClient", return_value=_mock_async_client(resp)),
    ):
        docs = await list_docs(TID)
    assert docs == [{"id": "d1", "name": "Handbook"}]


@pytest.mark.asyncio
async def test_list_docs_not_connected_returns_empty_not_raise():
    from vula.clickup.service import list_docs
    with patch("vula.clickup.service._creds_or_raise", side_effect=Exception("not connected")):
        docs = await list_docs(TID)
    assert docs == []


@pytest.mark.asyncio
async def test_list_docs_no_team_id_returns_empty():
    from vula.clickup.service import list_docs
    with patch("vula.clickup.service._creds_or_raise", return_value={"token": "t"}):
        docs = await list_docs(TID)
    assert docs == []


@pytest.mark.asyncio
async def test_list_docs_api_failure_returns_empty_not_raise():
    from vula.clickup.service import list_docs
    with (
        patch("vula.clickup.service._creds_or_raise", return_value=CREDS),
        patch("vula.clickup.service.httpx.AsyncClient", side_effect=RuntimeError("network down")),
    ):
        docs = await list_docs(TID)
    assert docs == []


# ── list_pages ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_pages_returns_content():
    from vula.clickup.service import list_pages
    resp = _mock_response([{"id": "p1", "name": "Overview", "content": "This project covers..."}])
    with (
        patch("vula.clickup.service._creds_or_raise", return_value=CREDS),
        patch("vula.clickup.service.httpx.AsyncClient", return_value=_mock_async_client(resp)) as mock_ac,
    ):
        pages = await list_pages(TID, "d1")
    assert pages == [{"id": "p1", "name": "Overview", "content": "This project covers..."}]
    # content_format is requested so content comes back in the SAME call (no N+1 per page).
    call = mock_ac.return_value.get.call_args
    assert call.kwargs["params"]["content_format"] == "text/md"


@pytest.mark.asyncio
async def test_list_pages_failure_returns_empty():
    from vula.clickup.service import list_pages
    with patch("vula.clickup.service._creds_or_raise", side_effect=Exception("not connected")):
        pages = await list_pages(TID, "d1")
    assert pages == []


# ── get_page_content ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_page_content_returns_the_text():
    from vula.clickup.service import get_page_content
    resp = _mock_response({"content": "Page body text."})
    with (
        patch("vula.clickup.service._creds_or_raise", return_value=CREDS),
        patch("vula.clickup.service.httpx.AsyncClient", return_value=_mock_async_client(resp)),
    ):
        content = await get_page_content(TID, "d1", "p1")
    assert content == "Page body text."


@pytest.mark.asyncio
async def test_get_page_content_failure_returns_none():
    from vula.clickup.service import get_page_content
    with patch("vula.clickup.service._creds_or_raise", side_effect=Exception("not connected")):
        content = await get_page_content(TID, "d1", "p1")
    assert content is None


# ── sync_tenant_clickup_kb: Docs half ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_ingests_every_doc_page_with_content():
    from vula.api.clickup import sync_tenant_clickup_kb

    docs = [{"id": "d1", "name": "Handbook"}]
    pages = [{"id": "p1", "name": "Intro", "content": "Welcome to the handbook."},
             {"id": "p2", "name": "Empty page", "content": ""}]
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=MagicMock(chunks_stored=2))

    with (
        patch("vula.api.clickup.get_tenant_clickup_creds", return_value={"list_ids": {}}),
        patch("vula.clickup.service.list_docs", new=AsyncMock(return_value=docs)),
        patch("vula.clickup.service.list_pages", new=AsyncMock(return_value=pages)),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        res = await sync_tenant_clickup_kb(TID)

    assert res["synced_doc_pages"] == 1  # only the page WITH content
    assert res["doc_chunks_added"] == 2
    mock_pipeline.ingest_text.assert_awaited_once()
    assert mock_pipeline.ingest_text.call_args.kwargs["doc_id"] == "clickup_doc_d1_p1"
    assert "Welcome to the handbook." in mock_pipeline.ingest_text.call_args.kwargs["content"]


@pytest.mark.asyncio
async def test_sync_skips_pages_with_no_content():
    from vula.api.clickup import sync_tenant_clickup_kb

    docs = [{"id": "d1", "name": "Handbook"}]
    pages = [{"id": "p1", "name": "Empty", "content": ""}, {"id": "p2", "name": "Blank", "content": "   "}]
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_text = AsyncMock()

    with (
        patch("vula.api.clickup.get_tenant_clickup_creds", return_value={"list_ids": {}}),
        patch("vula.clickup.service.list_docs", new=AsyncMock(return_value=docs)),
        patch("vula.clickup.service.list_pages", new=AsyncMock(return_value=pages)),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        res = await sync_tenant_clickup_kb(TID)

    assert res["synced_doc_pages"] == 0
    mock_pipeline.ingest_text.assert_not_called()


@pytest.mark.asyncio
async def test_sync_no_docs_at_all_is_not_an_error():
    from vula.api.clickup import sync_tenant_clickup_kb

    with (
        patch("vula.api.clickup.get_tenant_clickup_creds", return_value={"list_ids": {}}),
        patch("vula.clickup.service.list_docs", new=AsyncMock(return_value=[])),
    ):
        res = await sync_tenant_clickup_kb(TID)

    assert "error" not in res
    assert res["synced_doc_pages"] == 0


@pytest.mark.asyncio
async def test_docs_sync_failure_never_blocks_task_sync():
    """The two are independent — a Docs API outage must never stop tasks from syncing."""
    from vula.api.clickup import sync_tenant_clickup_kb

    creds = {"list_ids": {"list1": "HPC Bokaap"}}
    tasks = [{"title": "Pour foundations", "status": "open", "due_date": None, "assignees": []}]
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=MagicMock(chunks_stored=1))

    with (
        patch("vula.api.clickup.get_tenant_clickup_creds", return_value=creds),
        patch("vula.clickup.service.list_tasks", new=AsyncMock(return_value=tasks)),
        patch("vula.clickup.service.list_docs", new=AsyncMock(side_effect=RuntimeError("docs api down"))),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        res = await sync_tenant_clickup_kb(TID)

    assert res["synced_lists"] == 1
    assert res["synced_doc_pages"] == 0

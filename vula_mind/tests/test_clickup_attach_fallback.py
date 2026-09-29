"""2026-09-29 (DIGG): every attach into ATLANTIS FOODS failed "401 Unauthorized" on task
869e0tmnt while the same ClickUp token synced fine — the remembered documents task was gone or
private. A refused remembered task now falls back to a fresh documents task in the project's list."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from vula.clickup import service


class _Client:
    calls = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **_k):
        _Client.calls.append(url)
        req = httpx.Request("POST", url)
        if "869e0tmnt" in url:
            return httpx.Response(401, request=req, json={"err": "Team not authorized"})
        return httpx.Response(200, request=req, json={"id": "att-1"})


@pytest.mark.asyncio
async def test_a_refused_documents_task_is_replaced_not_reported_as_a_dead_connection(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(service, "_creds_or_raise", lambda _t: {"token": "tok"})
    monkeypatch.setattr(service.httpx, "AsyncClient", _Client)
    with patch.object(service, "find_task", AsyncMock(return_value=None)), \
         patch.object(service, "create_task", AsyncMock(return_value={"id": "newtask"})) as create:
        got = await service.attach_file_to_list("digg-demo", "L-atlantis", "rev10.pdf", b"%PDF",
                                                "application/pdf", task_id="869e0tmnt")
    assert got == {"task_id": "newtask", "attachment_id": "att-1"}
    create.assert_awaited_once()
    assert _Client.calls[-1].endswith("/task/newtask/attachment")


@pytest.mark.asyncio
async def test_a_refusal_with_no_list_to_fall_back_to_still_raises(monkeypatch):
    monkeypatch.setattr(service, "_creds_or_raise", lambda _t: {"token": "tok"})
    monkeypatch.setattr(service.httpx, "AsyncClient", _Client)
    with pytest.raises(httpx.HTTPStatusError):
        await service.attach_file_to_list("digg-demo", "", "x.pdf", b"x", task_id="869e0tmnt")

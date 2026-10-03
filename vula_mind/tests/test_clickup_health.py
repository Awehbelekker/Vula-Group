"""ClickUp's sign-in is checked, and a lost one is said out loud (2026-10-03, Ian/DIGG).

From 1 Aug no filed document reached ClickUp (0 of 129, against 78 of 85 in July) and the
assistant's list_tasks got 401 on team 90121524370 — Vula's stored token had lost the workspace.
Every failure was a log warning and the KB sync recorded "ok" (a refused list read as an empty
one), so the dashboard said "Connected" the whole time.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from vula.clickup import service

TID = "digg-demo"
TEAM = "90121524370"


def _client_returning(status, body=None, exc=None):
    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **_k):
            if exc:
                raise exc
            return httpx.Response(status, request=httpx.Request("GET", url), json=body or {})
    return _C


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,exc,ok", [
    (401, {"err": "Token invalid", "ECODE": "OAUTH_025"}, None, False),
    (200, {"teams": [{"id": "999", "name": "Someone else"}]}, None, False),   # workspace gone
    (200, {"teams": [{"id": TEAM, "name": "Workspace"}]}, None, True),
    (503, {}, None, None),                                                     # ClickUp down
    (0, None, httpx.ConnectError("boom"), None),                               # network
])
async def test_only_clickups_own_refusal_counts_as_a_lost_sign_in(monkeypatch, status, body, exc, ok):
    monkeypatch.setattr(service.httpx, "AsyncClient", _client_returning(status, body, exc))
    res = await service.check_access(TID, token="tok", team_id=TEAM)
    assert res["ok"] is ok


def test_a_lost_sign_in_is_flagged_and_the_owner_told(monkeypatch):
    writes = []
    table = MagicMock()
    table.update.side_effect = lambda patch_: writes.append(patch_) or table
    table.eq.return_value = table
    monkeypatch.setattr(service, "_client", lambda: MagicMock(table=lambda _n: table))
    with patch("vula.clickup.credentials.invalidate") as inv:
        service.mark_needs_reconnect(TID, "ClickUp refused Vula's sign-in (401)")
    assert writes[0]["status"] == "needs_reconnect" and writes[0]["last_sync_status"] == "error"
    assert "401" in writes[0]["last_sync_error"]
    inv.assert_called_once_with(TID)


@pytest.mark.asyncio
async def test_a_refused_attach_checks_the_sign_in(monkeypatch):
    from vula.integrations import doc_filing
    req = httpx.Request("POST", "https://api.clickup.com/api/v2/task/869e0tmnt/attachment")
    err = httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))
    monkeypatch.setattr(doc_filing, "_existing_project_task", lambda *_a: None)
    monkeypatch.setattr(doc_filing, "_canonical_list_for_project", lambda *_a: "901218996719")
    with patch.object(service, "attach_file_to_list", AsyncMock(side_effect=err)), \
         patch.object(service, "verify_or_flag", AsyncMock(return_value=False)) as vf:
        out = await doc_filing.attach_into_project(TID, "HPC Bokaap", None, "a.pdf", b"x", "application/pdf")
    assert out["clickup_task_id"] is None
    vf.assert_awaited_once_with(TID)


@pytest.mark.asyncio
async def test_the_sync_no_longer_reports_ok_on_a_dead_sign_in():
    from vula.api.clickup import sync_tenant_clickup_kb
    creds = {"token": "tok", "team_id": TEAM, "list_ids": {"901218996719": "Atlantis"}}
    with patch("vula.api.clickup.get_tenant_clickup_creds", return_value=creds), \
         patch.object(service, "check_access", AsyncMock(return_value={"ok": False, "reason": "refused (401)"})), \
         patch.object(service, "mark_needs_reconnect") as mark, \
         patch.object(service, "list_tasks", AsyncMock()) as lt:
        res = await sync_tenant_clickup_kb(TID)
    assert res["needs_reconnect"] and "401" in res["error"]
    mark.assert_called_once()
    lt.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_bad_personal_token_is_refused_before_it_is_stored():
    from fastapi import HTTPException
    from vula.api import clickup as api
    with patch.object(service, "check_access", AsyncMock(return_value={"ok": False, "reason": "refused (401)"})), \
         patch.object(api, "_store_connection") as store:
        with pytest.raises(HTTPException) as e:
            await api.connect(api.ConnectIn(tenant_id=TID, api_token="pk_bad"))
    assert e.value.status_code == 400 and "401" in e.value.detail
    store.assert_not_called()


@pytest.mark.asyncio
async def test_a_good_token_finds_the_workspace_and_sends_missed_documents():
    from vula.api import clickup as api
    info = {"team_id": TEAM, "team_name": "Workspace", "lists": [{"id": "901218996719", "name": "Atlantis"}],
            "default": "901218996719"}
    with patch.object(service, "check_access", AsyncMock(return_value={"ok": True})), \
         patch.object(service, "discover_team_and_lists", AsyncMock(return_value=info)), \
         patch.object(api, "_store_connection") as store, \
         patch.object(api, "_register_webhook", AsyncMock(return_value=True)), \
         patch.object(api, "_start_refile") as refile:
        out = await api.connect(api.ConnectIn(tenant_id=TID, api_token=" pk_good "))
    args = store.call_args.args
    assert args[1] == "pk_good" and args[2] == TEAM and args[4] == "901218996719"
    refile.assert_called_once_with(TID)
    assert out["workspace"] == "Workspace" and out["webhook_registered"]


@pytest.mark.asyncio
async def test_missed_documents_are_sent_and_recorded(monkeypatch):
    rows = [{"id": "d1", "project": "HPC Bokaap", "clickup_list_id": "901217344951",
             "filename": "EOT.pdf", "file_url": "u1", "mime": "application/pdf"},
            {"id": "d2", "project": "HPC Bokaap", "clickup_list_id": "901217344951",
             "filename": "PC.pdf", "file_url": "u2", "mime": "application/pdf"}]
    updates = []

    class _Q:
        def __getattr__(self, _n):
            return lambda *a, **k: self

        @property
        def not_(self):
            return self

        def update(self, patch_):
            updates.append(patch_)
            return self

        def execute(self):
            return type("R", (), {"data": rows})()

    monkeypatch.setattr(service, "_client", lambda: MagicMock(table=lambda _n: _Q()))
    with patch("vula.storage_links.fetch", AsyncMock(return_value=b"%PDF")), \
         patch("vula.integrations.doc_filing.attach_into_project",
               AsyncMock(side_effect=[{"clickup_task_id": "t9", "clickup_list_id": "901217344951"},
                                      {"clickup_task_id": None}])), \
         patch.object(service, "verify_or_flag", AsyncMock(return_value=None)):
        res = await service.refile_missing(TID)
    assert res == {"tenant_id": TID, "candidates": 2, "sent": 1, "failed": 1}
    assert updates == [{"clickup_task_id": "t9", "clickup_list_id": "901217344951"}]

"""Id-only routes scope records to the tenant require_auth authorized — however the request
named it. Code review 2026-09-27: a member of tenant A could name A in the body (passing the
auth check), omit ?tenant_id, and reach tenant B's records because the routes only scoped by
the query value; assign-project had the mirror gap (query named, body scoped)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from vula.api import server

MEMBER_A = {"Authorization": "Bearer member-a"}


async def _member_of_a(auth_header, tenant):
    return auth_header == "Bearer member-a" and tenant == "tenant-a"


@pytest.fixture()
def client(monkeypatch):
    from vula.api import master_auth
    monkeypatch.setattr(master_auth.settings, "api_key", "server-key")
    with patch.object(master_auth, "require_master", new=AsyncMock(side_effect=HTTPException(403))), \
         patch("vula.api.tenant_auth.is_tenant_member", new=_member_of_a):
        yield TestClient(server.app, raise_server_exceptions=False)


def test_field_task_of_another_tenant_via_body_tenant_is_not_found(client):
    db = MagicMock()
    db.get_task.return_value = SimpleNamespace(id="t1", tenant_id="tenant-b", assigned_to="c1", title="x")
    with patch("vula.api.field_ops.get_field_ops_db", return_value=db), \
         patch("vula.api.field_ops._send_wa_template", new=AsyncMock()) as send:
        r = client.post("/v1/field/task/t1/complete-request", json={"tenant_id": "tenant-a", "task_id": "t1"}, headers=MEMBER_A)
    assert r.status_code == 404
    send.assert_not_awaited()


def test_takeoff_job_of_another_tenant_via_body_tenant_is_not_found(client):
    from vula.takeoff import api as takeoff
    takeoff._jobs["jb"] = {"status": "complete", "tenant_id": "tenant-b", "orders": {"count": 1}}
    try:
        r = client.post("/takeoff/jb/send", json={"dry_run": True, "tenant_id": "tenant-a"}, headers=MEMBER_A)
    finally:
        takeoff._jobs.pop("jb", None)
    assert r.status_code == 404


def test_assign_project_of_another_tenants_doc_via_query_tenant_is_refused(client):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": "d1", "tenant_id": "tenant-b", "file_url": None}]
    with patch("vula.api.documents._client", return_value=db):
        r = client.post("/v1/documents/d1/assign-project?tenant_id=tenant-a",
                        json={"project": "Bokaap"}, headers=MEMBER_A)
    assert r.status_code == 200 and r.json() == {"error": "Document not found."}
    db.table.return_value.update.assert_not_called()


def test_own_tenant_record_is_still_reachable(client):
    db = MagicMock()
    db.get_task.return_value = SimpleNamespace(
        id="t1", tenant_id="tenant-a", assigned_to=None, title="x", trade="", status="open",
        project_id="p", due_date=None, notes="", created_at="", updated_at="")
    db.get_evidence.return_value, db.get_sign_off.return_value = [], None
    with patch("vula.api.field_ops.get_field_ops_db", return_value=db):
        r = client.get("/v1/field/task/t1?tenant_id=tenant-a", headers=MEMBER_A)
    assert r.status_code == 200 and r.json()["id"] == "t1"

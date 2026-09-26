"""require_auth accepts a signed-in member of the request's own tenant (2026-09-25).

The dashboard used to reach /v1/draft, /v1/agent, /ingest, /query and /documents by shipping
the shared server API key in its browser bundle (VITE_API_KEY). Now the user's session JWT is
enough — but only for the tenant that user belongs to, and only when every tenant_id the
request carries (path, query, body) names that same tenant.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient


async def _member_of_a(auth_header, tenant):
    return auth_header == "Bearer member-a" and tenant == "tenant-a"


@pytest.fixture()
def client(monkeypatch):
    from vula.api import master_auth
    monkeypatch.setattr(master_auth.settings, "api_key", "server-key")
    app = FastAPI()

    @app.post("/body", dependencies=[Depends(master_auth.require_auth)])
    async def body_route(payload: dict):
        return {"tenant": payload.get("tenant_id")}

    @app.get("/docs/{tenant_id}", dependencies=[Depends(master_auth.require_auth)])
    async def path_route(tenant_id: str):
        return {"tenant": tenant_id}

    @app.get("/global", dependencies=[Depends(master_auth.require_auth)])
    async def global_route():
        return {"ok": True}

    with patch.object(master_auth, "require_master",
                      new=AsyncMock(side_effect=HTTPException(status_code=403))), \
         patch("vula.api.tenant_auth.is_tenant_member", new=_member_of_a):
        yield TestClient(app)


MEMBER = {"Authorization": "Bearer member-a"}


def test_member_passes_for_own_tenant_in_path(client):
    assert client.get("/docs/tenant-a", headers=MEMBER).status_code == 200


def test_member_passes_for_own_tenant_in_json_body(client):
    r = client.post("/body", json={"tenant_id": "tenant-a"}, headers=MEMBER)
    assert r.status_code == 200 and r.json() == {"tenant": "tenant-a"}   # body still readable


def test_member_refused_for_another_tenant(client):
    assert client.get("/docs/tenant-b", headers=MEMBER).status_code == 401
    assert client.post("/body", json={"tenant_id": "tenant-b"}, headers=MEMBER).status_code == 401


def test_query_tenant_cannot_smuggle_a_different_body_tenant(client):
    r = client.post("/body?tenant_id=tenant-a", json={"tenant_id": "tenant-b"}, headers=MEMBER)
    assert r.status_code == 401


def test_member_cannot_reach_routes_with_no_tenant(client):
    assert client.get("/global", headers=MEMBER).status_code == 401


def test_api_key_still_works_and_nothing_else_does(client):
    assert client.get("/global", headers={"X-API-Key": "server-key"}).status_code == 200
    assert client.get("/docs/tenant-a").status_code == 401
    assert client.get("/docs/tenant-a", headers={"Authorization": "Bearer stranger"}).status_code == 401


def test_draft_by_id_is_scoped_to_the_member_tenant(monkeypatch):
    from vula.api import draft
    monkeypatch.setattr(draft._store, "get",
                        lambda did: {"id": did, "tenant_id": "tenant-a", "doc_type": "letter"})
    assert (asyncio.run(draft.get_draft("d1", tenant_id="tenant-a"))["id"]) == "d1"
    with pytest.raises(HTTPException) as e:
        asyncio.run(draft.get_draft("d1", tenant_id="tenant-b"))
    assert e.value.status_code == 404

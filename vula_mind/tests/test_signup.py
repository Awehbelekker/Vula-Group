"""Tests for the self-serve signup API (vula/api/signup.py) — the real fix for the onboarding
gap found 2026-08-15: creating the FIRST vula_tenant_users row for a brand-new tenant was
previously master-only or required already being a tenant member. This lets any authenticated
Supabase user create exactly one tenant they own.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vula.api.server import app

client = TestClient(app)

VALID_USER = {"id": "user-abc-123", "email": "new-owner@example.com"}


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Chainable table mock. `matches` maps a table name -> list[dict] to return on select();
    insert()/delete() calls are recorded on `calls` for assertion, never mutate `matches`."""

    def __init__(self, table_name, tables, calls, fail_on_insert=None):
        self.table_name = table_name
        self.tables = tables
        self.calls = calls
        self.fail_on_insert = fail_on_insert or set()
        self._is_delete = False

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def insert(self, payload):
        self.calls.append((self.table_name, "insert", payload))
        if self.table_name in self.fail_on_insert:
            def _raise():
                raise RuntimeError(f"insert failed for {self.table_name}")
            return type("Boom", (), {"execute": staticmethod(_raise)})()
        return self

    def delete(self):
        self._is_delete = True
        return self

    def execute(self):
        if self._is_delete:
            self.calls.append((self.table_name, "delete", None))
            return _Result([])
        return _Result(self.tables.get(self.table_name, []))


class _FakeClient:
    def __init__(self, tables=None, fail_on_insert=None):
        self.tables = tables or {}
        self.calls = []
        self.fail_on_insert = fail_on_insert or set()

    def table(self, name):
        return _FakeQuery(name, self.tables, self.calls, self.fail_on_insert)


def _auth_headers():
    return {"Authorization": "Bearer faketoken"}


# ── Auth gate ──────────────────────────────────────────────────────────────────

def test_signup_requires_bearer_token():
    resp = client.post("/v1/signup", json={"tenant_id": "my-shop"})
    assert resp.status_code == 401


def test_signup_rejects_invalid_token():
    with patch("vula.api.master_auth._verify_jwt", new=AsyncMock(return_value=None)):
        resp = client.post("/v1/signup", headers=_auth_headers(),
                           json={"tenant_id": "my-shop"})
    assert resp.status_code == 401


# ── Happy path ───────────────────────────────────────────────────────────────────

def test_signup_creates_tenant_and_owner_login():
    fake_client = _FakeClient(tables={"vula_tenant_config": [], "vula_tenant_users": []})
    with (
        patch("vula.api.master_auth._verify_jwt", new=AsyncMock(return_value=VALID_USER)),
        patch("vula.api.signup._client", return_value=fake_client),
    ):
        resp = client.post("/v1/signup", headers=_auth_headers(), json={
            "tenant_id": "My New Shop", "display_name": "My New Shop",
            "business_type": "retail",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant"]["tenant_id"] == "my-new-shop"  # server-side slugified
    assert data["role"] == "owner"

    inserts = [c for c in fake_client.calls if c[1] == "insert"]
    assert inserts[0][0] == "vula_tenant_config"
    assert inserts[0][2]["tenant_id"] == "my-new-shop"
    assert "products" in inserts[0][2]["modules"]  # retail preset
    assert inserts[1][0] == "vula_tenant_users"
    assert inserts[1][2] == {"user_id": "user-abc-123", "tenant_id": "my-new-shop", "role": "owner"}


def test_signup_defaults_business_type_to_other():
    fake_client = _FakeClient(tables={"vula_tenant_config": [], "vula_tenant_users": []})
    with (
        patch("vula.api.master_auth._verify_jwt", new=AsyncMock(return_value=VALID_USER)),
        patch("vula.api.signup._client", return_value=fake_client),
    ):
        resp = client.post("/v1/signup", headers=_auth_headers(), json={"tenant_id": "no-type-shop"})
    assert resp.status_code == 200
    config_insert = next(c for c in fake_client.calls if c[0] == "vula_tenant_config")
    assert config_insert[2]["business_type"] == "other"


# ── Collisions / caps ────────────────────────────────────────────────────────────

def test_signup_rejects_taken_slug():
    fake_client = _FakeClient(tables={"vula_tenant_config": [{"tenant_id": "taken-shop"}]})
    with (
        patch("vula.api.master_auth._verify_jwt", new=AsyncMock(return_value=VALID_USER)),
        patch("vula.api.signup._client", return_value=fake_client),
    ):
        resp = client.post("/v1/signup", headers=_auth_headers(), json={"tenant_id": "Taken Shop"})
    assert resp.status_code == 409
    assert not [c for c in fake_client.calls if c[1] == "insert"]


def test_signup_rejects_second_tenant_for_same_user():
    fake_client = _FakeClient(tables={
        "vula_tenant_config": [],
        "vula_tenant_users": [{"tenant_id": "already-own-this"}],
    })
    with (
        patch("vula.api.master_auth._verify_jwt", new=AsyncMock(return_value=VALID_USER)),
        patch("vula.api.signup._client", return_value=fake_client),
    ):
        resp = client.post("/v1/signup", headers=_auth_headers(), json={"tenant_id": "second-shop"})
    assert resp.status_code == 409
    assert "already has a workspace" in resp.json()["detail"]


def test_signup_rejects_empty_slug():
    fake_client = _FakeClient(tables={"vula_tenant_config": [], "vula_tenant_users": []})
    with (
        patch("vula.api.master_auth._verify_jwt", new=AsyncMock(return_value=VALID_USER)),
        patch("vula.api.signup._client", return_value=fake_client),
    ):
        resp = client.post("/v1/signup", headers=_auth_headers(), json={"tenant_id": "!!!"})
    assert resp.status_code == 400


# ── Compensating delete on partial failure ──────────────────────────────────────

def test_signup_rolls_back_tenant_config_if_user_insert_fails():
    fake_client = _FakeClient(
        tables={"vula_tenant_config": [], "vula_tenant_users": []},
        fail_on_insert={"vula_tenant_users"},
    )
    with (
        patch("vula.api.master_auth._verify_jwt", new=AsyncMock(return_value=VALID_USER)),
        patch("vula.api.signup._client", return_value=fake_client),
    ):
        resp = client.post("/v1/signup", headers=_auth_headers(), json={"tenant_id": "doomed-shop"})
    assert resp.status_code == 500
    kinds = [(c[0], c[1]) for c in fake_client.calls]
    assert ("vula_tenant_config", "insert") in kinds
    assert ("vula_tenant_users", "insert") in kinds
    assert ("vula_tenant_config", "delete") in kinds  # rolled back the orphaned row

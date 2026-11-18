"""Two-step sign-in (18 Nov 2026, pre-launch audit: 0 of 15 logins had it). A login with an
authenticator app set up is refused by every route until its session gave the code (Supabase
aal2), so a stolen password alone opens nothing; roles in MFA_REQUIRED_ROLES must set one up
before they get in; the login screen can ask which roles those are."""
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from config import settings
from vula.api import master_auth


def _token(aal):
    def part(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'sub': 'u1', 'aal': aal})}.sig"


def _supabase_says(user):
    resp = MagicMock(status_code=200)
    resp.json.return_value = user
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=resp)
    return patch.object(master_auth.httpx, "AsyncClient", return_value=client)


WITH_APP = {"id": "u1", "email": "ian@example.com", "factors": [{"id": "f1", "status": "verified"}]}
NO_APP = {"id": "u1", "email": "ian@example.com", "factors": []}
HALF_SET_UP = {"id": "u1", "email": "ian@example.com", "factors": [{"id": "f1", "status": "unverified"}]}


@pytest.mark.asyncio
async def test_a_login_with_an_app_is_refused_without_its_code():
    with _supabase_says(WITH_APP):
        assert await master_auth._verify_jwt(_token("aal1")) is None


@pytest.mark.asyncio
async def test_a_login_with_an_app_gets_in_once_it_gave_the_code():
    with _supabase_says(WITH_APP):
        assert (await master_auth._verify_jwt(_token("aal2")))["id"] == "u1"


@pytest.mark.asyncio
async def test_a_login_without_an_app_is_unchanged_while_nobody_is_made_to(monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_roles", "")
    with _supabase_says(NO_APP):
        assert (await master_auth._verify_jwt(_token("aal1")))["id"] == "u1"
    with _supabase_says(HALF_SET_UP):           # started but never confirmed: not set up
        assert (await master_auth._verify_jwt(_token("aal1")))["id"] == "u1"


@pytest.mark.asyncio
@pytest.mark.parametrize("role,let_in", [("master", False), ("owner", False), ("staff", True)])
async def test_required_roles_must_set_it_up_first(monkeypatch, role, let_in):
    monkeypatch.setattr(settings, "mfa_required_roles", "master, Owner")
    monkeypatch.setattr(master_auth, "_role_for_user", lambda uid: role)
    with _supabase_says(NO_APP):
        got = await master_auth._verify_jwt(_token("aal1"))
    assert (got is not None) == let_in


@pytest.mark.asyncio
async def test_a_required_role_with_an_app_still_needs_the_code(monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_roles", "master")
    monkeypatch.setattr(master_auth, "_role_for_user", lambda uid: "master")
    with _supabase_says(WITH_APP):
        assert await master_auth._verify_jwt(_token("aal1")) is None
        assert (await master_auth._verify_jwt(_token("aal2")))["id"] == "u1"


@pytest.mark.asyncio
async def test_a_master_route_refuses_a_session_without_the_code(monkeypatch):
    from fastapi import HTTPException
    master_auth._CACHE.clear()
    monkeypatch.setattr(master_auth, "_role_for_user", lambda uid: "master")
    with _supabase_says(WITH_APP), pytest.raises(HTTPException) as e:
        await master_auth.require_master(f"Bearer {_token('aal1')}")
    assert e.value.status_code == 401


def test_an_unreadable_token_never_counts_as_two_step():
    assert master_auth._token_claims("not-a-jwt") == {}
    assert master_auth.two_step_missing(WITH_APP, "not-a-jwt") == "code"


def test_the_login_screen_can_ask_which_roles_must_set_it_up(monkeypatch):
    from vula.api.server import app
    monkeypatch.setattr(settings, "mfa_required_roles", "owner,master")
    r = TestClient(app).get("/v1/auth/two-step-policy")
    assert r.status_code == 200 and r.json() == {"required_roles": ["master", "owner"]}

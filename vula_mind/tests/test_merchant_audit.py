"""Tests for the merchant-level audit trail (migration 107) — the tenant-scoped counterpart
to master's vula_admin_audit, closing the "role changes, access edits, and login management
leave no trail an owner can query" gap from the 2026-07-24 admin-depth audit. Covers
vula.api.merchant_audit, the tenant_auth.require_tenant_actor dependency, the write sites in
team.py/users.py, and the master.py direct-call sites that had to be updated to pass identity
through explicitly (they call these functions as plain Python, bypassing FastAPI's DI, so a
Depends() default would otherwise reach merchant_audit.audit() as a non-dict and blow up)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api import merchant_audit
from vula.api.tenant_auth import require_tenant_actor


# ── merchant_audit.audit / list_audit ───────────────────────────────────────────

def test_audit_writes_expected_row():
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.api.merchant_audit._client", return_value=mock_db):
        merchant_audit.audit("off-the-hook", {"user_id": "u1", "email": "owner@shop.com"},
                             "member_added", name="Jane")
    mock_table.insert.assert_called_once()
    row = mock_table.insert.call_args[0][0]
    assert row["tenant_id"] == "off-the-hook"
    assert row["actor_email"] == "owner@shop.com"
    assert row["actor_id"] == "u1"
    assert row["action"] == "member_added"
    assert row["detail"] == {"name": "Jane"}


def test_audit_never_raises_on_db_error():
    with patch("vula.api.merchant_audit._client", side_effect=RuntimeError("down")):
        merchant_audit.audit("off-the-hook", {"email": "a@b.com"}, "member_added")


def test_audit_handles_none_actor():
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.api.merchant_audit._client", return_value=mock_db):
        merchant_audit.audit("off-the-hook", None, "member_added")
    row = mock_table.insert.call_args[0][0]
    assert row["actor_email"] is None and row["actor_id"] is None


def test_list_audit_returns_rows():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[{"id": 1, "action": "member_added"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.api.merchant_audit._client", return_value=mock_db):
        result = merchant_audit.list_audit("off-the-hook")
    assert result == [{"id": 1, "action": "member_added"}]


def test_list_audit_returns_empty_on_error():
    with patch("vula.api.merchant_audit._client", side_effect=RuntimeError("down")):
        assert merchant_audit.list_audit("off-the-hook") == []


# ── tenant_auth.require_tenant_actor ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_require_tenant_actor_returns_identity_for_member():
    import vula.api.tenant_auth as ta
    ta._CACHE.clear(); ta._IDENTITY_CACHE.clear()
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[{"tenant_id": "off-the-hook", "role": "owner"}])
    with (
        patch("vula.api.master_auth._verify_jwt", new=AsyncMock(
            return_value={"id": "u1", "email": "owner@shop.com"})),
        patch("vula.commerce.service._client", return_value=mock_db),
        patch("vula.api.tenants.is_active", return_value=True),
    ):
        identity = await require_tenant_actor("off-the-hook", "Bearer tok")
    assert identity == {"user_id": "u1", "email": "owner@shop.com"}


@pytest.mark.asyncio
async def test_require_tenant_actor_401_without_token():
    with pytest.raises(Exception) as exc:
        await require_tenant_actor("off-the-hook", "")
    assert getattr(exc.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_require_tenant_actor_403_for_non_member():
    import vula.api.tenant_auth as ta
    ta._CACHE.clear(); ta._IDENTITY_CACHE.clear()
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[])
    with (
        patch("vula.api.master_auth._verify_jwt", new=AsyncMock(
            return_value={"id": "u2", "email": "x@y.com"})),
        patch("vula.commerce.service._client", return_value=mock_db),
    ):
        with pytest.raises(Exception) as exc:
            await require_tenant_actor("off-the-hook", "Bearer tok")
    assert getattr(exc.value, "status_code", None) == 403


# ── team.py write endpoints ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_member_writes_audit_row():
    from vula.api.team import add_member, MemberIn
    mock_table = MagicMock()
    mock_table.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "m1"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.api.team._client", return_value=mock_db),
        patch("vula.api.team.merchant_audit.audit") as mock_audit,
    ):
        await add_member("off-the-hook", MemberIn(name="Jane", role="staff"),
                         identity={"user_id": "u1", "email": "owner@shop.com"})
    mock_audit.assert_called_once()
    args = mock_audit.call_args[0]
    assert args[0] == "off-the-hook" and args[2] == "member_added"


@pytest.mark.asyncio
async def test_update_member_writes_audit_row():
    from vula.api.team import update_member, MemberPatch
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.api.team._client", return_value=mock_db),
        patch("vula.api.team.merchant_audit.audit") as mock_audit,
    ):
        await update_member("off-the-hook", "m1", MemberPatch(role="manager"),
                            identity={"user_id": "u1", "email": "owner@shop.com"})
    mock_audit.assert_called_once()
    assert mock_audit.call_args[0][2] == "member_updated"


@pytest.mark.asyncio
async def test_remove_member_writes_audit_row():
    from vula.api.team import remove_member
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.api.team._client", return_value=mock_db),
        patch("vula.api.team.merchant_audit.audit") as mock_audit,
    ):
        result = await remove_member("off-the-hook", "m1",
                                     identity={"user_id": "u1", "email": "owner@shop.com"})
    assert result == {"id": "m1", "removed": True}
    mock_audit.assert_called_once()
    assert mock_audit.call_args[0][2] == "member_removed"


@pytest.mark.asyncio
async def test_audit_log_endpoint_returns_events():
    from vula.api.team import audit_log
    with patch("vula.api.team.merchant_audit.list_audit", return_value=[{"id": 1}]) as mock_list:
        result = await audit_log("off-the-hook", 100, identity={"user_id": "u1", "email": "a@b.com"})
    assert result == {"events": [{"id": 1}]}
    mock_list.assert_called_once_with("off-the-hook", 100)


# ── users.py write endpoints ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_user_writes_audit_row():
    from vula.api.users import create_user, CreateUserIn
    mock_resp = MagicMock(status_code=201)
    mock_resp.json.return_value = {"id": "u9"}
    with (
        patch("vula.api.users._auth_admin", new=AsyncMock(return_value=mock_resp)),
        patch("vula.api.users._map_tenant_user"),
        patch("vula.api.users.merchant_audit.audit") as mock_audit,
    ):
        result = await create_user("off-the-hook", CreateUserIn(email="staff@shop.com", role="staff"),
                                   identity={"user_id": "u1", "email": "owner@shop.com"})
    assert result["email"] == "staff@shop.com"
    mock_audit.assert_called_once()
    assert mock_audit.call_args[0][2] == "login_created"


@pytest.mark.asyncio
async def test_reset_password_writes_audit_row():
    from vula.api.users import reset_password
    mock_resp = MagicMock(status_code=200)
    with (
        patch("vula.api.users._auth_admin", new=AsyncMock(return_value=mock_resp)),
        patch("vula.api.users.merchant_audit.audit") as mock_audit,
    ):
        result = await reset_password("off-the-hook", "u9",
                                      identity={"user_id": "u1", "email": "owner@shop.com"})
    assert "temp_password" in result
    mock_audit.assert_called_once_with(
        "off-the-hook", {"user_id": "u1", "email": "owner@shop.com"}, "password_reset", user_id="u9")


@pytest.mark.asyncio
async def test_update_role_writes_audit_row():
    from vula.api.users import update_role, RolePatch
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.api.users._client", return_value=mock_db),
        patch("vula.api.users.merchant_audit.audit") as mock_audit,
    ):
        result = await update_role("off-the-hook", "u9", RolePatch(role="manager"),
                                   identity={"user_id": "u1", "email": "owner@shop.com"})
    assert result["role"] == "manager"
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_remove_access_writes_audit_row():
    from vula.api.users import remove_access
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.api.users._client", return_value=mock_db),
        patch("vula.api.users.merchant_audit.audit") as mock_audit,
    ):
        result = await remove_access("off-the-hook", "u9",
                                     identity={"user_id": "u1", "email": "owner@shop.com"})
    assert result == {"removed": "u9"}
    mock_audit.assert_called_once()


# ── master.py direct-call sites (regression: must pass identity through) ─────────

@pytest.mark.asyncio
async def test_master_create_user_passes_identity_through():
    from vula.api import master
    mock_resp = MagicMock(status_code=201)
    mock_resp.json.return_value = {"id": "u9"}
    identity = {"user_id": "m1", "email": "master@vula.app", "role": "master"}
    with (
        patch("vula.api.users._auth_admin", new=AsyncMock(return_value=mock_resp)),
        patch("vula.api.users._map_tenant_user"),
        patch("vula.api.master.audit"),
        patch("vula.api.users.merchant_audit.audit") as mock_merchant_audit,
    ):
        result = await master.master_create_user(
            "off-the-hook", {"email": "staff@shop.com", "role": "staff"}, identity=identity)
    assert result["email"] == "staff@shop.com"
    mock_merchant_audit.assert_called_once()
    assert mock_merchant_audit.call_args[0][1] == identity


@pytest.mark.asyncio
async def test_master_reset_user_password_passes_identity_through():
    from vula.api import master
    mock_resp = MagicMock(status_code=200)
    identity = {"user_id": "m1", "email": "master@vula.app", "role": "master"}
    with (
        patch("vula.api.users._auth_admin", new=AsyncMock(return_value=mock_resp)),
        patch("vula.api.master.audit"),
        patch("vula.api.users.merchant_audit.audit") as mock_merchant_audit,
    ):
        result = await master.master_reset_user_password("off-the-hook", "u9", identity=identity)
    assert "temp_password" in result
    mock_merchant_audit.assert_called_once_with("off-the-hook", identity, "password_reset", user_id="u9")


@pytest.mark.asyncio
async def test_master_remove_user_passes_identity_through():
    from vula.api import master
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    identity = {"user_id": "m1", "email": "master@vula.app", "role": "master"}
    with (
        patch("vula.api.users._client", return_value=mock_db),
        patch("vula.api.master.audit"),
        patch("vula.api.users.merchant_audit.audit") as mock_merchant_audit,
    ):
        result = await master.master_remove_user("off-the-hook", "u9", identity=identity)
    assert result == {"removed": "u9"}
    mock_merchant_audit.assert_called_once_with("off-the-hook", identity, "login_access_removed", user_id="u9")

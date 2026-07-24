"""Tests for the admin-depth polish batch (2026-07-24): syncing "Disable" with actual login
revocation in team.py (previously the two systems — vula_team_members vs
vula_tenant_users/Supabase Auth — were independently tracked, so Disable looked like it
revoked dashboard access but didn't), and the migration-probe numbers in master.py's Health
panel staying honest after the 074/076 renumbering done earlier the same session."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── team.py: Disable revokes a matching login ───────────────────────────────────

@pytest.mark.asyncio
async def test_disabling_a_member_with_a_matching_login_revokes_it():
    from vula.api.team import update_member, MemberPatch
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[{"email": "staff@shop.com"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    identity = {"user_id": "u1", "email": "owner@shop.com"}
    with (
        patch("vula.api.team._client", return_value=mock_db),
        patch("vula.api.team.merchant_audit.audit") as mock_audit,
        patch("vula.api.users._find_user_id", new=AsyncMock(return_value="staff-uid")),
        patch("vula.api.users._client", return_value=mock_db),
    ):
        result = await update_member("off-the-hook", "m1", MemberPatch(active=False), identity=identity)
    assert result["login_revoked"] is True
    assert mock_audit.call_args.kwargs.get("login_revoked") is True


@pytest.mark.asyncio
async def test_disabling_a_member_with_no_matching_login_does_not_revoke():
    from vula.api.team import update_member, MemberPatch
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[{"email": "noone@shop.com"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    identity = {"user_id": "u1", "email": "owner@shop.com"}
    with (
        patch("vula.api.team._client", return_value=mock_db),
        patch("vula.api.team.merchant_audit.audit"),
        patch("vula.api.users._find_user_id", new=AsyncMock(return_value=None)),
    ):
        result = await update_member("off-the-hook", "m1", MemberPatch(active=False), identity=identity)
    assert result["login_revoked"] is False


@pytest.mark.asyncio
async def test_non_disable_patches_never_touch_logins():
    from vula.api.team import update_member, MemberPatch
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    identity = {"user_id": "u1", "email": "owner@shop.com"}
    with (
        patch("vula.api.team._client", return_value=mock_db),
        patch("vula.api.team.merchant_audit.audit"),
        patch("vula.api.team._revoke_matching_login", new=AsyncMock()) as mock_revoke,
    ):
        result = await update_member("off-the-hook", "m1", MemberPatch(role="manager"), identity=identity)
    mock_revoke.assert_not_called()
    assert result["login_revoked"] is False


@pytest.mark.asyncio
async def test_revoke_helper_swallows_errors():
    from vula.api.team import _revoke_matching_login
    with patch("vula.api.team._client", side_effect=RuntimeError("down")):
        result = await _revoke_matching_login("off-the-hook", "m1", {"email": "a@b.com"})
    assert result is False


# ── master.py: migration-probe numbering matches current migration files ────────

def test_migration_probes_reference_current_filenames():
    from pathlib import Path
    from vula.api.master import _MIGRATION_PROBES

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    files = {f.name for f in migrations_dir.glob("*.sql")}
    for num, table, _note in _MIGRATION_PROBES:
        matches = [f for f in files if f.startswith(f"{num}_")]
        assert matches, f"no migration file starts with {num}_ (probe for {table} is stale)"

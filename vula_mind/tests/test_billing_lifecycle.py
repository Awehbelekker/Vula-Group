"""Tests for master's billing/subscription lifecycle admin (mark-paid, extend-trial, cancel,
reactivate) — the last item from the 2026-07-24 admin-depth audit, previously flagged as
missing entirely (VulaSubscriptions.jsx/VulaAdmin.jsx were read-only views with no write
path). Also covers the vula_tenants.paid schema-drift bug found while scoping this: migration
001 always declared the column but it was never actually applied to production, which had been
silently breaking the PayFast webhook's payment confirmation and GET /v1/admin/signups this
whole time."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from vula.api import master

_IDENTITY = {"user_id": "m1", "email": "master@vula.app", "role": "master"}


def _mock_db():
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    return mock_db, mock_table


# ── mark-paid ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_paid_sets_paid_and_active_status():
    mock_db, mock_table = _mock_db()
    mock_table.update.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "off-the-hook", "paid": True, "status": "active"}])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master.audit"),
        patch("vula.api.merchant_audit.audit") as mock_merchant_audit,
    ):
        result = await master.master_mark_paid("off-the-hook", identity=_IDENTITY)
    assert result["paid"] is True
    mock_table.update.assert_called_with({"paid": True, "status": "active"})
    mock_merchant_audit.assert_called_once_with("off-the-hook", _IDENTITY, "billing_marked_paid")


@pytest.mark.asyncio
async def test_mark_paid_404s_when_tenant_not_in_vula_tenants():
    mock_db, mock_table = _mock_db()
    mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master.audit"),
    ):
        with pytest.raises(Exception) as exc:
            await master.master_mark_paid("nonexistent", identity=_IDENTITY)
    assert getattr(exc.value, "status_code", None) == 404


# ── extend-trial ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extend_trial_extends_from_existing_future_date():
    mock_db, mock_table = _mock_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=[{"trial_ends": future}])
    mock_table.update.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "digg-demo"}])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master.audit"),
        patch("vula.api.merchant_audit.audit") as mock_merchant_audit,
    ):
        await master.master_extend_trial("digg-demo", {"days": 14}, identity=_IDENTITY)
    new_end = mock_table.update.call_args[0][0]["trial_ends"]
    assert datetime.fromisoformat(new_end) > datetime.fromisoformat(future)
    mock_merchant_audit.assert_called_once_with("digg-demo", _IDENTITY, "billing_trial_extended", days=14)


@pytest.mark.asyncio
async def test_extend_trial_extends_from_today_when_already_expired():
    mock_db, mock_table = _mock_db()
    past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=[{"trial_ends": past}])
    mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master.audit"),
        patch("vula.api.merchant_audit.audit"),
    ):
        await master.master_extend_trial("digg-demo", {"days": 14}, identity=_IDENTITY)
    new_end = datetime.fromisoformat(mock_table.update.call_args[0][0]["trial_ends"])
    # extended from *today*, not from the long-expired date — should land ~14 days out, not ~16 years ago+14
    assert new_end > datetime.now(timezone.utc) + timedelta(days=13)


@pytest.mark.asyncio
async def test_extend_trial_defaults_to_14_days():
    mock_db, mock_table = _mock_db()
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=[{"trial_ends": None}])
    mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master.audit"),
        patch("vula.api.merchant_audit.audit"),
    ):
        await master.master_extend_trial("digg-demo", {}, identity=_IDENTITY)
    new_end = datetime.fromisoformat(mock_table.update.call_args[0][0]["trial_ends"])
    assert timedelta(days=13) < (new_end - datetime.now(timezone.utc)) < timedelta(days=15)


@pytest.mark.asyncio
async def test_extend_trial_404s_when_tenant_not_found():
    mock_db, mock_table = _mock_db()
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=[])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master.audit"),
    ):
        with pytest.raises(Exception) as exc:
            await master.master_extend_trial("nonexistent", {}, identity=_IDENTITY)
    assert getattr(exc.value, "status_code", None) == 404


# ── cancel / reactivate ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_sets_status_and_suspends_tenant_config():
    mock_db, mock_table = _mock_db()
    mock_table.update.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "off-the-hook", "status": "cancelled"}])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master.audit"),
        patch("vula.api.tenants.invalidate") as mock_invalidate,
        patch("vula.api.merchant_audit.audit") as mock_merchant_audit,
    ):
        result = await master.master_cancel_subscription("off-the-hook", identity=_IDENTITY)
    assert result["status"] == "cancelled"
    # two distinct update() calls happened: vula_tenants.status and vula_tenant_config.active
    update_calls = [c.args[0] for c in mock_table.update.call_args_list]
    assert {"status": "cancelled"} in update_calls
    assert {"active": False} in update_calls
    mock_invalidate.assert_called_once_with("off-the-hook")
    mock_merchant_audit.assert_called_once_with("off-the-hook", _IDENTITY, "billing_cancelled")


@pytest.mark.asyncio
async def test_cancel_404s_when_tenant_not_in_vula_tenants():
    mock_db, mock_table = _mock_db()
    mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master.audit"),
    ):
        with pytest.raises(Exception) as exc:
            await master.master_cancel_subscription("nonexistent", identity=_IDENTITY)
    assert getattr(exc.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_reactivate_sets_status_and_unsuspends_tenant_config():
    mock_db, mock_table = _mock_db()
    mock_table.update.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"tenant_id": "off-the-hook", "status": "active"}])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master.audit"),
        patch("vula.api.tenants.invalidate") as mock_invalidate,
        patch("vula.api.merchant_audit.audit") as mock_merchant_audit,
    ):
        result = await master.master_reactivate_subscription("off-the-hook", identity=_IDENTITY)
    assert result["status"] == "active"
    update_calls = [c.args[0] for c in mock_table.update.call_args_list]
    assert {"status": "active"} in update_calls
    assert {"active": True} in update_calls
    mock_invalidate.assert_called_once_with("off-the-hook")
    mock_merchant_audit.assert_called_once_with("off-the-hook", _IDENTITY, "billing_reactivated")


# ── migration probe: column-specific check ──────────────────────────────────────

def test_probe_migrations_checks_the_specific_column_for_column_only_entries():
    mock_table = MagicMock()
    mock_table.select.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch.object(master, "_MIGRATION_PROBES", [("108", "vula_tenants", "Billing paid column", "paid")]):
        result = master._probe_migrations(mock_db)
    mock_table.select.assert_called_once_with("paid")
    assert result == [{"migration": "108", "table": "vula_tenants",
                       "note": "Billing paid column", "applied": True}]


def test_probe_migrations_flags_not_applied_on_missing_column():
    mock_table = MagicMock()
    mock_table.select.return_value.limit.return_value.execute.side_effect = RuntimeError("column paid does not exist")
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch.object(master, "_MIGRATION_PROBES", [("108", "vula_tenants", "Billing paid column", "paid")]):
        result = master._probe_migrations(mock_db)
    assert result[0]["applied"] is False

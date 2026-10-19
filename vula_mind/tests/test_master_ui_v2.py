"""The redesigned dashboard, per business (migrations 215 / 217): master can switch one tenant
on or off through the audited tenant PATCH, and the tenant list shows which design each has."""
from unittest.mock import MagicMock, patch

import pytest

from vula.api import master


@pytest.mark.asyncio
async def test_master_can_switch_a_tenant_back_to_the_old_design():
    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"tenant_id": "digg-demo", "ui_v2": False}])
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.tenants.invalidate") as inval,
        patch("vula.api.master.audit") as audit,
    ):
        await master.master_update_tenant("digg-demo", {"ui_v2": 0}, identity={"user_id": "master-1"})
    mock_db.table.return_value.update.assert_called_once_with({"ui_v2": False})   # coerced to a bool
    inval.assert_called_once_with("digg-demo")
    assert audit.call_args.kwargs["patch"] == {"ui_v2": False}


def test_tenant_overview_shows_each_tenants_design():
    mock_db = MagicMock()

    def table(name):
        m = MagicMock()
        rows = {"vula_tenant_config": [{"tenant_id": "a", "display_name": "A", "ui_v2": True},
                                       {"tenant_id": "b", "display_name": "B", "ui_v2": False}],
                "vula_tenants": [], "vula_tenant_users": []}.get(name, [])
        m.select.return_value.order.return_value.execute.return_value = MagicMock(data=rows)
        m.select.return_value.execute.return_value = MagicMock(data=rows)
        return m

    mock_db.table.side_effect = table
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.master._tenant_signals", return_value={}),
        patch("vula.api.master._health", return_value={}),
    ):
        out = master.tenant_overview()["tenants"]
    assert {t["tenant_id"]: t["ui_v2"] for t in out} == {"a": True, "b": False}


def test_migration_217_turns_the_design_on_by_default_and_for_everyone():
    import pathlib
    sql = (pathlib.Path(__file__).resolve().parent.parent / "migrations" / "217_ui_v2_default_on.sql").read_text()
    assert "alter column ui_v2 set default true" in sql
    assert "update vula_tenant_config set ui_v2 = true" in sql

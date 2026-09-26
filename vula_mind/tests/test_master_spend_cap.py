"""Tests for /v1/master's spend-cap surfacing (vula/api/master.py) — go-live readiness pass,
Phase 1.1. spend_cap_usd is settable through the existing generic tenant-config PATCH, and
master_usage() surfaces per-tenant cap/capped_today alongside the existing cost view."""
from unittest.mock import MagicMock, patch

import pytest

from vula.api import master


def test_spend_cap_usd_is_an_allowed_patch_field():
    """The PATCH allowlist is inline in master_update_tenant — read its source rather than
    re-deriving it, so this fails loudly if the field is ever removed by accident."""
    import inspect
    src = inspect.getsource(master.master_update_tenant)
    assert '"spend_cap_usd"' in src


@pytest.mark.asyncio
async def test_master_update_tenant_sets_spend_cap():
    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"tenant_id": "digg-demo", "spend_cap_usd": 5.0}]
    )
    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.api.tenants.invalidate"),
        patch("vula.api.master.audit"),
    ):
        await master.master_update_tenant(
            "digg-demo", {"spend_cap_usd": 5.0}, identity={"user_id": "master-1"}
        )
    mock_db.table.return_value.update.assert_called_once_with({"spend_cap_usd": 5.0})


@pytest.mark.asyncio
async def test_master_usage_surfaces_cap_and_capped_today():
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()

    mock_db = MagicMock()

    def table(name):
        m = MagicMock()
        if name == "vula_ai_usage":
            m.select.return_value.gte.return_value.execute.return_value = MagicMock(
                data=[{"tenant_id": "digg-demo", "day": today, "model": "x",
                       "calls": 1, "est_cost_usd": 3.0}]
            )
        elif name == "vula_infra_snapshot":
            m.select.return_value.gte.return_value.execute.return_value = MagicMock(data=[])
        elif name == "vula_tenant_config":
            m.select.return_value.execute.return_value = MagicMock(
                data=[{"tenant_id": "digg-demo", "spend_cap_usd": 2.0, "plan": "starter"}]
            )
        elif name == "vula_filed_documents":
            m.select.return_value.execute.return_value = MagicMock(data=[])
        elif name == "vula_tenant_users":
            m.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
        return m

    mock_db.table.side_effect = table
    with patch("vula.api.master._client", return_value=mock_db):
        result = await master.master_usage()

    tenant = result["per_tenant"]["digg-demo"]
    assert tenant["spend_cap_usd"] == 2.0
    assert tenant["capped_today"] is True  # 3.0 spent >= 2.0 cap


@pytest.mark.asyncio
async def test_master_usage_omits_cap_fields_when_unset():
    mock_db = MagicMock()

    def table(name):
        m = MagicMock()
        if name in ("vula_ai_usage", "vula_infra_snapshot"):
            m.select.return_value.gte.return_value.execute.return_value = MagicMock(data=[])
        elif name == "vula_tenant_config":
            m.select.return_value.execute.return_value = MagicMock(data=[])
        elif name == "vula_filed_documents":
            m.select.return_value.execute.return_value = MagicMock(data=[])
        elif name == "vula_tenant_users":
            m.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
        return m

    mock_db.table.side_effect = table
    with patch("vula.api.master._client", return_value=mock_db):
        result = await master.master_usage()

    assert result["per_tenant"] == {}


@pytest.mark.asyncio
async def test_master_usage_surfaces_document_and_seat_plan_usage():
    """Go-live readiness pass Phase 4.3: /master's cost view must also show who's near/over
    their advertised plan limits (doc_count/doc_cap, seat_count/seat_cap), not just spend."""
    mock_db = MagicMock()

    def table(name):
        m = MagicMock()
        if name in ("vula_ai_usage", "vula_infra_snapshot"):
            m.select.return_value.gte.return_value.execute.return_value = MagicMock(data=[])
        elif name == "vula_tenant_config":
            m.select.return_value.execute.return_value = MagicMock(
                data=[{"tenant_id": "digg-demo", "plan": "starter"},
                      {"tenant_id": "off-the-hook", "plan": "growth"}]
            )
        elif name == "vula_filed_documents":
            # paged read (select().order().range()) — see master_usage
            m.select.return_value.order.return_value.range.return_value.execute.return_value = MagicMock(
                data=[{"tenant_id": "digg-demo"}] * 26
            )
        elif name == "vula_tenant_users":
            m.select.return_value.in_.return_value.order.return_value.range.return_value.execute.return_value = \
                MagicMock(data=[{"tenant_id": "digg-demo", "role": "owner"}])
        return m

    mock_db.table.side_effect = table
    with patch("vula.api.master._client", return_value=mock_db):
        result = await master.master_usage()

    digg = result["per_tenant"]["digg-demo"]
    assert digg["doc_count"] == 26
    assert digg["doc_cap"] == 25
    assert digg["seat_count"] == 1
    assert digg["seat_cap"] == 2

    oth = result["per_tenant"]["off-the-hook"]
    assert oth["doc_count"] == 0
    assert oth["doc_cap"] is None  # unlimited on Growth
    assert oth["seat_cap"] == 5

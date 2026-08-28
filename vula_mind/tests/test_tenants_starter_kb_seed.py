"""Tests for vula/api/tenants.py::create_tenant's 2026-08-28 starter_kb widening — this
master-created tenant path previously never seeded starter KB docs at all (only signup.py's
self-serve flow did), leaving master-created tenants with an empty KB on day one. Same
fire-and-forget, best-effort shape as signup.py — must never block tenant creation on failure,
and must never fire on a routine update to an already-existing tenant."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vula.api.tenants import TenantIn, create_tenant

FAKE_IDENTITY = {"email": "master@vula.ai", "role": "master"}


def _mock_client(existing_rows=None):
    mock = MagicMock()
    mock.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
        SimpleNamespace(data=existing_rows or [])
    return mock


@pytest.mark.asyncio
async def test_create_tenant_calls_seed_starter_kb_for_new_tenant():
    body = TenantIn(tenant_id="new-trades-co", business_type="trades", display_name="New Trades Co")
    with (
        patch("vula.api.tenants._client", return_value=_mock_client(existing_rows=[])),
        patch("vula.api.tenants._CACHE"),
        patch("vula.api.master.audit"),
        patch("vula.commerce.background_tasks.run_background") as mock_run_bg,
    ):
        result = await create_tenant(body, identity=FAKE_IDENTITY)

    assert "tenant" in result
    mock_run_bg.assert_called_once()
    args = mock_run_bg.call_args.args
    assert args[0] == "new-trades-co"
    assert args[1] == "starter_kb_seed"


@pytest.mark.asyncio
async def test_create_tenant_does_not_reseed_on_update_of_existing_tenant():
    body = TenantIn(tenant_id="off-the-hook", business_type="food")
    with (
        patch("vula.api.tenants._client", return_value=_mock_client(existing_rows=[{"tenant_id": "off-the-hook"}])),
        patch("vula.api.tenants._CACHE"),
        patch("vula.api.master.audit"),
        patch("vula.commerce.background_tasks.run_background") as mock_run_bg,
    ):
        await create_tenant(body, identity=FAKE_IDENTITY)

    mock_run_bg.assert_not_called()


@pytest.mark.asyncio
async def test_create_tenant_starter_kb_failure_does_not_break_tenant_creation():
    body = TenantIn(tenant_id="new-co", business_type="other")
    with (
        patch("vula.api.tenants._client", return_value=_mock_client(existing_rows=[])),
        patch("vula.api.tenants._CACHE"),
        patch("vula.api.master.audit"),
        patch("vula.commerce.background_tasks.run_background", side_effect=RuntimeError("boom")),
    ):
        result = await create_tenant(body, identity=FAKE_IDENTITY)

    assert "tenant" in result
    assert result["tenant"]["tenant_id"] == "new-co"


@pytest.mark.asyncio
async def test_create_tenant_defaults_business_type_to_other_for_starter_kb():
    body = TenantIn(tenant_id="no-type-co")  # business_type defaults to "other" on the model itself
    with (
        patch("vula.api.tenants._client", return_value=_mock_client(existing_rows=[])),
        patch("vula.api.tenants._CACHE"),
        patch("vula.api.master.audit"),
        patch("vula.commerce.background_tasks.run_background") as mock_run_bg,
    ):
        await create_tenant(body, identity=FAKE_IDENTITY)

    mock_run_bg.assert_called_once()

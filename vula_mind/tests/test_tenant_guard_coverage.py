"""tenant_admin_guard covers every tenant-scoped dashboard router (2026-09-25 review).

Before: only /v1/commerce/{t}/admin, /v1/team/{t} and /v1/users/{t} were guarded, so anyone who
knew a tenant slug could replace its payment-gateway credentials, read its bookings (customer
PII), project finances, filed documents and mailbox contacts, or disconnect its WhatsApp/Yoco.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from vula.api import server


GUARDED = [
    ("POST", "/v1/payments/off-the-hook/providers"),
    ("GET", "/v1/bookings/off-the-hook"),
    ("PUT", "/v1/bookings/off-the-hook/settings"),
    ("POST", "/v1/bookings/off-the-hook/b1/status"),
    ("GET", "/v1/projects/digg-demo/finances"),
    ("GET", "/v1/documents/digg-demo/filed"),
    ("GET", "/v1/email/contacts/digg-demo"),
    ("DELETE", "/v1/whatsapp/disconnect/off-the-hook"),
    ("DELETE", "/v1/yoco/disconnect/off-the-hook"),
    ("GET", "/v1/subscriptions/off-the-hook"),
    ("GET", "/v1/recurring-bills/off-the-hook"),
    ("GET", "/v1/qs/rates/digg-demo"),
    ("GET", "/v1/google/digg-demo/drive/search"),
    ("GET", "/v1/field/daily-tasks/digg-demo"),
]

PUBLIC = [
    ("GET", "/v1/bookings/off-the-hook/services"),
    ("GET", "/v1/bookings/off-the-hook/availability"),
    ("POST", "/v1/bookings/off-the-hook"),
    ("POST", "/v1/payments/webhook/off-the-hook/payfast"),
    ("GET", "/v1/google/authorize-url"),
    ("GET", "/v1/google/oauth/callback"),
    ("GET", "/v1/tenants/registry"),
    ("GET", "/v1/tenants/off-the-hook"),
    ("POST", "/v1/whatsapp/webhook"),
]

MASTER = [
    ("GET", "/v1/tenants"),
    ("GET", "/v1/whatsapp/accounts"),
    ("GET", "/v1/yoco/accounts"),
    ("POST", "/v1/training/seed"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", GUARDED)
async def test_tenant_paths_need_sign_in_and_membership(method, path):
    assert (await server._guard_check(method, path, "")).status_code == 401
    with patch("vula.api.tenant_auth.is_tenant_member", AsyncMock(return_value=False)) as m:
        assert (await server._guard_check(method, path, "Bearer t")).status_code == 403
    assert m.await_args.args[1] in ("off-the-hook", "digg-demo")
    with patch("vula.api.tenant_auth.is_tenant_member", AsyncMock(return_value=True)):
        assert await server._guard_check(method, path, "Bearer t") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", PUBLIC)
async def test_public_paths_stay_open(method, path):
    assert await server._guard_check(method, path, "") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", MASTER)
async def test_master_only_listings(method, path):
    assert (await server._guard_check(method, path, "")).status_code == 401
    with patch("vula.api.master_auth.require_master",
               AsyncMock(side_effect=HTTPException(status_code=403, detail="Master access required."))):
        assert (await server._guard_check(method, path, "Bearer t")).status_code == 403
    with patch("vula.api.master_auth.require_master", AsyncMock(return_value={"role": "master"})):
        assert await server._guard_check(method, path, "Bearer t") is None


@pytest.mark.asyncio
async def test_server_api_key_passes_for_jobs(monkeypatch):
    """n8n's daily-task workflows call /v1/field/daily-tasks/{t} with X-API-Key."""
    monkeypatch.setattr(server.settings, "api_key", "k-123")
    assert await server._guard_check("GET", "/v1/field/daily-tasks/digg-demo", "", "k-123") is None
    assert (await server._guard_check("GET", "/v1/field/daily-tasks/digg-demo", "", "wrong")).status_code == 401
    monkeypatch.setattr(server.settings, "api_key", "")
    assert (await server._guard_check("GET", "/v1/field/daily-tasks/digg-demo", "", "")).status_code == 401

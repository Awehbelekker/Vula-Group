"""Tests for the per-tenant LLM cost-metering context middleware
(vula/api/server.py::tenant_metering_context).

Verifies the request-scoped tenant contextvar (vula/integrations/metering.py's
_current_tenant) actually gets set for dashboard-triggered (/v1/commerce/{tenant}/...) requests
— previously only WhatsApp's entry points set this, so dashboard-triggered AI costs (page-copy
drafting, invoice-style cloning, marketing generation, voice-profile analysis, etc) were landing
in vula_ai_usage's "_unattributed" bucket instead of the responsible tenant.
"""
import pytest

from vula.api.server import tenant_metering_context
from vula.integrations import metering


class _FakeURL:
    def __init__(self, path):
        self.path = path


class _FakeRequest:
    def __init__(self, path):
        self.url = _FakeURL(path)


@pytest.mark.asyncio
async def test_sets_tenant_context_for_commerce_admin_request():
    captured = {}

    async def call_next(request):
        captured["tenant"] = metering._current_tenant.get()
        return "ok"

    req = _FakeRequest("/v1/commerce/off-the-hook/admin/pages/ai-draft")
    result = await tenant_metering_context(req, call_next)
    assert result == "ok"
    assert captured["tenant"] == "off-the-hook"


@pytest.mark.asyncio
async def test_sets_tenant_context_for_non_admin_commerce_path_too():
    # Deliberately broader than tenant_admin_guard's /admin-only regex — metering should cover
    # any AI-capable commerce route, not just the admin-gated ones.
    captured = {}

    async def call_next(request):
        captured["tenant"] = metering._current_tenant.get()
        return "ok"

    req = _FakeRequest("/v1/commerce/gerflor/brand")
    await tenant_metering_context(req, call_next)
    assert captured["tenant"] == "gerflor"


@pytest.mark.asyncio
async def test_leaves_context_unset_for_unrelated_path():
    metering._current_tenant.set(None)
    captured = {}

    async def call_next(request):
        captured["tenant"] = metering._current_tenant.get()
        return "ok"

    req = _FakeRequest("/v1/bookings/off-the-hook/services")
    await tenant_metering_context(req, call_next)
    assert captured["tenant"] is None


@pytest.mark.asyncio
async def test_never_blocks_the_request_even_if_metering_import_fails(monkeypatch):
    import vula.api.server as srv

    def _boom(*a, **kw):
        raise RuntimeError("metering unavailable")
    monkeypatch.setattr("vula.integrations.metering.set_request_tenant", _boom)

    async def call_next(request):
        return "ok"

    req = _FakeRequest("/v1/commerce/off-the-hook/admin/pages/ai-draft")
    result = await srv.tenant_metering_context(req, call_next)
    assert result == "ok"

"""Route auth sweep: every route on the app is either protected or deliberately public.

A 2026-09-26 inventory found /takeoff/*, the commerce job triggers (which message customers), the
Off the Hook briefings, id-only field-ops routes, document assignment and ClickUp connect all
reachable with no credentials. This test makes that class of gap impossible to reintroduce
silently: a new route must either be covered by an auth dependency / the tenant guard, or be
added to PUBLIC below with the reason it has to be open.

Two passes:
  • structural — every route is guarded, auth-dependent, or listed in PUBLIC;
  • behavioural — with API_KEY set and ENFORCE_TENANT_AUTH on, every non-public route refuses
    (401/403) a request with no credentials AND one from a signed-in member of another tenant.
    Guards run before handlers, so no handler (and no database) is ever reached.
"""
import re
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from vula.api import server

AUTH_DEPS = {"require_auth", "require_master", "_require_auth", "require_tenant_actor",
             "_require_admin", "require_authenticated_user"}

# (method, path template) -> why it is public. Keep reasons specific.
PUBLIC = {
    ("GET", "/status"): "health check (Railway)",
    ("GET", "/email/unsubscribe"): "signed unsubscribe link in campaign emails",
    ("POST", "/email/unsubscribe"): "signed unsubscribe link in campaign emails",
    ("GET", "/l/{code}"): "broadcast click-tracking redirect",
    ("GET", "/menu/{tenant_id}"): "public photo menu page",
    ("GET", "/payment/success"): "payment gateway return page",
    ("GET", "/payment/cancel"): "payment gateway return page",
    ("POST", "/v1/auth/login"): "sign-in",
    ("POST", "/v1/payfast/notify"): "PayFast ITN — signature-verified in handler",
    ("POST", "/v1/payments/webhook/{tenant_id}/{provider}"): "gateway webhook — verified in handler",
    ("POST", "/v1/yoco/webhook"): "Yoco webhook — signature-verified in handler",
    ("GET", "/v1/whatsapp/webhook"): "Meta verification handshake (verify token)",
    ("POST", "/v1/whatsapp/webhook"): "Meta webhook — X-Hub-Signature-256 verified in handler",
    ("POST", "/v1/whatsapp/connect"): "embedded signup — check_body_tenant in handler",
    ("POST", "/v1/yoco/connect"): "check_body_tenant in handler",
    ("POST", "/v1/email/connect"): "check_body_tenant in handler",
    ("POST", "/v1/clickup/webhook"): "ClickUp webhook",
    ("GET", "/v1/clickup/authorize-url"): "OAuth start",
    ("GET", "/v1/clickup/oauth/callback"): "OAuth callback (state-bound)",
    ("GET", "/v1/google/authorize-url"): "OAuth start",
    ("GET", "/v1/google/oauth/callback"): "OAuth callback (state-bound)",
    ("GET", "/v1/microsoft/authorize-url"): "OAuth start",
    ("GET", "/v1/microsoft/oauth/callback"): "OAuth callback (state-bound)",
    ("GET", "/v1/dynamics365/authorize-url"): "OAuth start",
    ("GET", "/v1/dynamics365/oauth/callback"): "OAuth callback (state-bound)",
    ("GET", "/v1/bookings/{tenant_id}/services"): "storefront booking widget",
    ("GET", "/v1/bookings/{tenant_id}/availability"): "storefront booking widget",
    ("POST", "/v1/bookings/{tenant_id}"): "storefront booking widget",
    ("GET", "/v1/commerce/{tenant_id}/brand"): "storefront",
    ("GET", "/v1/commerce/{tenant_id}/settings"): "storefront (public shop settings)",
    ("GET", "/v1/commerce/{tenant_id}/products"): "storefront",
    ("GET", "/v1/commerce/{tenant_id}/products/{slug}"): "storefront",
    ("GET", "/v1/commerce/{tenant_id}/products/{slug}/related"): "storefront",
    ("GET", "/v1/commerce/{tenant_id}/pages"): "storefront",
    ("GET", "/v1/commerce/{tenant_id}/pages/{slug}"): "storefront",
    ("GET", "/v1/commerce/{tenant_id}/robots.txt"): "storefront SEO",
    ("GET", "/v1/commerce/{tenant_id}/sitemap.xml"): "storefront SEO",
    ("GET", "/v1/commerce/{tenant_id}/cart/{session_id}"): "storefront cart (opaque session id)",
    ("POST", "/v1/commerce/{tenant_id}/cart/{session_id}/add"): "storefront cart (opaque session id)",
    ("POST", "/v1/commerce/{tenant_id}/cart/{session_id}/sync"): "storefront cart (opaque session id)",
    ("DELETE", "/v1/commerce/{tenant_id}/cart/{session_id}/{item_id}"): "storefront cart (opaque session id)",
    ("POST", "/v1/commerce/{tenant_id}/checkout"): "storefront checkout",
    ("POST", "/v1/commerce/{tenant_id}/discount-codes/validate"): "storefront checkout",
    ("POST", "/v1/commerce/{tenant_id}/reviews"): "customer review — verified purchase in handler",
    ("GET", "/v1/commerce/{tenant_id}/orders/{order_id}"): "customer order tracking (unguessable id)",
    ("GET", "/v1/commerce/{tenant_id}/invoices/{invoice_id}/approve"): "client approval page (token)",
    ("POST", "/v1/commerce/{tenant_id}/invoices/{invoice_id}/approve"): "client approval (token)",
    ("GET", "/v1/tenants/registry"): "static module/business-type catalogue (login + onboarding)",
    ("GET", "/v1/tenants/{tenant_id}"): "public tenant profile — _public() strips private fields",
    ("GET", "/v1/tenant/{tenant_id}/status"): "onboarding status after signup payment",
    ("GET", "/v1/training/status"): "shared KB status (no tenant data)",
    ("GET", "/v1/training/topics"): "shared KB topic list (no tenant data)",
    ("GET", "/v1/training/business/status"): "shared KB status (no tenant data)",
    ("GET", "/v1/training/business/topics"): "shared KB topic list (no tenant data)",
}


def _routes(routes=None):
    """Every endpoint, walking FastAPI's lazily-included routers."""
    for r in (server.app.routes if routes is None else routes):
        if type(r).__name__ == "_IncludedRouter":
            yield from _routes(r.effective_candidates())
        elif hasattr(r, "dependant") and getattr(r, "methods", None):
            yield r


def _deps(route) -> set:
    names = set()

    def walk(d):
        for sub in d.dependencies:
            names.add(getattr(sub.call, "__name__", ""))
            walk(sub)
    walk(route.dependant)
    return names


def _sample(path: str) -> str:
    return re.sub(r"\{tenant_id\}", "tenant-b", re.sub(r"\{(?!tenant_id)[^}]+\}", "x1", path))


def _covered(method, route) -> bool:
    sample = _sample(route.path)
    guarded = (any(rx.match(sample) for rx in server._TENANT_GUARD_RES)
               and not any(m == method and rx.match(sample) for m, rx in server._TENANT_GUARD_PUBLIC))
    master = any(m == method and rx.match(sample) for m, rx in server._MASTER_ONLY)
    return guarded or master or bool(_deps(route) & AUTH_DEPS)


def _endpoints():
    seen = set()
    for r in _routes():
        if r.path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
            continue
        for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
            if (m, r.path) not in seen:
                seen.add((m, r.path))
                yield m, r


def test_every_route_is_protected_or_deliberately_public():
    open_routes = [f"{m} {r.path}" for m, r in _endpoints()
                   if not _covered(m, r) and (m, r.path) not in PUBLIC]
    assert not open_routes, ("Routes reachable with no credentials — protect them "
                             "(Depends(require_auth)/tenant guard) or add to PUBLIC with a reason:\n"
                             + "\n".join(open_routes))


def test_public_list_has_no_stale_entries():
    live = {(m, r.path) for m, r in _endpoints()}
    stale = sorted(k for k in PUBLIC if k not in live)
    assert not stale, f"PUBLIC lists routes that no longer exist: {stale}"


async def _member_of_a(auth_header, tenant):
    return auth_header == "Bearer member-a" and tenant == "tenant-a"


@pytest.fixture()
def client(monkeypatch):
    from vula.api import master_auth
    monkeypatch.setattr(master_auth.settings, "api_key", "server-key")
    monkeypatch.setattr(server.settings, "enforce_tenant_auth", True)
    with patch.object(master_auth, "require_master", new=AsyncMock(side_effect=HTTPException(403))), \
         patch("vula.api.tenant_auth.is_tenant_member", new=_member_of_a), \
         patch("vula.commerce.service._client", side_effect=AssertionError("handler reached")):
        yield TestClient(server.app, raise_server_exceptions=False)


def _protected():
    return [(m, r) for m, r in _endpoints() if (m, r.path) not in PUBLIC]


@pytest.mark.parametrize("method,path", [(m, r.path) for m, r in _protected()])
def test_refused_without_credentials_and_for_another_tenants_member(client, method, path):
    url = _sample(path)
    body = {"tenant_id": "tenant-b"} if method in ("POST", "PUT", "PATCH") else None
    anon = client.request(method, url, json=body)
    assert anon.status_code in (401, 403), f"{method} {path} anonymous -> {anon.status_code}"
    other = client.request(method, url, json=body, headers={"Authorization": "Bearer member-a"})
    assert other.status_code in (401, 403), f"{method} {path} other tenant -> {other.status_code}"


def test_positive_control_own_tenant_member_gets_past_the_guards(client):
    """Proves the refusals above come from the guards, not from every request failing: a member
    of tenant-a on tenant-a's own routes reaches the handler (which here hits the stubbed DB)."""
    for method, url, body in [
        ("GET", "/v1/commerce/tenant-a/admin/orders", None),          # tenant guard middleware
        ("POST", "/v1/commerce/tenant-a/jobs/stock-alerts", None),    # require_auth, path tenant
        ("GET", "/takeoff/x1?tenant_id=tenant-a", None),              # require_auth, query tenant
    ]:
        r = client.request(method, url, json=body, headers={"Authorization": "Bearer member-a"})
        assert r.status_code not in (401, 403), f"{method} {url} -> {r.status_code}"

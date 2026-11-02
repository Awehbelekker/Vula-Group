"""tools/smoke.py — outside-in checks of a deployed API, against a mock transport and the real app."""
import importlib.util
import sys
from pathlib import Path

import httpx

_spec = importlib.util.spec_from_file_location(
    "smoke_tool", Path(__file__).resolve().parents[1] / "tools" / "smoke.py")
smoke = importlib.util.module_from_spec(_spec)
sys.modules["smoke_tool"] = smoke
_spec.loader.exec_module(smoke)


def _healthy(request: httpx.Request, *, ready=200, admin=401, verify=403) -> httpx.Response:
    p = request.url.path
    if p == "/status":
        return httpx.Response(200, json={"status": "ok"})
    if p == "/ready":
        if request.headers.get("x-api-key") != "the-key":
            return httpx.Response(401, json={"detail": "no key"})
        body = {"ready": ready == 200, "platform": [] if ready == 200 else
                [{"what": "Tenant admin routes are not access-checked", "fix": "x"}],
                "tenants": {"bakery": [{"what": "No way to take payment"}]}}
        return httpx.Response(ready, json=body)
    if p == "/v1/whatsapp/webhook":
        return httpx.Response(verify if request.method == "GET" else 403)
    if p.startswith("/v1/commerce/") and "/admin/" in p:
        return httpx.Response(admin, json=[])
    if p == "/takeoff/rates":
        return httpx.Response(401)
    return httpx.Response(404)


def _main(handler, argv=None, env=None):
    lines = []
    code = smoke.main(argv or ["--base-url", "https://staging.example", "--api-key-env", "SMOKE_KEY"],
                      transport=httpx.MockTransport(handler), env=env if env is not None else {"SMOKE_KEY": "the-key"},
                      out=lines.append)
    return code, "\n".join(lines)


def test_all_pass_on_a_healthy_deployment():
    code, out = _main(_healthy)
    assert code == 0, out
    assert "6/6 passed" in out and "FAIL" not in out
    assert "the-key" not in out                      # the API key is never printed
    assert "1 tenant(s) with gaps" in out


def test_ready_503_is_reported_not_a_crash():
    code, out = _main(lambda r: _healthy(r, ready=503))
    assert code == 0
    assert "NOT READY: 1 platform gap(s): Tenant admin routes are not access-checked" in out


def test_tenant_route_open_fails_the_run():
    """ENFORCE_TENANT_AUTH off: the admin route answers 200 with no credentials."""
    code, out = _main(lambda r: _healthy(r, admin=200))
    assert code == 1 and "FAIL  tenant admin route, no auth" in out and "1 FAILED" in out


def test_wrong_verify_token_accepted_fails():
    code, out = _main(lambda r: _healthy(r, verify=200))
    assert code == 1 and "FAIL  webhook verify, wrong token" in out


def test_without_a_key_ready_must_be_auth_protected():
    code, out = _main(_healthy, argv=["--base-url", "https://staging.example"], env={})
    assert code == 0 and "readiness needs the API key" in out


def test_unreachable_host_fails_every_check_without_crashing():
    def down(request):
        raise httpx.ConnectError("refused", request=request)
    code, out = _main(down)
    assert code == 1 and "0/6 passed" in out and "ConnectError" in out


def test_against_the_real_app(monkeypatch):
    """The checks agree with the app's own guards (API key set, tenant auth on, signatures on)."""
    from fastapi.testclient import TestClient
    from vula.api import server

    s = server.settings
    monkeypatch.setattr(s, "api_key", "the-key")
    monkeypatch.setattr(s, "enforce_tenant_auth", True)
    monkeypatch.setattr(s, "debug", False)
    monkeypatch.setattr(s, "whatsapp_verify_token", "the-real-verify-token")

    def no_db():
        raise RuntimeError("no database in tests")
    monkeypatch.setattr("vula.commerce.service._client", no_db)
    monkeypatch.setattr("vula.readiness.report", lambda: {"ready": False, "platform": [{"what": "x"}],
                                                          "tenants": {}})
    client = TestClient(server.app, raise_server_exceptions=False)
    results = {r.name: r for r in smoke.run_checks(client, api_key="the-key")}
    bad = {n: (r.status, r.detail) for n, r in results.items() if not r.ok}
    assert not bad, bad
    assert results["readiness (with API key)"].status == 503


def test_real_app_with_tenant_auth_off_is_caught(monkeypatch):
    from fastapi.testclient import TestClient
    from vula.api import server

    monkeypatch.setattr(server.settings, "api_key", "the-key")
    monkeypatch.setattr(server.settings, "enforce_tenant_auth", False)
    monkeypatch.setattr("vula.commerce.service._client",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    client = TestClient(server.app, raise_server_exceptions=False)
    r = smoke._check(client, "tenant admin route, no auth", "GET",
                     f"/v1/commerce/{smoke.SMOKE_TENANT}/admin/orders", (401,))
    assert not r.ok

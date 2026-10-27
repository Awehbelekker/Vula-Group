"""Gap audit 1.1: one report of what each switched-on feature still needs, and /ready."""
import pytest

from vula import readiness, startup_checks


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, _cols):
        return self

    def eq(self, k, v):
        return _Q([r for r in self.rows if r.get(k) == v])

    def execute(self):
        return type("R", (), {"data": list(self.rows)})()


class _DB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables.get(name, []))


@pytest.fixture
def setup(monkeypatch):
    s = readiness.settings
    for k, v in {"api_key": "k", "supabase_url": "https://x.supabase.co", "supabase_service_key": "s",
                 "whatsapp_token": "t", "openrouter_api_key": "o", "enforce_tenant_auth": True,
                 "platform_support_phone": "27820000000", "tap_reminder_template": "",
                 "vapid_public_key": "", "vapid_private_key": "", "tap_hash_pepper": "",
                 "tap_to_pay_tenants": ""}.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr("vula.api.tenants.is_active", lambda t: t != "suspended")
    monkeypatch.setattr(startup_checks, "unready_payment_tenants", lambda: ["bakery"])
    monkeypatch.setattr(startup_checks, "LAST_MISSING", [])
    db = _DB({
        "vula_tenant_config": [{"tenant_id": t} for t in ("digg-demo", "gerflor", "bakery", "suspended")],
        "vula_whatsapp_accounts": [{"tenant_id": "digg-demo", "status": "connected"},
                                   {"tenant_id": "gerflor", "status": "disconnected"}],
        "kb_settings": [{"tenant_id": "gerflor", "mode": "testing"}, {"tenant_id": "digg-demo", "mode": "off"}],
        "vula_payment_providers": [],
    })
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    return db


def test_nothing_missing_on_the_platform(setup):
    rep = readiness.report()
    assert rep["ready"] and rep["platform"] == []


def test_switched_on_features_list_what_they_need(setup):
    rep = readiness.report()
    gerflor = [g["what"] for g in rep["tenants"]["gerflor"]]
    assert any("no PayFast account" in w for w in gerflor)
    assert any("no connected WhatsApp number" in w for w in gerflor)
    assert any("push notifications" in w for w in gerflor)
    # testing mode: live-only items (reminder template, pepper) aren't asked for yet
    assert not any("reminder" in w or "TAP_HASH_PEPPER" in w for w in gerflor)
    assert [g["what"] for g in rep["tenants"]["bakery"]] == [
        "No way to take payment (no card gateway, no EFT details)"]
    # Tap to Pay off: no Tap to Pay gaps; a suspended tenant isn't checked at all
    assert "digg-demo" in rep["tenants_ready"] and "suspended" not in rep["tenants_ready"]
    assert all(g["fix"] for gs in rep["tenants"].values() for g in gs)


def test_live_needs_live_payfast_and_the_live_only_settings(setup):
    setup.tables["kb_settings"] = [{"tenant_id": "digg-demo", "mode": "live"}]
    setup.tables["vula_payment_providers"] = [{"tenant_id": "digg-demo", "provider": "payfast",
                                               "mode": "test", "active": True}]
    gaps = [g["what"] for g in readiness.report()["tenants"]["digg-demo"]]
    assert any("sandbox" in w for w in gaps)
    assert any("reminders" in w for w in gaps)
    assert any("Supabase key" in w for w in gaps)


def test_operator_override_counts_as_live(setup, monkeypatch):
    monkeypatch.setattr(readiness.settings, "tap_to_pay_tenants", "bakery")
    gaps = [g["what"] for g in readiness.report()["tenants"]["bakery"]]
    assert any("Tap to Pay is live" in w for w in gaps)


def test_platform_gaps_make_it_not_ready(setup, monkeypatch):
    monkeypatch.setattr(readiness.settings, "enforce_tenant_auth", False)
    monkeypatch.setattr(startup_checks, "LAST_MISSING", ["migration 229 (vula_project_direct_payments)"])
    rep = readiness.report()
    assert not rep["ready"]
    whats = [g["what"] for g in rep["platform"]]
    assert any("access-checked" in w for w in whats) and any("229" in w for w in whats)


def test_summary_is_one_line_with_a_count(setup):
    line = readiness.summary(readiness.report())
    assert line.startswith("🔧 Vula setup: 4 thing(s)") and "\n" not in line and "GET /ready" in line
    assert readiness.summary({"platform": [], "tenants": {}}) is None


def test_ready_needs_the_api_key():
    import inspect
    import vula.api.server as server
    src = inspect.getsource(server)
    assert '@app.get("/ready", dependencies=[Depends(require_auth)])' in src


def test_same_gaps_alert_once_across_redeploys(tmp_path, monkeypatch):
    monkeypatch.setattr(readiness.settings, "data_dir", tmp_path)
    assert readiness.is_new("🔧 Vula setup: 1 thing(s)")
    assert not readiness.is_new("🔧 Vula setup: 1 thing(s)")
    assert readiness.is_new("🔧 Vula setup: 2 thing(s)")

"""Tests for the onboarding API — no Supabase or WhatsApp needed."""
import hashlib as _hashlib
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from vula.api.server import app

client = TestClient(app)


# ── Input validation ──────────────────────────────────────────────────────────

def test_onboard_rejects_missing_fields():
    resp = client.post("/v1/onboard", json={})
    assert resp.status_code == 422


def test_onboard_rejects_invalid_email():
    resp = client.post("/v1/onboard", json={
        "company_name": "Test Co",
        "industry": "Construction",
        "contact_name": "Jane",
        "email": "not-an-email",
        "plan": "starter",
    })
    assert resp.status_code == 422


def test_onboard_rejects_invalid_plan():
    resp = client.post("/v1/onboard", json={
        "company_name": "Test Co",
        "industry": "Construction",
        "contact_name": "Jane",
        "email": "jane@test.co.za",
        "plan": "enterprise",  # not a valid plan
    })
    assert resp.status_code == 422


def test_onboard_rejects_empty_company_name():
    resp = client.post("/v1/onboard", json={
        "company_name": "   ",
        "industry": "Construction",
        "contact_name": "Jane",
        "email": "jane@test.co.za",
        "plan": "starter",
    })
    assert resp.status_code == 422


# ── Successful onboarding (mocked Supabase) ───────────────────────────────────

def test_onboard_success_when_supabase_not_configured():
    """When Supabase is not configured the API still provisions in-memory."""
    with patch("vula.api.onboarding._supabase") as mock_sb:
        mock_sb.select = AsyncMock(return_value=[])   # no duplicate
        mock_sb.insert = AsyncMock(return_value={})
        mock_sb.update = AsyncMock(return_value=None)

        resp = client.post("/v1/onboard", json={
            "company_name": "DIGG Interiors",
            "industry": "Construction & Engineering",
            "contact_name": "Judy Smith",
            "email": "judy@digg.co.za",
            "plan": "growth",
            "whatsapp": "+27821234567",
            "pain_points": ["Invoicing and cashflow is manual and slow"],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"]
    assert "digg-interiors" in data["workspace_url"]
    assert data["temp_password"]
    assert data["trial_ends"]


def test_onboard_409_on_duplicate_email():
    with patch("vula.api.onboarding._supabase") as mock_sb:
        mock_sb.select = AsyncMock(return_value=[{"email": "judy@digg.co.za"}])

        resp = client.post("/v1/onboard", json={
            "company_name": "DIGG Interiors",
            "industry": "Construction & Engineering",
            "contact_name": "Judy Smith",
            "email": "judy@digg.co.za",
            "plan": "growth",
        })

    assert resp.status_code == 409


# ── Tenant status ─────────────────────────────────────────────────────────────

def test_tenant_status_404_for_unknown():
    with patch("vula.api.onboarding._supabase") as mock_sb:
        mock_sb.select = AsyncMock(return_value=[])
        resp = client.get("/v1/tenant/nonexistent-id/status")
    assert resp.status_code == 404


def test_tenant_status_returns_fields():
    fake_tenant = {
        "tenant_id": "abc-123",
        "status": "active",
        "workspace_url": "https://app.vula.ai/digg-interiors",
        "trial_ends": "2026-06-21T00:00:00",
        "plan": "growth",
    }
    with patch("vula.api.onboarding._supabase") as mock_sb:
        mock_sb.select = AsyncMock(return_value=[fake_tenant])
        resp = client.get("/v1/tenant/abc-123/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["plan"] == "growth"
    assert data["trial_ends"] == "2026-06-21"


# ── Helpers ───────────────────────────────────────────────────────────────────

def test_slugify():
    from vula.api.onboarding import _slugify
    assert _slugify("DIGG Interiors Pty Ltd") == "digg-interiors-pty-ltd"
    assert _slugify("Smith & Associates") == "smith-associates"
    assert _slugify("  Spaces  ") == "spaces"


def test_valid_plans():
    from vula.api.onboarding import TIERS
    assert "starter" in TIERS
    assert "growth" in TIERS
    assert "business" in TIERS
    for tier_id, tier in TIERS.items():
        assert "price_cents" in tier
        assert tier["price_cents"] > 0


# ── PayFast ───────────────────────────────────────────────────────────────────

def test_payfast_url_returns_none_when_not_configured():
    from vula.api.onboarding import _payfast_url
    with patch("vula.api.onboarding.settings") as mock_settings:
        mock_settings.payfast_merchant_id = ""
        mock_settings.payfast_merchant_key = ""
        url = _payfast_url("tid-123", "growth", "test@test.co.za", "Jane Smith")
    assert url is None


def test_payfast_url_returns_sandbox_url_when_debug():
    from vula.api.onboarding import _payfast_url
    with patch("vula.api.onboarding.settings") as mock_settings:
        mock_settings.payfast_merchant_id = "10012345"
        mock_settings.payfast_merchant_key = "testkey"
        mock_settings.debug = True
        mock_settings.vula_base_url = "https://app.vula.ai"
        url = _payfast_url("tid-123", "growth", "test@test.co.za", "Jane Smith")
    assert url is not None
    assert "sandbox.payfast.co.za" in url
    assert "10012345" in url


def test_payfast_url_contains_required_params():
    from vula.api.onboarding import _payfast_url
    with patch("vula.api.onboarding.settings") as mock_settings:
        mock_settings.payfast_merchant_id = "10012345"
        mock_settings.payfast_merchant_key = "testkey"
        mock_settings.debug = True
        mock_settings.vula_base_url = "https://app.vula.ai"
        url = _payfast_url("tid-abc", "business", "ceo@bigco.co.za", "John Doe")
    assert "subscription_type" in url
    assert "frequency=3" in url   # monthly
    assert "cycles=0" in url      # until cancelled


# ── Admin auth ────────────────────────────────────────────────────────────────

def test_admin_signups_open_when_no_api_key_set():
    """Without API_KEY configured, admin endpoint is accessible (dev mode)."""
    with patch("vula.api.onboarding.settings") as mock_settings:
        mock_settings.api_key = ""
        mock_settings.supabase_url = ""
        mock_settings.supabase_service_key = ""
        resp = client.get("/v1/admin/signups")
    assert resp.status_code == 200


def test_admin_signups_requires_key_when_configured():
    with patch("vula.api.onboarding.settings") as mock_settings:
        mock_settings.api_key = "secret123"
        resp = client.get("/v1/admin/signups")
    assert resp.status_code == 401


# ── /onboard and /onboard/documents now require the same admin gate (2026-08-15) ───
# Previously fully public — anyone could create vula_tenants rows and trigger real welcome
# emails/WhatsApp sends to an attacker-supplied address, with no auth at all.

def test_onboard_requires_key_when_configured():
    with patch("vula.api.onboarding.settings") as mock_settings:
        mock_settings.api_key = "secret123"
        resp = client.post("/v1/onboard", json={
            "company_name": "Test Co", "industry": "Construction",
            "contact_name": "Jane", "email": "jane@test.co.za", "plan": "starter",
        })
    assert resp.status_code == 401


def test_onboard_accepts_valid_api_key():
    from vula.api.onboarding import settings as onboarding_settings
    with (
        patch.object(onboarding_settings, "api_key", "secret123"),
        patch("vula.api.onboarding._supabase") as mock_sb,
    ):
        mock_sb.select = AsyncMock(return_value=[])
        mock_sb.insert = AsyncMock(return_value={})
        mock_sb.update = AsyncMock(return_value=None)
        resp = client.post("/v1/onboard", headers={"X-API-Key": "secret123"}, json={
            "company_name": "Test Co", "industry": "Construction",
            "contact_name": "Jane", "email": "jane2@test.co.za", "plan": "starter",
        })
    assert resp.status_code == 200


def test_onboard_documents_requires_key_when_configured():
    with patch("vula.api.onboarding.settings") as mock_settings:
        mock_settings.api_key = "secret123"
        resp = client.post("/v1/onboard/documents",
                           data={"tenant_id": "some-tenant"},
                           files={"files": ("test.pdf", b"content", "application/pdf")})
    assert resp.status_code == 401


# ── Auth / Login ──────────────────────────────────────────────────────────────

def _pw_hash(pw: str) -> str:
    return _hashlib.sha256(pw.encode()).hexdigest()


def test_login_success():
    fake_tenant = {
        "tenant_id": "abc-123",
        "company_name": "DIGG Interiors",
        "contact_name": "Judy Smith",
        "email": "judy@digg.co.za",
        "plan": "growth",
        "status": "active",
        "trial_ends": "2026-06-21T00:00:00",
        "workspace_url": "https://app.vula.ai/digg-interiors",
        "temp_password_hash": _pw_hash("correct-password"),
    }
    with patch("vula.api.onboarding._supabase") as mock_sb:
        mock_sb.select = AsyncMock(return_value=[fake_tenant])
        resp = client.post("/v1/auth/login", json={"email": "judy@digg.co.za", "password": "correct-password"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == "abc-123"
    assert data["company_name"] == "DIGG Interiors"
    assert data["plan"] == "growth"
    assert data["trial_ends"] == "2026-06-21"


def test_login_wrong_password():
    fake_tenant = {
        "tenant_id": "abc-123",
        "email": "judy@digg.co.za",
        "temp_password_hash": _pw_hash("correct-password"),
    }
    with patch("vula.api.onboarding._supabase") as mock_sb:
        mock_sb.select = AsyncMock(return_value=[fake_tenant])
        resp = client.post("/v1/auth/login", json={"email": "judy@digg.co.za", "password": "wrong-password"})

    assert resp.status_code == 401


def test_login_unknown_email():
    with patch("vula.api.onboarding._supabase") as mock_sb:
        mock_sb.select = AsyncMock(return_value=[])
        with patch("vula.api.onboarding.settings") as mock_settings:
            mock_settings.supabase_url = "https://project.supabase.co"
            resp = client.post("/v1/auth/login", json={"email": "nobody@example.com", "password": "pw"})

    assert resp.status_code == 401


def test_login_rejects_invalid_email():
    resp = client.post("/v1/auth/login", json={"email": "not-an-email", "password": "pw"})
    assert resp.status_code == 422


def test_login_returns_no_password_hash():
    """Ensure the temp_password_hash is never returned to the client."""
    fake_tenant = {
        "tenant_id": "abc-123",
        "company_name": "DIGG",
        "contact_name": "Judy",
        "email": "judy@digg.co.za",
        "plan": "starter",
        "status": "active",
        "trial_ends": "2026-06-21T00:00:00",
        "workspace_url": "https://app.vula.ai/digg",
        "temp_password_hash": _pw_hash("pw"),
    }
    with patch("vula.api.onboarding._supabase") as mock_sb:
        mock_sb.select = AsyncMock(return_value=[fake_tenant])
        resp = client.post("/v1/auth/login", json={"email": "judy@digg.co.za", "password": "pw"})

    assert resp.status_code == 200
    assert "temp_password_hash" not in resp.json()
    assert "password" not in resp.json()

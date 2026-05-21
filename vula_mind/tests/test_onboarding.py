"""Tests for the onboarding API — no Supabase or WhatsApp needed."""
import pytest
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

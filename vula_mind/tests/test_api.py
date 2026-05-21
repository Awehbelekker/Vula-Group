"""Tests for the FastAPI server — uses TestClient, no real Ollama/Qdrant needed."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from vula.api.server import app

client = TestClient(app)


# ── Health check ──────────────────────────────────────────────────────────────

def test_status_endpoint_returns_json():
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value.get = AsyncMock(side_effect=Exception("not running"))

        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "service" in data
        assert data["service"] == "vula-api"


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_query_without_auth_when_no_key_configured():
    """When API_KEY is empty, requests should pass through."""
    with patch("vula.api.server.settings") as mock_settings:
        mock_settings.api_key = ""  # no auth required
        # just check the endpoint exists and validates input
        resp = client.post("/query", json={"tenant_id": "", "question": "test"})
        # 422 because tenant_id is empty — that's correct validation behaviour
        assert resp.status_code == 422


# ── Input validation ──────────────────────────────────────────────────────────

def test_query_rejects_empty_question():
    resp = client.post("/query", json={"tenant_id": "test", "question": "   "})
    assert resp.status_code == 422


def test_query_rejects_invalid_tenant_id():
    resp = client.post("/query", json={"tenant_id": "../../etc/passwd", "question": "test"})
    assert resp.status_code == 422


def test_query_rejects_tenant_with_special_chars():
    resp = client.post("/query", json={"tenant_id": "tenant<script>", "question": "test"})
    assert resp.status_code == 422


def test_valid_tenant_id_formats():
    from vula.api.server import validate_tenant
    assert validate_tenant("client_001") == "client_001"
    assert validate_tenant("DIGG-001") == "DIGG-001"
    assert validate_tenant("abc123") == "abc123"


def test_invalid_tenant_id_raises():
    from fastapi import HTTPException
    from vula.api.server import validate_tenant
    with pytest.raises(HTTPException) as exc:
        validate_tenant("../etc")
    assert exc.value.status_code == 422

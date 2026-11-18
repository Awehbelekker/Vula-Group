"""Pre-launch audit (18 Nov 2026): every API response tells the browser to keep to HTTPS (HSTS) and
not to guess its type; the dashboard's Vercel config does the same for its pages."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vula.api.server import app


def test_every_api_response_carries_the_security_headers():
    r = TestClient(app).get("/v1/auth/two-step-policy")
    assert r.headers["Strict-Transport-Security"].startswith("max-age=31536000")
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_the_dashboard_pages_carry_hsts():
    path = Path(__file__).resolve().parents[2] / "vula_dashboard" / "vercel.json"
    if not path.exists():
        pytest.skip("the dashboard isn't in this checkout")
    cfg = json.loads(path.read_text())
    every_page = next(h for h in cfg["headers"] if h["source"] == "/(.*)")
    keys = {x["key"]: x["value"] for x in every_page["headers"]}
    assert keys["Strict-Transport-Security"].startswith("max-age=31536000")
    assert keys["X-Frame-Options"] == "DENY" and keys["X-Content-Type-Options"] == "nosniff"

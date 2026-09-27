"""Customer-facing messages name the right business and point at real pages (2026-09-25)."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from vula.api import menu_page, tenants


def test_display_name_falls_back_to_slug():
    with patch.object(tenants, "get_config", return_value={}):
        assert tenants.display_name("digg-demo") == "Digg Demo"
    with patch.object(tenants, "get_config", return_value={"display_name": "DIGG Architects"}):
        assert tenants.display_name("digg-demo") == "DIGG Architects"


def test_payment_result_pages_exist():
    app = FastAPI()
    app.include_router(menu_page.router)
    c = TestClient(app)
    assert c.get("/payment/success?order=OTH-1").status_code == 200
    assert "not completed" in c.get("/payment/cancel").text

"""The redesign opt-in reaches the dashboard (2026-10-17, migration 215): GET /v1/tenants/{t}
returns ui_v2 (default off) and ui_labels, which lib/flags.js reads."""
from unittest.mock import patch

from vula.api import tenants


def test_public_shape_carries_the_redesign_flag_default_off():
    with patch("vula.api.tenants.tenant_profile", return_value={}):
        out = tenants._public({"tenant_id": "harbour-deli"})
    assert out["ui_v2"] is False and out["ui_labels"] is None


def test_public_shape_carries_a_switched_on_tenant_and_its_labels():
    labels = {"sections": {"people": "Care"}}
    with patch("vula.api.tenants.tenant_profile", return_value={}):
        out = tenants._public({"tenant_id": "harbour-deli", "ui_v2": True, "ui_labels": labels})
    assert out["ui_v2"] is True and out["ui_labels"] == labels

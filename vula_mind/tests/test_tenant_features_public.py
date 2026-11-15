"""15 Nov 2026: the dashboard shows a piloted screen (Estimating › Costing by trade) only where its
switch is on, so the tenant read carries the switches that are on — names only."""
from unittest.mock import patch

from vula.api import tenants


def test_the_tenant_read_lists_only_the_switches_that_are_on():
    with patch("vula.features.on", lambda t, name: t == "digg-demo" and name in ("trade_costing", "open_chat")):
        out = tenants._public({"tenant_id": "digg-demo", "display_name": "DIGG"})
        other = tenants._public({"tenant_id": "other", "display_name": "Other"})
    assert sorted(out["features"]) == ["open_chat", "trade_costing"]
    assert other["features"] == []


def test_a_switch_read_that_fails_shows_none():
    with patch("vula.features.on", side_effect=RuntimeError("down")):
        assert tenants._public({"tenant_id": "digg-demo"})["features"] == []
    assert tenants._public({})["features"] == []

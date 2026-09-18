"""Tests for _product_tools_for (2026-09-18): update_product's is_daily_catch field used to be
exposed to every tenant with the products module on, regardless of vertical — a seafood-only
concept in every tenant's tool schema. Now gated on business_type == "food".
"""
from unittest.mock import patch

from core.skills.commerce_admin import _product_tools_for, _tools_for, PRODUCT_TOOLS

TID = "test-tenant"


def _update_product_schema(tools):
    spec = next(t for t in tools if t["function"]["name"] == "update_product")
    return spec["function"]["parameters"]["properties"]


def test_food_tenant_keeps_is_daily_catch():
    with patch("vula.api.tenants.get_config", return_value={"business_type": "food"}):
        tools = _product_tools_for(TID)
    assert "is_daily_catch" in _update_product_schema(tools)


def test_non_food_tenant_has_is_daily_catch_stripped():
    with patch("vula.api.tenants.get_config", return_value={"business_type": "services"}):
        tools = _product_tools_for(TID)
    assert "is_daily_catch" not in _update_product_schema(tools)


def test_unknown_business_type_defaults_to_stripped():
    """Fail toward the generic (safer for most tenants) schema when the tenant config lookup
    fails or business_type is unset, rather than assuming food."""
    with patch("vula.api.tenants.get_config", side_effect=RuntimeError("no config")):
        tools = _product_tools_for(TID)
    assert "is_daily_catch" not in _update_product_schema(tools)


def test_create_product_schema_is_unaffected():
    with patch("vula.api.tenants.get_config", return_value={"business_type": "services"}):
        tools = _product_tools_for(TID)
    names = [t["function"]["name"] for t in tools]
    assert "create_product" in names
    assert len(tools) == len(PRODUCT_TOOLS)


def test_shared_product_tools_constant_never_mutated():
    """The trim must deep-copy — repeated calls for a non-food tenant must never leave
    is_daily_catch missing for a LATER food tenant sharing the same process."""
    with patch("vula.api.tenants.get_config", return_value={"business_type": "services"}):
        _product_tools_for(TID)
    original_schema = next(t for t in PRODUCT_TOOLS if t["function"]["name"] == "update_product")
    assert "is_daily_catch" in original_schema["function"]["parameters"]["properties"]


def test_tools_for_uses_business_type_gating_end_to_end():
    with (
        patch("vula.api.tenants.enabled_modules", return_value=["products"]),
        patch("vula.api.tenants.get_config", return_value={"business_type": "services"}),
    ):
        tools = _tools_for(TID, role=None)
    assert "is_daily_catch" not in _update_product_schema(tools)

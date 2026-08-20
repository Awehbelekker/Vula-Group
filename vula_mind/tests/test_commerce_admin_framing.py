"""Tests for the "AI employee" framing pass — commerce_admin.py's _role_label() and how it's
woven into the owner/staff and sales_rep system prompts. Framing only: none of this changes
tool availability or behavior, only how Vula introduces itself.
"""
from unittest.mock import patch

import pytest

from core.skills.commerce_admin import CommerceAdminSkill, _role_label, _DEFAULT_ROLE_LABEL

TID = "test-tenant"


@pytest.fixture
def skill():
    return CommerceAdminSkill()


def test_role_label_picks_bookkeeper_when_invoices_enabled():
    with patch("vula.api.tenants.enabled_modules", return_value=["invoices", "broadcasts"]):
        assert _role_label(TID) == "AI bookkeeper"


def test_role_label_falls_back_to_default_when_nothing_matches():
    with patch("vula.api.tenants.enabled_modules", return_value=["overview"]):
        assert _role_label(TID) == _DEFAULT_ROLE_LABEL


def test_role_label_falls_back_to_default_on_error():
    with patch("vula.api.tenants.enabled_modules", side_effect=Exception("no config")):
        assert _role_label(TID) == _DEFAULT_ROLE_LABEL


def test_role_label_respects_priority_order_over_set_iteration_order():
    # broadcasts AND invoices both enabled — invoices (earlier in _ROLE_LABELS) should win,
    # not whichever the underlying set happens to iterate first.
    with patch("vula.api.tenants.enabled_modules", return_value=["broadcasts", "invoices"]):
        assert _role_label(TID) == "AI bookkeeper"


def test_owner_system_prompt_introduces_role_label(skill):
    with patch("vula.api.tenants.enabled_modules", return_value=["purchase_orders"]):
        prompt = skill._system_prompt(TID, role=None, name="Owner")
    assert "AI procurement assistant" in prompt


def test_owner_system_prompt_falls_back_to_default_label(skill):
    with patch("vula.api.tenants.enabled_modules", return_value=[]):
        prompt = skill._system_prompt(TID, role=None)
    assert _DEFAULT_ROLE_LABEL in prompt


def test_sales_rep_system_prompt_introduces_itself_as_ai_sales_assistant(skill):
    prompt = skill._system_prompt(TID, role="sales_rep", name="Thabo")
    assert "AI sales assistant" in prompt
    assert "Thabo" in prompt

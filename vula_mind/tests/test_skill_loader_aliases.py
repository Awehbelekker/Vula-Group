"""Regression test: the "financial_reasoning" keyword-router alias used to resolve to
architecture_planning (construction-professional framing), so a generic knowledge-mode
revenue/profit/budget/cashflow question got answered in a construction-advisor persona. Now
that finance_admin.py is a real, implemented, tool-grounded skill, it should resolve there
instead.
"""
from core.skills.loader import get_skill
from core.skills.finance_admin import FinanceAdminSkill


def test_financial_reasoning_alias_resolves_to_finance_admin():
    skill = get_skill("financial_reasoning")
    assert isinstance(skill, FinanceAdminSkill)

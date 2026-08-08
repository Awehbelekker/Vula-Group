"""Tests for turning on adversarial verification for commerce_admin.py and finance_admin.py
(2026-08 accuracy audit follow-up).

core/verification.py's mechanism itself is already exhaustively tested generically
(tests/test_verification.py, via DummySkill). These tests confirm specifically that the two
real skills chosen for rollout (highest-stakes real-money skills — commerce_admin does real
invoice/stock/broadcast mutations, finance_admin reports money figures) actually have the
policy set, and that the full __call__() path (skill run → verification hook) works
end-to-end for both without any skill-specific wiring issue the generic test can't catch.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core import verification
from core.skills.base import SkillInput
from core.skills.commerce_admin import CommerceAdminSkill
from core.skills.finance_admin import FinanceAdminSkill

TENANT = "off-the-hook"


def test_commerce_admin_has_adversarial_policy():
    assert CommerceAdminSkill().verification_policy == "adversarial"


def test_finance_admin_has_adversarial_policy():
    assert FinanceAdminSkill().verification_policy == "adversarial"


@pytest.mark.asyncio
async def test_commerce_admin_full_call_path_runs_adversarial_check(monkeypatch):
    """End-to-end through __call__() (not just _agent_loop directly) — confirms the
    verification hook actually fires for this real skill, not just the generic DummySkill."""
    checked = {}

    async def _fake_check(question, answer, context=""):
        checked["called"] = True
        return {"verdict": "pass", "defects": [], "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _fake_check)

    async def _fake_run(self, inp):
        from core.skills.base import SkillOutput
        return SkillOutput(answer="Today's sales: R4,500.", skill_name="commerce_admin", confidence=0.8)

    with patch.object(CommerceAdminSkill, "run", new=_fake_run):
        out = await CommerceAdminSkill()(SkillInput(question="what were today's sales?", tenant_id=TENANT))

    assert checked.get("called") is True
    assert out.answer == "Today's sales: R4,500."
    assert out.verification["outcome"] == "accepted"


@pytest.mark.asyncio
async def test_finance_admin_full_call_path_flags_a_defect(monkeypatch):
    async def _fake_check(question, answer, context=""):
        return {"verdict": "fail", "defects": ["figure doesn't match the ledger"], "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _fake_check)

    async def _fake_run(self, inp):
        from core.skills.base import SkillOutput
        return SkillOutput(answer="HPC spent R18,500.", skill_name="finance_admin", confidence=0.8)

    with patch.object(FinanceAdminSkill, "run", new=_fake_run):
        out = await FinanceAdminSkill()(SkillInput(question="how much on HPC?", tenant_id=TENANT))

    assert out.confidence == 0.45
    assert "⚠️" in out.answer
    assert out.verification["defects"] == ["figure doesn't match the ledger"]

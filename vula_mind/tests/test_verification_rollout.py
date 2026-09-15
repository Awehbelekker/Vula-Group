"""Tests for turning on adversarial verification for commerce_admin.py, finance_admin.py
(2026-08 accuracy audit follow-up), and standards_lookup.py (2026-09-15, Master Build Brief
section 5a).

core/verification.py's mechanism itself is already exhaustively tested generically
(tests/test_verification.py, via DummySkill). These tests confirm specifically that the real
skills chosen for rollout actually have the policy set, and that the full __call__() path
(skill run → verification hook) works end-to-end for each without any skill-specific wiring
issue the generic test can't catch. standards_lookup additionally gets a test confirming the
checker actually receives its KB sources as grounding context (not a blind check) — the exact
gap base.py's tool_source() docstring warns commerce_admin/commerce_assistant/finance_admin
used to have before they were fixed.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core import verification
from core.skills.base import SkillInput
from core.skills.commerce_admin import CommerceAdminSkill
from core.skills.finance_admin import FinanceAdminSkill
from core.skills.standards_lookup import StandardsLookupSkill

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


def test_standards_lookup_has_adversarial_policy():
    assert StandardsLookupSkill().verification_policy == "adversarial"


@pytest.mark.asyncio
async def test_standards_lookup_full_call_path_runs_adversarial_check(monkeypatch):
    checked = {}

    async def _fake_check(question, answer, context=""):
        checked["called"] = True
        checked["context"] = context
        return {"verdict": "pass", "defects": [], "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _fake_check)

    async def _fake_run(self, inp):
        from core.skills.base import SkillOutput
        return SkillOutput(
            answer="SANS 10400-A clause 4.2 requires a minimum ceiling height of 2.4m.",
            skill_name="standards_lookup", confidence=0.8,
            sources=[{"type": "kb", "filename": "SANS10400-A.pdf",
                      "text": "4.2 Minimum ceiling height shall be 2.4m for habitable rooms."}],
        )

    with patch.object(StandardsLookupSkill, "run", new=_fake_run):
        out = await StandardsLookupSkill()(
            SkillInput(question="minimum ceiling height SANS 10400-A", tenant_id=TENANT))

    assert checked.get("called") is True
    # The whole point of the fix: standards_lookup already tags its sources {"type": "kb"} —
    # confirm the checker actually receives that text as grounding, not an empty string.
    assert "2.4m" in checked["context"]
    assert out.verification["outcome"] == "accepted"


@pytest.mark.asyncio
async def test_standards_lookup_full_call_path_flags_a_defect(monkeypatch):
    async def _fake_check(question, answer, context=""):
        return {"verdict": "fail", "defects": ["clause number doesn't match the cited standard"],
                "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _fake_check)

    async def _fake_run(self, inp):
        from core.skills.base import SkillOutput
        return SkillOutput(answer="SANS 10400-A clause 9.9 requires a 2.4m ceiling.",
                            skill_name="standards_lookup", confidence=0.8,
                            sources=[{"type": "kb", "filename": "SANS10400-A.pdf",
                                      "text": "4.2 Minimum ceiling height shall be 2.4m."}])

    with patch.object(StandardsLookupSkill, "run", new=_fake_run):
        out = await StandardsLookupSkill()(
            SkillInput(question="minimum ceiling height SANS 10400-A", tenant_id=TENANT))

    assert out.confidence == 0.45
    assert "⚠️" in out.answer
    assert out.verification["defects"] == ["clause number doesn't match the cited standard"]

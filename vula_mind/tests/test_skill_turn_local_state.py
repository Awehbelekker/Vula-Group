"""Per-request skill state is task-local (2026-09-25 review): skills are process-wide
singletons, and finance_admin/calculations kept each turn's verified figures on self, so two
concurrent tenants could mix up their anchors and sources."""
import asyncio

import pytest

from core.skills.base import begin_turn, turn_local


class _Skill:
    _verified = turn_local()


@pytest.mark.asyncio
async def test_concurrent_turns_do_not_share_state():
    skill = _Skill()
    seen = {}

    async def turn(tag, delay):
        begin_turn()
        skill._verified = []
        skill._verified.append(tag)
        await asyncio.sleep(delay)
        skill._verified.append(tag)
        seen[tag] = list(skill._verified)

    await asyncio.gather(turn("a", 0.02), turn("b", 0.01))
    assert seen == {"a": ["a", "a"], "b": ["b", "b"]}


def test_unset_attribute_behaves_like_missing_instance_attr():
    skill = _Skill()

    async def fresh():
        begin_turn()
        return hasattr(skill, "_verified"), getattr(skill, "_verified", "dflt")

    assert asyncio.run(fresh()) == (False, "dflt")


def test_finance_and_calculations_declare_turn_local():
    from core.skills.calculations import CalculationsSkill
    from core.skills.finance_admin import FinanceAdminSkill
    assert isinstance(FinanceAdminSkill.__dict__["_sources"], turn_local)
    assert isinstance(CalculationsSkill.__dict__["_verified"], turn_local)

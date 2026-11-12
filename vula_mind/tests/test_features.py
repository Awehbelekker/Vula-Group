"""Switches on for one business before everyone (vula/features.py, migration 261): each new
behaviour can be piloted on one business while every other business is unchanged."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from config import settings
from tests.fake_supabase import FakeSupabase
from vula import features
from vula.api import tenants

A, B = "off-the-hook", "digg"


@pytest.fixture()
def configs(monkeypatch):
    rows = {A: {"tenant_id": A, "features": {}}, B: {"tenant_id": B, "features": {}}}
    monkeypatch.setattr(tenants, "get_config", lambda tid, fresh=False: rows.get(tid, {}))
    return rows


def test_a_switch_on_for_one_business_is_off_for_the_others(configs):
    configs[A]["features"] = {"voice_compose": True}
    assert features.on(A, "voice_compose") and not features.on(B, "voice_compose")
    assert not features.on(None, "voice_compose")


def test_the_global_setting_still_turns_it_on_for_everyone(configs):
    with patch.object(settings, "client_memory_enabled", True):
        assert features.on(A, "client_memory") and features.on(B, "client_memory") and features.on(None, "client_memory")


def test_an_unreadable_config_counts_as_off(monkeypatch):
    monkeypatch.setattr(tenants, "get_config", MagicMock(side_effect=RuntimeError("db down")))
    assert features.on(A, "voice_compose") is False
    with pytest.raises(KeyError):
        features.on(A, "not_a_feature")


def test_validate_and_listing(configs):
    assert features.validate({"voice_compose": 1, "advisor_mode": 0}) == {"voice_compose": True, "advisor_mode": False}
    for bad in ({}, {"make_coffee": True}, "x"):
        with pytest.raises(ValueError):
            features.validate(bad)
    configs[A]["features"] = {"advisor_mode": True}
    rows = {r["name"]: r for r in features.listing(A)}
    assert set(rows) == set(features.FEATURES) and rows["advisor_mode"]["here"] and not rows["voice_compose"]["here"]


# ── each switch, per business ───────────────────────────────────────────────────────────────

def test_voice_follows_the_business(configs):
    from core import voice
    from core.skills.base import behaviour_preamble
    configs[A].update({"features": {"voice_compose": True}, "team_voice_prompt": "Direct."})
    configs[B].update({"team_voice_prompt": "Direct."})
    with voice.use(A, "team", "whatsapp"):
        assert "How this business sounds: Direct." in behaviour_preamble()
    with voice.use(B, "team", "whatsapp"):
        assert "How this business sounds" not in behaviour_preamble()


def test_private_notes_follow_the_business(configs):
    from vula import identity, personal_memory
    configs[A]["features"] = {"personal_memory": True}
    db = FakeSupabase({personal_memory.TABLE: []})
    with patch("vula.commerce.service._client", return_value=db):
        a = identity.Person(A, member_id="11111111-1111-1111-1111-111111111111")
        b = identity.Person(B, member_id="22222222-2222-2222-2222-222222222222")
        assert "just for you" in personal_memory.handle(a, "remember that I prefer short answers")
        assert personal_memory.handle(b, "remember that I prefer short answers") is None
    assert len(db.rows[personal_memory.TABLE]) == 1


def test_client_memory_follows_the_business(configs):
    from vula import client_memory
    configs[A]["features"] = {"client_memory": True}
    note = [{"tenant_id": t, "client_key": "27821112222", "summary": "2026-10-01: prefers hake.", "covers_until": "x"}
            for t in (A, B)]
    with patch("vula.commerce.service._client", return_value=FakeSupabase({client_memory.TABLE: note})):
        assert "prefers hake" in client_memory.block_for_prompt(A, "27821112222")
        assert client_memory.block_for_prompt(B, "27821112222") == ""


@pytest.mark.asyncio
async def test_strict_grounding_follows_the_business(configs):
    from core.skills.base import BUSINESS_FACT_NOT_ON_FILE_CUSTOMER, SkillInput
    from core.skills.reasoning import ReasoningSkill
    configs[A]["features"] = {"strict_tenant_grounding": True}
    pipeline = MagicMock()
    pipeline.query = AsyncMock(return_value=[])
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipeline), \
         patch("vula.kb_learning.record_turn"):
        out = await ReasoningSkill().run(SkillInput(question="Do you deliver to Paarl?", tenant_id=A))
        assert out.answer == BUSINESS_FACT_NOT_ON_FILE_CUSTOMER
        with patch("core.skills.loader.get_skill", return_value=AsyncMock(return_value=MagicMock(success=False))), \
             patch("core.llm_router.resolve_generation_route", AsyncMock(side_effect=RuntimeError("stop"))):
            out_b = await ReasoningSkill()(SkillInput(question="Do you deliver to Paarl?", tenant_id=B))
    assert out_b.answer != BUSINESS_FACT_NOT_ON_FILE_CUSTOMER


def test_short_answer_checks_follow_the_business(configs):
    from core import verification
    configs[A]["features"] = {"verify_short_assertions": True}
    kb = [{"type": "kb", "text": "We deliver to the CBD only."}]
    assert verification.nothing_to_check("Yes, we deliver to Paarl daily.", kb, A) == ""
    assert verification.nothing_to_check("Yes, we deliver to Paarl daily.", kb, B) == "no_figures_short"


def test_follow_up_routing_follows_the_business(configs):
    from core.hrm import orchestrator as orch
    configs[A]["features"] = {"follow_up_routing": True}
    o = orch.HRMOrchestrator.__new__(orch.HRMOrchestrator)
    prev = {"skill": "finance_admin", "question": "fuel spend this week?"}
    with orch.route_context(prev, A):
        assert "Context:" in o._classifier_prompt("and next week?")
    with orch.route_context(prev, B):
        assert "Context:" not in o._classifier_prompt("and next week?")


@pytest.mark.asyncio
async def test_earlier_topics_follow_the_business(configs):
    from datetime import datetime, timedelta, timezone
    from vula.commerce import service
    configs[A]["features"] = {"history_earlier_topics": True}
    when = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    db = FakeSupabase({"commerce_conversation_messages": [
        {"tenant_id": t, "session_id": "s", "role": "user", "content": "plan the roster", "created_at": when} for t in (A, B)]})
    with patch.object(service, "_client", return_value=db):
        assert "plan the roster" in await service.earlier_topics(A, "s")
        assert await service.earlier_topics(B, "s") == ""


def test_the_advisor_follows_the_business(configs):
    from core.skills import employees as em
    configs[A]["features"] = {"advisor_mode": True}
    with patch.object(em, "_client", side_effect=RuntimeError("no saved settings")):
        assert em.settings_for(A)["advisor"]["enabled"] is True
        assert em.settings_for(B)["advisor"]["enabled"] is False


# ── the operator's switch ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_operator_sets_a_business_switch_and_it_is_read_back_and_audited(monkeypatch):
    from vula.api import master
    db = FakeSupabase({"vula_tenant_config": [{"tenant_id": A, "features": {"advisor_mode": True}},
                                              {"tenant_id": B, "features": {}}]})
    monkeypatch.setattr(master, "_client", lambda: db)
    monkeypatch.setattr(tenants, "get_config",
                        lambda tid, fresh=False: next((r for r in db.rows["vula_tenant_config"] if r["tenant_id"] == tid), {}))
    audit = MagicMock()
    monkeypatch.setattr(master, "audit", audit)
    out = await master.master_set_tenant_features(A, {"voice_compose": True}, identity={"email": "op@vula"})
    rows = {r["tenant_id"]: r["features"] for r in db.rows["vula_tenant_config"]}
    assert rows[A] == {"advisor_mode": True, "voice_compose": True} and rows[B] == {}
    assert {r["name"]: r["here"] for r in out["features"]}["voice_compose"] is True
    audit.assert_called_once_with({"email": "op@vula"}, "tenant_features", A, patch={"voice_compose": True})
    for bad, code in (({"make_coffee": True}, 400), ({}, 400)):
        with pytest.raises(HTTPException) as e:
            await master.master_set_tenant_features(A, bad, identity={})
        assert e.value.status_code == code
    with pytest.raises(HTTPException) as e:
        await master.master_set_tenant_features("nobody", {"voice_compose": True}, identity={})
    assert e.value.status_code == 404

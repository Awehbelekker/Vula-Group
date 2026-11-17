"""Research findings kept and reused (vula/research_memory.py, migration 269, 13 Nov 2026): a
clean, cited answer is kept for the team unless the person keeps it private; prices are reused for
30 days and everything else for 6 months; a similar question is answered from it with its date and
no new searching; "Research again" replaces it; another business's notes, a colleague's private
one, an expired or replaced one, and one about a different product are never reused.
Made-up names, sites and figures."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula import identity, open_chat, privacy_data, research, research_memory as rm
from vula.api import commerce
from vula.commerce import service

T, OTHER = "digg-demo", "other-business"
JUDY, SAM = "m:m1", "m:m2"
RESULT = {"answer": "The Surf Shop lists the 9'6 day bag at R 1 450 [1].", "searched": ["board bag prices"],
          "read": 2, "unchecked": [], "documents": [],
          "sources": [{"n": 1, "title": "Surf Shop", "url": "https://shop.example/bags"}]}


@pytest.fixture()
def db():
    d = FakeSupabase({"vula_research_notes": []})
    with patch.object(service, "_client", return_value=d), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline.ingest_text", AsyncMock()), \
         patch.object(rm, "_candidates", AsyncMock(side_effect=RuntimeError("no index here"))):
        yield d


def _days(n):
    return (datetime.now(timezone.utc) + timedelta(days=n)).isoformat()


# ── keeping ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_clean_finding_is_kept_for_the_team_with_its_topic_lifetime(db):
    nid = await rm.save(T, JUDY, "What do 9'6 day bags cost?", RESULT)
    row = db.rows["vula_research_notes"][0]
    assert row["id"] == nid and row["shared"] is True and row["kind"] == "prices"
    assert row["created_by"] == JUDY and row["sources"][0]["url"] == "https://shop.example/bags"
    life = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
    assert life.days == 30
    await rm.save(T, JUDY, "How should a timber deck be sealed?", {**RESULT, "answer": "Oil it yearly [1]."})
    assert db.rows["vula_research_notes"][1]["kind"] == "general"


@pytest.mark.asyncio
async def test_an_unsourced_or_unchecked_finding_is_not_kept(db):
    assert await rm.save(T, JUDY, "q", {**RESULT, "sources": []}) is None
    assert await rm.save(T, JUDY, "q", {**RESULT, "unchecked": ["R 999"]}) is None
    assert await rm.save(T, JUDY, "q", {**RESULT, "reused": {"id": "x"}}) is None
    assert db.rows["vula_research_notes"] == []


# ── reusing ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_colleague_gets_a_shared_finding_but_not_a_private_one(db):
    await rm.save(T, JUDY, "What do 9'6 day bags cost?", RESULT)
    got = await rm.find(T, SAM, "what do 9'6 day bags cost in South Africa?")
    assert got and got["answer"].startswith("The Surf Shop")
    assert db.rows["vula_research_notes"][0]["reused_count"] == 1
    db.rows["vula_research_notes"][0]["shared"] = False
    assert await rm.find(T, SAM, "What do 9'6 day bags cost?") is None
    assert await rm.find(T, JUDY, "What do 9'6 day bags cost?")             # still hers
    assert await rm.find(OTHER, JUDY, "What do 9'6 day bags cost?") is None  # never another business


@pytest.mark.asyncio
async def test_expired_replaced_and_different_product_findings_are_not_reused(db):
    await rm.save(T, JUDY, "What do 9'6 day bags cost?", RESULT)
    row = db.rows["vula_research_notes"][0]
    assert await rm.find(T, JUDY, "What do 12' coffin bags cost?") is None   # a different product
    row["expires_at"] = _days(-1)
    assert await rm.find(T, JUDY, "What do 9'6 day bags cost?") is None
    row["expires_at"], row["superseded_by"] = _days(5), "newer"
    assert await rm.find(T, JUDY, "What do 9'6 day bags cost?") is None


@pytest.mark.asyncio
async def test_the_index_path_reads_the_row_back_and_applies_the_same_rules(db):
    nid = await rm.save(T, JUDY, "What do 9'6 day bags cost?", RESULT)
    with patch.object(rm, "_candidates", AsyncMock(return_value=[nid, "missing"])):
        assert (await rm.find(T, SAM, "What do 9'6 day bags cost?"))["id"] == nid
        db.rows["vula_research_notes"][0]["tenant_id"] = OTHER
        assert await rm.find(T, SAM, "What do 9'6 day bags cost?") is None


def test_only_the_person_who_asked_can_share_or_unshare(db):
    db.rows["vula_research_notes"].append({"id": "n1", "tenant_id": T, "created_by": JUDY, "shared": True,
                                           "created_at": _days(0), "kind": "prices"})
    assert rm.set_shared(T, SAM, "n1", False) is None
    assert rm.set_shared(T, JUDY, "n1", False)["shared"] is False
    assert db.rows["vula_research_notes"][0]["shared"] is False


# ── research mode uses it ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_question_researched_before_is_answered_without_searching(db):
    await rm.save(T, JUDY, "What do 9'6 day bags cost?", RESULT)
    plan = AsyncMock()
    with patch.object(research, "plan_queries", plan), patch.object(research, "_complete", AsyncMock()) as model:
        out = await research.run(T, "What do 9'6 day bags cost?", person_key=SAM)
    assert out["reused"]["date"] and out["reused"]["mine"] is False and out["sources"]
    plan.assert_not_called()
    model.assert_not_called()


@pytest.mark.asyncio
async def test_research_again_searches_keeps_the_new_finding_and_replaces_the_old(db):
    old = await rm.save(T, JUDY, "What do 9'6 day bags cost?", RESULT)
    with patch.object(research, "plan_queries", AsyncMock(return_value=["q"])), \
         patch.object(research, "_search", AsyncMock(return_value=[])), \
         patch.object(research, "read_pages", AsyncMock(return_value=[
             {"title": "Surf Shop", "url": "https://shop.example/bags", "text": "Day bag R 1 390. " * 30}])), \
         patch.object(research, "own_documents", AsyncMock(return_value=[])), \
         patch.object(research, "_complete", AsyncMock(return_value="Now R 1 390 at the Surf Shop [1].")):
        out = await research.run(T, "What do 9'6 day bags cost?", person_key=JUDY, fresh=True, private=True)
    assert "reused" not in out and out["note_id"] and out["shared"] is False
    rows = {r["id"]: r for r in db.rows["vula_research_notes"]}
    assert rows[old]["superseded_by"] == out["note_id"] and rows[out["note_id"]]["shared"] is False


@pytest.mark.asyncio
async def test_research_about_an_attached_file_is_neither_reused_nor_kept(db):
    await rm.save(T, JUDY, "What do 9'6 day bags cost?", RESULT)
    with patch.object(research, "plan_queries", AsyncMock(return_value=["q"])), \
         patch.object(research, "_search", AsyncMock(return_value=[])), \
         patch.object(research, "read_pages", AsyncMock(return_value=[
             {"title": "Surf Shop", "url": "https://shop.example/bags", "text": "Day bag R 1 450. " * 30}])), \
         patch.object(research, "own_documents", AsyncMock(return_value=[])), \
         patch.object(research, "_complete", AsyncMock(return_value="R 1 450 [1].")):
        out = await research.run(T, "What do 9'6 day bags cost?", person_key=JUDY,
                                 metadata={"attachments_block": "ATTACHED_FILE: prices.xlsx"})
    assert "reused" not in out and "note_id" not in out and len(db.rows["vula_research_notes"]) == 1


# ── Ask anything without the toggle ─────────────────────────────────────────────────────────

class _Skill:
    def __init__(self):
        self.seen = None

    async def __call__(self, inp):
        from core.skills.base import SkillOutput
        self.seen = inp
        return SkillOutput(answer="From research on 13 Nov…", skill_name="reasoning")


@pytest.mark.asyncio
async def test_ask_anything_answers_from_earlier_research(db):
    await rm.save(T, JUDY, "What do 9'6 day bags cost?", RESULT)
    req = MagicMock()
    req.headers = {"authorization": "Bearer tok"}
    skill = _Skill()
    open_chat.clear()
    with patch("vula.identity.person_for_login", AsyncMock(return_value=identity.Person(T, member_id="m2", role="staff"))), \
         patch("vula.personal_memory.handle", return_value=None), \
         patch("vula.personal_memory.load", AsyncMock(return_value="")), \
         patch("core.skills.loader.get_skill", return_value=skill), \
         patch("vula.features.on", side_effect=lambda t, n: n == "open_chat"):
        await commerce.admin_assistant(T, commerce.AssistantRequest(message="What do 9'6 day bags cost?", mode="open"), req)
    assert "EARLIER_RESEARCH" in skill.seen.metadata["past_research_block"]
    assert "shop.example" in skill.seen.metadata["past_research_block"]


@pytest.mark.asyncio
async def test_with_earlier_research_given_the_reasoning_skill_answers_from_it():
    from core.skills.base import SkillInput
    from core.skills.reasoning import ReasoningSkill
    seen = {}

    async def acompletion(**kw):
        seen.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="From research on 13 Nov: R 1 450."))])
    block = rm.block({"created_at": _days(0), "question": "What do our day bags cost elsewhere?",
                      "answer": "R 1 450 [1]", "sources": [{"url": "https://shop.example/bags"}]})
    with patch("core.skills.reasoning.resolve_generation_route", AsyncMock(return_value=("openrouter/x", "k", "b"))), \
         patch("litellm.acompletion", acompletion), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline.query", AsyncMock(return_value=[])):
        out = await ReasoningSkill().run(SkillInput(question="What do our day bags cost elsewhere?", tenant_id=T,
                                                    metadata={"open": True, "past_research_block": block}))
    assert out.answer.startswith("From research on")
    assert "EARLIER_RESEARCH" in seen["messages"][1]["content"]
    assert "Research again" in seen["messages"][0]["content"]


# ── the share route and "delete my data" ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_share_route_is_for_the_asker_only(db):
    db.rows["vula_research_notes"].append({"id": "n1", "tenant_id": T, "created_by": JUDY, "shared": True,
                                           "created_at": _days(0), "kind": "general"})
    req = MagicMock()
    req.headers = {"authorization": "Bearer tok"}
    with patch("vula.identity.person_for_login", AsyncMock(return_value=identity.Person(T, member_id="m2"))), \
         pytest.raises(HTTPException) as e:
        await commerce.admin_assistant_research_share(T, "n1", commerce.ResearchShare(shared=False), req)
    assert e.value.status_code == 404
    with patch("vula.identity.person_for_login", AsyncMock(return_value=identity.Person(T, member_id="m1"))):
        out = await commerce.admin_assistant_research_share(T, "n1", commerce.ResearchShare(shared=False), req)
    assert out["note"]["shared"] is False


def test_delete_my_data_removes_a_members_research_and_export_lists_it(db):
    db.rows["vula_research_notes"] += [
        {"id": "n1", "tenant_id": T, "created_by": JUDY, "question": "deck sealing", "created_at": _days(0)},
        {"id": "n2", "tenant_id": T, "created_by": SAM, "question": "roof paint", "created_at": _days(0)}]
    team = [{"id": "m1", "whatsapp": "0821112222"}]
    with patch("vula.team_index.active_members", lambda tid: team), \
         patch("vula.ingestion.pipeline.QdrantStore.delete_document", AsyncMock(return_value=1)) as gone:
        assert privacy_data.research_notes(T, "0821112222") == [{"question": "deck sealing", "date": _days(0)[:10]}]
        out = privacy_data.erase(T, "0821112222")
    assert out["removed"]["research_notes"] == 1
    assert [r["id"] for r in db.rows["vula_research_notes"]] == ["n2"]
    assert gone.await_args.args[-1] == "research_note_n1"


def test_research_note_index_entries_are_never_knowledge_results():
    from vula.ingestion.pipeline import VulaIngestionPipeline
    assert "research_note" in VulaIngestionPipeline._INDEX_ONLY

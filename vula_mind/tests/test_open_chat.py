"""The dashboard's open chat, "Ask anything" (vula/open_chat.py, 13 Nov 2026): a general question
goes to Vula's own AI (the reasoning skill, task "open_chat", local first); a question about the
business's records still goes to the business assistant. The business's daily spend cap is a
hard stop for it, Claude calls are costed properly, and an action the assistant previews can
now be confirmed from the dashboard."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from config import settings
from core.skills.base import SkillInput, SkillOutput
from tests.fake_supabase import FakeSupabase
from vula import identity, open_chat
from vula.api import commerce
from vula.commerce import service

T = "kelp-boardbags"
OWNER = identity.Person(T, member_id="m-own", name="Ian", role="owner", phone="27835550000")


@pytest.fixture(autouse=True)
def _fresh():
    open_chat.clear()
    yield
    open_chat.clear()


# ── which questions go where ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Give me 5 ideas for a winter promotion",
    "Write an Instagram caption for a new olive green board bag",
    "How should I price a custom build compared to a standard bag?",
    "Help me plan the stall for the surf expo",
    "What's a good way to write an invoice reminder email?",
    "Help me plan a project kickoff meeting",
    "Explain how VLOOKUP works in a spreadsheet",
])
def test_a_general_question_goes_to_the_open_chat(text):
    assert open_chat.needs_records(text) is False


@pytest.mark.parametrize("text", [
    "What does Thandi owe us?",
    "Who still owes us money?",
    "Send the invoice to Sam",
    "Create a quote for 3 day bags",
    "How were our sales this week?",
    "Change the stock for the 9ft bag to 4",
    "How is our Seaview project doing against budget?",
])
def test_a_question_about_the_records_goes_to_the_business_assistant(text):
    assert open_chat.needs_records(text) is True


def test_one_person_has_an_hourly_limit():
    assert all(open_chat.allow("webadmin:m:a", 3) for _ in range(3))
    assert open_chat.allow("webadmin:m:a", 3) is False
    assert open_chat.allow("webadmin:m:b", 3) is True                 # someone else's own count


# ── the route ───────────────────────────────────────────────────────────────────────────────

def _req():
    r = MagicMock()
    r.headers = {"authorization": "Bearer tok"}
    return r


class _Skill:
    def __init__(self, name, out=None):
        self.name, self.seen, self.out = name, None, out

    async def __call__(self, inp):
        self.seen = inp
        return self.out or SkillOutput(answer=f"from {self.name}", skill_name=self.name)


async def _ask(message, mode="open", on=True, db=None, admin_out=None):
    db = db or FakeSupabase()
    skills = {"reasoning": _Skill("reasoning"), "commerce_admin": _Skill("commerce_admin", admin_out)}
    with patch.object(service, "_client", return_value=db), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=OWNER)), \
         patch("core.skills.loader.get_skill", side_effect=lambda n: skills[n]), \
         patch("vula.features.on", side_effect=lambda t, n: on if n == "open_chat" else False):
        out = await commerce.admin_assistant(T, commerce.AssistantRequest(message=message, mode=mode), _req())
    return out, skills, db


@pytest.mark.asyncio
async def test_ask_anything_answers_a_general_question_on_vulas_own_ai():
    out, skills, _ = await _ask("Give me 5 ideas for a winter promotion")
    assert out["answered_by"] == "open" and out["answer"] == "from reasoning"
    assert skills["reasoning"].seen.metadata["open"] is True
    assert skills["reasoning"].seen.metadata["session_id"] == "webadmin:m:m-own"
    assert skills["commerce_admin"].seen is None


@pytest.mark.asyncio
async def test_ask_anything_hands_a_records_question_to_the_business_assistant():
    out, skills, _ = await _ask("What does Thandi owe us?")
    assert out["answered_by"] == "business" and out["answer"] == "from commerce_admin"
    assert "open" not in skills["commerce_admin"].seen.metadata


@pytest.mark.asyncio
async def test_the_business_chat_is_unchanged():
    out, skills, _ = await _ask("Give me 5 ideas for a winter promotion", mode=None, on=False)
    assert out["answered_by"] == "business" and skills["reasoning"].seen is None


@pytest.mark.asyncio
async def test_ask_anything_needs_the_switch_and_holds_the_hourly_limit():
    with pytest.raises(HTTPException) as e:
        await _ask("ideas please", on=False)
    assert e.value.status_code == 403
    with patch.object(settings, "open_chat_per_hour", 1):
        await _ask("ideas please")
        with pytest.raises(HTTPException) as e:
            await _ask("more ideas please")
    assert e.value.status_code == 429


# ── confirming a previewed action from the dashboard ────────────────────────────────────────

def _pending(phone=""):
    return {"id": "pc-1", "tenant_id": T, "phone": phone, "tool_name": "update_stock", "status": "pending",
            "tool_args": {"product": "9ft bag", "quantity": 4, "_caller": {"role": "owner", "name": "Ian"}},
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()}


@pytest.mark.asyncio
async def test_a_preview_is_returned_and_tied_to_this_persons_thread():
    db = FakeSupabase({"commerce_pending_confirmations": [_pending()]})
    out, _, db = await _ask("Change the stock for the 9ft bag to 4", mode="business", db=db, admin_out=SkillOutput(
        answer="Set 9ft bag stock to 4?", skill_name="commerce_admin",
        confirm_request={"id": "pc-1", "summary": "Set 9ft bag stock to 4?", "confirm_label": "Confirm",
                         "cancel_label": "Cancel"}))
    assert out["confirm_request"]["id"] == "pc-1"
    assert db.rows["commerce_pending_confirmations"][0]["phone"] == "webadmin:m:m-own"


async def _confirm(db, action="confirm", person=OWNER, dispatch=None):
    dispatch = dispatch or AsyncMock(return_value={"message": "Stock for 9ft bag is now 4."})
    with patch.object(service, "_client", return_value=db), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=person)), \
         patch("core.skills.commerce_admin.CommerceAdminSkill._dispatch_tool", dispatch):
        out = await commerce.admin_assistant_confirm(T, commerce.AssistantConfirm(id="pc-1", action=action), _req())
    return out, dispatch


@pytest.mark.asyncio
async def test_confirming_runs_the_original_action_once():
    db = FakeSupabase({"commerce_pending_confirmations": [_pending("webadmin:m:m-own")]})
    out, dispatch = await _confirm(db)
    name, args, _ctx = dispatch.await_args.args
    assert name == "update_stock" and args == {"product": "9ft bag", "quantity": 4, "confirm": True}
    assert out == {"answer": "Stock for 9ft bag is now 4.", "ok": True}
    again, dispatch2 = await _confirm(db)
    assert again["ok"] is False and "already been handled" in again["answer"]
    dispatch2.assert_not_awaited()


@pytest.mark.asyncio
async def test_someone_else_cannot_confirm_and_cancel_changes_nothing():
    db = FakeSupabase({"commerce_pending_confirmations": [_pending("webadmin:m:m-own")]})
    other = identity.Person(T, member_id="m-rep", name="Thandi", role="staff")
    out, dispatch = await _confirm(db, person=other)
    assert out["ok"] is False and dispatch.await_count == 0
    out, dispatch = await _confirm(db, action="cancel")
    assert out == {"answer": "Cancelled — nothing was changed.", "ok": False} and dispatch.await_count == 0
    assert db.rows["commerce_pending_confirmations"][0]["status"] == "cancelled"


# ── the reasoning skill's open mode ─────────────────────────────────────────────────────────

def _resp(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


async def _reason(open_mode=True, route=None):
    from core.skills.reasoning import ReasoningSkill
    seen = {}

    async def acompletion(**kw):
        seen.update(kw)
        return _resp("Here are five ideas…")
    route = route or AsyncMock(return_value=("openrouter/meta-llama/llama-3.3-70b-instruct", "k", "b"))
    with patch("core.skills.reasoning.resolve_generation_route", route), \
         patch("litellm.acompletion", acompletion), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline.query", AsyncMock(return_value=[])), \
         patch.dict("os.environ", {"REASONING_WEB_FALLBACK_DISABLED": "true"}):
        out = await ReasoningSkill().run(SkillInput(
            question="Give me 5 ideas for a winter promotion", tenant_id=T,
            metadata={"open": True} if open_mode else {}))
    return out, seen, route


@pytest.mark.asyncio
async def test_open_mode_routes_as_open_chat_and_writes_a_full_answer():
    out, seen, route = await _reason()
    assert route.await_args.kwargs["task_type"] == "open_chat"
    assert seen["max_tokens"] >= 1500
    assert "not WhatsApp" in seen["messages"][0]["content"]
    assert out.answer == "Here are five ideas…"                          # no caveat tacked on


@pytest.mark.asyncio
async def test_the_ordinary_reasoning_answer_is_unchanged():
    out, seen, route = await _reason(open_mode=False)
    assert route.await_args.kwargs["task_type"] == "reasoning"
    assert "suitable for WhatsApp" in seen["messages"][0]["content"]


@pytest.mark.asyncio
async def test_a_reached_cap_with_local_down_says_so_instead_of_using_the_cloud():
    from core.llm_router import SpendCapReached
    from core.skills.reasoning import OPEN_CHAT_CAPPED
    out, seen, _ = await _reason(route=AsyncMock(side_effect=SpendCapReached(T)))
    assert out.answer == OPEN_CHAT_CAPPED and not seen


# ── the router's hard stop ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_cap_is_a_hard_stop_for_open_chat_only():
    from core import llm_router as r
    with patch.object(r, "_spend_capped", return_value=True), \
         patch.object(r, "_alert_spend_cap_breach"), \
         patch.object(r, "ollama_available", AsyncMock(return_value=False)), \
         patch.object(settings, "openrouter_api_key", "k"), \
         patch.object(settings, "prefer_cloud_llm", False):
        with pytest.raises(r.SpendCapReached):
            await r.resolve_generation_route(task_type="open_chat")
        assert (await r.resolve_generation_route(task_type="reasoning"))[0].startswith("openrouter/")
        assert r.escalate_to_cloud("local_unreliable", task_type="open_chat") is None
        assert r.escalate_to_cloud("local_unreliable", task_type="reasoning") is not None


# ── costing ─────────────────────────────────────────────────────────────────────────────────

def test_claude_is_costed_at_its_own_price_and_the_providers_figure_wins():
    from vula.integrations import metering as m
    db = FakeSupabase({"vula_ai_usage": []})
    with patch.object(m, "_client", return_value=db):
        m.record_llm(T, "openrouter/anthropic/claude-sonnet-5", 1_000_000, 0)
        m.record_llm(T, "openrouter/anthropic/claude-haiku-5", 0, 0, cost_usd=0.42)
        m.record_llm(T, "ollama_chat/qwen3:14b", 5000, 5000, cost_usd=9.0)      # local is free
    cost = {r["model"]: float(r["est_cost_usd"]) for r in db.rows["vula_ai_usage"]}
    assert cost == {"anthropic/claude-sonnet-5": 3.0, "anthropic/claude-haiku-5": 0.42, "qwen3:14b": 0.0}


def test_the_month_is_summed():
    from vula.integrations import metering as m
    today = datetime.now(timezone.utc).date()
    last_month = (today.replace(day=1) - timedelta(days=1)).isoformat()
    db = FakeSupabase({"vula_ai_usage": [
        {"tenant_id": T, "day": today.isoformat(), "model": "a", "est_cost_usd": 1.25},
        {"tenant_id": T, "day": today.replace(day=1).isoformat(), "model": "b", "est_cost_usd": 0.75},
        {"tenant_id": T, "day": last_month, "model": "a", "est_cost_usd": 9.0},
        {"tenant_id": "other", "day": today.isoformat(), "model": "a", "est_cost_usd": 5.0}]})
    with patch.object(m, "_client", return_value=db):
        assert m.month_spend(T) == 2.0

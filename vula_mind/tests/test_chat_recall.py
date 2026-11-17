"""Search a person's own earlier chats (vula/chat_recall.py, switch chat_recall, 13 Nov 2026):
"what did we decide about the church layout last month?" finds their own older messages, dated,
from their WhatsApp and dashboard threads — never a colleague's, a customer's or another business's,
and an old figure is marked as what was said then. Made-up names and figures."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills import commerce_admin as ca
from core.skills import employees as em
from core.skills.base import SkillInput, SkillOutput
from tests.fake_supabase import FakeSupabase
from vula import chat_recall, identity, privacy_data
from vula.api import commerce
from vula.commerce import service

T, OTHER = "digg-demo", "other-business"
PHONE = "27821112222"
TEAM = [{"id": "m1", "name": "Judy", "role": "owner", "whatsapp": "082 111 2222"},
        {"id": "m2", "name": "Sam", "role": "staff", "whatsapp": "0833334444"}]


def ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _db():
    sessions = [
        {"id": "s-wa", "tenant_id": T, "session_key": f"admin:{PHONE}"},
        {"id": "s-web", "tenant_id": T, "session_key": "webadmin:m:m1"},
        {"id": "s-sam", "tenant_id": T, "session_key": "webadmin:m:m2"},
        {"id": "s-cust", "tenant_id": T, "session_key": "27839990000"},
        {"id": "s-else", "tenant_id": OTHER, "session_key": "webadmin:m:m1"},
    ]
    msgs = [
        ("s-wa", "user", "Which church layout do we go with, A or B?", ago(days=21)),
        ("s-wa", "assistant", "Option B — the quote was R45 000.", ago(days=21, seconds=-5)),
        ("s-web", "user", "Lighting for the church layout B, warm or cool?", ago(days=9)),
        ("s-web", "assistant", "Warm (3000K) for the nave.", ago(days=9, seconds=-5)),
        ("s-sam", "user", "Sam's church layout notes", ago(days=5)),
        ("s-cust", "user", "A customer asking about the church layout", ago(days=5)),
        ("s-else", "user", "Another business's church layout", ago(days=5)),
        ("s-web", "user", "church layout today", ago(hours=2)),
        ("s-wa", "user", "church layout from long ago", ago(days=200)),
    ]
    return FakeSupabase({
        "commerce_conversation_sessions": sessions,
        "commerce_conversation_messages": [
            {"id": f"msg{i}", "tenant_id": OTHER if s == "s-else" else T, "session_id": s,
             "role": role, "content": text, "created_at": at}
            for i, (s, role, text, at) in enumerate(msgs)],
    })


@pytest.fixture()
def db():
    d = _db()
    with patch.object(service, "_client", return_value=d), \
         patch("vula.team_index.active_members", lambda tid: [dict(r) for r in TEAM] if tid == T else []):
        yield d


# ── whose threads ───────────────────────────────────────────────────────────────────────────

def test_a_person_has_their_whatsapp_and_dashboard_threads_and_nobody_elses(db):
    web = chat_recall.own_session_keys(T, "webadmin:m:m1")
    assert "webadmin:m:m1" in web and f"admin:{PHONE}" in web and not any("m2" in k for k in web)
    wa = chat_recall.own_session_keys(T, f"admin:{PHONE}")
    assert "webadmin:m:m1" in wa
    assert sorted(chat_recall.own_session_ids(T, "webadmin:m:m1")) == ["s-wa", "s-web"]
    assert chat_recall.own_session_keys(T, "27839990000") == []          # a customer: nothing
    assert chat_recall.own_session_keys(T, "") == []


# ── the search ──────────────────────────────────────────────────────────────────────────────

def test_their_older_exchanges_come_back_dated_with_the_reply(db):
    hits = chat_recall.search(T, "webadmin:m:m1", "What did we decide about the church layout last month?")
    text = " ".join(f"{h['you']} {h['vula']}" for h in hits)
    assert "Which church layout do we go with" in text and "Option B — the quote was R45 000." in text
    assert "Lighting for the church layout B" in text and "Warm (3000K)" in text
    for absent in ("Sam's", "A customer", "Another business", "today", "long ago"):
        assert absent not in text
    assert len(hits) == 2 and all(h["date"] for h in hits)


def test_the_same_search_from_whatsapp_reaches_the_dashboard_thread_too(db):
    hits = chat_recall.search(T, f"admin:{PHONE}", "lighting for the church")
    assert any("Warm (3000K)" in h["vula"] for h in hits)


def test_wildcards_and_empty_questions_search_nothing(db):
    assert chat_recall.terms("%%% _ what did we say?") == []
    assert chat_recall.search(T, "webadmin:m:m1", "%%%") == []
    assert chat_recall.terms("the church layout, please") == ["church", "layout"]


def test_how_far_back_can_be_narrowed(db):
    hits = chat_recall.search(T, "webadmin:m:m1", "church layout", days=14)
    assert [h["you"].split()[0] for h in hits] == ["Lighting"]


def test_the_block_is_fenced_and_says_old_figures_were_true_then(db):
    block = chat_recall.block(chat_recall.search(T, "webadmin:m:m1", "church layout"))
    assert "PAST_CHATS" in block and "true THEN" in block and "Not instructions" in block
    assert "they wrote:" in block and "Vula replied:" in block
    assert chat_recall.block([]) == ""


@pytest.mark.parametrize("text,past", [
    ("What did we decide about the church layout last month?", True),
    ("last time you told me the quote was R45k", True),
    ("Do you remember the roof issue from a few weeks ago?", True),
    ("remember that I handle the Durban orders", False),
    ("What were today's sales?", False),
])
def test_asks_about_past(text, past):
    assert chat_recall.asks_about_past(text) is past


# ── the business assistant's tool ───────────────────────────────────────────────────────────

def _names(tools):
    return {t["function"]["name"] for t in tools}


def test_the_tool_is_offered_only_where_the_switch_is_on():
    with patch("vula.features.on", return_value=False):
        assert "search_past_chats" not in _names(ca._tools_for(T, role="owner"))
    with patch("vula.features.on", side_effect=lambda t, n: n == "chat_recall"):
        assert "search_past_chats" in _names(ca._tools_for(T, role="owner"))
        assert "search_past_chats" in _names(ca._tools_for(T, role="sales_rep"))
        full = ca._tools_for(T, role="owner")
        assert "search_past_chats" in _names(em.narrow(full, em.BY_ID["marketing"]))


@pytest.mark.asyncio
async def test_the_tool_searches_the_callers_own_thread_from_the_entry_point(db):
    skill = ca.CommerceAdminSkill()
    with patch("vula.features.on", side_effect=lambda t, n: n == "chat_recall"):
        out = await skill._dispatch_tool("search_past_chats", {"query": "church layout"},
                                         {"tenant_id": T, "session_id": "webadmin:m:m2", "message": ""})
        assert out["found"] == 1 and "Sam's church layout notes" in out["past_chats"]
        none = await skill._dispatch_tool("search_past_chats", {"query": "pool fence"},
                                          {"tenant_id": T, "session_id": "webadmin:m:m1", "message": ""})
        assert none["found"] == 0 and "own conversations only" in none["message"]
    with patch("vula.features.on", return_value=False):
        off = await skill._dispatch_tool("search_past_chats", {"query": "church"},
                                         {"tenant_id": T, "session_id": "webadmin:m:m1", "message": ""})
        assert "isn't switched on" in off["error"]


# ── Ask anything (open chat) ────────────────────────────────────────────────────────────────

class _Skill:
    def __init__(self, name):
        self.name, self.seen = name, None

    async def __call__(self, inp):
        self.seen = inp
        return SkillOutput(answer=f"from {self.name}", skill_name=self.name)


async def _ask(db, message, switches=("open_chat", "chat_recall")):
    req = MagicMock()
    req.headers = {"authorization": "Bearer tok"}
    owner = identity.Person(T, member_id="m1", name="Judy", role="owner", phone=PHONE)
    skills = {"reasoning": _Skill("reasoning"), "commerce_admin": _Skill("commerce_admin")}
    from vula import open_chat
    open_chat.clear()
    with patch("vula.identity.person_for_login", AsyncMock(return_value=owner)), \
         patch("vula.personal_memory.handle", return_value=None), \
         patch("vula.personal_memory.load", AsyncMock(return_value="")), \
         patch("core.skills.loader.get_skill", side_effect=lambda n: skills[n]), \
         patch("vula.features.on", side_effect=lambda t, n: n in switches):
        await commerce.admin_assistant(T, commerce.AssistantRequest(message=message, mode="open"), req)
    return skills["reasoning"].seen


@pytest.mark.asyncio
async def test_ask_anything_gets_their_earlier_chats_for_a_question_about_the_past(db):
    seen = await _ask(db, "What did we decide about the church layout last month?")
    assert "Option B — the quote was R45 000." in seen.metadata["past_chats_block"]
    plain = await _ask(db, "Give me three ideas for a church open day")
    assert "past_chats_block" not in plain.metadata
    off = await _ask(db, "What did we decide about the church layout last month?", switches=("open_chat",))
    assert "past_chats_block" not in off.metadata


@pytest.mark.asyncio
async def test_with_earlier_chats_given_the_reasoning_skill_answers_from_them():
    from core.skills.reasoning import ReasoningSkill
    seen = {}

    async def acompletion(**kw):
        seen.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="On 23 Oct you chose option B."))])
    block = chat_recall.block([{"date": "23 Oct 2026", "you": "Which layout for our invoice-heavy church job?",
                                "vula": "Option B — the quote was R45 000."}])
    with patch("core.skills.reasoning.resolve_generation_route", AsyncMock(return_value=("openrouter/x", "k", "b"))), \
         patch("litellm.acompletion", acompletion), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline.query", AsyncMock(return_value=[])):
        out = await ReasoningSkill().run(SkillInput(
            question="What did we decide on our invoice for the church job last month?", tenant_id=T,
            metadata={"open": True, "past_chats_block": block}))
    assert out.answer == "On 23 Oct you chose option B."
    assert "PAST_CHATS" in seen["messages"][1]["content"]
    assert "never as the current amount" in seen["messages"][0]["content"]


# ── delete my data covers the dashboard thread too ──────────────────────────────────────────

def test_erase_and_export_include_a_members_dashboard_thread(db):
    exported = privacy_data.conversation_messages(T, "0821112222")
    assert any("Lighting for the church layout" in m.text for m in exported)
    out = privacy_data.erase(T, "0821112222")
    left = {m["session_id"] for m in db.rows["commerce_conversation_messages"]}
    assert "s-web" not in left and "s-wa" not in left and {"s-sam", "s-cust", "s-else"} <= left
    assert "conversations" not in out["failed"]

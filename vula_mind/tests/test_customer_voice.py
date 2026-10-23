"""Off the Hook's customer voice, 23 Oct.

OTH's voice was learned mostly from the owner's own commands to Vula ("stock hake 5"), so customers
got "concise and direct… 👍… no full stops" — and once a bare "👍". OTH's real voice, in the posts
it forwards, is warm and upbeat: "Good morning all", 🐟🥳🙏. A shop's voice is now learned only from
what it writes to customers, shown to the team first, and used only after "Use this voice".
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import voice_profile as vp

PROMOS = [
    "🐟 FRESH TUNA – LIMITED STOCK! The season has begun 🥳🙏🐟 Big Eye Tuna at R250/kg",
    "Good morning all, \nDon't forget to get your orders in, delivery tomorrow 🥳",
    "Good evening 👋 \nPlease send all orders by 18:00. \n\nHave a great evening 🙏",
]
LEARNED = (
    "Chat replies: Warm and upbeat. Greet with \"Good morning\" or \"Hi\", use 🐟 🥳 🙏 sparingly, "
    "keep it to two or three short lines and close with \"Have a great day 🙏\".\n"
    "Promotions: A CAPS headline between fish emoji, a short line on why it's special, then "
    "\"Order now!\".\n"
    "Example: Thank you so much! 🙏 We'll be ready for you tomorrow 🐟"
)


@pytest.fixture()
def shop(monkeypatch):
    monkeypatch.setattr(vp, "audience", lambda t: "customer")
    monkeypatch.setattr(vp, "_promotions", AsyncMock(return_value=list(PROMOS) * 5))
    monkeypatch.setattr(vp, "_whatsapp_agent_replies", AsyncMock(return_value=["Hi Thandi, sure 🙏"]))
    monkeypatch.setattr(vp, "_email_excerpts", AsyncMock(return_value=[]))
    monkeypatch.setattr(vp, "_whatsapp_self_chat", AsyncMock(return_value=["stock hake 5"] * 40))
    monkeypatch.setattr(vp, "_meeting_notes", AsyncMock(return_value=["met supplier"]))


@pytest.mark.asyncio
async def test_a_shops_voice_leaves_out_its_commands_to_vula(shop):
    samples = await vp._gather_samples("off-the-hook", "customer")
    assert "stock hake 5" not in samples and "met supplier" not in samples
    assert any("FRESH TUNA" in s for s in samples) and "Hi Thandi, sure 🙏" in samples
    everything = await vp._gather_samples("digg-demo", "all")
    assert "stock hake 5" in everything            # other businesses keep every source


@pytest.mark.asyncio
async def test_learned_voice_is_two_parts_plus_an_example(shop, monkeypatch):
    seen, stored = {}, {}

    async def fake_completion(**kw):
        seen["prompt"] = kw["messages"][0]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=LEARNED))])

    db = MagicMock()
    db.table.return_value.update.side_effect = lambda row: stored.update(row) or MagicMock()
    monkeypatch.setattr(vp, "_client", lambda: db)
    monkeypatch.setattr("core.llm_router.resolve_generation_route", AsyncMock(return_value=("m", None, None)))
    with patch("litellm.acompletion", new=fake_completion):
        res = await vp.analyze_voice("off-the-hook")
    assert "talks to its customers" in seen["prompt"] and "stock hake" not in seen["prompt"]
    assert "R250" not in seen["prompt"]                       # prices aren't tone
    assert res["suggested"].startswith("Chat replies: Warm and upbeat")
    assert "\nPromotions: A CAPS headline" in res["suggested"] and "Example" not in res["suggested"]
    assert res["example"] == "Thank you so much! 🙏 We'll be ready for you tomorrow 🐟"
    assert stored["persona_prompt_suggested"] == res["suggested"]


def test_chat_and_promotion_parts():
    voice = LEARNED.rsplit("\nExample", 1)[0]
    chat = vp.persona_part(voice, "chat")
    assert chat.startswith("Warm and upbeat") and "CAPS" not in chat
    assert vp.persona_part(voice, "promotions").startswith("A CAPS headline")
    old = "Concise and direct, 👍 for agreement."
    assert vp.persona_part(old, "chat") == old and vp.persona_part(old, "promotions") == ""


def test_a_suggestion_identical_to_the_voice_in_use_is_not_pending():
    same = "Concise and direct."
    assert not vp.has_pending_suggestion({"persona_prompt": same, "persona_prompt_suggested": same})
    assert vp.has_pending_suggestion({"persona_prompt": same, "persona_prompt_suggested": "Warm."})
    assert not vp.has_pending_suggestion({"persona_prompt": same})


def test_the_customer_prompt_gets_only_the_chat_part(monkeypatch):
    from core.skills.commerce_assistant import CommerceAssistantSkill
    voice = LEARNED.rsplit("\nExample", 1)[0]
    monkeypatch.setattr("vula.api.tenants.get_config", lambda t, fresh=False: {"persona_prompt": voice})
    prompt = CommerceAssistantSkill()._system_prompt("off-the-hook", "")
    assert "How you should sound: Warm and upbeat" in prompt and "CAPS headline" not in prompt


def test_writing_a_promotion_gets_the_promotion_style(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    from vula.commerce import promotions
    monkeypatch.setattr(promotions, "examples", lambda t: PROMOS)
    monkeypatch.setattr("vula.api.tenants.get_config",
                        lambda t, fresh=False: {"persona_prompt": LEARNED.rsplit("\nExample", 1)[0]})
    res = CommerceAdminSkill()._recent_promotions("off-the-hook")
    assert res["style"].startswith("A CAPS headline")


# ── shown first, used only on "Use this voice" ───────────────────────────────

@pytest.mark.asyncio
async def test_the_team_sees_the_voice_before_customers_do(monkeypatch):
    from vula.api import whatsapp as wa
    from vula.commerce import promotions
    voice = LEARNED.rsplit("\nExample", 1)[0]
    monkeypatch.setattr(vp, "audience", lambda t: "customer")
    monkeypatch.setattr("vula.api.tenants.get_config", lambda t, fresh=False: {"persona_prompt": "Concise."})
    monkeypatch.setattr(promotions, "examples", lambda t, n=5: PROMOS * 2)
    monkeypatch.setattr(vp, "analyze_voice", AsyncMock(return_value={
        "suggested": voice, "sample_count": 16, "example": "Thank you so much! 🙏"}))
    sent, asked = [], []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, *a, **k: sent.append(m) or True))
    monkeypatch.setattr(wa, "_staff_role", lambda t, p: None)
    monkeypatch.setattr(wa, "_ask_admin_confirm", AsyncMock(side_effect=lambda *a, **k: asked.append((a, k))))
    assert await wa._propose_customer_voice("off-the-hook", "27645755210")
    assert "Warm and upbeat" in sent[0] and "A reply in this voice" in sent[0]
    (args, kw), = asked
    assert args[2] == "apply_voice_persona" and args[3] == {"persona_prompt": voice}
    assert kw["labels"] == ("Use this voice", "Keep current")


@pytest.mark.asyncio
async def test_no_proposal_while_one_is_waiting_or_with_few_promotions(monkeypatch):
    from vula.api import whatsapp as wa
    from vula.commerce import promotions
    analyze = AsyncMock()
    monkeypatch.setattr(vp, "analyze_voice", analyze)
    monkeypatch.setattr(vp, "audience", lambda t: "customer")
    monkeypatch.setattr("vula.api.tenants.get_config", lambda t, fresh=False: {
        "persona_prompt": "Concise.", "persona_prompt_suggested": "Warm."})
    monkeypatch.setattr(promotions, "examples", lambda t, n=5: PROMOS * 2)
    assert not await wa._propose_customer_voice("off-the-hook", "27645755210")
    monkeypatch.setattr("vula.api.tenants.get_config", lambda t, fresh=False: {"persona_prompt": "Concise."})
    monkeypatch.setattr(promotions, "examples", lambda t, n=5: PROMOS[:2])
    assert not await wa._propose_customer_voice("off-the-hook", "27645755210")
    assert not analyze.called


class _Table:
    def __init__(self, store, log):
        self.store, self.log, self._sel = store, log, False

    def update(self, row):
        self.log.append(row)
        self.store.update(row)
        return self

    def select(self, *a):
        self._sel = True
        return self

    def eq(self, *a):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        return MagicMock(data=[dict(self.store)] if self._sel else [])


@pytest.mark.asyncio
async def test_use_this_voice_sets_it_and_reads_it_back(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill, checked_reply
    store, log = {"persona_prompt": "Concise.", "persona_prompt_suggested": "Chat replies: Warm."}, []
    db = MagicMock()
    db.table.side_effect = lambda n: _Table(store, log)
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    res = await CommerceAdminSkill()._apply_voice_persona("off-the-hook", "Chat replies: Warm.", True)
    assert store["persona_prompt"] == "Chat replies: Warm." and store["persona_prompt_suggested"] is None
    assert "now sounds like this" in checked_reply(res)


@pytest.mark.asyncio
async def test_keep_current_drops_the_suggestion_and_changes_nothing(monkeypatch):
    from vula.api import whatsapp as wa
    store, log = {"persona_prompt": "Concise.", "persona_prompt_suggested": "Chat replies: Warm."}, []
    claimed = MagicMock(data=[{"id": "pc1", "tool_name": "apply_voice_persona"}])

    class _Pending:
        def update(self, *a):
            return self

        def eq(self, *a):
            return self

        def gt(self, *a):
            return self

        def execute(self):
            return claimed

    db = MagicMock()
    db.table.side_effect = lambda n: _Pending() if n == "commerce_pending_confirmations" else _Table(store, log)
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    sent = []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, *a, **k: sent.append(m) or True))
    await wa._handle_admin_confirm_reply("27645755210", "admin_cancel:pc1", "off-the-hook")
    assert store["persona_prompt"] == "Concise." and store["persona_prompt_suggested"] is None
    assert "keeps its current voice" in sent[0]

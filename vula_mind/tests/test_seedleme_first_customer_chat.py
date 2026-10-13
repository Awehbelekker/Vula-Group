"""SeedleMe's first customer chat (2026-10-13), replayed. Ian, as a customer:
  "Do you do round basins" → the owner was asked "❓ A customer asked…" (no product search),
      and the customer got "Sawubona! Ons is kyk na die vraag oor ronde wasbakke…"
  "Do you have round basins?" → one sink offered, of 20-odd round basins on sale
  "What colours does the Countertop Butler basin come in?" → two wrong products, not its 7 colours
and every reply ended "⚠️ Worth double-checking — tell me what and I'll fix it", an owner's note."""
from types import SimpleNamespace

import pytest

from core import verification
from core.skills import commerce_assistant as ca
from core.skills.base import BaseSkill, SkillInput, SkillOutput


def _p(name, desc="Handmade concrete.", price=149900, category="basin"):
    slug = name.lower().replace(" – ", "-").replace(" ", "-")
    return {"slug": slug, "name": name, "description": desc, "price_cents": price,
            "category": category, "in_stock": True, "status": "active"}


CATALOGUE = [
    _p("Round Concrete Basin – Charcoal – 40 x 12cm"),
    _p("Round Concrete Basin – Sandstone – 40 x 12cm"),
    _p("Round Concave Concrete Basin – Grey – 42 x 14cm", price=169900),
    _p("Grey Round Bespoke Concrete Sink 30 x 30 x 13cm", "A round basin, made to order.", 139900, "sinks"),
    _p("Countertop Butler Concrete Basin – Ivory – 31 x 31 x 12cm"),
    _p("Countertop Butler Concrete Basin – Black – 31 x 31 x 12cm"),
    _p("Countertop Butler Concrete Basin – Terracotta – 36 x 36 x 12cm", price=159900),
    _p("Ivory Namibian Dream Indoor & Outdoor Basin 42 x 24 x 11.5cm", price=259900),
    _p("Modern Oval Concrete Basin – Grey – 59 x 39 x 12cm", price=179900),
]


@pytest.fixture()
def shop(monkeypatch):
    async def list_products(tid, **_k):
        return [dict(p) for p in CATALOGUE]
    monkeypatch.setattr(ca.service, "list_products", list_products)
    return ca.CommerceAssistantSkill()


@pytest.mark.asyncio
async def test_round_basins_finds_every_round_basin_not_one_sink(shop):
    names = [p["name"] for p in await shop._exec_list_products("seedleme", {"search": "round basins"})]
    assert names[:3] == ["Round Concave Concrete Basin – Grey – 42 x 14cm",
                         "Round Concrete Basin – Charcoal – 40 x 12cm",
                         "Round Concrete Basin – Sandstone – 40 x 12cm"]       # name matches first
    assert "Grey Round Bespoke Concrete Sink 30 x 30 x 13cm" in names         # "round basin" in its description
    assert not any("Butler" in n or "Oval" in n for n in names)


@pytest.mark.asyncio
async def test_countertop_butler_lists_its_colours_and_nothing_else(shop):
    names = [p["name"] for p in await shop._exec_list_products("seedleme", {"search": "Countertop Butler basin"})]
    assert len(names) == 3 and all(n.startswith("Countertop Butler Concrete Basin") for n in names)


def test_question_words_are_not_product_words():
    assert ca.search_terms("What colours does the Countertop Butler basin come in?") == ["countertop", "butler", "basin"]
    assert ca.search_terms("Do you do round basins") == ["round", "basin"]


@pytest.mark.asyncio
async def test_a_question_the_catalogue_answers_never_goes_to_the_owner(shop, monkeypatch):
    asked = []

    async def ask_team(tid, phone, args):
        asked.append(args)
        return {"escalated": True}
    monkeypatch.setattr(shop, "_exec_ask_team", ask_team)
    ctx = {"tenant_id": "seedleme", "session_id": "s", "customer_phone": "27645755210"}
    res = await shop._dispatch_tool("ask_team", {"question": "Do you do round basins"}, ctx)
    assert asked == [] and res["matches"] == 4 and "don't hand this to the team" in res["instruction_to_assistant"]
    await shop._dispatch_tool("ask_team", {"question": "How long does delivery take?"}, ctx)
    assert asked == [{"question": "How long does delivery take?"}]               # not a product question


def _reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))])


@pytest.mark.asyncio
async def test_a_reply_in_the_wrong_language_is_rewritten_on_the_cloud_model(shop, monkeypatch):
    import litellm
    calls = []
    replies = iter(["Sawubona! Ons is kyk na die vraag oor ronde wasbakke. Ons sal jou gou weer kontak.",
                    "Yes — we have round concrete basins from R1,399."])

    async def completion(**kw):
        calls.append(kw["model"])
        return _reply(next(replies))

    async def route(**_k):
        return "ollama_chat/llama3.1:8b", None, None
    monkeypatch.setattr(litellm, "acompletion", completion)
    monkeypatch.setattr(ca, "resolve_generation_route", route)
    monkeypatch.setattr("core.llm_router.escalate_to_cloud",
                        lambda reason, **k: ("openrouter/google/gemini-2.5-flash", "k", None))
    ctx = {"tenant_id": "seedleme", "session_id": "s", "customer_phone": "27645755210",
           "language": ca._expected_language("en", "Do you do round basins")}
    answer = await shop._agent_loop("system", "", "Do you do round basins", ctx)
    assert answer == "Yes — we have round concrete basins from R1,399."
    assert calls == ["ollama_chat/llama3.1:8b", "openrouter/google/gemini-2.5-flash"]


class _Customer(BaseSkill):
    name = "commerce_assistant"
    description = "customer"
    verification_policy = "adversarial"

    async def run(self, inp):
        return SkillOutput(answer="We have 4 round basins.", skill_name=self.name, confidence=0.9)


class _Owner(_Customer):
    name = "commerce_admin"


@pytest.mark.asyncio
async def test_customers_never_get_the_owners_double_check_note(monkeypatch):
    async def defect(question, answer, context=""):
        return {"verdict": "fail", "defects": ["x"], "checker_ms": 1}
    monkeypatch.setattr(verification, "adversarial_check", defect)
    monkeypatch.setattr("core.reasoning_telemetry.emit", lambda **kw: None)
    inp = SkillInput(question="round basins?", tenant_id="seedleme")
    customer = await _Customer()(inp)
    assert customer.answer == "We have 4 round basins." and customer.confidence == 0.45
    owner = await _Owner()(inp)
    assert "Worth double-checking" in owner.answer

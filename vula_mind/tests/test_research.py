"""Research mode for Ask anything (vula/research.py, 13 Nov 2026): planned searches with no
personal details, pages read through the safe fetcher, the business's own documents alongside,
citations checked in code, an unsourced price flagged, and the daily spend cap respected.
Made-up names, sites and figures."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.llm_router import CAP_HARD_TASKS, SpendCapReached
from tests.fake_supabase import FakeSupabase
from vula import identity, open_chat, research
from vula.api import commerce
from vula.commerce import service

T = "digg-demo"
PAGE = ("Board bags from the Surf Shop. The 9'6 day bag costs R 1 450 and ships in 3 days. "
        "Warranty is one year. " * 6)
OTHER = ("Bags Direct price list: travel board bag R 1 390, coffin bag R 2 100. Free delivery over "
         "R 1 000. " * 6)


# ── planning ────────────────────────────────────────────────────────────────────────────────

def test_queries_never_carry_an_email_or_a_number():
    assert research.clean_query("quote for jo@client.co.za 082 555 1234 roof sheeting") == "quote for roof sheeting"


def test_the_plan_is_parsed_from_the_models_list_or_falls_back_to_the_question():
    assert research._parse_queries('Here: ["board bag prices South Africa", "board bag suppliers", '
                                   '"x", "y"]', "q") == ["board bag prices South Africa", "board bag suppliers", "x"]
    assert research._parse_queries("no list here", "What do board bags cost?") == ["What do board bags cost?"]


@pytest.mark.asyncio
async def test_planning_survives_the_model_failing():
    with patch.object(research, "_complete", AsyncMock(side_effect=SpendCapReached(T))):
        assert await research.plan_queries("roof sheeting prices") == ["roof sheeting prices"]


def test_research_is_held_by_the_spend_cap():
    assert {"research", "research_plan"} <= CAP_HARD_TASKS


# ── reading ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_results_are_merged_across_searches_without_repeats():
    lists = {"a": [{"title": "A1", "url": "https://a.example/1"}, {"title": "A2", "url": "https://a.example/2"}],
             "b": [{"title": "dup", "url": "https://a.example/1#top"}, {"title": "B1", "url": "https://b.example/1"}]}
    with patch("core.skills.web_search._ddg_search", AsyncMock(side_effect=lambda q, limit: lists[q])):
        hits = await research._search(["a", "b"])
    assert [h["url"] for h in hits] == ["https://a.example/1", "https://a.example/2", "https://b.example/1"]


@pytest.mark.asyncio
async def test_pages_are_read_through_the_safe_fetcher_and_unsafe_or_thin_ones_skipped():
    from vula.commerce.reference_url import UnsafeUrlError
    html = {"https://shop.example/bags": f"<title>Surf Shop</title><p>{PAGE}</p>",
            "https://thin.example/": "<p>hi</p>"}

    async def fetch(url, max_bytes):
        if url not in html:
            raise UnsafeUrlError("internal address")
        return html[url].encode()
    with patch("vula.commerce.reference_url.safe_fetch_bytes", fetch):
        pages = await research.read_pages([{"title": "x", "url": u} for u in
                                           ("http://169.254.169.254/", "https://thin.example/", "https://shop.example/bags")])
    assert [p["url"] for p in pages] == ["https://shop.example/bags"]
    assert pages[0]["title"] == "Surf Shop" and "R 1 450" in pages[0]["text"]


# ── the answer ──────────────────────────────────────────────────────────────────────────────

PAGES = [{"title": "Surf Shop", "url": "https://shop.example/bags", "text": PAGE},
         {"title": "Bags Direct", "url": "https://bags.example/prices", "text": OTHER}]
DOCS = [{"title": "price-list.pdf", "text": "Our 9'6 day bag: R 1 500."}]


def test_citations_are_checked_and_the_sources_are_what_was_cited():
    out = research.finish("The day bag is R 1 450 [1]; ours is R 1 500 [D1]. Made up [7] [D4].", PAGES, DOCS)
    assert "[7]" not in out["answer"] and "[D4]" not in out["answer"] and "[1]" in out["answer"]
    assert [s["url"] for s in out["sources"]] == ["https://shop.example/bags"]
    assert out["documents"] == [{"n": "D1", "title": "price-list.pdf"}]
    assert out["unchecked"] == []


def test_a_price_no_source_states_is_flagged():
    out = research.finish("Bags Direct sells it for R 999 [2].", PAGES, DOCS)
    assert out["unchecked"] and "Check before relying on" in out["answer"]


def test_with_no_citations_every_page_read_is_listed():
    out = research.finish("Prices vary.", PAGES, [])
    assert [s["n"] for s in out["sources"]] == [1, 2]
    assert "https://bags.example/prices" in research.sources_note(out)


@pytest.mark.asyncio
async def test_a_full_run_writes_from_the_pages_and_documents_fenced():
    seen = {}

    async def complete(task, msgs, max_tokens, timeout_s, accept=None):
        if task == "research_plan":
            return '["board bag prices South Africa"]'
        seen["system"], seen["user"] = msgs[0]["content"], msgs[1]["content"]
        return "The Surf Shop lists the day bag at R 1 450 [1], a little under your R 1 500 [D1]."
    with patch.object(research, "_complete", complete), \
         patch.object(research, "_search", AsyncMock(return_value=[{"title": "x", "url": p["url"]} for p in PAGES])), \
         patch.object(research, "read_pages", AsyncMock(return_value=PAGES)), \
         patch.object(research, "own_documents", AsyncMock(return_value=DOCS)):
        out = await research.run(T, "How do our board bag prices compare?")
    assert "WEB_PAGES" in seen["user"] and "OWN_DOCUMENTS" in seen["user"] and "[D1] price-list.pdf" in seen["user"]
    assert "Cite every fact" in seen["system"]
    assert out["searched"] == ["board bag prices South Africa"] and out["read"] == 2
    assert [s["n"] for s in out["sources"]] == [1] and out["documents"][0]["n"] == "D1"


@pytest.mark.asyncio
async def test_nothing_found_and_the_cap_are_said_plainly():
    with patch.object(research, "plan_queries", AsyncMock(return_value=["q"])), \
         patch.object(research, "_search", AsyncMock(return_value=[])), \
         patch.object(research, "read_pages", AsyncMock(return_value=[])), \
         patch.object(research, "own_documents", AsyncMock(return_value=[])):
        assert (await research.run(T, "q"))["answer"] == research.NOTHING_FOUND
    with patch.object(research, "plan_queries", AsyncMock(return_value=["q"])), \
         patch.object(research, "_search", AsyncMock(return_value=[])), \
         patch.object(research, "read_pages", AsyncMock(return_value=PAGES)), \
         patch.object(research, "own_documents", AsyncMock(return_value=[])), \
         patch.object(research, "_complete", AsyncMock(side_effect=SpendCapReached(T))):
        assert (await research.run(T, "q"))["answer"] == research.CAPPED


# ── the dashboard route ─────────────────────────────────────────────────────────────────────

async def _ask(message, mode="research", switches=("open_chat",), result=None):
    req = MagicMock()
    req.headers = {"authorization": "Bearer tok"}
    owner = identity.Person(T, member_id="m1", name="Judy", role="owner")
    db = FakeSupabase()
    run = AsyncMock(return_value=result or {
        "answer": "R 1 450 at the Surf Shop [1].", "searched": ["q"], "read": 2, "unchecked": [],
        "sources": [{"n": 1, "title": "Surf Shop", "url": "https://shop.example/bags"}], "documents": []})
    with patch.object(service, "_client", return_value=db), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=owner)), \
         patch("vula.personal_memory.handle", return_value=None), \
         patch("vula.personal_memory.load", AsyncMock(return_value="")), \
         patch("vula.features.on", side_effect=lambda t, n: n in switches), \
         patch.object(research, "run", run):
        out = await commerce.admin_assistant(T, commerce.AssistantRequest(message=message, mode=mode), req)
    return out, run, db


@pytest.mark.asyncio
async def test_research_mode_answers_with_sources_and_history_keeps_them():
    open_chat.clear()
    out, run, db = await _ask("What do board bags cost in South Africa?")
    assert out["answered_by"] == "research" and out["research"]["sources"][0]["url"] == "https://shop.example/bags"
    saved = [m["content"] for m in db.rows["commerce_conversation_messages"] if m["role"] == "assistant"]
    assert saved and "Sources:\n[1] Surf Shop — https://shop.example/bags" in saved[0]
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_research_needs_ask_anything_and_has_its_own_hourly_limit():
    open_chat.clear()
    with pytest.raises(HTTPException) as e:
        await _ask("prices?", switches=())
    assert e.value.status_code == 403
    from config import settings
    with patch.object(settings, "research_per_hour", 1):
        await _ask("first")
        with pytest.raises(HTTPException) as e:
            await _ask("second")
    assert e.value.status_code == 429 and "research" in e.value.detail
    open_chat.clear()


# ── local first, Gemini Flash when it has to go to the cloud ─────────────────────────────────

def test_research_uses_gemini_flash_in_the_cloud_unless_the_map_names_another():
    from config import settings
    from core.llm_router import cloud_model_for
    with patch.object(settings, "cloud_model_by_task", ""):
        assert cloud_model_for("research") == "google/gemini-2.5-flash"
        assert cloud_model_for("research_plan") == "google/gemini-2.5-flash"
        assert cloud_model_for("reasoning") == settings.model_worker_cloud
    with patch.object(settings, "cloud_model_by_task", '{"research": "x/other"}'):
        assert cloud_model_for("research") == "x/other"


def test_the_prompt_is_cut_to_fit_the_local_window():
    from core.llm_router import assess_complexity, local_prompt_cap_tokens
    big = [{"title": f"P{i}", "url": f"https://p{i}.example/", "text": "word " * 2000} for i in range(6)]
    docs = [{"title": "d.pdf", "text": "doc " * 1000}] * 3
    room = local_prompt_cap_tokens() * 4 - 7000
    pages, dd, hist = research.fit(big, docs, "h" * 9000, room)
    total = sum(len(p["text"]) for p in pages) + sum(len(d["text"]) for d in dd) + len(hist)
    assert total <= room and len(pages) == 6 and len(hist) <= 1200
    assert big[0]["text"] == "word " * 2000                                 # originals untouched
    assert assess_complexity([{"role": "user", "content": "s" * 7000 + "x" * total}]) is None


def test_cites_something():
    assert research.cites_something("It is R 1 450 [2].", pages=2, docs=0)
    assert research.cites_something("Ours is [D1].", pages=0, docs=1)
    assert not research.cites_something("No sources [5].", pages=2, docs=0)


@pytest.mark.asyncio
async def test_a_local_answer_that_cites_nothing_goes_to_the_cloud_logged():
    calls = []

    async def acompletion(**kw):
        calls.append(kw["model"])
        text = "Prices vary a lot." if kw["model"].startswith("ollama") else "R 1 450 at the Surf Shop [1]."
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
    esc = MagicMock(return_value=("openrouter/google/gemini-2.5-flash", "k", "b"))
    with patch("core.llm_router.resolve_generation_route", AsyncMock(return_value=("ollama_chat/llama3.1:8b", None, "b"))), \
         patch("core.llm_router.escalate_to_cloud", esc), \
         patch("core.llm_router.looks_unreliable", return_value=False), \
         patch("litellm.acompletion", acompletion):
        out = await research._complete("research", [{"role": "user", "content": "q"}], 100, 5,
                                       accept=lambda a: research.cites_something(a, 1, 0))
        assert out == "R 1 450 at the Surf Shop [1]."
        assert esc.call_args.args[0] == "research_check_failed"
        calls.clear()
        esc.reset_mock()

        async def good(**kw):
            calls.append(kw["model"])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Cited [1]."))])
        with patch("litellm.acompletion", good):
            assert await research._complete("research", [{"role": "user", "content": "q"}], 100, 5,
                                             accept=lambda a: research.cites_something(a, 1, 0)) == "Cited [1]."
        assert calls == ["ollama_chat/llama3.1:8b"] and not esc.called

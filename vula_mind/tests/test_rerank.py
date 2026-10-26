"""vula/ingestion/rerank.py — a second look at the top 20: re-orders, never adds or drops; the
local model only, falling back to word matching."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.ingestion import rerank as rr


def _hits(*texts):
    return [{"chunk_id": f"c{i}", "text": t, "filename": f"f{i}.pdf"} for i, t in enumerate(texts)]


def test_word_matching_lifts_the_passage_that_holds_the_answer():
    hits = _hits("Our company history and values", "Delivery areas in Cape Town",
                 "Atlantis Seafood bank account number 62001234567 FNB",
                 "Terms and conditions of sale")
    order = rr.lexical_order("What is the bank account number of Atlantis Seafood?", hits)
    assert order[0] == 2 and sorted(order) == [0, 1, 2, 3]


def test_no_matching_words_keeps_the_first_pass_order():
    hits = _hits("alpha", "beta", "gamma")
    assert rr.lexical_order("What is the VAT number?", hits) == [0, 1, 2]


def test_the_label_counts_as_well_as_the_text():
    hits = [{"text": "Total R1 204.61", "ctx_label": "Invoice from DIY"},
            {"text": "Total R1 204.61", "ctx_label": "Invoice from SOLID CAPE"}]
    assert rr.lexical_order("Total on the Solid Cape invoice?", hits)[0] == 1


@pytest.mark.parametrize("raw,n,want", [("3, 1, 3, 9", 4, [2, 0]), ("0", 4, []), ("none", 4, []),
                                        ("[2] then [4]", 4, [1, 3])])
def test_parse_order(raw, n, want):
    assert rr.parse_order(raw, n) == want


def _resp(text):
    return MagicMock(choices=[MagicMock(message=MagicMock(content=text))])


@pytest.mark.asyncio
async def test_the_local_model_choice_comes_first_then_the_rest():
    hits = _hits("a", "b", "c", "d")
    with patch("core.llm_router.resolve_generation_route", new=AsyncMock(return_value=("ollama/llama3.1:8b", None, "http://x"))), \
         patch("core.llm_router.is_local_model", return_value=True), \
         patch("litellm.acompletion", new=AsyncMock(return_value=_resp("3, 2"))):
        out = await rr.rerank("q", hits, "llm")
    assert [h["chunk_id"] for h in out] == ["c2", "c1", "c0", "c3"]


@pytest.mark.asyncio
async def test_documents_never_go_to_a_cloud_model_for_reranking():
    hits = _hits("Atlantis bank details", "other")
    call = AsyncMock(side_effect=AssertionError("cloud call"))
    with patch("core.llm_router.resolve_generation_route", new=AsyncMock(return_value=("openrouter/x", "k", None))), \
         patch("core.llm_router.is_local_model", return_value=False), \
         patch("litellm.acompletion", new=call):
        out = await rr.rerank("Atlantis bank details?", hits, "llm")
    call.assert_not_awaited()
    assert out[0]["chunk_id"] == "c0"            # fell back to word matching


@pytest.mark.asyncio
async def test_a_slow_or_empty_model_falls_back_to_word_matching():
    hits = _hits("x", "Solid Cape VAT 4560123456")
    with patch("core.llm_router.resolve_generation_route", new=AsyncMock(return_value=("ollama/m", None, "http://x"))), \
         patch("core.llm_router.is_local_model", return_value=True), \
         patch("litellm.acompletion", new=AsyncMock(return_value=_resp("0"))):
        out = await rr.rerank("Solid Cape VAT number", hits, "llm")
    assert out[0]["chunk_id"] == "c1"


@pytest.mark.asyncio
async def test_off_or_unknown_mode_changes_nothing_and_only_the_top_20_move():
    hits = _hits(*[f"t{i}" for i in range(25)])
    assert await rr.rerank("q", hits, "off") is hits
    hits[22]["text"] = "needle needle needle"
    out = await rr.rerank("needle", hits, "lexical")
    assert out[20:] == hits[20:] and len(out) == 25


@pytest.mark.asyncio
async def test_query_reranks_only_when_asked_and_reads_deeper():
    from tests.test_query_expansion import _pipeline
    from vula.ingestion import pipeline as pl
    first = [{"chunk_id": f"c{i}", "text": f"filler {i}", "score": 0.6 - i * 0.01} for i in range(10)]
    first[7] = {"chunk_id": "ans", "text": "Atlantis Seafood bank account 62001234567", "score": 0.5}
    p, calls = _pipeline([list(first)])
    with patch.object(pl, "expand_query", AsyncMock(return_value=[])):
        out = await p.query("Atlantis Seafood bank account number?", top_k=4, rerank="lexical")
    assert "ans" in [h["chunk_id"] for h in out]
    assert calls[0]["limit"] >= 20
    p, calls = _pipeline([list(first)])
    with patch.object(pl, "expand_query", AsyncMock(return_value=[])):
        out = await p.query("Atlantis Seafood bank account number?", top_k=4, rerank="off")
    assert calls[0]["limit"] == 8

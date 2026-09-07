"""A terse question must still find the document that answers it.

2026-09-07, from the gerflor rep's real messages. He uploaded DT SOH and Planning 07.09.26.pdf
at 12:32 and asked about it at 12:37 and 13:42. Both times Vula said "I couldn't find any
information" — while the answer sat in the knowledge base the whole time. Measured against the
live collection:

    "Which importer does the creation range"  -> 0 chunks        (threshold 0.30)
    "Who imports the Creation range?"         -> 3 chunks, 0.465
    "How stocks creations"                    -> 2 chunks, both irrelevant
    "How is the Creation range stocked?"      -> 3 chunks, the right documents

Retrieval was never the problem. The people who most need this tool type the least carefully,
so the query is rewritten before embedding — and the ORIGINAL is always one of the variants,
which makes expansion strictly additive.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.ingestion import pipeline as pl


# ── salient terms ───────────────────────────────────────────────────────────────

def test_product_names_survive_and_filler_does_not():
    assert pl._salient_terms("Which importer does the creation range") == ["importer", "creation"]
    assert pl._salient_terms("How stocks creations") == ["stocks", "creations"]


def test_a_question_of_pure_filler_yields_no_terms():
    assert pl._salient_terms("what is the for and of") == []


# ── chunk identity ──────────────────────────────────────────────────────────────

def test_the_same_chunk_from_two_variants_is_one_result():
    a = {"chunk_id": "c1", "text": "x", "score": 0.4}
    b = {"chunk_id": "c1", "text": "x", "score": 0.9}
    assert pl._chunk_key(a) == pl._chunk_key(b)


def test_chunks_without_an_id_are_keyed_by_content():
    a = {"source": "d.pdf", "text": "Creation stock on hand"}
    b = {"source": "d.pdf", "text": "Creation stock on hand"}
    c = {"source": "d.pdf", "text": "something else entirely"}
    assert pl._chunk_key(a) == pl._chunk_key(b)
    assert pl._chunk_key(a) != pl._chunk_key(c)


# ── expansion ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_long_well_formed_question_is_not_rewritten():
    """Rewriting a careful question costs latency and buys nothing."""
    with patch("litellm.acompletion", AsyncMock()) as llm:
        out = await pl.expand_query(
            "Could you please tell me which importer supplies the Creation range of vinyl "
            "flooring to us this quarter?")
    assert out == []
    llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_terse_question_is_rewritten():
    pl._EXPANSION_CACHE.clear()
    resp = MagicMock(choices=[MagicMock(message=MagicMock(
        content="Who imports the Creation range?\nWhich distributor supplies Creation?"))])
    with patch("core.llm_router.resolve_generation_route",
               AsyncMock(return_value=("m", "k", None))), \
         patch("litellm.acompletion", AsyncMock(return_value=resp)):
        out = await pl.expand_query("Which importer does the creation range", "gerflor")
    assert out == ["Who imports the Creation range?", "Which distributor supplies Creation?"]


@pytest.mark.asyncio
async def test_expansion_failure_falls_back_to_the_original_only():
    """The model being down must never cost a search — it just stops improving it."""
    pl._EXPANSION_CACHE.clear()
    with patch("core.llm_router.resolve_generation_route",
               AsyncMock(side_effect=RuntimeError("router down"))):
        assert await pl.expand_query("How stocks creations", "gerflor") == []


@pytest.mark.asyncio
async def test_rewrites_are_cached_so_a_repeated_question_costs_nothing():
    pl._EXPANSION_CACHE.clear()
    resp = MagicMock(choices=[MagicMock(message=MagicMock(content="Who imports Creation?"))])
    with patch("core.llm_router.resolve_generation_route",
               AsyncMock(return_value=("m", "k", None))), \
         patch("litellm.acompletion", AsyncMock(return_value=resp)) as llm:
        await pl.expand_query("How stocks creations", "gerflor")
        await pl.expand_query("how stocks CREATIONS", "gerflor")
    assert llm.await_count == 1, "case-insensitive cache hit"


@pytest.mark.asyncio
async def test_numbering_and_quotes_are_stripped_from_rewrites():
    pl._EXPANSION_CACHE.clear()
    resp = MagicMock(choices=[MagicMock(message=MagicMock(
        content='1. "Who imports the Creation range?"\n- Which distributor stocks Creation?'))])
    with patch("core.llm_router.resolve_generation_route",
               AsyncMock(return_value=("m", "k", None))), \
         patch("litellm.acompletion", AsyncMock(return_value=resp)):
        out = await pl.expand_query("creation importer", "gerflor")
    assert out == ["Who imports the Creation range?", "Which distributor stocks Creation?"]


# ── merged retrieval ────────────────────────────────────────────────────────────

def _pipeline(search_results, keyword_results=None):
    p = pl.VulaIngestionPipeline.__new__(pl.VulaIngestionPipeline)
    p.tenant_id = "gerflor"
    p.embedder = MagicMock(embed=AsyncMock(return_value=[0.0]))
    calls = []

    async def _search(tenant, emb, **kw):
        calls.append(kw)
        return search_results.pop(0) if search_results else []

    p.store = MagicMock(
        search=_search,
        keyword_search=AsyncMock(return_value=keyword_results or []),
    )
    return p, calls


@pytest.mark.asyncio
async def test_a_variant_finds_what_the_original_could_not():
    """The exact failure: the typed question returns nothing, the rewrite returns the answer."""
    p, _ = _pipeline([
        [],                                                          # original -> nothing
        [{"chunk_id": "c1", "text": "SOH 07.09.26", "score": 0.465}],  # rewrite -> the answer
    ])
    with patch.object(pl, "expand_query", AsyncMock(return_value=["Who imports Creation?"])):
        out = await p.query("Which importer does the creation range")
    assert [h["chunk_id"] for h in out] == ["c1"]


@pytest.mark.asyncio
async def test_the_original_question_is_always_searched():
    """Expansion is additive: whatever the original found before, it still finds."""
    p, _ = _pipeline([[{"chunk_id": "orig", "score": 0.31}], []])
    with patch.object(pl, "expand_query", AsyncMock(return_value=["a rewrite"])):
        out = await p.query("How stocks creations")
    assert "orig" in [h["chunk_id"] for h in out]


@pytest.mark.asyncio
async def test_a_chunk_found_twice_appears_once_at_its_best_score():
    p, _ = _pipeline([
        [{"chunk_id": "c1", "score": 0.31}],
        [{"chunk_id": "c1", "score": 0.47}],
    ])
    with patch.object(pl, "expand_query", AsyncMock(return_value=["r"])):
        out = await p.query("creation stock")
    assert len(out) == 1
    assert out[0]["score"] == 0.47


@pytest.mark.asyncio
async def test_results_come_back_best_first():
    p, _ = _pipeline([
        [{"chunk_id": "low", "score": 0.31}],
        [{"chunk_id": "high", "score": 0.62}],
    ])
    with patch.object(pl, "expand_query", AsyncMock(return_value=["r"])):
        out = await p.query("creation")
    assert [h["chunk_id"] for h in out] == ["high", "low"]


@pytest.mark.asyncio
async def test_the_keyword_pass_only_runs_when_the_semantic_side_is_thin():
    p, _ = _pipeline([[{"chunk_id": f"c{i}", "score": 0.5} for i in range(5)]])
    with patch.object(pl, "expand_query", AsyncMock(return_value=[])):
        await p.query("creation", top_k=5)
    p.store.keyword_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_literal_term_match_rescues_an_empty_search():
    """"creation" is right there in the documents; a pure-vector miss should not be the end."""
    p, _ = _pipeline([[]], keyword_results=[{"chunk_id": "kw1", "text": "Creation ...",
                                             "score": 0.0, "match": "keyword"}])
    with patch.object(pl, "expand_query", AsyncMock(return_value=[])):
        out = await p.query("Which importer does the creation range")
    assert [h["chunk_id"] for h in out] == ["kw1"]


@pytest.mark.asyncio
async def test_keyword_hits_are_not_buried_by_higher_scoring_boilerplate():
    """First live run: rewrites scored 0.63 against generic marketing ("All our flooring is
    certified by independent bodies...") and displaced real 'Creation' chunks that the keyword
    pass had found. A keyword hit carries no similarity score, so ranking it alongside vector
    hits always loses — it gets reserved slots instead."""
    p, _ = _pipeline(
        [[{"chunk_id": f"boiler{i}", "score": 0.68} for i in range(4)]],
        keyword_results=[{"chunk_id": "creation1", "text": "Creation 55", "score": 0.0,
                          "match": "keyword"}],
    )
    with patch.object(pl, "expand_query", AsyncMock(return_value=[])):
        out = await p.query("How stocks creations", top_k=4)
    ids = [h["chunk_id"] for h in out]
    assert "creation1" in ids, "the literal match must survive"
    assert len(out) == 4


@pytest.mark.asyncio
async def test_a_terse_question_gets_the_keyword_pass_even_when_search_looks_full():
    """"How stocks creations" returned 2 semantically-plausible but irrelevant chunks. A full
    result set is not evidence of a good one when the question is telegraphic."""
    p, _ = _pipeline([[{"chunk_id": f"c{i}", "score": 0.4} for i in range(4)]],
                     keyword_results=[{"chunk_id": "kw", "score": 0.0}])
    with patch.object(pl, "expand_query", AsyncMock(return_value=[])):
        await p.query("How stocks creations", top_k=4)
    p.store.keyword_search.assert_awaited()


@pytest.mark.parametrize("rewrite,original", [
    # Both produced live by ollama/llama3.1:8b against the gerflor rep's real questions.
    ("What are the stock creation processes for SAP ERP?", "How stocks creations"),
    ("What is the creation date range for the importer?",
     "Which importer does the creation range"),
    ("What is the warranty on Taralay Impression?", "How stocks creations"),
])
def test_a_rewrite_that_invents_subject_matter_is_rejected(rewrite, original):
    """SAP ERP has nothing to do with a flooring rep's documents, and 'date range' is not what
    was asked. The model's own judgement is what failed, so the check is deterministic."""
    assert pl._grounded_rewrite(rewrite, original) is False


@pytest.mark.parametrize("rewrite,original", [
    ("Who imports the Creation range?", "Which importer does the creation range"),
    ("Which importer supplies the creation range?", "Which importer does the creation range"),
    ("How is the Creation range stocked?", "How stocks creations"),
    ("What are the stock levels for Creation?", "How stocks creations"),
])
def test_a_genuine_rewrite_survives(rewrite, original):
    """Shared prefixes keep the useful morphology: importer->imports, stocks->stocked."""
    assert pl._grounded_rewrite(rewrite, original) is True


@pytest.mark.asyncio
async def test_an_ungrounded_rewrite_never_reaches_the_search():
    pl._EXPANSION_CACHE.clear()
    resp = MagicMock(choices=[MagicMock(message=MagicMock(
        content="What are the stock creation processes for SAP ERP?\n"
                "How is the Creation range stocked?"))])
    with patch("core.llm_router.resolve_generation_route",
               AsyncMock(return_value=("m", "k", None))), \
         patch("core.llm_router.escalate_to_cloud", lambda *a, **k: None), \
         patch("litellm.acompletion", AsyncMock(return_value=resp)):
        out = await pl.expand_query("How stocks creations", "gerflor")
    assert out == ["How is the Creation range stocked?"]


def test_the_rewrite_prompt_forbids_adding_the_company_name():
    """Passing tenant identity as context made every rewrite open with "Gerflor - Western Cape
    Sales is seeking information regarding..." — which matched company boilerplate, not the
    document. A retrieval query wants the question, not who is asking."""
    import inspect
    src = inspect.getsource(pl.expand_query)
    assert "NO company names" in src
    assert "get_config" not in src, "tenant identity must not be fed into the rewrite"


@pytest.mark.asyncio
async def test_expand_false_searches_only_what_it_was_given():
    p, calls = _pipeline([[{"chunk_id": "c1", "score": 0.4}]])
    with patch.object(pl, "expand_query", AsyncMock()) as ex:
        await p.query("a careful query", expand=False)
    ex.assert_not_awaited()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_one_failing_variant_does_not_lose_the_others():
    async def _search(tenant, emb, **kw):
        if not hasattr(_search, "n"):
            _search.n = 0
        _search.n += 1
        if _search.n == 1:
            raise RuntimeError("qdrant hiccup")
        return [{"chunk_id": "c2", "score": 0.5}]

    p, _ = _pipeline([])
    p.store.search = _search
    with patch.object(pl, "expand_query", AsyncMock(return_value=["r"])):
        out = await p.query("creation")
    assert [h["chunk_id"] for h in out] == ["c2"]


@pytest.mark.asyncio
async def test_the_authoritative_threshold_is_unchanged():
    p, calls = _pipeline([[], []])
    with patch.object(pl, "expand_query", AsyncMock(return_value=[])):
        await p.query("creation", authoritative_only=True)
    assert calls[0]["score_threshold"] == 0.35
    assert calls[0]["exclude_source_types"] == pl.VulaIngestionPipeline._NON_AUTHORITATIVE

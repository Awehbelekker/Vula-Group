"""Document search phase A (1 Nov 2026): the settings comparison measures and changes nothing.
The overrides apply only inside kb_overrides(); without them every search behaves as before."""
import pytest

from evals import retrieval as r
from vula.ingestion import pipeline


@pytest.mark.asyncio
async def test_no_override_means_todays_behaviour():
    before = await r.run_fixture()
    with pipeline.kb_overrides():
        after = await r.run_fixture()
    strip = lambda rep: [{k: v for k, v in row.items() if k != "ms"} for row in rep["rows"]]  # noqa: E731
    assert strip(before) == strip(after)


@pytest.mark.asyncio
async def test_overrides_are_scoped_to_the_block():
    assert pipeline._KB_OVERRIDES.get() == {}
    with pipeline.kb_overrides(score_threshold=0.9):
        with pipeline.kb_overrides(rerank="lexical"):
            assert pipeline._KB_OVERRIDES.get() == {"score_threshold": 0.9, "rerank": "lexical"}
        assert pipeline._KB_OVERRIDES.get() == {"score_threshold": 0.9}
    assert pipeline._KB_OVERRIDES.get() == {}


@pytest.mark.asyncio
async def test_threshold_and_lone_word_match_overrides_take_effect():
    fixture = r.load_fixture()
    tenant = next(iter(fixture))
    q = "what is the warranty"           # common words only, no identifier
    with r.offline():
        await r.build_fixture(fixture)
        p = pipeline.VulaIngestionPipeline(tenant_id=tenant)
        probe: dict = {}
        with pipeline.kb_overrides(score_threshold=0.0, probe=probe):
            await p.query(q, top_k=4, expand=False)
        assert probe["vector_hits"] > 0 and isinstance(probe["top_vector"], float)
        # nothing clears an impossible floor, and a word match must then share 99 terms
        with pipeline.kb_overrides(score_threshold=1.01, lone_keyword_min_overlap=99):
            assert await p.query(q, top_k=4, expand=False) == []


def test_score_bands_split():
    scores = [{"type": "lookup", "top_vector": 0.62}, {"type": "spec", "top_vector": 0.55},
              {"type": "unanswerable", "top_vector": 0.38}, {"type": "unanswerable", "top_vector": 0.44},
              {"type": "sensitive", "top_vector": 0.99}]
    b = r.score_bands(scores)
    assert b["present"] == {"n": 2, "min": 0.55, "p50": 0.62, "max": 0.62}
    assert b["absent"]["max"] == 0.44 and b["clean_split"] is True
    scores.append({"type": "unanswerable", "top_vector": 0.6})
    assert r.score_bands(scores)["clean_split"] is False


@pytest.mark.asyncio
async def test_fixture_comparison_runs_every_variant_with_the_same_rewrites():
    variants = {"today": {}, "strict": {"score_threshold": 1.01, "lone_keyword_min_overlap": 99}}
    rep = await r.run_compare_fixture(variants)
    assert set(rep["summary"]) == {"today", "strict"} and rep["mode"] == "fixture"
    today, strict = rep["summary"]["today"], rep["summary"]["strict"]
    assert today["errors"] == 0 and strict["errors"] == 0
    # an impossible floor can only make the search say "not on file" more often
    assert (strict["abstention"] or 0) >= (today["abstention"] or 0)
    assert len(rep["scores"]) == rep["cases"] and rep["matrix"]


@pytest.mark.asyncio
async def test_kb_job_is_registered():
    from vula import kb_maintenance
    assert "retrieval_compare" in kb_maintenance.JOBS

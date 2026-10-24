"""evals/retrieval_diag.py — per-stage ranks and a cause for each miss, run offline against the
in-memory Qdrant and the real ingest + query path (stub embedder, no LLM rewrites)."""
import pytest

from evals import retrieval as r
from evals import retrieval_diag as d


def test_classify_each_cause():
    th = 0.3
    assert d.classify({"prod_rank": 1}, th) == "found"
    assert d.classify({}, th) == "not_retrievable"
    assert d.classify({"vec_rank": 2, "vec_score": 0.21}, th) == "below_threshold"
    assert d.classify({"vec_rank": 9, "vec_score": 0.5}, th) == "ranked_low"
    assert d.classify({"kw_rank": 1, "vec_rank": 12}, th) == "keyword_only"
    assert d.classify({"vec_rank": 3, "vec_score": 0.5}, th) == "lost_in_merge"


def test_summarize_counts_and_threshold_sweep():
    rows = [
        {"tenant": "t", "cause": "found", "prod_rank": 1, "deep_rank": 1, "vec_rank": 1, "vec_score": 0.5,
         "top_score": 0.5, "source_chars": 500, "synthetic": True},
        {"tenant": "t", "cause": "below_threshold", "vec_rank": 2, "vec_score": 0.22, "deep_rank": None,
         "top_score": 0.25, "source_chars": 2500, "synthetic": True},
        {"tenant": "t", "cause": "error"},
    ]
    s = d.summarize(rows)
    assert s["cases"] == 3 and s["errors"] == 1
    assert s["causes"]["found"] == 1 and s["causes"]["below_threshold"] == 1
    assert s["recall@4"] == 0.5
    assert s["vector_recall@4_no_threshold"] == 1.0
    assert s["vector_recall@4_by_threshold"]["0.2"] == 1.0
    assert s["vector_recall@4_by_threshold"]["0.3"] == 0.5
    assert s["recall@4_chunk_0-900"] == 1.0 and s["recall@4_chunk_2000-up"] == 0.0


@pytest.mark.asyncio
async def test_diagnose_runs_every_stage_offline():
    case = next(c for c in r.load_cases()
                if c["tenant"] in r.load_fixture() and c.get("expected_docs") and c["type"] != "unanswerable")
    with r.offline():
        await r.build_fixture()
        row = await d.diagnose(case)
    assert row["cause"] in d.CAUSES, row
    for k in ("vec_rank", "kw_rank", "prod_rank", "deep_rank", "vec_score", "top_score", "threshold"):
        assert k in row
    if row["prod_rank"]:
        assert row["cause"] == "found"


def test_finished_rows_takes_each_tenant_from_the_newest_partial():
    newer = {"rows": [{"tenant": "a", "id": "a2"}]}
    older = {"rows": [{"tenant": "a", "id": "a1"}, {"tenant": "b", "id": "b1"}, {"tenant": "x", "id": "x1"}]}
    got = d.finished_rows([None, newer, older], ["a", "b", "c"])
    assert got == {"a": [{"tenant": "a", "id": "a2"}], "b": [{"tenant": "b", "id": "b1"}]}


@pytest.mark.asyncio
async def test_run_saves_after_each_tenant_and_reuses_finished_ones(monkeypatch):
    calls = []

    async def fake_synth(t, **kw):
        calls.append(t)
        return [{"tenant": t, "question": "q", "expected_docs": ["x.pdf"], "synthetic": True}]

    async def fake_diag(c):
        return {"tenant": c["tenant"], "cause": "found", "prod_rank": 1}

    monkeypatch.setattr(d, "load_cases", lambda: [])
    monkeypatch.setattr(d, "synthesize", fake_synth)
    monkeypatch.setattr(d, "diagnose", fake_diag)
    saved = []

    async def on_tenant(rep):
        saved.append(sorted(rep["by_tenant"]))

    rep = await d.run(["a", "b"], done_rows={"a": [{"tenant": "a", "cause": "found", "prod_rank": 2}]},
                      on_tenant=on_tenant)
    assert calls == ["b"]                       # a was reused, not redone
    assert saved == [["a"], ["a", "b"]]
    assert rep["synthesized"] == {"a": "reused", "b": 1}
    assert rep["summary"]["cases"] == 2


@pytest.mark.asyncio
async def test_replay_reruns_an_earlier_reports_questions(monkeypatch):
    async def no_synth(*a, **k):
        raise AssertionError("a replay must not synthesize")

    seen = []

    async def fake_diag(c):
        seen.append((c["question"], c["expected_docs"]))
        return {"tenant": c["tenant"], "cause": "found", "prod_rank": 1}

    monkeypatch.setattr(d, "synthesize", no_synth)
    monkeypatch.setattr(d, "diagnose", fake_diag)
    old = [{"tenant": "a", "question": "q1", "expected": ["x.pdf"], "cause": "ranked_low"},
           {"tenant": "z", "question": "other", "expected": ["y.pdf"]}]
    rep = await d.run(["a"], replay=old)
    assert seen == [("q1", ["x.pdf"])]
    assert rep["synthesized"] == {"a": "replayed"}

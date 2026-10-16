"""The retrieval eval (evals/retrieval.py): its metrics, its in-memory Qdrant, and the offline
fixture run that CI uses as the retrieval baseline. See evals/RETRIEVAL.md for the numbers."""
import pytest

from evals import retrieval as r


def test_rank_metrics():
    docs = ["a.pdf", "Price list.pdf", "c.pdf", "Stock sheet.pdf"]
    assert r.recall_at(docs, ["price list"], 5) == 1.0
    assert r.recall_at(docs, ["price list", "stock sheet"], 2) == 0.5
    assert r.recall_at(docs, ["missing"], 20) == 0.0
    assert r.reciprocal_rank(docs, ["stock"]) == 0.25
    assert r.reciprocal_rank(docs, ["missing"]) == 0.0
    assert r.ndcg_at(["x.pdf", "want.pdf"], ["want"], 10) == pytest.approx(1 / 1.5849625, rel=1e-4)
    assert r.ndcg_at(["want.pdf"], ["want"], 10) == 1.0


def test_ranked_docs_dedupes_in_order():
    chunks = [{"filename": "b"}, {"filename": "a"}, {"filename": "b"}, {"filename": None}]
    assert r.ranked_docs(chunks) == ["b", "a"]


def test_every_case_is_well_formed():
    fixture = r.load_fixture()
    for c in r.load_cases():
        assert c["type"] in r.TYPES, c
        assert c.get("entry", "owner") in ("owner", "customer"), c
        if c["type"] == "unanswerable":
            assert not c.get("expected_docs"), c
        elif c["type"] == "sensitive":
            assert c.get("forbidden_docs") and c.get("entry") == "customer", c
        else:
            assert c.get("expected_docs"), c
        if c["tenant"] in fixture:       # a fixture case must point at a real fixture document
            names = [d["filename"] for d in fixture[c["tenant"]]]
            for e in c.get("expected_docs") or []:
                assert any(e.lower() in n.lower() for n in names), (c["id"], e)


@pytest.mark.asyncio
async def test_fixture_run_is_offline_complete_and_deterministic():
    first = await r.run_fixture()
    second = await r.run_fixture()
    assert first["summary"]["errors"] == 0, [x for x in first["rows"] if "error" in x]
    assert first["total"] == len([c for c in r.load_cases() if c["tenant"] in r.load_fixture()])
    strip = lambda rep: [{k: v for k, v in row.items() if k != "ms"} for row in rep["rows"]]  # noqa: E731
    assert strip(first) == strip(second)
    for name in ("recall@5", "recall@20", "mrr", "ndcg@10", "in_prompt"):
        assert 0.0 <= first["summary"][name] <= 1.0


@pytest.mark.asyncio
async def test_memory_qdrant_full_text_match_needs_its_index():
    """Like real Qdrant: a full-text filter on an unindexed field is a 400, which is what makes
    QdrantStore._keyword_one create the index on first use."""
    import httpx
    mem = r.MemoryQdrant()
    async with httpx.AsyncClient(transport=mem.transport(), base_url="http://q") as c:
        await c.put("/collections/x", json={})
        await c.put("/collections/x/points", json={"points": [{"id": 1, "vector": [0.1], "payload": {"text": "Creation SOH"}}]})
        flt = {"filter": {"must": [{"key": "text", "match": {"text": "creation"}}]}, "limit": 5}
        assert (await c.post("/collections/x/points/scroll", json=flt)).status_code == 400
        await c.put("/collections/x/index", json={"field_name": "text", "field_schema": "text"})
        got = (await c.post("/collections/x/points/scroll", json=flt)).json()["result"]["points"]
        assert [p["id"] for p in got] == [1]
        bad = await c.put("/collections/x/points", json={"points": [{"id": "not-a-uuid", "vector": [0.1], "payload": {}}]})
        assert bad.status_code == 400


@pytest.mark.asyncio
async def test_fixture_floors_after_phase_1():
    """The phase-1 fixes, held (evals/RETRIEVAL.md): before them, 24% of facts that WERE retrieved
    never reached the prompt (the [:900] cut), and every customer question about cost prices or
    wages was grounded on a supplier invoice or the roster."""
    s = (await r.run_fixture())["summary"]
    assert s["fact_in_prompt"] >= 0.95, s
    assert s["leak_rate"] == 0.0, s
    assert s["recall@5"] >= 0.95 and s["in_prompt"] >= 0.94, s

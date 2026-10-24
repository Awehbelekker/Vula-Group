"""
evals/retrieval_diag.py — WHERE does retrieval lose the right document? (2026-10-21)

evals/retrieval.py says whether the right document reached the prompt. This says which stage lost
it when it didn't, so the fix goes where the misses are. Per case it records the expected
document's rank at each stage of VulaIngestionPipeline.query():

  vec_rank     the original question alone, vector search, no score threshold, top 50 chunks
  vec_score    the best similarity of any chunk from the expected document
  kw_rank      the literal-term pass alone (QdrantStore.keyword_search on _salient_terms)
  prod_rank    the full query() at the skills' top_k (4): what the model actually sees
  deep_rank    the full query() at top_k 20

and a cause for each miss:

  found            prod_rank set
  below_threshold  vector search ranks it in the top 4 but its score is under kb_score_threshold
  ranked_low       vector search finds it, just not in the top 4
  keyword_only     only the literal pass finds it in the top 4, and query() didn't keep it
  lost_in_merge    vector top 4 at a passing score, yet query() dropped it (variants, merge)
  not_retrievable  neither search has it in its top 50 / 20

Cases: each tenant's answerable cases from cases/retrieval.yaml, plus questions synthesized from
the tenant's own chunks by the local-first model (retrieval.synthesize; owner-only documents are
skipped). Synthetic questions are written FROM the chunk, so they flatter vector search; read the
seeded real cases beside them. Read-only: searches, scrolls and model calls, no writes.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, List, Optional

from evals.retrieval import DEEP_TOP_K, PROD_TOP_K, _matches, load_cases, ranked_docs, synthesize

log = logging.getLogger(__name__)

VEC_DEPTH = 50
CAUSES = ("found", "below_threshold", "ranked_low", "keyword_only", "lost_in_merge", "not_retrievable")
SWEEP = (0.2, 0.25, 0.3, 0.35, 0.4)


def _rank(docs: List[str], expected: List[str]) -> Optional[int]:
    for i, d in enumerate(docs, 1):
        if any(_matches(d, e) for e in expected):
            return i
    return None


def _best_score(hits: List[Dict[str, Any]], expected: List[str]) -> Optional[float]:
    s = [h.get("score") or 0.0 for h in hits if any(_matches(str(h.get("filename") or ""), e) for e in expected)]
    return round(max(s), 4) if s else None


def classify(row: Dict[str, Any], threshold: float, top_k: int = PROD_TOP_K) -> str:
    if row.get("prod_rank"):
        return "found"
    vec, kw, score = row.get("vec_rank"), row.get("kw_rank"), row.get("vec_score")
    if vec is None and kw is None:
        return "not_retrievable"
    if vec is not None and vec <= top_k and score is not None and score < threshold:
        return "below_threshold"
    if kw is not None and kw <= top_k and (vec is None or vec > top_k):
        return "keyword_only"
    if vec is not None and vec <= top_k:
        return "lost_in_merge"
    return "ranked_low"


async def diagnose(case: Dict[str, Any]) -> Dict[str, Any]:
    from vula.ingestion.pipeline import QdrantStore, VulaIngestionPipeline, _salient_terms, kb_score_threshold
    tid, q = case["tenant"], case["question"]
    expected = list(case.get("expected_docs") or [])
    row: Dict[str, Any] = {k: case.get(k) for k in ("id", "tenant", "question", "synthetic", "source_chars")}
    row["expected"] = expected
    pipe, store = VulaIngestionPipeline(tenant_id=tid), QdrantStore()
    exclude = pipe._NON_AUTHORITATIVE
    threshold = kb_score_threshold(True)
    try:
        emb = await pipe.embedder.embed(q)
        vec = await store.search(tid, emb, limit=VEC_DEPTH, score_threshold=0.0, exclude_source_types=exclude)
        terms = _salient_terms(q)
        kw = await store.keyword_search(tid, terms, limit=DEEP_TOP_K, exclude_source_types=exclude) if terms else []
        prod = await pipe.query(q, top_k=PROD_TOP_K, authoritative_only=True)
        deep = await pipe.query(q, top_k=DEEP_TOP_K, authoritative_only=True)
    except Exception as exc:  # noqa: BLE001 — one broken case must not sink the run
        row.update(error=f"{type(exc).__name__}: {str(exc)[:200]}", cause="error")
        return row
    row["top_score"] = round(vec[0].get("score") or 0.0, 4) if vec else None
    row["vec_rank"] = _rank(ranked_docs(vec), expected)
    row["vec_score"] = _best_score(vec, expected)
    row["kw_rank"] = _rank(ranked_docs(kw), expected)
    row["prod_rank"] = _rank(ranked_docs(prod), expected)
    row["deep_rank"] = _rank(ranked_docs(deep), expected)
    if case.get("source_chunk"):
        ids = [h.get("chunk_id") for h in vec]
        row["chunk_rank"] = ids.index(case["source_chunk"]) + 1 if case["source_chunk"] in ids else None
    row["threshold"] = threshold
    row["cause"] = classify(row, threshold)
    return row


def _share(rows: List[Dict[str, Any]], pred) -> Optional[float]:
    return round(sum(1 for r in rows if pred(r)) / len(rows), 3) if rows else None


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in rows if r.get("cause") != "error"]
    out: Dict[str, Any] = {"cases": len(rows), "errors": len(rows) - len(ok),
                           "causes": {c: sum(1 for r in ok if r.get("cause") == c) for c in CAUSES}}
    out["recall@4"] = _share(ok, lambda r: r.get("prod_rank") is not None)
    out["recall@20"] = _share(ok, lambda r: r.get("deep_rank") is not None)
    out["vector_recall@4_no_threshold"] = _share(ok, lambda r: (r.get("vec_rank") or 99) <= PROD_TOP_K)
    out["vector_recall@20_no_threshold"] = _share(ok, lambda r: (r.get("vec_rank") or 99) <= DEEP_TOP_K)
    out["keyword_recall@4"] = _share(ok, lambda r: (r.get("kw_rank") or 99) <= PROD_TOP_K)
    # What a different threshold would do to the vector top 4 (the score sits on the expected doc).
    out["vector_recall@4_by_threshold"] = {
        str(t): _share(ok, lambda r, t=t: (r.get("vec_rank") or 99) <= PROD_TOP_K and (r.get("vec_score") or 0) >= t)
        for t in SWEEP}
    found = [r["vec_score"] for r in ok if r.get("vec_score") is not None]
    tops = [r["top_score"] for r in ok if r.get("top_score") is not None]
    out["expected_score_p50"] = round(statistics.median(found), 3) if found else None
    out["top_score_p50"] = round(statistics.median(tops), 3) if tops else None
    # Does a long source chunk embed worse? (synthetic cases only; chars of the source chunk)
    synth = [r for r in ok if r.get("source_chars")]
    for lo, hi in ((0, 900), (900, 2000), (2000, 10**9)):
        b = [r for r in synth if lo <= r["source_chars"] < hi]
        out[f"recall@4_chunk_{lo}-{hi if hi < 10**9 else 'up'}"] = _share(b, lambda r: r.get("prod_rank") is not None)
        out[f"n_chunk_{lo}-{hi if hi < 10**9 else 'up'}"] = len(b)
    seeded = [r for r in ok if not r.get("synthetic")]
    out["seeded_cases"] = len(seeded)
    out["seeded_recall@4"] = _share(seeded, lambda r: r.get("prod_rank") is not None)
    return out


def finished_rows(reports: List[Optional[Dict[str, Any]]], tenants: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Rows per tenant from earlier partial reports (newest first). A partial report is saved only
    after a tenant finishes, so a tenant's rows in one are complete; the newest report wins."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for rep in reports:
        for t in tenants:
            if t not in out:
                rows = [r for r in (rep or {}).get("rows") or [] if r.get("tenant") == t]
                if rows:
                    out[t] = rows
    return out


async def run(tenants: List[str], per_doc: int = 1, max_docs: int = 25,
              done_rows: Optional[Dict[str, List[Dict[str, Any]]]] = None,
              on_tenant=None, replay: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Tenant by tenant. done_rows holds rows of tenants already diagnosed by an earlier, cut-off
    run (reused, not redone); on_tenant(partial_report) is awaited after each tenant so a restart
    loses at most one tenant's work (2026-10-24: a deploy killed a 9-hour run that saved nothing).
    replay: the rows of an earlier report, whose questions are rerun instead of synthesizing."""
    yaml_cases = [c for c in load_cases() if c.get("expected_docs") and c.get("type") != "unanswerable"]
    rows: List[Dict[str, Any]] = []
    synth_counts: Dict[str, Any] = {}
    done_rows = done_rows or {}
    for t in tenants:
        if done_rows.get(t):
            rows += done_rows[t]
            synth_counts[t] = "reused"
        elif replay is not None:
            cases = [{**r, "expected_docs": r.get("expected") or []} for r in replay if r.get("tenant") == t]
            synth_counts[t] = "replayed"
            for c in cases:
                rows.append(await diagnose(c))
        else:
            cases = [c for c in yaml_cases if c["tenant"] == t]
            try:
                synth = await synthesize(t, per_doc=per_doc, max_docs=max_docs, skip_audiences=("owner",))
            except Exception as exc:  # noqa: BLE001
                synth, synth_counts[t] = [], f"{type(exc).__name__}: {str(exc)[:120]}"
            else:
                synth_counts[t] = len(synth)
            for c in cases + synth:
                rows.append(await diagnose(c))
        if on_tenant:
            await on_tenant(_report(rows, [x for x in tenants if any(r["tenant"] == x for r in rows)],
                                    synth_counts))
    return _report(rows, tenants, synth_counts)


def _report(rows: List[Dict[str, Any]], tenants: List[str], synth_counts: Dict[str, Any]) -> Dict[str, Any]:
    by_tenant = {t: summarize([r for r in rows if r["tenant"] == t]) for t in tenants}
    return {"layer": "retrieval_diag", "summary": summarize(rows), "by_tenant": by_tenant,
            "synthesized": dict(synth_counts), "rows": rows}

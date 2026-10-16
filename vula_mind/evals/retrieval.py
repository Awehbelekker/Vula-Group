"""
evals/retrieval.py — does the knowledge base FIND the right document, and does it reach the prompt?

KB retrieval project, phase 0 (2026-10-16). harness.py scores which skill and which tool;
benchmark.py scores whole answers. Neither says whether retrieval itself found the document that
holds the answer, or whether the fact survived into the prompt — so a wrong answer could not be
pinned on retrieval, truncation or the model. This layer measures exactly that, per case in
evals/cases/retrieval.yaml:

  recall@5 / recall@20   share of the expected documents among the top 5 / 20 distinct documents
  MRR, nDCG@10           how high the first / all expected documents rank
  in_prompt              an expected document is among the chunks the skill actually uses
  fact_in_prompt         the case's must_contain text is in the grounding block the model sees
  abstention             on an unanswerable question, nothing reaches the prompt (the skills'
                         deterministic "not on file" guards then decline without a model call)
  leak                   a customer question pulls a staff/owner-only document
  latency, embed calls and grounding tokens per query

Two modes:

  fixture  (CI, tests/test_retrieval_eval.py) — the cases whose tenant is in
           cases/retrieval_fixture.yaml, against an in-memory Qdrant (MemoryQdrant) fed by the
           REAL ingest path (chunker, upsert, payload) and queried through the REAL
           VulaIngestionPipeline.query(). The embedder is a stub and the LLM rewrites are off,
           so it is deterministic and offline. Its numbers measure the pipeline's logic (variants,
           keyword pass, merge, truncation, filters), not embedding quality.
  live     the remaining cases, against the real tenant collections, read-only (search, scroll
           and Supabase reads only). Runs from Master › Benchmark (evals/benchmark.py::run) or
           `python -m evals.run retrieval --live` where the keys are.

The repository is public (evals/replay.py): cases carry questions and filenames, never a phone
number, a customer's name or a tenant's figures. Synthetic cases generated from a real tenant's
chunks (`python -m evals.run retrieval-synth`) go to the git-ignored evals/reports/ for that
reason, and are reviewed there by hand.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import statistics
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import patch

import httpx
import yaml

log = logging.getLogger(__name__)

CASES = Path(__file__).parent / "cases" / "retrieval.yaml"
FIXTURE = Path(__file__).parent / "cases" / "retrieval_fixture.yaml"
TYPES = ("lookup", "spec", "price", "policy", "multi_hop", "follow_up", "sensitive", "unanswerable")
PROD_TOP_K = 4      # core/skills/base.py::SkillInput.top_k — what reasoning and commerce_assistant use
SPEC_TOP_K = 8      # reasoning.py widens a spec question to max(top_k, 8)
DEEP_TOP_K = 20     # for recall@20 / MRR / nDCG


def load_cases(path: Path = CASES) -> List[Dict[str, Any]]:
    return yaml.safe_load(path.read_text())["cases"]


def load_fixture(path: Path = FIXTURE) -> Dict[str, List[Dict[str, Any]]]:
    return yaml.safe_load(path.read_text())["tenants"]


# ── Scoring a ranked list ─────────────────────────────────────────────────────

def _matches(filename: str, pattern: str) -> bool:
    """An expected doc is a case-insensitive filename substring (live filenames vary by upload)."""
    return pattern.lower() in (filename or "").lower()


def ranked_docs(chunks: List[Dict[str, Any]]) -> List[str]:
    """Distinct filenames in rank order."""
    out: List[str] = []
    for c in chunks or []:
        f = str(c.get("filename") or "")
        if f and f not in out:
            out.append(f)
    return out


def recall_at(docs: List[str], expected: List[str], k: int) -> float:
    if not expected:
        return 0.0
    top = docs[:k]
    return sum(1 for e in expected if any(_matches(d, e) for d in top)) / len(expected)


def reciprocal_rank(docs: List[str], expected: List[str]) -> float:
    for i, d in enumerate(docs, 1):
        if any(_matches(d, e) for e in expected):
            return 1.0 / i
    return 0.0


def ndcg_at(docs: List[str], expected: List[str], k: int = 10) -> float:
    found: set = set()
    dcg = 0.0
    for i, d in enumerate(docs[:k], 1):
        hit = next((e for e in expected if e not in found and _matches(d, e)), None)
        if hit:
            found.add(hit)
            dcg += 1.0 / math.log2(i + 1)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(expected), k) + 1))
    return dcg / ideal if ideal else 0.0


def _contains_all(text: str, needles: List[str]) -> bool:
    low = (text or "").lower()
    return all(n.lower() in low for n in needles)


# ── Offline stand-ins: an in-memory Qdrant and a stub embedder ────────────────

_STOP = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "is", "are", "was", "do", "does",
    "did", "how", "what", "which", "who", "when", "where", "why", "can", "you", "we", "our", "us",
    "me", "my", "i", "it", "that", "this", "with", "from", "have", "has", "any", "please", "tell",
    "be", "by", "at", "as", "en", "die", "van", "het", "wat", "is", "ons", "jy", "julle", "hoe",
}


def _features(text: str) -> Dict[str, float]:
    """Stemmed words (weight 1) plus their character trigrams (0.1 each, so a misspelling still
    overlaps). Deterministic and crude on purpose — it only has to rank like a weak embedder."""
    out: Dict[str, float] = {}
    for t in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if t in _STOP or len(t) < 2:
            continue
        s = t[:-1] if len(t) > 4 and t.endswith("s") else t
        out["w:" + s] = 1.0
        if len(s) >= 4:
            p = f"#{s}#"
            for i in range(len(p) - 2):
                out.setdefault("c:" + p[i:i + 3], 0.1)
    return out


def _vec(text: str) -> List[float]:
    d = hashlib.sha256((text or "").encode()).digest()
    return [round(b / 255.0 + 0.001, 6) for b in d[:16]]


def _vkey(v: List[float]) -> tuple:
    return tuple(round(float(x), 6) for x in v)


class StubEmbedder:
    """Stands in for EmbeddingEngine: a vector that identifies the text, so MemoryQdrant can
    score the query text against each chunk's text. Counts calls (= embed cost per query)."""

    dimension = 16

    def __init__(self, registry: Dict[tuple, str]):
        self.registry = registry
        self.calls = 0

    async def embed(self, text: str) -> List[float]:
        self.calls += 1
        v = _vec(text)
        self.registry[_vkey(v)] = text
        return v

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [await self.embed(t) for t in texts]


class _Bad(Exception):
    pass


class MemoryQdrant:
    """Just enough of Qdrant's REST API, in memory, for QdrantStore to run unmodified: create
    collection, payload indexes, upsert, retrieve, search (with filter + score_threshold),
    scroll (with filter, incl. full-text match that needs a text index), delete by filter or ids.
    Search scores the query text against each chunk by idf-weighted feature coverage (0..0.8)."""

    def __init__(self) -> None:
        self.cols: Dict[str, Dict[str, Any]] = {}
        self.texts: Dict[tuple, str] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # -- helpers
    @staticmethod
    def _pid(raw: Any) -> Any:
        if isinstance(raw, int):
            return raw
        try:
            return str(uuid.UUID(str(raw)))
        except ValueError:
            raise _Bad(f"Unable to parse UUID: {raw}")

    @staticmethod
    def _order(pid: Any) -> tuple:
        return (0, pid, "") if isinstance(pid, int) else (1, 0, pid)

    def _cond(self, col: Dict[str, Any], c: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        key, m = c.get("key"), c.get("match") or {}
        if "text" in m:
            if col["indexes"].get(key) != "text":
                raise _Bad(f'Index required but not found for "{key}" of one of the following types: [text]')
            toks = set(re.findall(r"\w+", str(payload.get(key) or "").lower()))
            return all(t in toks for t in re.findall(r"\w+", str(m["text"]).lower()))
        if key not in payload:
            return False
        vals = payload[key] if isinstance(payload[key], list) else [payload[key]]
        if "value" in m:
            return m["value"] in vals
        if "any" in m:
            return any(v in m["any"] for v in vals)
        if "except" in m:
            return not any(v in m["except"] for v in vals)
        return False

    def _ok(self, col: Dict[str, Any], flt: Optional[Dict[str, Any]], payload: Dict[str, Any]) -> bool:
        if not flt:
            return True
        if not all(self._cond(col, c, payload) for c in flt.get("must") or []):
            return False
        if any(self._cond(col, c, payload) for c in flt.get("must_not") or []):
            return False
        should = flt.get("should") or []
        return not should or any(self._cond(col, c, payload) for c in should)

    def _idf(self, col: Dict[str, Any]) -> Dict[str, float]:
        if col.get("_idf") is None:
            df: Dict[str, int] = {}
            pts = col["points"].values()
            for _v, pl in pts:
                for f in _features(pl.get("text") or ""):
                    df[f] = df.get(f, 0) + 1
            n = max(1, len(col["points"]))
            col["_idf"] = {f: math.log(1 + n / (1 + d)) for f, d in df.items()}
            col["_n"] = n
        return col["_idf"]

    def score(self, col: Dict[str, Any], query: str, text: str) -> float:
        idf = self._idf(col)
        default = math.log(1 + col["_n"])
        q = _features(query)
        d = _features(text)
        total = sum(w * idf.get(f, default) for f, w in q.items())
        if not total:
            return 0.0
        hit = sum(w * idf.get(f, default) for f, w in q.items() if f in d)
        return round(0.8 * hit / total, 4)

    # -- the API
    def _handle(self, req: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(req.content or b"{}") if req.content else {}
            parts = [p for p in req.url.path.split("/") if p]
            if not parts:
                return httpx.Response(200, json={"title": "qdrant", "version": "memory"})
            if parts[0] != "collections":
                raise httpx.ConnectError(f"MemoryQdrant: no route for {req.url}")
            if len(parts) == 1:
                return httpx.Response(200, json={"result": {"collections": [{"name": n} for n in self.cols]}})
            name, rest = parts[1], parts[2:]
            col = self.cols.get(name)
            if not rest:
                if req.method == "GET":
                    if col is None:
                        return httpx.Response(404, json={"status": {"error": "Not found"}})
                    return httpx.Response(200, json={"result": {"points_count": len(col["points"]),
                                                                "payload_schema": dict(col["indexes"])}})
                if req.method == "PUT":
                    if col is not None:
                        return httpx.Response(409, json={"status": {"error": f"Collection `{name}` already exists!"}})
                    self.cols[name] = {"points": {}, "indexes": {}, "config": body, "_idf": None}
                    return httpx.Response(200, json={"result": True})
            if col is None:
                return httpx.Response(404, json={"status": {"error": f"Collection `{name}` doesn't exist!"}})
            if rest == ["index"]:
                col["indexes"][body["field_name"]] = body["field_schema"]
                return httpx.Response(200, json={"result": {"status": "acknowledged"}})
            if rest == ["points"] and req.method == "PUT":
                for p in body.get("points") or []:
                    col["points"][self._pid(p["id"])] = (p.get("vector"), dict(p.get("payload") or {}))
                col["_idf"] = None
                return httpx.Response(200, json={"result": {"status": "completed"}})
            if rest == ["points"] and req.method == "POST":
                ids = [self._pid(i) for i in body.get("ids") or []]
                res = [{"id": i, "payload": col["points"][i][1]} for i in ids if i in col["points"]]
                return httpx.Response(200, json={"result": res})
            if rest == ["points", "search"]:
                query = self.texts.get(_vkey(body.get("vector") or []), "")
                thr = body.get("score_threshold")
                hits = []
                for pid, (_v, pl) in col["points"].items():
                    if not self._ok(col, body.get("filter"), pl):
                        continue
                    s = self.score(col, query, pl.get("text") or "")
                    if thr is not None and s < thr:
                        continue
                    hits.append({"id": pid, "version": 0, "score": s, "payload": dict(pl)})
                hits.sort(key=lambda h: (-h["score"], self._order(h["id"])))
                return httpx.Response(200, json={"result": hits[:int(body.get("limit") or 10)]})
            if rest == ["points", "scroll"]:
                ids = sorted((pid for pid, (_v, pl) in col["points"].items()
                              if self._ok(col, body.get("filter"), pl)), key=self._order)
                start = 0
                if body.get("offset") is not None:
                    off = self._pid(body["offset"])
                    start = next((i for i, x in enumerate(ids) if x == off), len(ids))
                lim = int(body.get("limit") or 10)
                page = ids[start:start + lim]
                nxt = ids[start + lim] if start + lim < len(ids) else None
                return httpx.Response(200, json={"result": {
                    "points": [{"id": i, "payload": dict(col["points"][i][1])} for i in page],
                    "next_page_offset": nxt}})
            if rest == ["points", "delete"]:
                if "points" in body:
                    gone = [self._pid(i) for i in body["points"]]
                else:
                    gone = [pid for pid, (_v, pl) in col["points"].items()
                            if self._ok(col, body.get("filter"), pl)]
                for pid in gone:
                    col["points"].pop(pid, None)
                col["_idf"] = None
                return httpx.Response(200, json={"result": {"status": "completed"}})
            raise httpx.ConnectError(f"MemoryQdrant: no route for {req.method} {req.url}")
        except _Bad as exc:
            return httpx.Response(400, json={"status": {"error": str(exc)}})


async def _no_rewrites(*_a, **_kw) -> List[str]:
    return []


async def _no_filed(*_a, **_kw) -> Dict[str, Any]:
    return {}


@contextmanager
def offline(mem: Optional[MemoryQdrant] = None) -> Iterator[MemoryQdrant]:
    """Every QdrantStore call goes to `mem`; every EmbeddingEngine is the stub; no LLM rewrites,
    no Supabase reads. Anything else that tries the network fails loudly (MockTransport)."""
    mem = mem or MemoryQdrant()
    real_client = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = mem.transport()
        return real_client(*a, **kw)

    with patch("httpx.AsyncClient", _client), \
            patch("vula.ingestion.pipeline.EmbeddingEngine", new=lambda *a, **k: StubEmbedder(mem.texts)), \
            patch("vula.ingestion.pipeline.expand_query", new=_no_rewrites), \
            patch("vula.commerce.service.filed_amounts_by_filename", new=_no_filed), \
            patch("vula.commerce.doc_titles.titles_for", new=lambda *_a, **_k: {}):
        yield mem


async def build_fixture(fixture: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, int]:
    """Ingest the fixture documents through the real ingest_text path. Call inside offline()."""
    from vula.ingestion.pipeline import VulaIngestionPipeline
    fixture = fixture if fixture is not None else load_fixture()
    stored: Dict[str, int] = {}
    for tid, docs in fixture.items():
        p = VulaIngestionPipeline(tenant_id=tid)
        for d in docs:
            res = await p.ingest_text(content=d["text"], filename=d["filename"],
                                      source_type=d.get("source_type", "document"),
                                      category=d.get("category"))
            if res.status != "success":
                raise RuntimeError(f"fixture ingest failed for {tid}/{d['filename']}: {res.error}")
            stored[tid] = stored.get(tid, 0) + res.chunks_stored
    return stored


# ── Running one case through the production retrieval ─────────────────────────

async def _owner_retrieval(tenant_id: str, question: str) -> tuple:
    """Mirrors core/skills/reasoning.py::ReasoningSkill.run's retrieval step: authoritative-only
    query (wider for a spec question), the spec-product guard, then format_kb_chunks — the block
    the model actually sees. Returns (chunks used, grounding block)."""
    from core.skills.base import format_kb_chunks, looks_like_spec_question, spec_product_missing
    from vula.ingestion.pipeline import VulaIngestionPipeline
    spec = looks_like_spec_question(question)
    chunks = await VulaIngestionPipeline(tenant_id=tenant_id).query(
        question, top_k=SPEC_TOP_K if spec else PROD_TOP_K, authoritative_only=True)
    if spec and chunks and spec_product_missing(question, chunks):
        chunks = []
    return chunks, (await format_kb_chunks(tenant_id, chunks) if chunks else "")


async def _customer_retrieval(tenant_id: str, question: str) -> tuple:
    """The customer path is the real method — core/skills/commerce_assistant.py::_retrieve_kb."""
    from core.skills.base import SkillInput
    from core.skills.commerce_assistant import CommerceAssistantSkill
    block, sources = await CommerceAssistantSkill()._retrieve_kb(
        SkillInput(question=question, tenant_id=tenant_id))
    return [{"filename": s.get("filename"), "text": s.get("text")} for s in sources], block


async def _deep(tenant_id: str, question: str, customer: bool) -> List[Dict[str, Any]]:
    from vula.ingestion.pipeline import VulaIngestionPipeline
    return await VulaIngestionPipeline(tenant_id=tenant_id).query(
        question, top_k=DEEP_TOP_K, authoritative_only=not customer)


class _SearchCounter:
    """Vector searches per query = embed calls per query (one per query variant), in both
    modes — counted at QdrantStore.search so it sees every pipeline instance a case creates."""

    def __init__(self) -> None:
        self.n = 0

    @contextmanager
    def counting(self) -> Iterator[None]:
        from vula.ingestion.pipeline import QdrantStore
        orig = QdrantStore.search
        counter = self

        async def _counted(self_, *a, **kw):
            counter.n += 1
            return await orig(self_, *a, **kw)

        with patch.object(QdrantStore, "search", _counted):
            yield


async def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    tid, q = case["tenant"], case["question"]
    customer = case.get("entry", "owner") == "customer"
    expected = list(case.get("expected_docs") or [])
    must = case.get("must_contain") or []
    must = [must] if isinstance(must, str) else list(must)
    forbidden = list(case.get("forbidden_docs") or [])
    row: Dict[str, Any] = {k: case.get(k) for k in ("id", "tenant", "question", "type", "language", "synthetic")}
    row["entry"] = case.get("entry", "owner")
    counter = _SearchCounter()
    t0 = time.perf_counter()
    try:
        with counter.counting():
            chunks, block = await (_customer_retrieval if customer else _owner_retrieval)(tid, q)
        row["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        deep = await _deep(tid, q, customer)
    except Exception as exc:  # noqa: BLE001 — one broken case must not sink the run
        row.update(error=f"{type(exc).__name__}: {str(exc)[:200]}", ok=False)
        return row
    row["embed_calls"] = counter.n
    row["context_tokens"] = len(block) // 4
    prod_docs, deep_docs = ranked_docs(chunks), ranked_docs(deep)
    row["docs"] = prod_docs
    row["deep_docs"] = deep_docs[:10]
    row["abstained"] = not chunks
    if expected:
        row["recall@5"] = recall_at(deep_docs, expected, 5)
        row["recall@20"] = recall_at(deep_docs, expected, 20)
        row["rr"] = reciprocal_rank(deep_docs, expected)
        row["ndcg@10"] = ndcg_at(deep_docs, expected, 10)
        row["in_prompt"] = any(_matches(d, e) for d in prod_docs for e in expected)
    if must:
        row["fact_retrieved"] = any(_contains_all(c.get("text") or "", must) for c in chunks)
        row["fact_in_prompt"] = _contains_all(block, must)
    if forbidden:
        row["leak"] = any(_matches(d, f) for d in prod_docs for f in forbidden)
    if case.get("type") == "unanswerable":
        row["ok"] = row["abstained"]
    elif case.get("type") == "sensitive" and forbidden:
        row["ok"] = not row["leak"]
    else:
        row["ok"] = bool(row.get("in_prompt", True)) and bool(row.get("fact_in_prompt", True))
    return row


# ── Aggregation ───────────────────────────────────────────────────────────────

_METRICS = ("recall@5", "recall@20", "rr", "ndcg@10", "in_prompt", "fact_in_prompt")


def _mean(vals: List[Any]) -> Optional[float]:
    vals = [float(v) for v in vals if v is not None]
    return round(statistics.mean(vals), 3) if vals else None


def _pct(vals: List[float], p: float) -> Optional[float]:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    return round(vals[min(len(vals) - 1, max(0, math.ceil(p * len(vals)) - 1))], 1)


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok_rows = [r for r in rows if "error" not in r]
    out: Dict[str, Any] = {"cases": len(rows), "errors": len(rows) - len(ok_rows),
                           "passed": sum(1 for r in rows if r.get("ok"))}
    for m in _METRICS:
        out["mrr" if m == "rr" else m] = _mean([r.get(m) for r in ok_rows])
    un = [r for r in ok_rows if r.get("type") == "unanswerable"]
    out["abstention"] = _mean([r["abstained"] for r in un]) if un else None
    ans = [r for r in ok_rows if r.get("type") not in ("unanswerable",) and "in_prompt" in r]
    out["false_abstention"] = _mean([r["abstained"] for r in ans]) if ans else None
    leaks = [r["leak"] for r in ok_rows if "leak" in r]
    out["leak_rate"] = _mean(leaks) if leaks else None
    ms = [r["ms"] for r in ok_rows if r.get("ms") is not None]
    out["p50_ms"], out["p95_ms"] = _pct(ms, 0.5), _pct(ms, 0.95)
    out["context_tokens"] = _mean([r.get("context_tokens") for r in ok_rows])
    out["embed_calls"] = _mean([r.get("embed_calls") for r in ok_rows])
    return out


def _by(rows: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r.get(key)), []).append(r)
    return {k: summarize(v) for k, v in sorted(groups.items())}


def report(rows: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    return {"layer": "retrieval", "mode": mode, "summary": summarize(rows),
            "by_tenant": _by(rows, "tenant"), "by_type": _by(rows, "type"),
            "passed": sum(1 for r in rows if r.get("ok")), "total": len(rows), "rows": rows}


# ── Entry points ──────────────────────────────────────────────────────────────

async def run_fixture(cases: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The deterministic CI subset."""
    fixture = load_fixture()
    cases = [c for c in (cases or load_cases()) if c["tenant"] in fixture]
    with offline():
        await build_fixture(fixture)
        rows = [await run_case(c) for c in cases]
    return report(rows, "fixture")


async def run_live(cases: Optional[List[Dict[str, Any]]] = None,
                   tenant: Optional[str] = None) -> Dict[str, Any]:
    """Real tenants, read-only. Production expansion (LLM rewrites) included."""
    fixture = load_fixture()
    cases = [c for c in (cases or load_cases())
             if c["tenant"] not in fixture and (not tenant or c["tenant"] == tenant)]
    rows = [await run_case(c) for c in cases]
    return report(rows, "live")


def benchmark_rows(rep: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Retrieval rows in the benchmark scorecard's shape (evals/benchmark.py::scorecard)."""
    out = []
    for r in rep["rows"]:
        why = []
        if r.get("error"):
            why.append(r["error"])
        elif not r.get("ok"):
            if r.get("type") == "unanswerable":
                why.append(f"didn't abstain: {', '.join(r.get('docs') or [])[:200]}")
            elif r.get("leak"):
                why.append("customer question reached a staff/owner document")
            elif r.get("in_prompt") is False:
                why.append(f"expected document not retrieved (got {', '.join(r.get('docs') or [])[:200]})")
            elif r.get("fact_in_prompt") is False:
                why.append("document retrieved but the fact didn't reach the prompt")
        out.append({"id": f"retrieval:{r.get('id') or r['question'][:30]}",
                    "component": f"Retrieval · {r['tenant']}", "prompt": r["question"],
                    "ok": bool(r.get("ok")), "why": why, "secs": (r.get("ms") or 0) / 1000})
    return out


# ── Synthetic cases from a real tenant's own chunks ───────────────────────────

_SYNTH_PROMPT = (
    "Below is an excerpt from a small business's own document. Write ONE short question (under 15 "
    "words) that a staff member might type on WhatsApp and that this excerpt answers. Use the "
    "product, supplier or figure names exactly as written. Reply with the question only.\n\n"
    "Excerpt:\n{text}")


async def synthesize(tenant_id: str, per_doc: int = 5, max_docs: int = 40) -> List[Dict[str, Any]]:
    """Read-only: scroll the tenant's collection, pick up to `per_doc` chunks spread through
    each document, and have the local-first model write one question per chunk, labelled with
    the source document. Output is UNREVIEWED (synthetic: true, reviewed: false)."""
    import litellm
    from core.llm_router import resolve_generation_route
    from vula.ingestion.pipeline import QdrantStore
    litellm.drop_params = True
    store = QdrantStore()
    name = store._collection_name(tenant_id)
    by_doc: Dict[str, List[Dict[str, Any]]] = {}
    offset = None
    async with httpx.AsyncClient(timeout=20.0, headers=store._headers()) as client:
        while True:
            body: Dict[str, Any] = {"limit": 256, "with_payload": True}
            if offset is not None:
                body["offset"] = offset
            resp = await client.post(f"{store.base}/collections/{name}/points/scroll", json=body)
            resp.raise_for_status()
            res = resp.json().get("result", {})
            for p in res.get("points", []):
                pl = p.get("payload") or {}
                if pl.get("source_type") in ("conversation", "learned") or len(pl.get("text") or "") < 200:
                    continue
                by_doc.setdefault(str(pl.get("doc_id")), []).append(pl)
            offset = res.get("next_page_offset")
            if offset is None:
                break
    model, key, base = await resolve_generation_route()
    cases: List[Dict[str, Any]] = []
    for doc_id, chunks in list(by_doc.items())[:max_docs]:
        chunks.sort(key=lambda c: c.get("chunk_index") or 0)
        step = max(1, len(chunks) // per_doc)
        for c in chunks[::step][:per_doc]:
            try:
                resp = await litellm.acompletion(
                    model=model, api_key=key, api_base=base, temperature=0.4, max_tokens=60,
                    messages=[{"role": "user", "content": _SYNTH_PROMPT.format(text=c["text"][:2500])}])
                question = (resp.choices[0].message.content or "").strip().strip('"').splitlines()[0]
            except Exception as exc:  # noqa: BLE001
                log.debug("synth question failed for %s: %s", doc_id, exc)
                continue
            if len(question) < 8:
                continue
            cases.append({"id": f"synth-{tenant_id}-{doc_id[:8]}-{c.get('chunk_index')}",
                          "tenant": tenant_id, "question": question, "type": "lookup",
                          "expected_docs": [c.get("filename")], "language": "en",
                          "synthetic": True, "reviewed": False,
                          "source_chunk": c.get("chunk_id")})
    return cases

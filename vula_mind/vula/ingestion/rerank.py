"""Second look at the search's top 20 before the best 4 reach the model (2026-10-26).

The retrieval diagnostic, after rank fusion and document labels: the right document is in the
top 20 for 81% of questions but in the top 4 the model sees for only 64%. The first pass ranks
chunks against the question one embedding at a time; a second pass that reads the question and
each candidate together can order them better. Two ways, chosen by KB_RERANK and measured side by
side in evals/retrieval_diag.py before either is switched on:

  lexical — BM25 of the question's words (plus their stems and business synonyms, the same terms
            the literal search uses) over each candidate's label + text, scored among the 20
            candidates only, then rank-fused with the first pass's order. No model, instant.
  llm     — the local model is shown the question and the 20 candidates (label + a snippet) and
            names the ones that answer it, best first. Local only: business documents are never
            sent to a cloud model for this. If the local model is unreachable, slow (KB_RERANK
            _TIMEOUT_S) or replies with nothing usable, it falls back to `lexical`.

Either way the result is a re-ordering of the same candidates: nothing is added, nothing is
dropped from the pool, and the original order breaks ties.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CANDIDATES = 20
_K = 10            # the same rank-fusion constant as pipeline._fuse
_SNIPPET = 360


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9][a-z0-9\-/]*", (text or "").lower())


def _doc_text(h: Dict[str, Any]) -> str:
    return f"{h.get('ctx_label') or h.get('filename') or ''} {h.get('text') or ''}"


def lexical_order(question: str, hits: List[Dict[str, Any]]) -> List[int]:
    """Indices of `hits` best first: BM25 over the candidates, fused with their first-pass rank."""
    from vula.ingestion.pipeline import _salient_terms
    if not hits:
        return []
    terms = list(dict.fromkeys(_salient_terms(question) + [w for w in _words(question) if len(w) >= 4]))
    docs = [_words(_doc_text(h)) for h in hits]
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in terms}
    k1, b = 1.2, 0.75
    scores = []
    for d in docs:
        tf: Dict[str, int] = {}
        for w in d:
            tf[w] = tf.get(w, 0) + 1
        s = 0.0
        for t in terms:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg))
        scores.append(s)
    by_bm25 = sorted(range(n), key=lambda i: -scores[i])
    bm25_rank = {i: r for r, i in enumerate(by_bm25, 1)}
    # A candidate with no matching word gets no lexical credit, only its first-pass rank.
    fused = {i: 1 / (_K + i + 1) + (1 / (_K + bm25_rank[i]) if scores[i] > 0 else 0) for i in range(n)}
    # A tie between the two orders goes to the words: that's what the second look is for.
    return sorted(range(n), key=lambda i: (-fused[i], -scores[i], i))


def _prompt(question: str, hits: List[Dict[str, Any]]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        label = (h.get("ctx_label") or h.get("filename") or "document")[:120]
        snippet = re.sub(r"\s+", " ", h.get("text") or "")[:_SNIPPET]
        lines.append(f"[{i}] ({label}) {snippet}")
    return ("Below are numbered passages from a business's documents, and a question. Which "
            "passages contain the answer to the question? Reply with ONLY their numbers, best "
            "first, separated by commas (at most 6). Reply 0 if none do.\n\n"
            f"Question: {question}\n\n" + "\n".join(lines) + "\n\nNumbers:")


def parse_order(raw: str, n: int) -> List[int]:
    """Candidate indices (0-based) named in the model's reply, first mention first, in range."""
    out: List[int] = []
    for m in re.findall(r"\d+", raw or ""):
        i = int(m) - 1
        if 0 <= i < n and i not in out:
            out.append(i)
    return out


async def llm_order(question: str, hits: List[Dict[str, Any]], timeout_s: float) -> Optional[List[int]]:
    """The local model's choice, or None (unavailable, not local, slow, or nothing usable)."""
    try:
        import litellm
        from core.llm_router import is_local_model, resolve_generation_route
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route(task_type="kb_rerank")
        if not is_local_model(model):
            return None      # never send business documents to a cloud model for this
        resp = await asyncio.wait_for(litellm.acompletion(
            model=model, messages=[{"role": "user", "content": _prompt(question, hits)}],
            temperature=0, max_tokens=40, api_key=api_key, api_base=api_base), timeout=timeout_s)
        raw = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.DOTALL)
    except Exception as exc:  # noqa: BLE001 — a second look is optional; the first pass stands
        logger.debug("llm rerank unavailable: %s", type(exc).__name__)
        return None
    picked = parse_order(raw, len(hits))
    return picked or None


async def rerank(question: str, hits: List[Dict[str, Any]], mode: str,
                 timeout_s: float = 8.0) -> List[Dict[str, Any]]:
    """`hits` re-ordered by `mode` ("lexical" | "llm"); any other mode, or a failure, returns them
    unchanged. Only the first CANDIDATES are re-ordered; the rest keep their place after them."""
    if mode not in ("lexical", "llm") or len(hits) < 2:
        return hits
    head, tail = hits[:CANDIDATES], hits[CANDIDATES:]
    order: Optional[List[int]] = None
    if mode == "llm":
        picked = await llm_order(question, head, timeout_s)
        if picked:
            lex = lexical_order(question, head)
            order = picked + [i for i in lex if i not in picked]
    if order is None:
        order = lexical_order(question, head)
    return [head[i] for i in order] + tail

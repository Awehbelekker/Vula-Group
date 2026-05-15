"""Merger — four strategies for combining ThinKMesh branch outputs."""
from __future__ import annotations

import logging
from collections import Counter

import httpx

from core.thinkmesh.graph import GraphStatus, MergeStrategy, TaskBranch, TaskGraph

log = logging.getLogger(__name__)


def _vote(branches: list[TaskBranch]) -> str:
    """Majority vote on the first sentence of each answer."""
    leads = [b.answer.split(".")[0].strip() for b in branches]
    most_common, _ = Counter(leads).most_common(1)[0]
    for b in branches:
        if b.answer.startswith(most_common):
            return b.answer
    return branches[0].answer


def _fastest(branches: list[TaskBranch]) -> str:
    return min(branches, key=lambda b: b.latency_ms).answer


def _best_confidence(branches: list[TaskBranch]) -> str:
    return max(branches, key=lambda b: b.confidence).answer


def _synthesize(branches: list[TaskBranch], ollama_url: str, model: str) -> str:
    """Feed all think traces + answers to synthesis model."""
    parts = []
    for i, b in enumerate(branches, 1):
        parts.append(f"Branch {i} reasoning:\n{b.think_trace}\n\nBranch {i} answer:\n{b.answer}")

    synthesis_prompt = (
        "You have multiple reasoning branches that approached the same problem. "
        "Synthesize their insights into the single best answer. "
        "Do not explain what you are doing — just give the final answer.\n\n"
        + "\n\n---\n\n".join(parts)
        + "\n\nSynthesized answer:"
    )

    try:
        resp = httpx.post(
            f"{ollama_url}/api/generate",
            json={"model": model, "prompt": synthesis_prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", branches[0].answer).strip()
    except Exception as exc:
        log.error("Synthesis failed: %s — falling back to best_confidence", exc)
        return _best_confidence(branches)


class ThinKMeshMerger:
    def __init__(self, ollama_url: str = "http://localhost:11434", synthesis_model: str = "deepseek-r1:7b"):
        self.ollama_url = ollama_url
        self.synthesis_model = synthesis_model

    def merge(self, graph: TaskGraph) -> TaskGraph:
        graph.status = GraphStatus.MERGING
        good = graph.successful_branches

        if not good:
            graph.final_answer = "[No successful branches — all failed]"
            graph.status = GraphStatus.ERROR
            return graph

        if len(good) == 1:
            graph.final_answer = good[0].answer
            graph.status = GraphStatus.DONE
            return graph

        strategy = graph.merge_strategy
        if strategy == MergeStrategy.VOTE:
            graph.final_answer = _vote(good)
        elif strategy == MergeStrategy.SYNTHESIZE:
            graph.final_answer = _synthesize(good, self.ollama_url, self.synthesis_model)
        elif strategy == MergeStrategy.FASTEST:
            graph.final_answer = _fastest(good)
        else:
            graph.final_answer = _best_confidence(good)

        graph.status = GraphStatus.DONE
        import time
        graph.completed_at = time.time()

        log.info(
            "Merged task=%s strategy=%s answer_len=%d",
            graph.task_id[:8], strategy.value, len(graph.final_answer),
        )
        return graph

"""ThinKMesh executor — runs branches in parallel, parses think traces."""
from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from config import settings
from core.thinkmesh.graph import BranchStatus, GraphStatus, ModelTier, TaskBranch, TaskGraph

log = logging.getLogger(__name__)

MODEL_MAP: dict[ModelTier, str] = {
    ModelTier.EDGE: settings.model_edge,
    ModelTier.WORKER: settings.model_worker,
    ModelTier.REASONER: settings.model_reasoner,
    ModelTier.CEILING: settings.model_reasoner,  # fallback — no 32b by default
}

THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _parse_think(raw: str) -> tuple[str, str]:
    """Split <think>...</think> from answer. Returns (think_trace, clean_answer)."""
    match = THINK_RE.search(raw)
    if match:
        trace = match.group(1).strip()
        answer = THINK_RE.sub("", raw).strip()
        return trace, answer
    return "", raw.strip()


def _estimate_confidence(think_trace: str, answer: str) -> float:
    """Heuristic confidence from think trace quality signals."""
    if not think_trace:
        return 0.4
    score = 0.5
    # rewording/revision signals → lower confidence
    if any(w in think_trace.lower() for w in ["wait", "actually", "hmm", "reconsider"]):
        score -= 0.1
    # structured reasoning → higher
    if any(w in think_trace.lower() for w in ["therefore", "because", "step", "first", "second"]):
        score += 0.15
    # answer length sanity
    if len(answer) > 50:
        score += 0.1
    if len(answer) > 200:
        score += 0.05
    return round(min(1.0, max(0.1, score)), 3)


async def _run_branch(branch: TaskBranch, ollama_url: str) -> TaskBranch:
    model = MODEL_MAP[branch.model_tier]
    branch.status = BranchStatus.RUNNING
    start = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={"model": model, "prompt": branch.prompt, "stream": False},
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")

        branch.think_trace, branch.answer = _parse_think(raw)
        branch.confidence = _estimate_confidence(branch.think_trace, branch.answer)
        branch.status = BranchStatus.COMPLETE
    except Exception as exc:
        log.error("Branch %s failed: %s", branch.branch_id, exc)
        branch.status = BranchStatus.FAILED
        branch.answer = f"[branch failed: {exc}]"

    branch.latency_ms = (time.monotonic() - start) * 1000
    return branch


class ThinKMeshExecutor:
    def __init__(self, ollama_url: str = ""):
        self.ollama_url = ollama_url or settings.ollama_base

    async def execute(self, graph: TaskGraph) -> TaskGraph:
        graph.status = GraphStatus.EXECUTING

        tasks = [_run_branch(b, self.ollama_url) for b in graph.branches]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        for branch, result in zip(graph.branches, results):
            branch.__dict__.update(result.__dict__)

        log.info(
            "ThinKMesh executed task=%s branches=%d successful=%d",
            graph.task_id[:8], len(graph.branches), len(graph.successful_branches),
        )
        return graph

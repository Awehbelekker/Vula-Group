"""
core/thinkmesh/merger.py

ThinKMesh Branch Merger.

Takes completed branches from a TaskGraph and produces a single
merged output using the graph's specified merge strategy.

Merge strategies:
    VOTE        — majority answer wins (good for factual tasks)
    SYNTHESIZE  — HRM synthesises all outputs into one coherent answer
    FASTEST     — first completed branch answer is used
    BEST        — highest confidence branch wins
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import List, Optional

import httpx

from core.thinkmesh.graph import (
    TaskGraph, TaskBranch, MergeStrategy, BranchStatus
)

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"
MERGE_MODEL = "deepseek-r1:7b"   # Used for SYNTHESIZE strategy


class ThinKMeshMerger:
    """
    Merges parallel branch outputs into a single coherent response.
    
    The key insight is that we have access to both the final answers AND
    the DeepSeek think traces from each branch. For SYNTHESIZE, we feed
    both into the merge prompt — giving the synthesis model visibility into
    HOW each branch reasoned, not just what it concluded.
    """

    def __init__(
        self,
        ollama_base: str = OLLAMA_BASE,
        merge_model: str = MERGE_MODEL,
    ):
        self.ollama_base = ollama_base
        self.merge_model = merge_model

    def merge(self, graph: TaskGraph) -> str:
        """
        Merge completed branches according to graph's merge strategy.
        Returns the final merged output string.
        """
        done_branches = graph.done_branches

        if not done_branches:
            logger.error(f"No completed branches to merge for graph {graph.graph_id[:8]}")
            return "Unable to complete task — all reasoning branches failed."

        if len(done_branches) == 1:
            # Single branch — no merging needed
            return done_branches[0].answer

        strategy = graph.merge_strategy

        if strategy == MergeStrategy.FASTEST:
            return self._merge_fastest(done_branches)
        elif strategy == MergeStrategy.VOTE:
            return self._merge_vote(done_branches)
        elif strategy == MergeStrategy.BEST_CONFIDENCE:
            return self._merge_best_confidence(done_branches)
        else:
            # Default: SYNTHESIZE
            return self._merge_synthesize(graph.goal, done_branches)

    # -------------------------------------------------------------------------
    # Merge strategies
    # -------------------------------------------------------------------------

    def _merge_fastest(self, branches: List[TaskBranch]) -> str:
        """Return the answer from the branch with the lowest latency."""
        fastest = min(
            (b for b in branches if b.latency_ms > 0),
            key=lambda b: b.latency_ms,
            default=branches[0]
        )
        logger.debug(f"FASTEST merge: branch {fastest.branch_id} ({fastest.latency_ms}ms)")
        return fastest.answer

    def _merge_best_confidence(self, branches: List[TaskBranch]) -> str:
        """Return the answer from the highest-confidence branch."""
        best = max(branches, key=lambda b: b.confidence)
        logger.debug(
            f"BEST_CONFIDENCE merge: branch {best.branch_id} "
            f"(confidence={best.confidence:.2f})"
        )
        return best.answer

    def _merge_vote(self, branches: List[TaskBranch]) -> str:
        """
        Majority vote on factual answers.
        Groups similar answers using a simple normalisation,
        returns the most common one.
        """
        # Normalise answers for comparison
        normalised = [self._normalise_for_vote(b.answer) for b in branches]
        counter = Counter(normalised)
        winner_normalised = counter.most_common(1)[0][0]

        # Return the original (non-normalised) answer that matches
        for branch in branches:
            if self._normalise_for_vote(branch.answer) == winner_normalised:
                logger.debug(
                    f"VOTE merge: winner is branch {branch.branch_id} "
                    f"(votes={counter[winner_normalised]})"
                )
                return branch.answer

        return branches[0].answer  # Fallback

    def _merge_synthesize(self, goal: str, branches: List[TaskBranch]) -> str:
        """
        Synthesise all branch outputs into one coherent answer.
        
        Feeds both the think traces AND answers to the synthesis model.
        This is what makes ThinKMesh unique — we synthesise reasoning
        processes, not just conclusions.
        """
        # Build synthesis prompt
        branch_summaries = []
        for i, branch in enumerate(branches, 1):
            summary = f"--- Branch {i} (model: {branch.model_tier.value}, confidence: {branch.confidence:.0%}) ---\n"
            if branch.think_trace:
                # Include condensed think trace (first 300 chars)
                think_preview = branch.think_trace[:300].strip()
                if len(branch.think_trace) > 300:
                    think_preview += "..."
                summary += f"Reasoning: {think_preview}\n"
            summary += f"Answer: {branch.answer}\n"
            branch_summaries.append(summary)

        prompt = (
            f"You have received {len(branches)} parallel answers to the same goal.\n"
            f"Goal: {goal}\n\n"
            f"{''.join(branch_summaries)}\n"
            f"Synthesise these into one optimal, comprehensive answer. "
            f"Use the best reasoning from each branch. "
            f"Resolve any contradictions by favouring higher-confidence branches. "
            f"Do not mention that you are synthesising multiple answers — "
            f"just give the best unified response."
        )

        try:
            merged = self._ollama_generate(prompt)
            logger.debug(f"SYNTHESIZE merge complete ({len(merged)} chars)")
            return merged
        except Exception as e:
            logger.error(f"Synthesis failed, falling back to BEST_CONFIDENCE: {e}")
            return self._merge_best_confidence(branches)

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    def _normalise_for_vote(self, text: str) -> str:
        """Normalise text for voting comparison."""
        return " ".join(text.lower().strip().split())[:200]

    def _ollama_generate(self, prompt: str, max_tokens: int = 2048) -> str:
        """Synchronous Ollama call for synthesis."""
        payload = {
            "model": self.merge_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,  # Lower temp for synthesis = more coherent
                "num_predict": max_tokens,
            },
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{self.ollama_base}/api/generate", json=payload)
            resp.raise_for_status()

            response_text = resp.json().get("response", "")

            # Strip think tags from synthesis output if present
            import re
            response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL)
            return response_text.strip()

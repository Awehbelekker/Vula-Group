"""
core/thinkmesh/executor.py

ThinKMesh Parallel Branch Executor.

Runs all TaskBranch instances in a TaskGraph concurrently.
Captures DeepSeek R1 <think> traces separately from final answers.
Feeds completed branches to the merger.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Dict, Any, Optional

import httpx

from core.thinkmesh.graph import (
    TaskGraph, TaskBranch, BranchStatus, GraphStatus, ModelTier
)

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"

# Map model tiers to Ollama model names
MODEL_MAP: Dict[ModelTier, str] = {
    ModelTier.EDGE: "deepseek-r1:1.5b",
    ModelTier.STANDARD: "deepseek-r1:7b",
    ModelTier.ADVANCED: "deepseek-r1:14b",
    ModelTier.CEILING: "deepseek-r1:32b",
}

# Fallback if DeepSeek not available
FALLBACK_MODEL = "qwen2.5:7b"


class ThinKMeshExecutor:
    """
    Executes all branches of a TaskGraph in parallel.
    
    Key insight: DeepSeek R1 wraps its reasoning in <think>...</think> tags.
    We capture that trace separately from the final answer. The merger uses
    both the think trace AND the answer when combining branch outputs.
    This gives us more signal than just comparing final answers.
    """

    def __init__(
        self,
        ollama_base: str = OLLAMA_BASE,
        model_map: Dict[ModelTier, str] = MODEL_MAP,
        branch_timeout_s: float = 60.0,
    ):
        self.ollama_base = ollama_base
        self.model_map = model_map
        self.branch_timeout_s = branch_timeout_s

    async def execute(self, graph: TaskGraph) -> TaskGraph:
        """
        Run all branches in the graph concurrently.
        Returns the graph with all branches populated.
        """
        graph.mark_running()
        logger.info(
            f"ThinKMesh executing {len(graph.branches)} branches "
            f"for graph {graph.graph_id[:8]}"
        )

        # Build system context from memory
        memory_context = self._build_memory_context(graph)

        # Run all branches concurrently
        tasks = [
            self._run_branch(branch, graph.goal, memory_context)
            for branch in graph.branches
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            f"Graph {graph.graph_id[:8]} execution complete: "
            f"{len(graph.done_branches)} done, "
            f"{len(graph.failed_branches)} failed"
        )

        return graph

    async def _run_branch(
        self,
        branch: TaskBranch,
        goal: str,
        memory_context: str,
    ) -> None:
        """Run a single branch with timeout protection."""
        branch.mark_started()
        model = self.model_map.get(branch.model_tier, FALLBACK_MODEL)

        prompt = self._build_prompt(
            subtask=branch.subtask,
            goal=goal,
            skill=branch.skill,
            memory_context=memory_context,
        )

        try:
            async with asyncio.timeout(self.branch_timeout_s):
                response_text = await self._ollama_generate_async(model, prompt)

            think_trace, answer = self._parse_deepseek_response(response_text)
            confidence = self._estimate_confidence(think_trace, answer)

            branch.mark_done(
                answer=answer,
                think_trace=think_trace,
                confidence=confidence,
            )
            branch.tokens_used = len(response_text.split())  # Approximate

            logger.debug(
                f"Branch {branch.branch_id} done: "
                f"model={model} confidence={confidence:.2f} "
                f"latency={branch.latency_ms}ms"
            )

        except asyncio.TimeoutError:
            branch.status = BranchStatus.TIMEOUT
            branch.error = f"Timeout after {self.branch_timeout_s}s"
            logger.warning(f"Branch {branch.branch_id} timed out")

        except Exception as e:
            branch.mark_failed(str(e))
            logger.error(f"Branch {branch.branch_id} failed: {e}")

    def _build_prompt(
        self,
        subtask: str,
        goal: str,
        skill: str,
        memory_context: str,
    ) -> str:
        """Construct the full prompt for a branch."""
        parts = []

        if memory_context:
            parts.append(f"Relevant context from memory:\n{memory_context}\n")

        parts.append(
            f"You are a {skill} specialist. Think carefully before answering.\n\n"
            f"Main goal: {goal}\n\n"
            f"Your specific task: {subtask}\n\n"
            f"Provide a thorough, accurate response."
        )

        return "\n".join(parts)

    def _parse_deepseek_response(self, response: str) -> tuple[str, str]:
        """
        Extract <think> trace and final answer from DeepSeek R1 response.
        
        DeepSeek R1 format:
            <think>
            ... reasoning steps ...
            </think>
            ... final answer ...
        """
        think_match = re.search(r"<think>(.*?)</think>", response, re.DOTALL)

        if think_match:
            think_trace = think_match.group(1).strip()
            # Final answer is everything after </think>
            answer = response[think_match.end():].strip()
        else:
            # No think tags — model didn't use chain of thought
            think_trace = ""
            answer = response.strip()

        return think_trace, answer

    def _estimate_confidence(self, think_trace: str, answer: str) -> float:
        """
        Estimate answer confidence from the think trace.
        
        Heuristics:
        - Longer think traces generally indicate more careful reasoning
        - Uncertainty phrases lower confidence
        - Contradiction phrases lower confidence
        - Definitive conclusions raise confidence
        """
        if not answer:
            return 0.0

        base = 0.6

        # Think trace quality signals
        if think_trace:
            word_count = len(think_trace.split())
            # Longer reasoning = more thorough
            if word_count > 200:
                base += 0.1
            elif word_count > 100:
                base += 0.05

            # Uncertainty markers
            uncertainty_phrases = [
                "i'm not sure", "i don't know", "unclear", "uncertain",
                "might be", "could be", "possibly", "not certain",
            ]
            uncertainty_hits = sum(
                1 for phrase in uncertainty_phrases
                if phrase in think_trace.lower()
            )
            base -= uncertainty_hits * 0.08

            # Confident conclusion markers
            confident_phrases = [
                "therefore", "thus", "clearly", "definitely",
                "the answer is", "in conclusion", "this means",
            ]
            confident_hits = sum(
                1 for phrase in confident_phrases
                if phrase in think_trace.lower()
            )
            base += confident_hits * 0.04

        # Answer quality signals
        if len(answer.split()) < 5:
            base -= 0.1  # Very short answers are suspicious

        return round(max(0.0, min(1.0, base)), 2)

    def _build_memory_context(self, graph: TaskGraph) -> str:
        """Format relevant memories into a context string."""
        if not graph.relevant_memories:
            return ""
        lines = []
        for mem in graph.relevant_memories[:3]:  # Cap at 3 memories
            lines.append(f"- {mem.get('summary', '')}")
        return "\n".join(lines)

    async def _ollama_generate_async(self, model: str, prompt: str) -> str:
        """Async Ollama generation call."""
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 2048,
            },
        }
        async with httpx.AsyncClient(timeout=self.branch_timeout_s + 5) as client:
            resp = await client.post(
                f"{self.ollama_base}/api/generate",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json().get("response", "")

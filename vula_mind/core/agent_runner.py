"""
core/agent_runner.py

The Vula multi-agent loop. Ties together:

    HRM Orchestrator  → decides skill, complexity, branch count, merge strategy
    Skill Loader      → provides concrete skill implementations
    Parallel execution→ runs branches concurrently (asyncio.gather)
    ThinKMesh Merger  → combines branch outputs
    Reflection Store  → logs outcome for future routing hints

Usage:
    runner = AgentRunner()
    result = await runner.run(question="...", tenant_id="digg-demo")
    print(result.final_answer)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, List

from core.hrm.orchestrator import HRMOrchestrator
from core.skills.base import SkillInput, SkillOutput
from core.skills.loader import get_skill
from core.thinkmesh.graph import BranchStatus, GraphStatus, TaskGraph
from core.thinkmesh.merger import ThinKMeshMerger

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    final_answer: str
    skill_used: str
    complexity: int
    merge_strategy: str
    branch_count: int
    sources: List[dict] = field(default_factory=list)
    latency_ms: int = 0
    confidence: float = 0.0
    task_id: str = ""
    branches: List[dict] = field(default_factory=list)


class AgentRunner:
    def __init__(self) -> None:
        self._hrm = HRMOrchestrator()
        self._merger = ThinKMeshMerger()

    async def run(
        self,
        question: str,
        tenant_id: str,
        conversation_history: str = "",
        metadata: dict[str, Any] | None = None,
        use_llm_scoring: bool = False,
        max_branches: int = 0,   # 0 = no cap; set 1 for cost-bounded (WhatsApp)
    ) -> AgentResult:
        started = time.monotonic()

        # 1. HRM plans the task graph (skill + complexity + branches + merge)
        graph = TaskGraph(original_prompt=question, tenant_id=tenant_id)

        # Inject routing hints from reflection store
        try:
            from core.memory.reflection import ReflectionAgent
            agent = ReflectionAgent()
            hints = agent.get_routing_hints(question)
            if hints:
                graph.routing_hints = hints[0] if isinstance(hints, list) else hints
        except Exception:
            pass

        self._hrm.plan(graph, use_llm_scoring=use_llm_scoring)

        # Cost cap: keep only the first N branches (1 branch = 1 LLM call)
        if max_branches and len(graph.branches) > max_branches:
            graph.branches = graph.branches[:max_branches]

        graph.status = GraphStatus.EXECUTING

        # 2. Run all branches in parallel using real skill implementations
        skill_input = SkillInput(
            question=question,
            tenant_id=tenant_id,
            conversation_history=conversation_history,
            metadata=metadata or {},
        )

        async def _run_branch(branch) -> None:
            skill = get_skill(branch.skill_id)
            branch.status = BranchStatus.RUNNING
            output: SkillOutput = await skill(skill_input)
            branch.answer = output.answer
            branch.confidence = output.confidence
            branch.latency_ms = output.latency_ms
            branch.think_trace = output.answer[:500]  # used by synthesize merge
            branch.metadata = {"sources": output.sources, "error": output.error}
            branch.status = (
                BranchStatus.COMPLETE if output.success else BranchStatus.FAILED
            )

        await asyncio.gather(*[_run_branch(b) for b in graph.branches])

        # 3. Merge branch outputs
        self._merger.merge(graph)

        # 4. Collect sources from all successful branches
        all_sources: List[dict] = []
        for b in graph.successful_branches:
            all_sources.extend(b.metadata.get("sources", []) or [])

        # Best confidence among successful branches
        best_conf = max(
            (b.confidence for b in graph.successful_branches), default=0.0
        )

        latency_ms = int((time.monotonic() - started) * 1000)

        # 5. Reflect on the completed graph for future routing improvement
        try:
            from core.memory.reflection import ReflectionAgent
            graph.completed_at = time.time()
            ReflectionAgent().reflect(graph)
        except Exception as exc:
            logger.debug("Reflection logging skipped: %s", exc)

        return AgentResult(
            final_answer=graph.final_answer,
            skill_used=graph.primary_skill,
            complexity=graph.complexity,
            merge_strategy=graph.merge_strategy.value,
            branch_count=len(graph.branches),
            sources=all_sources,
            latency_ms=latency_ms,
            confidence=round(best_conf, 2),
            task_id=graph.task_id,
            branches=[
                {
                    "skill": b.skill_id,
                    "status": b.status.value,
                    "confidence": b.confidence,
                    "latency_ms": b.latency_ms,
                }
                for b in graph.branches
            ],
        )


_runner: AgentRunner | None = None


def get_agent_runner() -> AgentRunner:
    global _runner
    if _runner is None:
        _runner = AgentRunner()
    return _runner

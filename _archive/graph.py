"""
core/thinkmesh/graph.py

TaskGraph — the central data structure for Universal Soul AI.
Every task becomes a graph. HRM populates it. ThinKMesh executes it.
Mobile receives the merged output.
"""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class ModelTier(str, Enum):
    """Available model tiers in the DeepSeek R1 stack."""
    EDGE = "1.5b"       # Routing, triage, mobile — ~2GB VRAM
    STANDARD = "7b"     # Everyday tasks — ~6GB VRAM
    ADVANCED = "14b"    # Complex reasoning, code — ~10GB VRAM
    CEILING = "32b"     # RTX 3090 max — ~22GB VRAM


class DeviceRole(str, Enum):
    """Device roles in the mesh network."""
    MOBILE = "mobile"       # Thin client — dispatches tasks, shows results
    LAPTOP = "laptop"       # Mid-tier compute node
    DESKTOP = "desktop"     # Primary compute node (X299 + RTX 3090)
    SERVER = "server"       # Dedicated inference node


class MergeStrategy(str, Enum):
    """How to combine outputs from parallel branches."""
    VOTE = "vote"               # Majority answer wins
    SYNTHESIZE = "synthesize"   # HRM synthesises all outputs into one
    FASTEST = "fastest"         # First branch to complete wins
    BEST_CONFIDENCE = "best"    # Highest confidence score wins


class BranchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"


class GraphStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    MERGING = "merging"
    DONE = "done"
    FAILED = "failed"


@dataclass
class TaskBranch:
    """
    A single reasoning branch within a TaskGraph.

    Each branch runs independently on a specific model tier and device.
    DeepSeek R1's <think> traces are captured separately from the final
    answer — ThinKMesh uses both for merge decisions.
    """
    branch_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    model_tier: ModelTier = ModelTier.STANDARD
    device: DeviceRole = DeviceRole.DESKTOP
    skill: str = "reasoning"        # Skill name from registry
    subtask: str = ""               # The specific sub-question this branch answers

    # Outputs — populated after execution
    think_trace: str = ""           # DeepSeek <think>...</think> content
    answer: str = ""                # Final answer after thinking
    confidence: float = 0.0        # 0.0–1.0, derived from think trace coherence
    latency_ms: int = 0
    tokens_used: int = 0

    status: BranchStatus = BranchStatus.PENDING
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def mark_started(self) -> None:
        self.status = BranchStatus.RUNNING
        self.started_at = time.time()

    def mark_done(self, answer: str, think_trace: str = "", confidence: float = 0.8) -> None:
        self.status = BranchStatus.DONE
        self.answer = answer
        self.think_trace = think_trace
        self.confidence = confidence
        self.completed_at = time.time()
        if self.started_at:
            self.latency_ms = int((self.completed_at - self.started_at) * 1000)

    def mark_failed(self, error: str) -> None:
        self.status = BranchStatus.FAILED
        self.error = error
        self.completed_at = time.time()

    @property
    def is_complete(self) -> bool:
        return self.status in (BranchStatus.DONE, BranchStatus.FAILED, BranchStatus.TIMEOUT)


@dataclass
class ReflectionLog:
    """
    Post-task learning delta. Written after every completed TaskGraph.
    Accumulated in memory store — drives HRM routing improvement over time.
    """
    graph_id: str
    goal: str
    merge_strategy_used: MergeStrategy
    outcome_score: float            # 0.0–1.0 — how well the goal was achieved
    skills_used: List[str]
    model_tiers_used: List[str]
    winning_branch_id: Optional[str]
    total_latency_ms: int
    what_worked: str                # Natural language reflection
    what_to_try_next: str           # Suggestion for future similar tasks
    timestamp: float = field(default_factory=time.time)


@dataclass
class TaskGraph:
    """
    The central contract for every task in Universal Soul AI.

    Flow:
        1. User submits goal via mobile (TwoSoul)
        2. HRM reads goal → assigns complexity + skill + model tiers
        3. ThinKMesh spawns branches → runs in parallel
        4. Merger combines branch outputs via merge_strategy
        5. Reflection agent scores outcome → writes ReflectionLog to memory
        6. merged_output returned to mobile
    """
    goal: str
    graph_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    complexity: float = 0.5                     # HRM-assigned 0.0–1.0
    primary_skill: str = "reasoning"            # Top-level skill from registry
    branches: List[TaskBranch] = field(default_factory=list)
    merge_strategy: MergeStrategy = MergeStrategy.SYNTHESIZE

    # Populated after execution
    merged_output: str = ""
    reflection: Optional[ReflectionLog] = None

    status: GraphStatus = GraphStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    # Context carried from memory
    relevant_memories: List[Dict[str, Any]] = field(default_factory=list)
    session_id: Optional[str] = None
    device_origin: DeviceRole = DeviceRole.MOBILE

    def add_branch(self, branch: TaskBranch) -> None:
        self.branches.append(branch)

    def mark_running(self) -> None:
        self.status = GraphStatus.RUNNING

    def mark_done(self, merged_output: str) -> None:
        self.status = GraphStatus.DONE
        self.merged_output = merged_output
        self.completed_at = time.time()

    def mark_failed(self) -> None:
        self.status = GraphStatus.FAILED
        self.completed_at = time.time()

    @property
    def done_branches(self) -> List[TaskBranch]:
        return [b for b in self.branches if b.status == BranchStatus.DONE]

    @property
    def failed_branches(self) -> List[TaskBranch]:
        return [b for b in self.branches if b.status == BranchStatus.FAILED]

    @property
    def total_latency_ms(self) -> int:
        if not self.completed_at:
            return 0
        return int((self.completed_at - self.created_at) * 1000)

    @property
    def complexity_label(self) -> str:
        if self.complexity < 0.3:
            return "simple"
        elif self.complexity < 0.7:
            return "moderate"
        else:
            return "complex"

    def summary(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "goal": self.goal[:100],
            "complexity": self.complexity,
            "complexity_label": self.complexity_label,
            "branches": len(self.branches),
            "done": len(self.done_branches),
            "failed": len(self.failed_branches),
            "merge_strategy": self.merge_strategy.value,
            "status": self.status.value,
            "latency_ms": self.total_latency_ms,
            "output_preview": self.merged_output[:200] if self.merged_output else None,
        }

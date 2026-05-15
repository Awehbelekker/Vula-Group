"""Central data contract — every task becomes a TaskGraph."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class ModelTier(Enum):
    EDGE = "1.5b"
    WORKER = "7b"
    REASONER = "14b"
    CEILING = "32b"


class DeviceRole(Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    EDGE = "edge"


class MergeStrategy(Enum):
    VOTE = "vote"
    SYNTHESIZE = "synthesize"
    FASTEST = "fastest"
    BEST_CONFIDENCE = "best_confidence"


class BranchStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class GraphStatus(Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    EXECUTING = "executing"
    MERGING = "merging"
    DONE = "done"
    ERROR = "error"


@dataclass
class TaskBranch:
    branch_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    device_role: DeviceRole = DeviceRole.PRIMARY
    model_tier: ModelTier = ModelTier.WORKER
    skill_id: str = "reasoning"
    prompt: str = ""
    think_trace: str = ""
    answer: str = ""
    confidence: float = 0.0
    latency_ms: float = 0.0
    status: BranchStatus = BranchStatus.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskGraph:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    original_prompt: str = ""
    complexity: int = 1          # 1=simple 2=moderate 3=complex
    branches: list[TaskBranch] = field(default_factory=list)
    merge_strategy: MergeStrategy = MergeStrategy.BEST_CONFIDENCE
    final_answer: str = ""
    status: GraphStatus = GraphStatus.QUEUED
    tenant_id: str = "default"
    routing_hints: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=lambda: __import__("time").time())
    completed_at: float = 0.0

    def add_branch(self, **kwargs) -> TaskBranch:
        branch = TaskBranch(**kwargs)
        self.branches.append(branch)
        return branch

    @property
    def successful_branches(self) -> list[TaskBranch]:
        return [b for b in self.branches if b.status == BranchStatus.COMPLETE]

    # Alias used by reflection.py and main.py
    @property
    def done_branches(self) -> list[TaskBranch]:
        return self.successful_branches

    @property
    def goal(self) -> str:
        return self.original_prompt

    @property
    def primary_skill(self) -> str:
        return self.branches[0].skill_id if self.branches else "reasoning"

    @property
    def merged_output(self) -> str:
        return self.final_answer

    @property
    def graph_id(self) -> str:
        return self.task_id

    @property
    def total_latency_ms(self) -> float:
        return self.elapsed_ms

    @property
    def elapsed_ms(self) -> float:
        if self.completed_at:
            return (self.completed_at - self.created_at) * 1000
        return (time.time() - self.created_at) * 1000


@dataclass
class ReflectionLog:
    graph_id: str
    goal: str
    merge_strategy_used: MergeStrategy
    outcome_score: float
    skills_used: List[str]
    model_tiers_used: List[str]
    winning_branch_id: Optional[str]
    total_latency_ms: float
    what_worked: str
    what_to_try_next: str
    timestamp: float = field(default_factory=time.time)

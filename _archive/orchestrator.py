"""
core/hrm/orchestrator.py

HRM Orchestrator — Universal Soul AI's routing brain.

HRM (27M params) does NOT generate answers.
It makes three decisions:
    1. How complex is this task? (0.0–1.0)
    2. Which skill should handle it?
    3. Which model tier(s) and device(s) should run branches?

This keeps HRM lean, fast, and runnable on any device including mobile.
Heavy generation is delegated to DeepSeek R1 tiers via ThinKMesh.
"""

from __future__ import annotations

import json
import re
import time
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import httpx  # Ollama HTTP client

from core.thinkmesh.graph import (
    TaskGraph, TaskBranch, ModelTier, DeviceRole,
    MergeStrategy, GraphStatus
)

logger = logging.getLogger(__name__)

# Path to skill registry
REGISTRY_PATH = Path(__file__).parent.parent / "skills" / "registry.json"

# Ollama endpoint (local)
OLLAMA_BASE = "http://localhost:11434"
HRM_MODEL = "qwen2.5:3b"   # Lightweight model acting as HRM until custom weights ready


class HRMOrchestrator:
    """
    The HRM orchestration layer.

    Reads a natural language goal and produces a populated TaskGraph
    ready for ThinKMesh execution. Uses a small local model for
    complexity scoring and skill matching.
    """

    def __init__(
        self,
        ollama_base: str = OLLAMA_BASE,
        hrm_model: str = HRM_MODEL,
        registry_path: Path = REGISTRY_PATH,
    ):
        self.ollama_base = ollama_base
        self.hrm_model = hrm_model
        self.skill_registry = self._load_registry(registry_path)
        self._mesh_state: Dict[str, Any] = {}  # device_id → capabilities

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def build_graph(
        self,
        goal: str,
        session_id: Optional[str] = None,
        device_origin: DeviceRole = DeviceRole.MOBILE,
        relevant_memories: Optional[List[Dict]] = None,
    ) -> TaskGraph:
        """
        Main entry point. Takes a goal string, returns a fully populated
        TaskGraph ready for ThinKMesh execution.
        """
        logger.info(f"HRM building graph for goal: {goal[:80]}...")

        # Step 1: Score complexity
        complexity = self._score_complexity(goal)

        # Step 2: Match skill
        skill = self._match_skill(goal)

        # Step 3: Determine merge strategy
        merge_strategy = self._select_merge_strategy(complexity, skill)

        # Step 4: Build the graph
        graph = TaskGraph(
            goal=goal,
            complexity=complexity,
            primary_skill=skill["name"],
            merge_strategy=merge_strategy,
            session_id=session_id,
            device_origin=device_origin,
            relevant_memories=relevant_memories or [],
        )

        # Step 5: Spawn branches based on complexity
        branches = self._spawn_branches(goal, complexity, skill)
        for branch in branches:
            graph.add_branch(branch)

        logger.info(
            f"Graph {graph.graph_id[:8]} built: "
            f"complexity={complexity:.2f} skill={skill['name']} "
            f"branches={len(branches)} strategy={merge_strategy.value}"
        )

        return graph

    def update_mesh_state(self, mesh_state: Dict[str, Any]) -> None:
        """Receive current mesh state from discovery module."""
        self._mesh_state = mesh_state

    # -------------------------------------------------------------------------
    # Complexity scoring
    # -------------------------------------------------------------------------

    def _score_complexity(self, goal: str) -> float:
        """
        Score task complexity 0.0–1.0 using lightweight heuristics + HRM model.
        Heuristics run first (fast path). Model consulted for ambiguous cases.
        """
        # Fast path: keyword heuristics
        simple_keywords = [
            "what is", "define", "who is", "when did", "how many",
            "translate", "summarise", "weather", "time", "date",
        ]
        complex_keywords = [
            "analyse", "analyze", "compare", "build", "architect",
            "strategy", "plan", "debug", "optimize", "research",
            "write code", "design", "evaluate", "synthesise", "reason",
        ]

        goal_lower = goal.lower()
        simple_hits = sum(1 for kw in simple_keywords if kw in goal_lower)
        complex_hits = sum(1 for kw in complex_keywords if kw in goal_lower)

        word_count = len(goal.split())

        # Heuristic score
        heuristic = 0.5
        if simple_hits > complex_hits:
            heuristic = max(0.1, 0.5 - (simple_hits * 0.1))
        elif complex_hits > 0:
            heuristic = min(0.95, 0.5 + (complex_hits * 0.15))

        # Word count modifier
        if word_count > 50:
            heuristic = min(0.95, heuristic + 0.1)
        elif word_count < 10:
            heuristic = max(0.05, heuristic - 0.1)

        # If clearly simple or clearly complex, skip model call
        if heuristic < 0.2 or heuristic > 0.85:
            return round(heuristic, 2)

        # Ambiguous — ask the HRM model
        try:
            model_score = self._llm_complexity_score(goal)
            # Blend heuristic and model score
            blended = (heuristic * 0.4) + (model_score * 0.6)
            return round(blended, 2)
        except Exception as e:
            logger.warning(f"HRM model complexity scoring failed, using heuristic: {e}")
            return round(heuristic, 2)

    def _llm_complexity_score(self, goal: str) -> float:
        """Ask the local HRM model to score complexity."""
        prompt = (
            "You are a task complexity scorer. Given a user goal, output ONLY "
            "a single decimal number between 0.0 and 1.0 representing complexity. "
            "0.0 = trivially simple (single fact lookup). "
            "1.0 = extremely complex (multi-step research, code architecture, strategy). "
            "Output NOTHING except the number.\n\n"
            f"Goal: {goal}\n\nComplexity score:"
        )

        response = self._ollama_generate(prompt, max_tokens=10)
        # Extract first float from response
        match = re.search(r"\d+\.?\d*", response.strip())
        if match:
            score = float(match.group())
            return max(0.0, min(1.0, score))
        return 0.5

    # -------------------------------------------------------------------------
    # Skill matching
    # -------------------------------------------------------------------------

    def _match_skill(self, goal: str) -> Dict[str, Any]:
        """Match goal to best skill from registry using keyword overlap."""
        goal_lower = goal.lower()
        best_skill = None
        best_score = 0

        for skill in self.skill_registry["skills"]:
            score = sum(
                1 for kw in skill["trigger_keywords"]
                if kw.lower() in goal_lower
            )
            if score > best_score:
                best_score = score
                best_skill = skill

        # Default to general reasoning if no match
        if not best_skill or best_score == 0:
            best_skill = next(
                (s for s in self.skill_registry["skills"] if s["name"] == "reasoning"),
                self.skill_registry["skills"][0]
            )

        return best_skill

    # -------------------------------------------------------------------------
    # Branch spawning
    # -------------------------------------------------------------------------

    def _spawn_branches(
        self,
        goal: str,
        complexity: float,
        skill: Dict[str, Any],
    ) -> List[TaskBranch]:
        """
        Decide how many branches to spawn and at what model tiers.
        
        Simple tasks  → 1 branch (fast path)
        Moderate      → 2 branches (standard + verify)
        Complex       → 3 branches (parallel exploration)
        """
        preferred_tier = ModelTier(skill.get("model_tier", "7b"))

        if complexity < 0.3:
            # Single branch, lowest sufficient tier
            return [
                TaskBranch(
                    model_tier=ModelTier.EDGE if complexity < 0.15 else ModelTier.STANDARD,
                    device=self._best_device_for_tier(ModelTier.STANDARD),
                    skill=skill["name"],
                    subtask=goal,
                )
            ]

        elif complexity < 0.7:
            # Two branches: preferred tier + one tier up for verification
            tier_up = self._tier_up(preferred_tier)
            return [
                TaskBranch(
                    model_tier=preferred_tier,
                    device=self._best_device_for_tier(preferred_tier),
                    skill=skill["name"],
                    subtask=goal,
                ),
                TaskBranch(
                    model_tier=tier_up,
                    device=self._best_device_for_tier(tier_up),
                    skill=skill["name"],
                    subtask=f"Verify and expand on: {goal}",
                ),
            ]

        else:
            # Three branches: explore different angles in parallel
            return [
                TaskBranch(
                    model_tier=ModelTier.STANDARD,
                    device=self._best_device_for_tier(ModelTier.STANDARD),
                    skill=skill["name"],
                    subtask=f"Direct answer to: {goal}",
                ),
                TaskBranch(
                    model_tier=ModelTier.ADVANCED,
                    device=self._best_device_for_tier(ModelTier.ADVANCED),
                    skill=skill["name"],
                    subtask=f"Detailed analysis of: {goal}",
                ),
                TaskBranch(
                    model_tier=ModelTier.ADVANCED,
                    device=self._best_device_for_tier(ModelTier.ADVANCED),
                    skill=skill["name"],
                    subtask=f"Alternative perspective on: {goal}",
                ),
            ]

    # -------------------------------------------------------------------------
    # Merge strategy selection
    # -------------------------------------------------------------------------

    def _select_merge_strategy(
        self,
        complexity: float,
        skill: Dict[str, Any],
    ) -> MergeStrategy:
        """Select the best merge strategy for the task type."""
        skill_name = skill["name"]

        # Factual tasks: vote for consensus
        if skill_name in ("web_search", "memory_recall", "file_parse"):
            return MergeStrategy.VOTE

        # Speed-critical tasks
        if complexity < 0.2:
            return MergeStrategy.FASTEST

        # Complex reasoning: synthesise
        return MergeStrategy.SYNTHESIZE

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    def _tier_up(self, tier: ModelTier) -> ModelTier:
        """Return one tier higher, capped at CEILING."""
        tiers = [ModelTier.EDGE, ModelTier.STANDARD, ModelTier.ADVANCED, ModelTier.CEILING]
        idx = tiers.index(tier)
        return tiers[min(idx + 1, len(tiers) - 1)]

    def _best_device_for_tier(self, tier: ModelTier) -> DeviceRole:
        """
        Select best available device for a model tier.
        Falls back to desktop if mesh state unavailable.
        """
        if not self._mesh_state:
            return DeviceRole.DESKTOP

        # Filter devices with enough VRAM
        vram_requirements = {
            ModelTier.EDGE: 2,
            ModelTier.STANDARD: 6,
            ModelTier.ADVANCED: 10,
            ModelTier.CEILING: 22,
        }
        required_vram = vram_requirements[tier]

        for device_id, caps in self._mesh_state.items():
            if caps.get("vram_free_gb", 0) >= required_vram:
                return DeviceRole(caps.get("role", "desktop"))

        return DeviceRole.DESKTOP

    def _load_registry(self, path: Path) -> Dict[str, Any]:
        """Load skill registry from JSON."""
        if not path.exists():
            logger.warning(f"Skill registry not found at {path}, using default")
            return self._default_registry()
        with open(path) as f:
            return json.load(f)

    def _default_registry(self) -> Dict[str, Any]:
        """Minimal fallback registry."""
        return {
            "skills": [
                {
                    "name": "reasoning",
                    "trigger_keywords": [],
                    "model_tier": "7b",
                    "device_pref": "any",
                    "timeout_ms": 30000,
                }
            ]
        }

    def _ollama_generate(self, prompt: str, max_tokens: int = 1000) -> str:
        """Make a synchronous call to local Ollama."""
        payload = {
            "model": self.hrm_model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.1},
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{self.ollama_base}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")

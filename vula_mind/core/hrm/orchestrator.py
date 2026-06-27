"""HRM orchestrator — routes tasks, never generates answers."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from config import settings
from core.thinkmesh.graph import (
    DeviceRole,
    GraphStatus,
    MergeStrategy,
    ModelTier,
    TaskGraph,
)

log = logging.getLogger(__name__)

SKILL_REGISTRY_PATH = Path(__file__).parent.parent / "skills" / "registry.json"

COMPLEXITY_KEYWORDS = {
    3: ["analyze", "analyse", "compare", "design", "architect", "synthesize", "evaluate", "critique"],
    2: ["explain", "summarize", "summarise", "calculate", "plan", "estimate", "research"],
    1: ["what", "who", "when", "where", "list", "define", "show"],
}

SKILL_KEYWORDS: dict[str, list[str]] = {
    # ClickUp first — explicit task-management phrasing only, so it never shadows
    # construction/field-ops queries. The skill itself defers if ClickUp isn't connected.
    "clickup_admin":        ["clickup", "click up", "add a task", "create a task", "new task",
                             "my tasks", "list tasks", "task list", "to-do", "to do list",
                             "remind me", "set a reminder", "reminder"],
    # Commerce next — clear ordering intent
    "commerce_assistant":   ["order", "buy", "cart", "checkout", "stock", "in stock", "menu",
                             "catalog", "catalogue", "track order", "add to cart",
                             "product", "fish", "seafood", "catch"],
    # Calculation intent BEFORE architecture_planning so "how many seats / what
    # width / occupancy" get deterministic arithmetic (computed, not guessed).
    "calculations":         ["how many seats", "how many people", "how many can", "how many bays",
                             "how many parking", "parking bays", "occupant load", "occupancy load",
                             "maximum occupancy", "max occupancy", "what width", "minimum width",
                             "how wide", "calculate", "floor area", "how many units",
                             "what does it cost", "what would it cost", "how much does", "how much will",
                             "how much is", "using our rates", "cost of", "rate for", "what do we charge",
                             "estimate the cost", "total cost", "what will it cost"],
    # Microsoft OneDrive + Outlook (draft-only email). Defers if not connected.
    "microsoft_admin":      ["onedrive", "one drive", "outlook", "sharepoint"],
    # Google Drive + Gmail (draft-only email). Defers if not connected.
    "google_admin":         ["google drive", "my drive", "in my drive", "google doc", "gmail",
                             "my email", "my emails", "check email", "draft an email",
                             "draft a reply", "draft email", "reply to the email", "inbox"],
    # Explicit standard/code lookup → cited search of the code library.
    "standards_lookup":     ["look up", "which standard", "which code", "what standard",
                             "what does sans", "code library", "sans clause", "which sans",
                             "standard cover", "applicable standard"],
    # Architecture/construction BEFORE file_parse so "Stage 4 documentation",
    # "fees", "SACAP" etc consult the SA construction KB (not just tenant docs).
    "architecture_planning":["sacap", "nhbrc", "jbcc", "nec ", "sans", "cidb", "procsa",
                             "bbbee", "b-bbee", "heritage", "sahra", "zoning", "town planning",
                             "fee", "fees", "stage 1", "stage 2", "stage 3", "stage 4", "stage 5",
                             "work stage", "documentation", "tender", "boq", "bill of quantities",
                             "contractor", "subcontract", "municipal", "building plan", "occupation certificate",
                             "residential", "commercial", "fitout", "construction", "architect",
                             "design", "drawing", "elevation", "quantity surveyor", "preliminaries",
                             "provisional sum", "retention", "practical completion", "snag",
                             "structure", "infrastructure", "plan"],
    "web_search":           ["search", "find online", "latest", "current", "news", "tender alert",
                             "research", "look up", "google"],
    "code_execution":       ["run", "execute", "compute", "code", "script"],
    "memory_recall":        ["remember", "previous", "history", "last time", "before", "we discussed"],
    "file_parse":           ["this file", "this document", "pdf", "parse", "extract from", "summarise this"],
    "image_analysis":       ["image", "photo", "picture", "screenshot", "diagram"],
    "financial_reasoning":  ["revenue", "profit", "budget", "cashflow"],
    "reasoning":            [],  # fallback
}


class HRMOrchestrator:
    def __init__(
        self,
        ollama_url: str | None = None,
        model: str = "qwen2.5:3b",
    ):
        self.ollama_url = ollama_url or settings.ollama_base
        self.model = model
        self._skill_registry: dict[str, Any] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        try:
            with open(SKILL_REGISTRY_PATH) as f:
                data = json.load(f)
                # registry.json uses "name" as the unique key
                self._skill_registry = {s["name"]: s for s in data.get("skills", [])}
            log.debug("Loaded %d skills from registry", len(self._skill_registry))
        except FileNotFoundError:
            log.warning("Skill registry not found at %s", SKILL_REGISTRY_PATH)
        except (KeyError, json.JSONDecodeError) as exc:
            log.error("Skill registry parse error: %s", exc)

    def _keyword_complexity(self, prompt: str) -> int:
        lower = prompt.lower()
        for level in (3, 2, 1):
            if any(kw in lower for kw in COMPLEXITY_KEYWORDS[level]):
                return level
        return 1

    def _llm_complexity(self, prompt: str) -> int:
        """Ask local LLM to score complexity 1–3. Falls back to keyword heuristic."""
        try:
            resp = httpx.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": (
                        "Rate this task complexity 1 (simple) to 3 (complex). "
                        f"Reply with only the number.\nTask: {prompt}"
                    ),
                    "stream": False,
                    "options": {"num_predict": 5},
                },
                timeout=10,
            )
            score = int(resp.json().get("response", "1").strip()[0])
            return max(1, min(3, score))
        except Exception:
            return self._keyword_complexity(prompt)

    def _match_skill(self, prompt: str) -> str:
        lower = prompt.lower()
        for skill_name, keywords in SKILL_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                return skill_name
        return "reasoning"

    def _select_model(self, complexity: int, routing_hints: dict) -> ModelTier:
        hint = routing_hints.get("preferred_tier")
        if hint:
            try:
                return ModelTier(hint)
            except ValueError:
                pass
        return {1: ModelTier.WORKER, 2: ModelTier.WORKER, 3: ModelTier.REASONER}[complexity]

    def _select_merge(self, complexity: int, skill_name: str) -> MergeStrategy:
        if complexity == 3:
            return MergeStrategy.SYNTHESIZE
        if skill_name in ("financial_reasoning", "code_execution"):
            return MergeStrategy.BEST_CONFIDENCE
        return MergeStrategy.FASTEST

    def plan(self, graph: TaskGraph, use_llm_scoring: bool = False) -> TaskGraph:
        graph.status = GraphStatus.PLANNING
        prompt = graph.original_prompt

        complexity = (
            self._llm_complexity(prompt) if use_llm_scoring
            else self._keyword_complexity(prompt)
        )
        graph.complexity = complexity

        skill_name = self._match_skill(prompt)
        model_tier = self._select_model(complexity, graph.routing_hints)
        merge = self._select_merge(complexity, skill_name)
        graph.merge_strategy = merge

        branch_count = {1: 1, 2: 2, 3: 3}[complexity]

        for i in range(branch_count):
            role = DeviceRole.PRIMARY if i == 0 else DeviceRole.SECONDARY
            graph.add_branch(
                device_role=role,
                model_tier=model_tier,
                skill_id=skill_name,
                prompt=prompt,
            )

        log.info(
            "HRM planned task=%s complexity=%d skill=%s branches=%d merge=%s",
            graph.task_id[:8], complexity, skill_name, branch_count, merge.value,
        )
        return graph

"""Tests for HRM Orchestrator — routing logic, no LLM calls required."""
import pytest
from core.hrm.orchestrator import HRMOrchestrator
from core.thinkmesh.graph import GraphStatus, MergeStrategy, ModelTier, TaskGraph


@pytest.fixture
def hrm():
    return HRMOrchestrator()


def make_graph(prompt: str) -> TaskGraph:
    return TaskGraph(original_prompt=prompt)


# ── Complexity scoring ────────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt,expected", [
    ("What is the capital of France?", 1),
    ("Explain how ThinKMesh works", 2),
    ("Analyse and compare DeepSeek R1 vs GPT-4 for enterprise AI", 3),
])
def test_keyword_complexity(hrm, prompt, expected):
    score = hrm._keyword_complexity(prompt)
    assert score == expected


# ── Skill matching ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt,expected_skill", [
    ("Search for the latest news on AI in South Africa", "web_search"),
    ("Write a Python script to parse a CSV", "code_execution"),
    ("What did we discuss last time?", "memory_recall"),
    ("Read this PDF document", "file_parse"),
    ("What is the cost per square metre for fitout?", "financial_reasoning"),
    ("Just tell me something", "reasoning"),
])
def test_skill_matching(hrm, prompt, expected_skill):
    assert hrm._match_skill(prompt) == expected_skill


# ── Plan output ───────────────────────────────────────────────────────────────

def test_plan_simple_task(hrm):
    g = hrm.plan(make_graph("What is 2 + 2?"))
    assert g.status == GraphStatus.PLANNING
    assert g.complexity == 1
    assert len(g.branches) == 1
    assert g.merge_strategy == MergeStrategy.FASTEST


def test_plan_complex_task(hrm):
    g = hrm.plan(make_graph("Analyse and design a system architecture for a mesh AI network"))
    assert g.complexity == 3
    assert len(g.branches) == 3
    assert g.merge_strategy == MergeStrategy.SYNTHESIZE


def test_plan_assigns_correct_model_tier(hrm):
    simple = hrm.plan(make_graph("Who is the president of South Africa?"))
    assert simple.branches[0].model_tier == ModelTier.WORKER

    complex_ = hrm.plan(make_graph("Evaluate and critique the architecture of our AI system"))
    assert complex_.branches[0].model_tier == ModelTier.REASONER


def test_plan_all_branches_have_prompts(hrm):
    g = hrm.plan(make_graph("Explain and summarise how Qdrant vector search works"))
    for branch in g.branches:
        assert branch.prompt
        assert branch.skill_id


def test_plan_routing_hint_overrides_tier(hrm):
    g = TaskGraph(original_prompt="Simple task", routing_hints={"preferred_tier": "14b"})
    g = hrm.plan(g)
    assert g.branches[0].model_tier == ModelTier.REASONER


# ── Registry loading ──────────────────────────────────────────────────────────

def test_skill_registry_loads(hrm):
    assert len(hrm._skill_registry) > 0
    assert "reasoning" in hrm._skill_registry


def test_skill_registry_uses_name_key(hrm):
    for key, skill in hrm._skill_registry.items():
        assert key == skill["name"], f"Key mismatch: {key} != {skill['name']}"

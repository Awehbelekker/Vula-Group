"""Tests for HRM Orchestrator — routing logic, no LLM calls required."""
from unittest.mock import MagicMock

import pytest
from core.hrm.orchestrator import HRMOrchestrator
from core.thinkmesh.graph import GraphStatus, MergeStrategy, ModelTier, TaskGraph


@pytest.fixture
def hrm(monkeypatch):
    # Off by default so every existing test stays a real unit test (no network calls) —
    # tests that specifically exercise the LLM fallback path re-enable it themselves.
    from config import settings
    monkeypatch.setattr(settings, "skill_llm_fallback_enabled", False)
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
    # "fitout" is in architecture_planning keywords, so this correctly routes there.
    ("What is the cost per square metre for fitout?", "architecture_planning"),
])
def test_skill_matching(hrm, prompt, expected_skill):
    assert hrm._match_skill(prompt) == expected_skill


def test_no_keyword_match_falls_back_to_reasoning_when_llm_fallback_disabled(hrm, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "skill_llm_fallback_enabled", False)
    assert hrm._match_skill("Just tell me something") == "reasoning"


# ── LLM classification fallback (keyword-miss path only) ───────────────────────

def test_llm_fallback_used_when_no_keyword_matches(hrm, monkeypatch):
    from config import settings
    import httpx as httpx_module
    monkeypatch.setattr(settings, "skill_llm_fallback_enabled", True)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "finance_admin"}
    monkeypatch.setattr(httpx_module, "post", lambda *a, **kw: mock_resp)

    assert hrm._match_skill("Just tell me something") == "finance_admin"


def test_llm_fallback_not_called_when_keyword_already_matched(hrm, monkeypatch):
    from config import settings
    import httpx as httpx_module
    monkeypatch.setattr(settings, "skill_llm_fallback_enabled", True)

    def _boom(*a, **kw):
        raise AssertionError("LLM fallback should not fire when a keyword already matched")

    monkeypatch.setattr(httpx_module, "post", _boom)
    assert hrm._match_skill("check my email") == "email_admin"


def test_llm_fallback_disabled_skips_classification_call(hrm, monkeypatch):
    from config import settings
    import httpx as httpx_module
    monkeypatch.setattr(settings, "skill_llm_fallback_enabled", False)

    def _boom(*a, **kw):
        raise AssertionError("LLM fallback should not fire when disabled")

    monkeypatch.setattr(httpx_module, "post", _boom)
    assert hrm._match_skill("Just tell me something") == "reasoning"


def test_llm_fallback_ignores_unrecognised_response(hrm, monkeypatch):
    from config import settings
    import httpx as httpx_module
    monkeypatch.setattr(settings, "skill_llm_fallback_enabled", True)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "not_a_real_skill_name"}
    monkeypatch.setattr(httpx_module, "post", lambda *a, **kw: mock_resp)

    assert hrm._match_skill("Just tell me something") == "reasoning"


def test_llm_fallback_fails_open_on_error(hrm, monkeypatch):
    from config import settings
    import httpx as httpx_module
    monkeypatch.setattr(settings, "skill_llm_fallback_enabled", True)

    def _boom(*a, **kw):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(httpx_module, "post", _boom)
    assert hrm._match_skill("Just tell me something") == "reasoning"


# ── Appointment-booking routing (customer-facing, not clickup_admin's internal tasks) ──

@pytest.mark.parametrize("prompt", [
    "I'd like to book an appointment for Tuesday",
    "can I book a slot next week?",
    "book a session with you",
    "book a consultation please",
    "I want to make an appointment",
    "cancel my appointment",
    "please cancel my booking",
    "I need to reschedule my appointment",
    "what are your available slots tomorrow?",
    "any available times on Friday",
    "can you check availability for me",
])
def test_appointment_booking_routes_to_commerce_assistant(hrm, prompt):
    assert hrm._match_skill(prompt) == "commerce_assistant", (
        f"Expected commerce_assistant (booking) for: {prompt!r}"
    )


@pytest.mark.parametrize("prompt", [
    "book a meeting with Nolo on Friday",
    "schedule a meeting about the site inspection",
    "set up a meeting for Monday",
])
def test_internal_meeting_scheduling_still_routes_to_clickup(hrm, prompt):
    """Staff scheduling an internal meeting is a different action from a customer booking an
    appointment — must not be stolen by the new commerce_assistant booking keywords."""
    assert hrm._match_skill(prompt) == "clickup_admin", f"Expected clickup_admin for: {prompt!r}"


# ── ClickUp routing: meeting / todo / follow-up ───────────────────────────────

@pytest.mark.parametrize("prompt", [
    "Schedule a meeting with Nolo on Friday",
    "Set up a meeting about the site inspection",
    "Book a meeting for Monday at 10am",
    "Add a task: call the engineer tomorrow",
    "Create a task to review the BOQ",
    "Set a reminder to send the invoice",
    "Remind me to follow up with the client",
    "Give Nolo a todo: update the drawings",
    "What's on my to-do list?",
    "Show me my tasks",
    "assign a task to Yanga",  # contains "assign a task"
])
def test_clickup_routing(hrm, prompt):
    assert hrm._match_skill(prompt) == "clickup_admin", f"Expected clickup_admin for: {prompt!r}"


@pytest.mark.parametrize("prompt", [
    "follow up with the supplier about the delivery",
    "follow-up with Nolo about the drawings",
    "follow up on the outstanding quote",
    "create a follow-up task for the client meeting",
    "add a follow-up reminder for Friday",
])
def test_followup_routes_to_clickup(hrm, prompt):
    """Follow-up phrases (with or without context) must go to clickup_admin,
    NOT email_admin, because clickup_admin is first in the keyword dict."""
    assert hrm._match_skill(prompt) == "clickup_admin", (
        f"Follow-up routing collision: expected clickup_admin but got something else for {prompt!r}"
    )


@pytest.mark.parametrize("prompt", [
    "draft an email to the client",
    "check my email",
    "check email",
    "reply to the email from the supplier",
    "my inbox has 10 emails",
    "summarise the email",
    "read the latest email",
    "follow up email from yesterday",
    "follow-up email about the invoice",
])
def test_email_routing(hrm, prompt):
    """Email-specific phrases must still route to email_admin."""
    skill = hrm._match_skill(prompt)
    assert skill == "email_admin", f"Expected email_admin for: {prompt!r}, got: {skill}"


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

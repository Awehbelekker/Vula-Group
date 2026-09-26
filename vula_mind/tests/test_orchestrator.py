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
])
def test_skill_matching(hrm, prompt, expected_skill):
    assert hrm._match_skill(prompt) == expected_skill


# ── 2026-09-18: architecture_planning's weak (generic) keywords are business_type-gated ──
# "fitout"/"design"/"fee"/"plan"/etc used to match unconditionally for every tenant, so an
# unrelated tenant's ordinary question could get misrouted into architecture_planning. Now
# gated on the tenant's business_type being "services"/"trades" — see _architecture_weak_ok.

def test_weak_architecture_keyword_matches_for_a_services_tenant(hrm, monkeypatch):
    from vula.api import tenants as tenants_module
    monkeypatch.setattr(tenants_module, "get_config", lambda tid, **kw: {"business_type": "services"})
    assert hrm._match_skill("What is the cost per square metre for fitout?",
                            tenant_id="digg-demo") == "architecture_planning"


@pytest.mark.parametrize("business_type", ["food", "retail", None])
def test_weak_architecture_keyword_does_not_match_for_other_verticals(hrm, monkeypatch, business_type):
    from vula.api import tenants as tenants_module
    monkeypatch.setattr(tenants_module, "get_config", lambda tid, **kw: {"business_type": business_type})
    assert hrm._match_skill("What is the cost per square metre for fitout?",
                            tenant_id="some-tenant") != "architecture_planning"


def test_weak_architecture_keyword_does_not_match_without_a_tenant_id(hrm):
    """No tenant context at all fails toward NOT matching — the safe direction for a keyword
    class that exists specifically to stop over-matching."""
    assert hrm._match_skill("What is the cost per square metre for fitout?") != "architecture_planning"


def test_strong_architecture_keyword_still_matches_unconditionally(hrm):
    """Unambiguous AEC jargon (never split into the weak tier) still needs no tenant context."""
    assert hrm._match_skill("What does SACAP require for Stage 3 sign-off?") == "architecture_planning"


# ── 2026-08-24 chat-accuracy audit: routing-priority collisions ──────────────────
# calculations' intentionally generic phrases ("how much is", "occupancy load") used to
# intercept questions meant for finance_admin/standards_lookup before either got a chance,
# since _match_skill returns the FIRST dict-order match. Fixed by moving calculations to
# after both in SKILL_KEYWORDS — these pin the two real collisions the audit found, plus a
# regression check that calculations still wins for genuine arithmetic questions.

@pytest.mark.parametrize("prompt,expected_skill", [
    ("How much is left on the budget for Stage 3", "finance_admin"),
    ("What standard covers occupancy load calculations", "standards_lookup"),
    ("how many seats fit in this hall", "calculations"),
    ("what does sans 10400 say about fire escapes", "standards_lookup"),
])
def test_routing_priority_collision_fixes(hrm, prompt, expected_skill):
    assert hrm._match_skill(prompt) == expected_skill


def test_no_keyword_match_falls_back_to_reasoning_when_llm_fallback_disabled(hrm, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "skill_llm_fallback_enabled", False)
    assert hrm._match_skill("Just tell me something") == "reasoning"


# ── Routing telemetry: matched_by reason ──────────────────────────────────────

def test_route_with_reason_reports_keyword_match(hrm):
    assert hrm._route_with_reason("check my email") == ("email_admin", "keyword")


def test_route_with_reason_reports_default_fallthrough(hrm):
    # llm fallback disabled by the fixture → bare 'reasoning' default
    assert hrm._route_with_reason("Just tell me something") == ("reasoning", "default")


def test_route_with_reason_reports_llm_fallback(hrm, monkeypatch):
    from config import settings
    import httpx as httpx_module
    monkeypatch.setattr(settings, "skill_llm_fallback_enabled", True)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "finance_admin"}
    monkeypatch.setattr(httpx_module, "post", lambda *a, **kw: mock_resp)
    assert hrm._route_with_reason("Just tell me something") == ("finance_admin", "llm_fallback")


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


# ── mailbox fallback for a tenant-data question that missed every keyword (2026-09-22) ──
#
# Real DIGG transcript: "I want a breakdown on what has been spend at jackhammer" and "Please
# check expenses from Jack Hammer" both matched no keyword and no LLM classification, so they
# silently defaulted to 'reasoning' — a zero-tool skill — and it answered from an unrelated KB
# chunk rather than admitting it didn't know. A tenant-data-shaped question for a tenant with a
# connected mailbox should reach email_admin instead, so find_document/email_thread_summary get
# a real chance before giving up.

@pytest.mark.parametrize("prompt", [
    "I want a breakdown on what has been spend at jackhammer",
    "Please check expenses from Jack Hammer",
    "what did we pay the supplier for that invoice",
])
def test_tenant_data_question_routes_to_email_admin_when_mailbox_connected(hrm, monkeypatch, prompt):
    monkeypatch.setattr(HRMOrchestrator, "_has_connected_mailbox", lambda self, tid: True)
    assert hrm._match_skill(prompt, tenant_id="digg-demo") == "email_admin"


def test_tenant_data_question_stays_on_reasoning_without_a_connected_mailbox(hrm, monkeypatch):
    # (2026-09-23: a supplier-shaped prompt now routes to email_admin even without a mailbox —
    # see test_supplier_history_routes_without_a_mailbox — so this uses a project-expenses one.)
    monkeypatch.setattr(HRMOrchestrator, "_has_connected_mailbox", lambda self, tid: False)
    assert hrm._match_skill("Please check the expenses for the Belladonna project",
                            tenant_id="digg-demo") == "reasoning"


def test_non_tenant_data_question_stays_on_reasoning_even_with_a_connected_mailbox(hrm, monkeypatch):
    """The override is gated on looks_like_tenant_data_question — a mailbox being connected
    alone must never hijack an ordinary general-knowledge question."""
    monkeypatch.setattr(HRMOrchestrator, "_has_connected_mailbox", lambda self, tid: True)
    assert hrm._match_skill("Just tell me something", tenant_id="digg-demo") == "reasoning"


def test_mailbox_fallback_never_fires_without_a_tenant_id(hrm):
    # Exercises the real _has_connected_mailbox (not mocked) — it must return False for
    # tenant_id=None without attempting a DB call, so no-tenant-id turns stay safe.
    assert hrm._match_skill("I want a breakdown on what has been spend at jackhammer") == "reasoning"


def test_mailbox_fallback_yields_to_a_keyword_match(hrm, monkeypatch):
    """A real keyword match must always win — the mailbox fallback only fires on a genuine
    keyword+LLM miss."""
    def _boom(self, tid):
        raise AssertionError("mailbox fallback should not even be checked when a keyword hit")
    monkeypatch.setattr(HRMOrchestrator, "_has_connected_mailbox", _boom)
    assert hrm._match_skill("check my email", tenant_id="digg-demo") == "email_admin"


def test_has_connected_mailbox_returns_false_without_a_tenant_id(hrm):
    assert hrm._has_connected_mailbox(None) is False


def test_has_connected_mailbox_fails_closed_on_error(hrm, monkeypatch):
    import vula.email_imap.credentials as creds_module

    def _boom(tenant_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(creds_module, "get_email_creds", _boom)
    assert hrm._has_connected_mailbox("digg-demo") is False


def test_route_with_reason_reports_mailbox_fallback(hrm, monkeypatch):
    # (2026-09-23: this used "a breakdown on what has been spend at jackhammer", which is now
    # caught earlier as a supplier-history question — see the supplier_history tests below.)
    monkeypatch.setattr(HRMOrchestrator, "_has_connected_mailbox", lambda self, tid: True)
    assert hrm._route_with_reason(
        "Please check the expenses for the Belladonna project",
        tenant_id="digg-demo") == ("email_admin", "mailbox_fallback")


# ── Supplier spend/materials history → email_admin's find_document (2026-09-23, DIGG) ──
# "What materials did we buy from Jack Hammer?" hit commerce_assistant's "buy" (the customer
# shopping skill); "How much have we spent with Jack Hammer" hit finance_admin's ledger.

@pytest.mark.parametrize("prompt", [
    "What materials did we buy from Jack Hammer?",
    "How much have we spent with Jack Hammer",
    "Need all jack hammer invoice and summary of what was spent",
    "Summary of materials from Jack Hammer",
    "I want a breakdown on what has been spend at jackhammer",
    "Please check expenses from Jack Hammer",
    "What have we bought from Gardens Handiman this month?",
])
def test_supplier_history_routes_to_email_admin(hrm, monkeypatch, prompt):
    monkeypatch.setattr(HRMOrchestrator, "_has_connected_mailbox", lambda self, tid: True)
    assert hrm._route_with_reason(prompt, tenant_id="digg-demo") == (
        "email_admin", "supplier_history")


@pytest.mark.parametrize("prompt,expected", [
    ("How much have we spent on Stage 3", "finance_admin"),          # budget, not supplier
    ("What's left on the budget for Stage 3", "finance_admin"),
    ("I want to buy 2kg of hake", "commerce_assistant"),             # a real customer order
    ("What does Jack Hammer charge for cement? I want to buy some", "commerce_assistant"),
    ("Draft an email to Jack Hammer about the invoices from them", "email_admin"),
    ("Remind me to pay all the Jack Hammer invoices", "clickup_admin"),  # explicit match wins
])
def test_supplier_history_override_stays_narrow(hrm, monkeypatch, prompt, expected):
    monkeypatch.setattr(HRMOrchestrator, "_has_connected_mailbox", lambda self, tid: True)
    assert hrm._match_skill(prompt, tenant_id="digg-demo") == expected


def test_supplier_history_routes_without_a_mailbox(hrm, monkeypatch):
    # 2026-09-23: email_admin now runs find_document-only without a mailbox, so a tenant with
    # no mailbox connected still reaches filed documents instead of the shop/ledger skills.
    monkeypatch.setattr(HRMOrchestrator, "_has_connected_mailbox", lambda self, tid: False)
    assert hrm._route_with_reason("What materials did we buy from Jack Hammer?",
                                  tenant_id="digg-demo") == ("email_admin", "supplier_history")


def test_supplier_history_needs_a_tenant(hrm):
    # Filed documents are per tenant; with no tenant the normal keyword route stands.
    assert hrm._match_skill("What materials did we buy from Jack Hammer?") == "commerce_assistant"


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
    g = TaskGraph(original_prompt="Simple task", routing_hints={"winning_tier": "14b"})
    g = hrm.plan(g)
    assert g.branches[0].model_tier == ModelTier.REASONER


# ── Registry loading ──────────────────────────────────────────────────────────

def test_skill_registry_loads(hrm):
    assert len(hrm._skill_registry) > 0
    assert "reasoning" in hrm._skill_registry


def test_skill_registry_uses_name_key(hrm):
    for key, skill in hrm._skill_registry.items():
        assert key == skill["name"], f"Key mismatch: {key} != {skill['name']}"


def test_skill_registry_matches_real_implemented_skills(hrm):
    """Regression guard: registry.json drifted stale from core/skills/loader.py's real
    skill set once already (2026-07-28 cleanup) — every entry that isn't explicitly marked
    alias_of must correspond to a real, loadable skill, and vice versa."""
    from core.skills.loader import available_skills

    real = set(available_skills())
    registry_real = {name for name, s in hrm._skill_registry.items() if "alias_of" not in s}
    registry_aliases = {name: s["alias_of"] for name, s in hrm._skill_registry.items()
                        if "alias_of" in s}

    assert registry_real == real, (
        f"registry.json's real (non-alias) entries {registry_real} don't match "
        f"loader.py's actually-implemented skills {real}"
    )
    for alias_name, target in registry_aliases.items():
        assert target in real, f"registry.json: {alias_name} aliases {target!r}, which isn't a real skill"


# (2026-09-15: the tests above through test_skill_registry_uses_name_key used to be duplicated
# verbatim a second time here — Python silently shadowed the earlier copies, so half of it never
# actually ran under pytest. Removed the dead duplicate while adding the tests below.)


# ── Mass Mind cold-start fallback (2026-09-15) ──────────────────────────────────

def test_cold_start_consults_the_pattern_library_when_the_tenant_has_no_hint(hrm, monkeypatch):
    """No tenant-specific routing_hints (a brand-new tenant, or an established one asking
    something unlike its own past) — the pattern library's suggestion should win over the
    static complexity-based default."""
    monkeypatch.setattr("core.mass_mind.patterns.suggest_tier",
                        lambda tenant_id, skill: "14b")
    g = TaskGraph(original_prompt="Who is the president of South Africa?", tenant_id="new-tenant")
    g = hrm.plan(g)
    assert g.branches[0].model_tier == ModelTier.REASONER  # not the static WORKER default


def test_a_tenants_own_hint_still_wins_over_the_pattern_library(hrm, monkeypatch):
    """The pattern library is a fallback, never an override — confirm it isn't even consulted
    once the tenant's own signal already answered the question."""
    called = []
    monkeypatch.setattr("core.mass_mind.patterns.suggest_tier",
                        lambda tenant_id, skill: called.append(1) or "1.5b")
    g = TaskGraph(original_prompt="Simple task", tenant_id="off-the-hook",
                 routing_hints={"winning_tier": "14b"})
    g = hrm.plan(g)
    assert g.branches[0].model_tier == ModelTier.REASONER
    assert called == [], "pattern library must not be consulted when the tenant has its own hint"


def test_cold_start_falls_through_to_the_static_default_when_the_library_has_nothing(hrm, monkeypatch):
    monkeypatch.setattr("core.mass_mind.patterns.suggest_tier",
                        lambda tenant_id, skill: None)
    g = TaskGraph(original_prompt="Who is the president of South Africa?", tenant_id="new-tenant")
    g = hrm.plan(g)
    assert g.branches[0].model_tier == ModelTier.WORKER  # the ordinary complexity-1 default


def test_cold_start_fails_open_when_the_pattern_library_lookup_raises(hrm, monkeypatch):
    def _raise(tenant_id, skill):
        raise RuntimeError("pattern table not migrated yet")
    monkeypatch.setattr("core.mass_mind.patterns.suggest_tier", _raise)
    g = TaskGraph(original_prompt="Who is the president of South Africa?", tenant_id="new-tenant")
    g = hrm.plan(g)  # must not raise
    assert g.branches[0].model_tier == ModelTier.WORKER


def test_llm_classifier_sends_tunnel_auth_and_uses_a_served_model(monkeypatch):
    """The fallback posted to the CF-Access-protected tunnel with no service token (so it always
    failed in production) and named qwen2.5:3b, which the box may not serve."""
    from unittest.mock import MagicMock, patch
    from config import settings
    from core.hrm.orchestrator import HRMOrchestrator
    monkeypatch.setattr(settings, "skill_classifier_model", "")
    hrm = HRMOrchestrator()
    assert hrm.model == settings.model_worker_cheap_local
    resp = MagicMock()
    resp.json.return_value = {"response": "email_admin"}
    with patch("core.llm_router._ollama_headers", return_value={"CF-Access-Client-Id": "x"}), \
         patch("core.hrm.orchestrator.httpx.post", return_value=resp) as post:
        assert hrm._llm_classify_skill("where's that thing from the supplier") == "email_admin"
    assert post.call_args.kwargs["headers"] == {"CF-Access-Client-Id": "x"}

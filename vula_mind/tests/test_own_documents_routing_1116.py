"""16 Nov 2026 (answer checks): Gerflor's "Which versions of Taralay Millenium are there?" and "What
gym flooring does Sports Flooring Warehouse offer?" were sent to web search by the classifier,
though the answers are in Gerflor's own filed catalogues."""
from unittest.mock import patch

from core.hrm import orchestrator as orch
from tests.fake_supabase import FakeSupabase

DOCS = FakeSupabase({"vula_filed_documents": [
    {"id": "d1", "tenant_id": "gerflor", "summary": "Detailed specifications for Gerflor's Taralay Millenium Compact and Comfort vinyl floor coverings"},
    {"id": "d2", "tenant_id": "gerflor", "summary": "A catalogue from Sports Flooring Warehouse detailing gym flooring: Ultrafloor, interlocking tiles"},
    {"id": "d3", "tenant_id": "other", "summary": "Acme widgets price list"},
]})


def _route(prompt, tenant="gerflor", classified="web_search"):
    o = orch.HRMOrchestrator()
    with patch("vula.commerce.service._client", lambda: DOCS), \
         patch.object(orch.settings, "skill_llm_fallback_enabled", True), \
         patch.object(o, "_llm_classify_skill", lambda p: classified), \
         patch.object(o, "_has_connected_mailbox", lambda t: False):
        return o._route_with_reason(prompt, tenant_id=tenant)


def test_a_question_naming_the_businesss_own_documents_is_answered_from_them():
    assert _route("Which versions of Taralay Millenium are there?") == ("reasoning", "own_documents")
    assert _route("What gym flooring does Sports Flooring Warehouse offer?") == ("reasoning", "own_documents")


def test_live_information_still_goes_to_the_web():
    assert _route("Is Sports Flooring Warehouse open today?") == ("web_search", "llm_fallback")


def test_a_name_not_in_this_businesss_documents_keeps_the_classifiers_choice():
    assert _route("What does Acme Widgets sell?") == ("web_search", "llm_fallback")       # another tenant's
    assert _route("Which versions of Taralay Millenium are there?", classified="reasoning") == ("reasoning", "llm_fallback")


def test_name_terms_pick_the_names():
    assert orch._name_terms("Which versions of Taralay Millenium are there?")[:2] == ["Taralay", "Millenium"]

"""Product specs come from the tenant's own data sheets only (Ian, 2026-09-30: "what is the slip
rate on the Affinity — will it tell me?"). Gerflor's test report rates Mipolam Affinity R10."""
import asyncio

import pytest

from core.skills.base import looks_like_spec_question
from vula.commerce import doc_titles
from vula.ingestion.pipeline import _salient_terms, _spec_expansion, _synonym_variant


@pytest.mark.parametrize("q", [
    "What is the slip rate on the Affinity?",
    "Whats the slip rating of mipolam affinity",
    "Is Taraflex anti-slip?",
    "Is it R11?",
    "fire rating of Taraflex Sport",
    "How thick is the wear layer on Creation 55?",
    "send me the Affinity data sheet",
    "acoustic rating for Marmorette Acoustic",
    "does it meet EN 13501",
])
def test_spec_questions_are_recognised(q):
    assert looks_like_spec_question(q)


@pytest.mark.parametrize("q", [
    "Here is my fuel slip",
    "Log this payment slip",
    "I need to fire an employee — what's the CCMA process?",
    "What's the price of Affinity?",
    "Is there stock of Creation 55?",
    "Sounds good, thanks",
])
def test_ordinary_messages_are_not_spec_questions(q):
    assert not looks_like_spec_question(q)


def test_slip_rate_searches_the_data_sheet_words_not_prices():
    """'rate' is a price synonym — a slip-rate question must not become a price-list search."""
    extra, suppressed = _spec_expansion("What is the slip rate on the Affinity?")
    assert "R10" in extra and "resistance" in extra
    assert "rate" in suppressed
    variant = _synonym_variant("What is the slip rate on the Affinity?")
    assert "resistance" in variant and "price" not in variant and "pricing" not in variant
    terms = _salient_terms("slip rate affinity")
    assert "affinity" in terms and "price" not in terms


def test_a_fuel_slip_is_not_expanded():
    extra, _ = _spec_expansion("Here is my fuel slip for R300")
    assert extra == []


def _fake_pipeline(monkeypatch, chunks_by_tenant):
    import vula.ingestion.pipeline as pl

    class _P:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id

        async def query(self, question, top_k=5, authoritative_only=False, **_):
            return chunks_by_tenant.get(self.tenant_id, [])

    monkeypatch.setattr(pl, "VulaIngestionPipeline", _P)


def test_lookup_names_the_data_sheet_and_carries_the_spec_rule(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    _fake_pipeline(monkeypatch, {"gerflor": [
        {"doc_id": "596d", "filename": "Report 20260827-1001.pdf",
         "text": "Mipolam Affinity ... EN 16165 Annex B ... classification R10"}]})
    monkeypatch.setattr("vula.commerce.stock_sheet.answer_stock_query", lambda *a: None)
    monkeypatch.setattr(doc_titles, "titles_for",
                        lambda tid, ids: {"596d": "Mipolam Affinity – slip resistance test (R10) 2026-08-27.pdf"})
    out = asyncio.run(CommerceAdminSkill()._lookup_business_info(
        "gerflor", {"query": "slip rate affinity"}))
    assert out["found"] and "slip resistance test" in out["results"][0]["source"]
    assert "SPECIFICATION" in out["instruction"]


def test_lookup_never_falls_back_to_general_knowledge_for_a_spec(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    _fake_pipeline(monkeypatch, {"business_basics": [{"filename": "x", "text": "generic"}]})
    monkeypatch.setattr("vula.commerce.stock_sheet.answer_stock_query", lambda *a: None)
    out = asyncio.run(CommerceAdminSkill()._lookup_business_info(
        "gerflor", {"query": "fire rating of Taraflex Sport"}))
    assert out["found"] is False and out.get("spec_question")


def test_web_research_refuses_a_spec_question():
    from core.skills.commerce_admin import CommerceAdminSkill
    out = asyncio.run(CommerceAdminSkill()._competitor_check(
        "gerflor", {"query": "Mipolam Affinity slip rating"}, {"message": "slip rate affinity"}))
    assert "data sheets" in out["error"]


def test_reasoning_declines_a_spec_it_has_no_document_for(monkeypatch):
    from core.skills.base import SkillInput
    from core.skills.reasoning import ReasoningSkill
    _fake_pipeline(monkeypatch, {})
    out = asyncio.run(ReasoningSkill().run(SkillInput(
        question="What is the fire rating of Taraflex Sport?", tenant_id="gerflor")))
    assert "isn't in our data sheets" in out.answer


# ── document titles ─────────────────────────────────────────────────────────────

def test_generic_names_are_spotted():
    assert doc_titles.generic_name("Report 20260827-1001.pdf")
    assert doc_titles.generic_name("General Document 20260827-1001.pdf")
    assert not doc_titles.generic_name("Invoice - Solid Cape 20260827-1001.pdf")
    assert not doc_titles.generic_name("Mipolam Affinity – slip resistance test (R10) 2026-08-27.pdf")


def test_titles_must_come_from_the_summary():
    s = ("This report details the slip resistance test results for Gerflor's Mipolam Affinity "
         "flooring, conducted according to EN 16165 - Annex B. The flooring achieved an R10 "
         "classification for slip resistance")
    assert doc_titles.grounded_title("Mipolam Affinity – slip resistance test (R10)", s)
    assert not doc_titles.grounded_title("Taraflex Sport – slip resistance test (R11)", s)


def test_new_name_keeps_the_date_and_extension():
    assert doc_titles.with_title("Mipolam Affinity – slip resistance test (R10)",
                                 "Report 20260827-1001.pdf") == \
        "Mipolam Affinity – slip resistance test (R10) 2026-08-27.pdf"


def test_money_documents_are_never_retitled():
    assert doc_titles.is_money_category("Invoice")
    assert doc_titles.is_money_category("Proof of Payment")
    assert not doc_titles.is_money_category("Report")


def test_retitle_preview_writes_nothing(monkeypatch):
    rows = [
        {"id": 1, "filename": "Report 20260827-1001.pdf", "category": "Report",
         "summary": "Slip resistance test results for Mipolam Affinity. R10.", "created_at": "2026-08-27"},
        {"id": 2, "filename": "Invoice 20260827-1001.pdf", "category": "Invoice",
         "summary": "Invoice from X", "created_at": "2026-08-27"},
    ]
    writes = []

    class _T:
        def __init__(self): self._upd = None
        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **k): return self
        def limit(self, *a): return self
        def update(self, d): writes.append(d); return self
        def execute(self):
            class R: data = rows
            return R()

    class _C:
        def table(self, name): return _T()

    monkeypatch.setattr("vula.commerce.service._client", lambda: _C())

    async def _title(summary, category):
        return "Mipolam Affinity – slip resistance test (R10)"
    monkeypatch.setattr(doc_titles, "title_from_summary", _title)
    rep = asyncio.run(doc_titles.retitle_tenant("gerflor", apply=False))
    assert rep["proposed"] == 1 and rep["items"][0]["id"] == 1
    assert writes == []

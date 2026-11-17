"""17 Nov 2026: the answer checks after the Railway model change (15 of 17).

- DIGG "Find the Solid Cape invoice for R7,571.44": the model searched "Solid Cape" without the
  amount, matched by name, quoted another figure, and the price check replaced the reply. The
  amount is now matched in code before any model runs.
- Gerflor "What gym flooring does Sports Flooring Warehouse offer?": answered from the SFW court
  catalogue; the gym catalogue (Ultrafloor) wasn't in the top 4. The document the question names
  best now goes first."""
import asyncio
from unittest.mock import AsyncMock, patch

from core.hrm import orchestrator as orch
from core.skills import email_admin, reasoning
from tests.fake_supabase import FakeSupabase

FOUND = {"status": "found", "matched_by": "amount R7,571.44 (exact, or the same amount with/without VAT)",
         "matches": [{"party": "SOLID CAPE", "category": "Invoice", "amount": 7571.44,
                      "filed_at": "2026-07-14T09:00:00+00:00", "filename": "INV-1042.pdf"}]}


def test_the_amount_in_the_question_is_matched_in_code():
    find = AsyncMock(return_value=FOUND)
    with patch("vula.commerce.service.find_filed_document", find):
        reply = asyncio.run(email_admin.answer_by_exact_amount("digg-demo", "Find the Solid Cape invoice for R7,571.44"))
    assert reply.startswith("Found it:") and "R7,571.44" in reply and "SOLID CAPE" in reply
    # the whole question is searched, so the amount is always in it — not whatever a model types
    assert "R7,571.44" in find.call_args.args[1]


def test_no_amount_or_no_exact_match_leaves_the_usual_path():
    find = AsyncMock(return_value={**FOUND, "matched_by": "name"})
    with patch("vula.commerce.service.find_filed_document", find):
        assert asyncio.run(email_admin.answer_by_exact_amount("digg-demo", "Find the Solid Cape invoice for R7,571.44")) is None
        assert asyncio.run(email_admin.answer_by_exact_amount("digg-demo", "Find the Solid Cape invoice")) is None
    find.side_effect = RuntimeError("db down")
    with patch("vula.commerce.service.find_filed_document", find):
        assert asyncio.run(email_admin.answer_by_exact_amount("digg-demo", "Find the Solid Cape invoice for R7,571.44")) is None


def test_the_skill_answers_from_the_record_before_the_model(monkeypatch):
    from core.skills.base import SkillInput
    monkeypatch.setattr(email_admin, "answer_by_exact_amount", AsyncMock(return_value="Found it:\n• SOLID CAPE"))
    skill = email_admin.EmailAdminSkill()
    loop = AsyncMock(return_value="should not run")
    monkeypatch.setattr(skill, "_loop", loop)
    with patch("vula.commerce.service.answer_supplier_history_continuation", AsyncMock(return_value=None)):
        out = asyncio.run(skill.run(SkillInput(question="Find the Solid Cape invoice for R7,571.44",
                                               tenant_id="digg-demo")))
    assert out.answer.startswith("Found it:") and not loop.called


DOCS = FakeSupabase({"vula_filed_documents": [
    {"id": "f1", "doc_id": "k1", "tenant_id": "gerflor", "filename": "Menu - Sports Flooring Warehouse.pdf",
     "summary": "A catalogue from Sports Flooring Warehouse: hard court surfacing, vinyl flooring and synthetic sports turf"},
    {"id": "f2", "doc_id": "k2", "tenant_id": "gerflor", "filename": "SFW_Gym_Flooring_Catalogue.pdf",
     "summary": "A catalogue from Sports Flooring Warehouse detailing their gym flooring: Ultrafloor, interlocking tiles"},
    {"id": "f3", "doc_id": "k3", "tenant_id": "other", "filename": "x.pdf",
     "summary": "Sports Flooring Warehouse gym flooring resale list"},
]})


def test_the_document_naming_the_most_of_the_question_comes_first():
    with patch("vula.commerce.service._client", lambda: DOCS):
        got = orch.own_documents_named("gerflor", "What gym flooring does Sports Flooring Warehouse offer?")
        assert [d["doc_id"] for d in got] == ["k2", "k1"]           # never another business's
        assert orch.named_in_own_documents("gerflor", "What gym flooring does Sports Flooring Warehouse offer?")
        assert orch.own_documents_named("gerflor", "Is Sports Flooring Warehouse open today?") == []


class _Pipeline:
    def __init__(self):
        self.calls = []

    async def query(self, question, top_k=4, authoritative_only=True, doc_id=None):
        self.calls.append(doc_id)
        if doc_id == "k2":
            return [{"doc_id": "k2", "text": "Ultrafloor gym flooring", "filename": "SFW_Gym_Flooring_Catalogue.pdf"}]
        return [{"doc_id": "k1", "text": "Hard court surfacing"},
                {"doc_id": "k2", "text": "Ultrafloor gym flooring"}]


def test_reasoning_reads_the_named_document_first_without_repeats():
    p = _Pipeline()
    usual = asyncio.run(p.query("q"))
    with patch("vula.commerce.service._client", lambda: DOCS):
        chunks = asyncio.run(reasoning._named_document_first(
            p, "gerflor", "What gym flooring does Sports Flooring Warehouse offer?", usual))
    assert [c["text"] for c in chunks] == ["Ultrafloor gym flooring", "Hard court surfacing"]
    assert p.calls[-1] == "k2"


def test_a_question_naming_nothing_on_file_keeps_the_usual_results():
    p = _Pipeline()
    usual = [{"doc_id": "k1", "text": "Hard court surfacing"}]
    with patch("vula.commerce.service._client", lambda: DOCS):
        assert asyncio.run(reasoning._named_document_first(p, "gerflor", "How do I clean a floor?", usual)) == usual
    assert p.calls == []

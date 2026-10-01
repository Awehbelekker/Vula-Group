"""Fixes from the benchmark run of 1 Oct (113/141), in the benchmark's own wording."""
from unittest.mock import patch

import pytest

from core.skills.base import (
    delete_request_reply, looks_like_supplier_history_question, spec_product_missing,
)


def _async_return(value):
    async def f(*a, **k):
        return value
    return f


# ── 1. one invoice is not the supplier's history ───────────────────────────────

@pytest.mark.parametrize("text", ["Find the Solid Cape invoice for R7,571.44",
                                  "Find the Gardens Handiman invoice no 27-37898"])
def test_a_specific_invoice_is_left_to_find_document(text):
    assert not looks_like_supplier_history_question(text)


@pytest.mark.parametrize("text", ["Show me all Solid Cape invoices", "How much have we spent at Solid Cape?",
                                  "Can you summarize Jack hammer invoices"])
def test_history_questions_still_get_the_history(text):
    assert looks_like_supplier_history_question(text)


# ── 2. deleting records from chat ───────────────────────────────────────────────

@pytest.mark.parametrize("text", ["Delete all the HPC invoices", "please remove the Solid Cape receipt",
                                  "Can you delete the expense from yesterday"])
def test_a_request_to_delete_records_is_refused_plainly(text):
    reply = delete_request_reply(text)
    assert reply and "nothing has been removed" in reply


@pytest.mark.parametrize("text", ["delete my data", "Remove me from your list", "What invoices did we delete?"])
def test_other_delete_wording_is_left_alone(text):
    assert delete_request_reply(text) is None


@pytest.mark.asyncio
async def test_commerce_admin_refuses_before_any_tool():
    from core.skills.base import SkillInput
    from core.skills.commerce_admin import CommerceAdminSkill
    with patch("core.skills.commerce_admin._stock_sheet_answer", return_value=None):
        out = await CommerceAdminSkill().run(SkillInput(question="Delete all the HPC invoices",
                                                        tenant_id="digg-demo"))
    assert "won't delete" in out.answer


# ── 3. a programme question with no programme loaded ───────────────────────────

@pytest.mark.asyncio
async def test_no_programme_loaded_is_said_plainly():
    from vula.commerce import project_programme as pp
    with patch.object(pp, "programme_tasks", return_value=[]), \
         patch("vula.api.tenants.uses_projects", return_value=True), \
         patch.object(pp, "running_projects", return_value=["Belladonna", "HPC Bokaap"]):
        out = await pp.programme_answer("digg-demo", "What's on the Belladonna programme for today?")
    assert "No programme is loaded for Belladonna" in out


# ── 4. spec answers never borrow another product's figure ──────────────────────

def test_a_chunk_about_a_different_product_does_not_count():
    chunks = [{"text": "Taralay Impression Control — reaction to fire Bfl-s1", "filename": "General Document.pdf"}]
    assert spec_product_missing("fire rating Taraflex Sport M Plus", chunks)


def test_the_right_product_in_the_text_or_title_counts():
    assert not spec_product_missing("Whats the slip rating of mipolam affinity",
                                    [{"text": "MIPOLAM AFFINITY slip R10 EN 16165", "filename": "x.pdf"}])
    assert not spec_product_missing("slip rating Affinity",
                                    [{"text": "slip R10", "doc_id": "d1"}], {"d1": "Mipolam Affinity test report"})


@pytest.mark.asyncio
async def test_lookup_business_info_says_not_on_file_for_another_products_sheet():
    from core.skills.commerce_admin import CommerceAdminSkill
    chunks = [{"text": "Taralay Impression — fire Bfl-s1", "filename": "General Document.pdf", "doc_id": "d1"}]
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe, \
         patch("vula.commerce.doc_titles.titles_for", return_value={}):
        pipe.return_value.query = _async_return(chunks)
        out = await CommerceAdminSkill()._lookup_business_info(
            "gerflor", {"query": "fire rating Taraflex Sport M Plus"})
    assert out["found"] is False and out["spec_question"] is True


@pytest.mark.asyncio
async def test_reasoning_looks_wider_for_a_spec_question():
    from core.skills.base import SkillInput
    from core.skills.reasoning import ReasoningSkill
    seen = {}

    async def query(q, top_k=4, authoritative_only=False):
        seen["top_k"] = top_k
        return []
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe:
        pipe.return_value.query = query
        out = await ReasoningSkill().run(SkillInput(question="Whats the slip rating of mipolam affinity",
                                                    tenant_id="gerflor"))
    assert seen["top_k"] >= 8 and "isn't in our data sheets" in out.answer


# ── 5. broadcast preview in a dry run; judge rubric ────────────────────────────

@pytest.mark.asyncio
async def test_a_broadcast_without_confirm_previews_for_real_in_a_dry_run():
    from core import dry_run

    class _S:
        @dry_run.guard_dispatch
        async def _dispatch(self, name, args, tid):
            return {"preview": True, "would_reach": 87}
    with dry_run.session() as st:
        prev = await _S()._dispatch("send_broadcast", {"template_name": "fresh_in"}, "off-the-hook")
        sent = await _S()._dispatch("send_broadcast", {"template_name": "fresh_in", "confirm": True}, "off-the-hook")
    assert prev["would_reach"] == 87
    assert sent["status"] == "dry_run_not_performed"
    assert [c["executed"] for c in st["calls"]] == [True, False]


def test_the_judge_is_told_confirm_first_is_by_design():
    from evals.benchmark import _JUDGE_SYSTEM
    assert "designed safety step" in _JUDGE_SYSTEM and "NO matching tool" in _JUDGE_SYSTEM


@pytest.mark.asyncio
async def test_the_benchmark_mirrors_the_delete_refusal():
    from evals import benchmark
    got = await benchmark._shortcut_answer({"tenant": "digg-demo", "prompt": "Delete all the HPC invoices"})
    assert got[0] == "shortcut:delete_refused"

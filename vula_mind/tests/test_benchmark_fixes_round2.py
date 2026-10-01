"""Fixes from the capability benchmark run of 30 Sep (73/141). Each test uses the benchmark's
own wording."""
from unittest.mock import patch

import pytest

from core.skills.base import (
    SkillInput, check_in_reply, drop_unverified_price_sentences, looks_like_spec_question,
    strip_narration, unbacked_action_claim, tool_source,
)


# ── narration leak ─────────────────────────────────────────────────────────────

def test_a_reply_that_narrates_the_user_gives_only_the_reply():
    raw = ('The user is asking about delivery. The tool call returned the areas. '
           'Here is a revised response: "We deliver to Sea Point and Green Point on Fridays."')
    assert strip_narration(raw) == "We deliver to Sea Point and Green Point on Fridays."


def test_a_normal_reply_is_untouched():
    text = "We deliver to Sea Point on Fridays. The user guide is on our site."
    assert strip_narration(text) == text


# ── unverified prices: drop the sentence, keep the rest ─────────────────────────

def test_only_the_sentence_with_the_unconfirmed_price_is_dropped():
    out = drop_unverified_price_sentences(
        "Hake fillets are in today. They cost R999/kg. Delivery is on Friday.", ["R999"])
    assert "R999" not in out and "Hake fillets are in today." in out and "Friday" in out


# ── calculations: computed, not guessed ─────────────────────────────────────────

def test_tiles_are_counted_with_waste():
    from core.skills.calculations import deterministic_answer
    assert "47" in deterministic_answer("How many 600x600 tiles for a 4.2m by 3.6m room with 10% waste?")


def test_vat_gives_vat_and_total():
    from core.skills.calculations import deterministic_answer
    out = deterministic_answer("What is 15% VAT on R48,300?")
    assert "R7,245.00" in out and "R55,545.00" in out


# ── "Are you ok?" ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["Are you ok?", "hi", "Hello Vula!", "how are you", "you there?"])
def test_a_check_in_gets_a_short_friendly_reply(text):
    reply = check_in_reply(text)
    assert reply and "help" in reply


@pytest.mark.parametrize("text", ["Hi, what's my stock?", "ok", "Are you open on Sunday?"])
def test_anything_with_a_real_question_goes_on_to_the_skills(text):
    assert check_in_reply(text) is None


# ── Gerflor: spec questions and stock changes ───────────────────────────────────

def test_a_rep_asking_to_change_stock_is_declined(monkeypatch):
    from core.skills.commerce_admin import _stock_sheet_answer
    monkeypatch.setattr("vula.api.tenants.tenant_profile", lambda t: {"sells_products": False})
    with patch("vula.commerce.stock_sheet.answer_stock_query") as read:
        reply = _stock_sheet_answer("gerflor", "Set the Taraflex stock to zero")
    assert "can't change stock" in reply
    read.assert_not_called()


def test_a_spec_question_never_gets_the_stock_level(monkeypatch):
    from core.skills.commerce_admin import _stock_sheet_answer
    monkeypatch.setattr("vula.api.tenants.tenant_profile", lambda t: {"sells_products": False})
    assert looks_like_spec_question("Whats the slip rating of mipolam affinity")
    with patch("vula.commerce.stock_sheet.answer_stock_query") as read:
        assert _stock_sheet_answer("gerflor", "What's the slip rating of the Affinity stock range?") is None
    read.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_business_info_skips_the_stock_sheet_for_a_spec_question():
    from core.skills.commerce_admin import CommerceAdminSkill
    with patch("vula.commerce.stock_sheet.answer_stock_query") as read, \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe:
        pipe.return_value.query = _async_return([])
        await CommerceAdminSkill()._lookup_business_info("gerflor", {"query": "slip rating Affinity"})
    read.assert_not_called()


def test_a_spec_question_with_no_keyword_routes_to_reasoning_not_the_classifier():
    from core.hrm.orchestrator import HRMOrchestrator
    with patch.object(HRMOrchestrator, "_llm_classify_skill", return_value="draft_admin") as clf:
        skill, why = HRMOrchestrator()._route_with_reason("Whats the slip rating of mipolam affinity", "gerflor")
    assert (skill, why) == ("reasoning", "spec")
    clf.assert_not_called()


# ── broadcast ───────────────────────────────────────────────────────────────────

def test_send_all_customers_offers_the_broadcast_tools():
    from core.skills.commerce_admin import _match_groups
    assert "broadcasts" in _match_groups("Send all customers a message that fresh yellowtail is in")


# ── programme and project list ──────────────────────────────────────────────────

def test_a_named_project_programme_question_is_recognised():
    from vula.commerce.project_programme import looks_like_programme_question
    assert looks_like_programme_question("What's on the Belladonna programme for today?")
    assert looks_like_programme_question("What's on the programme today?")


@pytest.mark.asyncio
async def test_the_programme_answer_is_filtered_to_the_named_project():
    from vula.commerce import project_programme as pp
    brief = {"projects": [{"project": "Belladonna Residence", "owner": "BELLA today"},
                          {"project": "HPC Bokaap", "owner": "HPC today"}]}
    with patch.object(pp, "programme_tasks", return_value=[{"id": 1}]), \
         patch.object(pp, "morning_briefs", side_effect=_async_return(brief)):
        out = await pp.programme_answer("digg-demo", "What's on the Belladonna programme for today?")
    assert out == "BELLA today"


def test_which_projects_are_we_running_reads_the_register():
    from vula.commerce import project_programme as pp
    with patch.object(pp, "running_projects", return_value=["HPC Bokaap", "Belladonna"]):
        out = pp.projects_answer("digg-demo", "Which projects are we running at the moment?")
    assert "2 active projects" in out and "• Belladonna" in out and "• HPC Bokaap" in out
    assert pp.projects_answer("digg-demo", "What's the slip rating?") is None


# ── daily catch names everything it returns ─────────────────────────────────────

@pytest.mark.asyncio
async def test_the_daily_catch_names_all_six():
    from core.skills.commerce_assistant import CommerceAssistantSkill
    fish = [{"name": n, "slug": n.lower(), "price_cents": 10000, "sold_by": "kg", "category": "fresh_fish"}
            for n in ("Hake", "Kingklip", "Yellowtail", "Jacopever", "Octopus", "Tuna")]
    with patch("vula.commerce.service.list_products", side_effect=_async_return(fish)):
        out = await CommerceAssistantSkill()._exec_get_daily_catch("off-the-hook")
    for n in ("Jacopever", "Octopus", "Tuna"):
        assert n in out["message"]


# ── sales R0 is online orders only ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sales_summary_says_it_counts_online_orders_only():
    from core.skills.commerce_admin import CommerceAdminSkill
    with patch("vula.commerce.service.list_orders", side_effect=_async_return([])):
        out = await CommerceAdminSkill()._sales_summary("off-the-hook", "week")
    assert "card-machine" in out["note"]


# ── money in / out ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_money_in_out_falls_back_to_bank_lines_for_last_month():
    from core.skills import finance_admin as fa
    bank = {"money_in": 120000.0, "money_out": 95000.5, "net": 24999.5, "transactions": 41,
            "source": "bank statement lines"}
    with patch("vula.integrations.finances.all_finance_rows", return_value=[]), \
         patch.object(fa, "_bank_in_out", return_value=bank) as b:
        out = await fa.FinanceAdminSkill()._dispatch("money_in_out", {"period": "last_month"}, "digg-demo")
    assert out["source"] == "bank statement lines" and out["period"] == "last_month"
    start, end = b.call_args[0][1:]
    assert start < end and start.endswith("-01") and end.endswith("-01")


@pytest.mark.asyncio
async def test_an_empty_period_is_stated_as_zero_not_couldnt_find():
    from core.skills import finance_admin as fa
    skill = fa.FinanceAdminSkill()

    async def _loop(self, *a, **k):
        self._record_dispatch("money_in_out", {"period": "last_month", "money_in": 0, "money_out": 0,
                                               "net": 0, "transactions": 0,
                                               "note": "No bank lines in this period — the latest "
                                                       "statement line on file is 2026-09-12."})
        return ""
    with patch("vula.integrations.finances.has_project_ledger", return_value=True), \
         patch.object(fa.FinanceAdminSkill, "_loop", _loop):
        out = await skill.run(SkillInput(question="How much money came in and went out last month?",
                                         tenant_id="digg-demo"))
    assert "Nothing recorded for last month" in out.answer and "R0.00 in" in out.answer
    assert "2026-09-12" in out.answer and "couldn't find" not in out.answer


# ── benchmark grading ───────────────────────────────────────────────────────────

def test_a_dry_run_recorded_action_backs_a_would_do_claim():
    from evals.benchmark import rule_checks
    calls = [{"tool": "add_expense", "args": {}, "executed": False}]
    checks = rule_checks({"prompt": "I spent R450 on diesel"}, skill="commerce_admin",
                         answer="I've logged R450 for diesel.", calls=calls, truth=[], offered=[],
                         error=None)
    assert checks["no_false_action_claim"] is True


def test_a_claim_with_no_tool_at_all_still_fails():
    assert unbacked_action_claim("I've logged R450 for diesel.", [tool_source("find_document", {"x": 1})])


def test_a_filing_the_resolver_is_unsure_about_is_not_a_failure():
    from evals import benchmark
    docs = [{"id": "a", "filename": "x.pdf", "summary": "", "fields": {}, "project": "HPC Bokaap"},
            {"id": "b", "filename": "y.pdf", "summary": "", "fields": {}, "project": "HPC Bokaap"}]

    class _Q:
        def __getattr__(self, _):
            return lambda *a, **k: self
        not_ = property(lambda self: self)

        def execute(self):
            return type("R", (), {"data": docs})()
    with patch("vula.commerce.service._client") as cl, \
         patch("vula.integrations.project_resolver.resolve",
               side_effect=[{}, {"project": "Porterfield"}]):
        cl.return_value.table.return_value = _Q()
        rows = benchmark.run_filing_accuracy("digg-demo")
    assert [r["ok"] for r in rows] == [True, False]
    assert rows[0]["unsure"] is True


@pytest.mark.asyncio
async def test_the_benchmark_uses_the_whatsapp_shortcuts_for_owner_questions():
    from evals import benchmark
    with patch("core.skills.commerce_admin._stock_sheet_answer", return_value=None), \
         patch("vula.commerce.project_programme.programme_answer", side_effect=_async_return(None)), \
         patch("vula.commerce.project_programme.running_projects", return_value=["HPC Bokaap"]):
        got = await benchmark._shortcut_answer({"tenant": "digg-demo",
                                                "prompt": "Which projects are we running at the moment?"})
    assert got[0] == "shortcut:projects" and "HPC Bokaap" in got[1]
    assert await benchmark._shortcut_answer({"tenant": "gerflor", "entry": "rep",
                                             "prompt": "Are you ok?"}) is None


def _async_return(value):
    async def f(*a, **k):
        return value
    return f

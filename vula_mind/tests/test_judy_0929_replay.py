"""Replay of every text Judy (digg-demo owner, 27827077080) sent on 2026-09-29, against the code as
it now is. Ian: "can you not test against the messages from Judy this morning to see if it's
answering correctly". Real wording, real data shapes (read-only from production the same day):
"Jack Hammer" is her saved alias for GARDENS HANDIMAN CENTRE (22 invoices), a council letter was
waiting for a project, and Atlantis Paarden Eiland is registered without a signed baseline yet.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.hrm.orchestrator import HRMOrchestrator
from core.skills.base import looks_like_supplier_history_question
from vula.commerce import job_costing
from vula.integrations import doc_filing

TID = "digg-demo"
JUDY = "27827077080"

# (message, where it must go)
MORNING = [
    ("What data are you using to reference cost", "cost_basis"),
    ("?", "assistant"),
    ("Are you ok?", "assistant"),
    ("Can you give me the landlord details that Barend shared with me in email", "email_admin"),
    ("Can you summarize Jack hammer invoices", "supplier_history"),
    ("Belladonna", "pending_document"),
    ("Can you show me the invoice", "supplier_history"),
    ("Can I have all invoices for gardens handyman / Jack hammer", "supplier_history"),
]


def _destination(text: str) -> str:
    if job_costing.looks_like_cost_basis_question(text):
        return "cost_basis"
    if doc_filing.looks_like_project_answer(text):
        return "pending_document"
    if looks_like_supplier_history_question(text):
        return "supplier_history"
    skill, _why = HRMOrchestrator()._route_with_reason(text, tenant_id=TID)
    return "email_admin" if skill == "email_admin" else "assistant"


@pytest.mark.parametrize("text,where", MORNING)
def test_each_message_goes_where_it_should(text, where, monkeypatch):
    monkeypatch.setattr("config.settings.skill_llm_fallback_enabled", False)
    assert _destination(text) == where


def test_supplier_history_questions_reach_the_skill_that_can_read_invoices(monkeypatch):
    monkeypatch.setattr("config.settings.skill_llm_fallback_enabled", False)
    for text in ("Can you summarize Jack hammer invoices",
                 "Can I have all invoices for gardens handyman / Jack hammer"):
        assert HRMOrchestrator()._route_with_reason(text, tenant_id=TID)[0] == "email_admin"


_SUPPLIERS = [{"name": "GARDENS HANDIMAN CENTRE", "aliases": ["Jack Hammer"]},
              {"name": "Sea Point Hire", "aliases": []}]
_RESULT = {"status": "found", "match_type": "exact", "resolved_supplier": "GARDENS HANDIMAN CENTRE",
           "total_matches": 22, "total_amount": "R28,647.50", "total_amount_cents": 2864750,
           "matches_with_amount": 22, "materials": [], "materials_distinct": 0,
           "matches": [{"filename": "POS Account Sale 24-225537.pdf", "amount": 942.0,
                        "filed_at": "2026-09-22T11:40:03"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Can you summarize Jack hammer invoices",
                                  "Can I have all invoices for gardens handyman / Jack hammer"])
async def test_both_invoice_questions_answer_gardens_handiman_without_a_model(text):
    from vula.commerce import service as svc
    find = AsyncMock(return_value=_RESULT)
    with (patch.object(svc, "list_suppliers", new=AsyncMock(return_value=_SUPPLIERS)),
          patch.object(svc, "find_filed_document", new=find)):
        out = await svc.answer_supplier_history(TID, text)
    find.assert_awaited_once_with(TID, "GARDENS HANDIMAN CENTRE", category="Invoice")
    assert "*GARDENS HANDIMAN CENTRE*: 22 documents, total spend *R28,647.50*" in out


@pytest.mark.asyncio
async def test_show_me_the_invoice_is_not_answered_with_every_supplier():
    """10:30 "Can you show me the invoice" names nothing — it's a follow-up for the email skill's
    search with the conversation, never the all-suppliers dump."""
    from vula.commerce import service as svc
    with patch.object(svc, "_all_invoice_rows", side_effect=AssertionError("must not fetch")):
        assert await svc.answer_all_invoices(TID, "Can you show me the invoice") is None


# ── "What data are you using to reference cost" ──────────────────────────────────────────────

TXNS = [
    {"txn_date": "2026-07-10", "project": "HPC Bokaap", "direction": "in", "amount_cents": 151443813,
     "account_code": "sales"},
    {"txn_date": "2026-07-15", "project": "HPC Bokaap", "direction": "out", "amount_cents": 47300000,
     "account_code": "casual_labour", "trade": "Labour"},
    {"txn_date": "2026-09-12", "project": None, "direction": "out", "amount_cents": 21700000,
     "account_code": "cost_of_sales", "description": "HPC DOORS"},
    {"txn_date": "2026-08-01", "project": None, "direction": "out", "amount_cents": 7500000,
     "account_code": "other_expense", "description": "SOFTWARE"},
]


def _fake_client(boq):
    t = MagicMock()
    t.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=boq)
    return MagicMock(table=lambda _n: t)


def test_cost_basis_says_where_the_numbers_come_from(monkeypatch):
    monkeypatch.setattr(job_costing, "_txns", lambda _t, since=None: list(TXNS))
    monkeypatch.setattr(job_costing, "terms", lambda _t: {"*": 10.0})
    monkeypatch.setattr(job_costing, "_variations", lambda _t: {})
    monkeypatch.setattr(job_costing, "_own_wages", lambda _t, _x: False)
    monkeypatch.setattr(job_costing, "_client", lambda: _fake_client(
        [{"project": "Porterfield", "total_cents": 24055300, "baseline_locked": False}]))
    monkeypatch.setattr("vula.commerce.project_programme.running_projects",
                        lambda _t: ["Belladonna", "Atlantis Paarden Eiland"])
    out = job_costing.cost_basis(TID)
    assert "4 lines from 2026-07-10 to 2026-09-12. 2 are on a project" in out
    assert "1 job-cost lines (R217,000.00) aren't on a project yet" in out
    assert "HPC Bokaap: cost R473,000.00, received R1,514,438.13" in out
    assert "cost + 10%" in out
    assert "No signed baseline yet for: Belladonna, Atlantis Paarden Eiland" in out
    assert "set up Belladonna" in out


def test_a_locked_baseline_is_named(monkeypatch):
    monkeypatch.setattr(job_costing, "_txns", lambda _t, since=None: [])
    monkeypatch.setattr(job_costing, "terms", lambda _t: {"*": 10.0})
    monkeypatch.setattr(job_costing, "_variations", lambda _t: {})
    monkeypatch.setattr(job_costing, "_client", lambda: _fake_client(
        [{"project": "Atlantis Paarden Eiland", "total_cents": 59302200, "baseline_locked": True}]))
    monkeypatch.setattr("vula.commerce.project_programme.running_projects",
                        lambda _t: ["Atlantis Paarden Eiland"])
    out = job_costing.cost_basis(TID)
    assert "Atlantis Paarden Eiland: signed baseline R593,022.00" in out
    assert "none imported yet" in out and "No signed baseline yet" not in out


@pytest.mark.parametrize("text,expected", [
    ("What data are you using to reference cost", True),
    ("Which figures do you use for the costs?", True),
    ("Where do the cost figures come from", True),
    ("What does the tiler cost per m2?", False),
    ("How much has HPC cost so far", False),
])
def test_cost_basis_wording(text, expected):
    assert job_costing.looks_like_cost_basis_question(text) is expected


@pytest.mark.asyncio
async def test_on_whatsapp_the_owner_gets_the_answer_not_the_knowledge_base():
    from vula.api.whatsapp import _handle_message
    send = AsyncMock(return_value=True)
    rag = AsyncMock(return_value="generic")
    with (
        patch("vula.api.whatsapp._maybe_helper_escalation_answer", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._maybe_allocate_pending_expense", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._maybe_bank_review_answer", new=AsyncMock(return_value=None)),
        patch("vula.integrations.notify.handle_preference_command", return_value=None),
        patch("vula.api.whatsapp._maybe_start_signature_capture", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._maybe_project_setup", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._caller_identity", return_value=("Judy Downing", "owner")),
        patch("vula.commerce.job_costing.cost_basis", return_value="💡 *Where your project costs come from*"),
        patch("vula.api.whatsapp._rag_reply", new=rag),
        patch("vula.api.whatsapp._send_reply", new=send),
    ):
        await _handle_message(JUDY, "What data are you using to reference cost", "wamid.1",
                              route_tenant_id=TID)
    rag.assert_not_awaited()
    assert "Where your project costs come from" in send.await_args.args[1]


# ── 2026-09-30: found by the capability benchmark ──────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("How is HPC doing — are we making our 10%?", True),
    ("What should I charge per m² for ceiling boarding so I make my 10%?", True),
    ("Are we losing money on Belladonna?", True),
    ("What's 15% VAT on R48,300?", False),
    ("Can you summarize Jack hammer invoices", False),
    ("Are you ok?", False),
])
def test_job_costing_and_pricing_questions_are_owner_admin(text, expected):
    from core.skills.base import looks_like_owner_admin_question
    assert looks_like_owner_admin_question(text) is expected


def test_delete_all_invoices_is_never_answered_with_every_invoice():
    assert not looks_like_supplier_history_question("Delete all the HPC invoices")


@pytest.mark.asyncio
async def test_an_owner_pricing_question_on_a_knowledge_line_reaches_commerce_admin():
    from core.skills.base import SkillOutput
    from vula.api import whatsapp
    seen = {}

    class FakeAdmin:
        async def __call__(self, inp):
            seen["role"] = inp.metadata["caller_role"]
            return SkillOutput(answer="R216/m² — R168 paid · +12% overrun · +5% overheads · +10% fee",
                               skill_name="commerce_admin")

    runner = MagicMock()
    runner.run = AsyncMock(side_effect=AssertionError("must not reach the skill picker"))
    with (patch("core.skills.loader.get_skill", return_value=FakeAdmin()),
          patch("core.agent_runner.get_agent_runner", return_value=runner),
          patch.object(whatsapp, "_maybe_learn_supplier_alias", new=AsyncMock(return_value=None))):
        out = await whatsapp._rag_reply(TID, "What should I charge per m² for ceiling boarding?",
                                        phone=JUDY, caller_name="Judy Downing", caller_role="owner")
    assert out.startswith("R216/m²") and seen["role"] == "owner"


@pytest.mark.asyncio
async def test_the_public_never_reaches_commerce_admin_that_way():
    from vula.api import whatsapp
    runner = MagicMock()
    runner.run = AsyncMock(return_value=MagicMock(final_answer="general answer", confidence=0.7,
                                                  skill_used="reasoning", latency_ms=5))
    with (patch("core.skills.loader.get_skill", side_effect=AssertionError("public must not reach admin")),
          patch("core.agent_runner.get_agent_runner", return_value=runner)):
        out = await whatsapp._rag_reply(TID, "What should I charge per m² for ceiling boarding?",
                                        phone="27820009999", caller_name=None, caller_role=None)
    assert out == "general answer"


# ── 2026-09-30: audit of every real DIGG question since 31 Aug ─────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Just give me the full list.", True), ("Please send full list", True),
    ("Please show all and do a full.break down in excel", True), ("Yes please", True),
    ("Please check attachments", True),
    ("Are you ok?", False), ("?", False), ("Belladonna", False),
    ("How can I colour a cast iron fireplace? Or can it not be done?", False),
])
def test_follow_ups_are_recognised(text, expected):
    from core.skills.base import looks_like_follow_up
    assert looks_like_follow_up(text) is expected


@pytest.mark.parametrize("text", ["Sorry can I have just the invoice for jack hammer",
                                  "Please look up jack hammer invoice and give me a summary"])
def test_these_phrasings_now_reach_the_invoice_skill(text, monkeypatch):
    monkeypatch.setattr("config.settings.skill_llm_fallback_enabled", False)
    assert HRMOrchestrator()._route_with_reason(text, tenant_id=TID)[0] == "email_admin"


def test_a_new_project_instruction_reaches_project_setup():
    from vula.commerce.project_programme import _SETUP_HINT
    assert _SETUP_HINT.search("so we need to prepare for a new project to track costing the project "
                              "name is belladonna and its located in vredehoek")


@pytest.mark.asyncio
async def test_a_follow_up_goes_back_to_the_skill_that_answered():
    """23 Sep: after a Gardens Handiman answer, "Just give me the full list." went to `reasoning`."""
    from core.skills.base import SkillOutput
    from vula.api import whatsapp
    whatsapp._LAST_SKILL.clear()
    whatsapp._remember_skill(TID, JUDY, "email_admin")
    seen = []

    class FakeSkill:
        async def __call__(self, inp):
            seen.append(inp.question)
            return SkillOutput(answer="*GARDENS HANDIMAN CENTRE*: 22 documents, total spend *R28,647.50*",
                               skill_name="email_admin")

    runner = MagicMock()
    runner.run = AsyncMock(side_effect=AssertionError("a follow-up must not be re-routed"))
    with (patch("core.skills.loader.get_skill", return_value=FakeSkill()),
          patch("core.agent_runner.get_agent_runner", return_value=runner),
          patch.object(whatsapp, "_maybe_learn_supplier_alias", new=AsyncMock(return_value=None))):
        out = await whatsapp._rag_reply(TID, "Just give me the full list.", phone=JUDY,
                                        caller_name="Judy Downing", caller_role="owner")
    assert "22 documents" in out and seen == ["Just give me the full list."]
    whatsapp._LAST_SKILL.clear()


@pytest.mark.asyncio
async def test_a_new_request_is_never_forced_back_to_the_last_skill():
    from vula.api import whatsapp
    whatsapp._LAST_SKILL.clear()
    whatsapp._remember_skill(TID, JUDY, "email_admin")
    runner = MagicMock()
    runner.run = AsyncMock(return_value=MagicMock(final_answer="VAT is R7,245.00", confidence=0.9,
                                                  skill_used="calculations", latency_ms=5))
    with (patch("core.skills.loader.get_skill", side_effect=AssertionError("not sticky")),
          patch("core.agent_runner.get_agent_runner", return_value=runner),
          patch.object(whatsapp, "_maybe_learn_supplier_alias", new=AsyncMock(return_value=None))):
        out = await whatsapp._rag_reply(TID, "What's 15% VAT on R48,300?", phone=JUDY,
                                        caller_name="Judy Downing", caller_role="owner")
    assert out == "VAT is R7,245.00"
    assert whatsapp._last_skill(TID, JUDY) == "calculations"
    whatsapp._LAST_SKILL.clear()


def test_the_public_has_no_sticky_skill():
    from vula.api import whatsapp
    whatsapp._LAST_SKILL.clear()
    whatsapp._remember_skill(TID, "27820009999", "reasoning")      # not a tool skill
    assert whatsapp._last_skill(TID, "27820009999") is None

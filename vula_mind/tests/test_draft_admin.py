"""Tests for the draft_admin skill: orchestrator routing (no collision with email_admin) and
the letter-PDF renderer's markdown handling. The full draft_letter tool call (LLM generation +
WhatsApp send + Drive upload) was verified live against digg-demo during development — see the
feature's commit message — matching how sibling admin skills (email_admin, google_admin) are
tested in this codebase (routing + pure logic here, live verification for the LLM loop itself)."""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.hrm.orchestrator import HRMOrchestrator
from core.skills.loader import available_skills, get_skill
from core.skills.draft_admin import _fee_proposal_gaps, draft_letter


def _mock_weasyprint(monkeypatch, captured: dict):
    """Intercepts render_letter_pdf's local `from weasyprint import HTML` without needing the
    real native libgobject dependency (missing on this Windows dev machine — a pre-existing,
    environment-only gap; the real thing runs fine in CI/Railway). Captures the rendered HTML
    string so template-content assertions can run locally instead of being skipped."""
    fake = MagicMock()

    def _html_ctor(*a, **kw):
        captured["html"] = kw.get("string") or (a[0] if a else "")
        instance = MagicMock()
        instance.write_pdf.return_value = b"%PDF-1.4 fake"
        return instance

    fake.HTML.side_effect = _html_ctor
    monkeypatch.setitem(sys.modules, "weasyprint", fake)


def test_draft_admin_registered():
    assert "draft_admin" in available_skills()
    from core.skills.draft_admin import DraftAdminSkill
    assert isinstance(get_skill("draft_admin"), DraftAdminSkill)


@pytest.mark.parametrize("text,expected", [
    ("draft a fee proposal for the Bokaap job", "draft_admin"),
    ("draft me a letter of appointment for the contractor", "draft_admin"),
    ("write a letter to the client about the delay", "draft_admin"),
    ("we need a tender invitation for the new contractor", "draft_admin"),
    # Must NOT be stolen by draft_admin's broad "draft a "/"draft me" cousins in email_admin.
    ("draft a reply to that email", "email_admin"),
    ("compose an email to the supplier", "email_admin"),
])
def test_orchestrator_routes_draft_admin_without_colliding_email(text, expected):
    o = HRMOrchestrator()
    assert o._match_skill(text) == expected


def test_split_paragraphs():
    from vula.commerce.pdf import _split_paragraphs
    assert _split_paragraphs("Para one.\n\nPara two.\n\nPara three.") == [
        "Para one.", "Para two.", "Para three.",
    ]
    assert _split_paragraphs("Single line, no blank-line breaks.") == [
        "Single line, no blank-line breaks."
    ]


def test_letter_pdf_header_shows_logo_only_when_logo_present(monkeypatch):
    """2026-07-29 fix: DIGG's proposal PDF showed the logo AND a duplicate text company name
    right under it. Logo present -> no text heading at all (the logo already carries it)."""
    captured: dict = {}
    _mock_weasyprint(monkeypatch, captured)
    from vula.commerce.pdf import render_letter_pdf
    render_letter_pdf(
        tenant_id="digg-demo", body="Body text.", doc_label="Fee Proposal",
        tenant_profile={"name": "DIGG Architects", "logo_url": "https://example.com/logo.png"},
    )
    html = captured["html"]
    assert 'src="https://example.com/logo.png"' in html
    # No text-name heading in the brand/header block — the footer legitimately still shows
    # the name (standard letterhead convention), so check absence of <h1>, not of the name.
    assert "<h1>" not in html


def test_letter_pdf_header_shows_name_when_no_logo(monkeypatch):
    captured: dict = {}
    _mock_weasyprint(monkeypatch, captured)
    from vula.commerce.pdf import render_letter_pdf
    render_letter_pdf(
        tenant_id="digg-demo", body="Body text.", doc_label="Fee Proposal",
        tenant_profile={"name": "DIGG Architects", "logo_url": ""},
    )
    html = captured["html"]
    assert "<img" not in html
    assert "<h1>DIGG Architects</h1>" in html


def test_render_letter_pdf_plain_text():
    try:
        from weasyprint import HTML  # noqa: F401 — skip if not installed
    except ImportError:
        pytest.skip("weasyprint not installed in test environment")

    from vula.commerce.pdf import render_letter_pdf
    pdf = render_letter_pdf(
        tenant_id="digg-demo", body="First paragraph.\n\nSecond paragraph.",
        doc_label="Letter of Appointment", recipient="Mr R Downing\nCape Town",
        sign_off="Kind regards,\nJudy Downing",
    )
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1024


def test_render_letter_pdf_markdown():
    try:
        from weasyprint import HTML  # noqa: F401
    except ImportError:
        pytest.skip("weasyprint not installed in test environment")

    from vula.commerce.pdf import render_letter_pdf
    pdf = render_letter_pdf(
        tenant_id="digg-demo",
        body_markdown="## Fee Proposal\n\n**Scope:**\n\n- Concept design\n- Documentation\n",
        doc_label="Fee Proposal",
    )
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"


# ── Fee-proposal completeness gate (2026-07-29: ask, don't guess) ─────────────

def test_gaps_all_missing_from_bare_brief():
    gaps = _fee_proposal_gaps({"brief": "Draft a fee proposal for the Bokaap job."})
    assert len(gaps) == 4


def test_gaps_none_missing_when_brief_has_all_four_signals():
    brief = ("Fee proposal for the Bokaap job. Construction value R2,500,000, floor area "
             "450 m2, covering concept design and documentation, at 8% of construction cost.")
    assert _fee_proposal_gaps({"brief": brief}) == []


def test_gaps_satisfied_by_structured_fields_even_with_bare_brief():
    args = {"brief": "Fee proposal for the Bokaap job.", "project_value_zar": 2500000,
           "floor_area_m2": 450, "work_stages": "concept and documentation", "fee_basis": "8%"}
    assert _fee_proposal_gaps(args) == []


@pytest.mark.parametrize("brief,still_missing", [
    ("Value is R2,500,000, 450 m2, concept stage.", "fee basis"),
    ("Value is R2,500,000, 450 m2, at 8%.", "work stage"),
    ("Value is R2,500,000, concept stage, at 8%.", "floor area"),
    ("450 m2, concept stage, at 8%.", "project value"),
])
def test_gaps_reports_only_the_actually_missing_item(brief, still_missing):
    gaps = _fee_proposal_gaps({"brief": brief})
    assert len(gaps) == 1
    assert still_missing in gaps[0]


# ── draft_letter: need_info gate + placeholder flag ────────────────────────────

@pytest.mark.asyncio
async def test_draft_letter_returns_need_info_for_incomplete_fee_proposal():
    result = await draft_letter(
        {"document_type": "fee_proposal", "brief": "Draft a fee proposal for the Bokaap job."},
        tenant_id="digg-demo", phone="27645755210",
    )
    assert result["status"] == "need_info"
    assert len(result["missing"]) == 4


@pytest.mark.asyncio
async def test_draft_letter_does_not_gate_non_fee_proposal_types():
    with (
        patch("vula.api.draft._retrieve_context", new=AsyncMock(return_value=("", 0))),
        patch("vula.api.draft._generate_document", new=AsyncMock(return_value=("Body text.", "test-model"))),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "DIGG Architects"}),
        patch("vula.commerce.pdf.render_letter_pdf", return_value=b"%PDF-fake"),
    ):
        result = await draft_letter(
            {"document_type": "appointment_letter", "brief": "Appoint the contractor for site 12."},
            tenant_id="digg-demo", phone="",
        )
    assert "status" not in result
    assert result["document_type"] == "appointment_letter"


@pytest.mark.asyncio
async def test_draft_letter_flags_placeholders_in_output():
    brief = ("Fee proposal, R2,500,000, 450 m2, concept and documentation, 8% of "
             "construction cost.")
    with (
        patch("vula.api.draft._retrieve_context", new=AsyncMock(return_value=("", 0))),
        patch("vula.api.draft._generate_document",
              new=AsyncMock(return_value=("Fee: [PLACEHOLDER]", "test-model"))),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "DIGG Architects"}),
        patch("vula.commerce.pdf.render_letter_pdf", return_value=b"%PDF-fake"),
    ):
        result = await draft_letter(
            {"document_type": "fee_proposal", "brief": brief}, tenant_id="digg-demo", phone="",
        )
    assert result["has_placeholders"] is True


@pytest.mark.asyncio
async def test_draft_letter_no_placeholder_flag_when_clean():
    brief = ("Fee proposal, R2,500,000, 450 m2, concept and documentation, 8% of "
             "construction cost.")
    with (
        patch("vula.api.draft._retrieve_context", new=AsyncMock(return_value=("", 0))),
        patch("vula.api.draft._generate_document",
              new=AsyncMock(return_value=("Fee: R200,000.", "test-model"))),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "DIGG Architects"}),
        patch("vula.commerce.pdf.render_letter_pdf", return_value=b"%PDF-fake"),
    ):
        result = await draft_letter(
            {"document_type": "fee_proposal", "brief": brief}, tenant_id="digg-demo", phone="",
        )
    assert result["has_placeholders"] is False


# ── _retrieve_context: rate-card grounding ──────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieve_context_injects_rate_table_for_fee_proposal():
    from vula.api.draft import _retrieve_context

    mock_pipeline = MagicMock()
    mock_pipeline.query = AsyncMock(return_value=[])
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
        patch("vula.api.qs.search_rates",
              return_value=[{"description": "Concept design", "rate": 50000, "unit": "project"}]),
    ):
        context, _sources = await _retrieve_context("digg-demo", "fee proposal brief", "fee_proposal", None)

    assert "YOUR_OWN_RATE_TABLE" in context
    assert "Concept design" in context
    assert "R50000" in context


@pytest.mark.asyncio
async def test_retrieve_context_no_rate_table_for_other_doc_types():
    from vula.api.draft import _retrieve_context

    mock_pipeline = MagicMock()
    mock_pipeline.query = AsyncMock(return_value=[])
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
        patch("vula.api.qs.search_rates") as mock_search,
    ):
        await _retrieve_context("digg-demo", "letter brief", "appointment_letter", None)

    mock_search.assert_not_called()


def test_system_prompt_includes_agentic_rules():
    # 2026-08-22: a platform-wide audit of every tool-calling skill found draft_admin was the
    # one calling behaviour_preamble() bare — missing AGENTIC_RULES entirely (the "don't guess a
    # tool", "never claim success without a tool call", and "don't fabricate a retry/issue"
    # rules other agentic skills already had). This is the regression guard for that gap.
    from core.skills.draft_admin import DraftAdminSkill

    prompt = DraftAdminSkill()._system()
    assert "closest-sounding tool" in prompt
    assert "Never claim a retry, issue, or problem happened" in prompt

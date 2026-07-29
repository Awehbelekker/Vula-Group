"""Tests for the draft_admin skill: orchestrator routing (no collision with email_admin) and
the letter-PDF renderer's markdown handling. The full draft_letter tool call (LLM generation +
WhatsApp send + Drive upload) was verified live against digg-demo during development — see the
feature's commit message — matching how sibling admin skills (email_admin, google_admin) are
tested in this codebase (routing + pure logic here, live verification for the LLM loop itself)."""
import sys
from unittest.mock import MagicMock

import pytest

from core.hrm.orchestrator import HRMOrchestrator
from core.skills.loader import available_skills, get_skill


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

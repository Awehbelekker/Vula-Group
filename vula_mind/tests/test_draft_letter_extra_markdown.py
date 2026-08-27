"""Tests for draft_letter's extra_markdown param (2026-08-27): literal markdown appended AFTER
the LLM's generated content and BEFORE PDF render, for content that must survive verbatim (real
![](url) photo embeds from _log_meeting's visit-report photos) rather than being paraphrased by
the generation step, which treats `brief` as prose to write FROM, not markdown to reproduce."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.draft_admin import draft_letter


@pytest.mark.asyncio
async def test_extra_markdown_appended_to_pdf_body():
    with (
        patch("vula.api.draft._retrieve_context", new=AsyncMock(return_value=("", 0))),
        patch("vula.api.draft._generate_document", new=AsyncMock(return_value=("Site notes here.", "test-model"))),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "DIGG Architects"}),
        patch("vula.commerce.pdf.render_letter_pdf", return_value=b"%PDF-fake") as mock_render,
    ):
        result = await draft_letter(
            {"document_type": "site_meeting_minutes", "brief": "Visited the site."},
            tenant_id="digg-demo", phone="",
            extra_markdown="Site Photos:\n\n![Photo](https://storage/front.jpg)",
        )

    body = mock_render.call_args.kwargs["body_markdown"]
    assert "Site notes here." in body
    assert "![Photo](https://storage/front.jpg)" in body
    assert "status" not in result


@pytest.mark.asyncio
async def test_no_extra_markdown_leaves_body_unchanged():
    with (
        patch("vula.api.draft._retrieve_context", new=AsyncMock(return_value=("", 0))),
        patch("vula.api.draft._generate_document", new=AsyncMock(return_value=("Site notes here.", "test-model"))),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "DIGG Architects"}),
        patch("vula.commerce.pdf.render_letter_pdf", return_value=b"%PDF-fake") as mock_render,
    ):
        await draft_letter(
            {"document_type": "site_meeting_minutes", "brief": "Visited the site."},
            tenant_id="digg-demo", phone="",
        )

    assert mock_render.call_args.kwargs["body_markdown"] == "Site notes here."


@pytest.mark.asyncio
async def test_extra_markdown_does_not_affect_placeholder_or_word_count_flags():
    """extra_markdown is appended AFTER has_placeholders/word_count are computed from the LLM's
    own output — a real photo URL should never be mistaken for a placeholder or inflate the
    word count used for those flags."""
    with (
        patch("vula.api.draft._retrieve_context", new=AsyncMock(return_value=("", 0))),
        patch("vula.api.draft._generate_document", new=AsyncMock(return_value=("Clean body.", "test-model"))),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.merge_branding", return_value={"name": "DIGG Architects"}),
        patch("vula.commerce.pdf.render_letter_pdf", return_value=b"%PDF-fake"),
    ):
        result = await draft_letter(
            {"document_type": "site_meeting_minutes", "brief": "Visited the site."},
            tenant_id="digg-demo", phone="",
            extra_markdown="![Photo](https://storage/[PLACEHOLDER].jpg)",
        )

    assert result["has_placeholders"] is False
    assert result["word_count"] == 2

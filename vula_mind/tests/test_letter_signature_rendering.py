"""Tests for the 2026-09-18 signature feature (migration 165): a tenant's captured signature
image, rendered onto generated letters alongside the plain-text sign_off. No e-signature
capability existed anywhere in the codebase before this.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce.pdf import merge_branding, render_letter_pdf


# ── merge_branding carries the new fields ─────────────────────────────────────────

def test_merge_branding_carries_signature_fields():
    branding = merge_branding("digg-demo", {
        "signature_url": "https://storage.example/sig.png", "signature_name": "Judy Downing",
    })
    assert branding["signature_url"] == "https://storage.example/sig.png"
    assert branding["signature_name"] == "Judy Downing"


def test_merge_branding_defaults_signature_fields_to_empty():
    branding = merge_branding("digg-demo", {"company_name": "DIGG Architects"})
    assert branding["signature_url"] == ""
    assert branding["signature_name"] == ""


def test_merge_branding_with_no_settings_still_returns_a_dict():
    branding = merge_branding("digg-demo", None)
    assert isinstance(branding, dict)


# ── render_letter_pdf: HTML content (no real WeasyPrint render needed) ───────────

def _rendered_html(**kwargs):
    """Capture the HTML string render_letter_pdf hands to WeasyPrint, without actually
    rendering a PDF — weasyprint.HTML is patched to just record its `string=` argument."""
    mock_wp = MagicMock()
    mock_wp.return_value.write_pdf.return_value = b"%PDF-fake"
    with patch("weasyprint.HTML", mock_wp):
        render_letter_pdf(tenant_id="digg-demo", **kwargs)
    return mock_wp.call_args.kwargs["string"]


def test_signature_image_rendered_when_url_given():
    html = _rendered_html(body="Body text.", sign_off="Kind regards,\nJudy Downing",
                          signature_url="https://storage.example/sig.png")
    assert '<img src="https://storage.example/sig.png"' in html
    assert "letter-signature-img" in html


def test_no_signature_image_when_url_not_given():
    """Backward compat: a letter with no signature renders exactly as before — no <img>,
    same as every letter generated before this feature existed."""
    html = _rendered_html(body="Body text.", sign_off="Kind regards,\nJudy Downing")
    sign_block = html.split('class="letter-sign"')[1].split("</div>")[0]
    assert "<img" not in sign_block


def test_signature_name_rendered_under_the_image():
    html = _rendered_html(body="Body.", sign_off="Kind regards,",
                          signature_url="https://storage.example/sig.png",
                          signature_name="Judy Downing")
    assert "letter-signature-name" in html
    assert "Judy Downing" in html


def test_signature_url_param_overrides_branding():
    html = _rendered_html(body="Body.", tenant_profile={"signature_url": "https://from-branding/sig.png"},
                          signature_url="https://explicit-param/sig.png")
    assert "https://explicit-param/sig.png" in html
    assert "https://from-branding/sig.png" not in html


def test_branding_signature_used_when_no_explicit_param():
    html = _rendered_html(body="Body.", tenant_profile={"signature_url": "https://from-branding/sig.png"})
    assert "https://from-branding/sig.png" in html


def test_letter_sign_block_absent_with_neither_sign_off_nor_signature():
    html = _rendered_html(body="Body text only, no closing.")
    assert 'class="letter-sign"' not in html


# ── PDF smoke tests (real render, matches existing test_draft_admin.py style) ────

def test_render_letter_pdf_with_signature_produces_valid_pdf_bytes():
    try:
        from weasyprint import HTML  # noqa: F401
    except ImportError:
        pytest.skip("weasyprint not installed in test environment")
    pdf = render_letter_pdf(
        tenant_id="digg-demo", body="Body paragraph.", doc_label="Letter",
        sign_off="Kind regards,\nJudy Downing",
        signature_url="https://storage.example/sig.png", signature_name="Judy Downing",
    )
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"


# ── draft_admin._resolve_sign_off ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_sign_off_uses_real_sender_name_when_known():
    from core.skills.draft_admin import _resolve_sign_off
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"name": "Judy Downing"}])
    with patch("vula.commerce.service._client", return_value=mock_client):
        result = await _resolve_sign_off("digg-demo", "27827077080",
                                         {"trading_as": "DIGG Architects"})
    assert result == "Kind regards,\nJudy Downing\nDIGG Architects"


@pytest.mark.asyncio
async def test_resolve_sign_off_falls_back_to_business_name_only():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    from core.skills.draft_admin import _resolve_sign_off
    with patch("vula.commerce.service._client", return_value=mock_client):
        result = await _resolve_sign_off("digg-demo", "27827077080",
                                         {"trading_as": "DIGG Architects"})
    assert result == "Kind regards,\nDIGG Architects"


@pytest.mark.asyncio
async def test_resolve_sign_off_never_raises_on_db_failure():
    from core.skills.draft_admin import _resolve_sign_off
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        result = await _resolve_sign_off("digg-demo", "27827077080", {"name": "DIGG"})
    assert result == "Kind regards,\nDIGG"


# ── draft_letter actually passes a non-empty sign_off now ─────────────────────────

@pytest.mark.asyncio
async def test_draft_letter_passes_a_real_sign_off_to_render_letter_pdf():
    from core.skills.draft_admin import draft_letter

    with (
        patch("core.skills.draft_admin._fee_proposal_gaps", return_value=[]),
        patch("vula.api.draft._retrieve_context", new=AsyncMock(return_value=("", 0))),
        patch("vula.api.draft._generate_document",
              new=AsyncMock(return_value=("Some content.", "test-model"))),
        patch("core.skills.draft_admin._resolve_sign_off",
              new=AsyncMock(return_value="Kind regards,\nDIGG Architects")) as mock_sign_off,
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.pdf.render_letter_pdf", return_value=b"%PDF-fake") as mock_render,
        patch("vula.api.whatsapp._send_invoice_document", new=AsyncMock(return_value=True)),
    ):
        await draft_letter(
            {"document_type": "appointment_letter", "brief": "Appoint the contractor."},
            "digg-demo", "27827077080")

    mock_sign_off.assert_awaited_once()
    assert mock_render.call_args.kwargs["sign_off"] == "Kind regards,\nDIGG Architects"

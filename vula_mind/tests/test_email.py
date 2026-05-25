"""Tests for the email notification module."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── No-ops when not configured ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_welcome_skips_when_no_api_key():
    with patch("vula.api.email.settings") as mock_settings:
        mock_settings.resend_api_key = ""
        from vula.api.email import send_welcome_email
        result = await send_welcome_email(
            to="judy@digg.co.za",
            first_name="Judy",
            company_name="DIGG Interiors",
            workspace_url="https://app.vula.ai/digg-interiors",
            temp_password="abc123",
            plan="growth",
            trial_ends="2026-06-25",
        )
    assert result is False


@pytest.mark.asyncio
async def test_send_team_alert_skips_when_no_team_email():
    with patch("vula.api.email.settings") as mock_settings:
        mock_settings.resend_api_key = "re_abc123"
        mock_settings.team_email = ""
        from vula.api.email import send_team_alert_email
        result = await send_team_alert_email(
            company_name="DIGG Interiors",
            contact_name="Judy Smith",
            email="judy@digg.co.za",
            whatsapp="+27821234567",
            plan="growth",
            industry="Construction",
            pain_points=["Invoicing"],
            workspace_url="https://app.vula.ai/digg-interiors",
        )
    assert result is False


# ── Successful send ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_welcome_calls_resend():
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "em_abc123"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch("vula.api.email.settings") as mock_settings,
        patch("vula.api.email.httpx.AsyncClient", return_value=mock_client),
    ):
        mock_settings.resend_api_key = "re_abc123"
        mock_settings.from_email = "Vula Group <hello@vula.ai>"

        from vula.api.email import send_welcome_email
        result = await send_welcome_email(
            to="judy@digg.co.za",
            first_name="Judy",
            company_name="DIGG Interiors",
            workspace_url="https://app.vula.ai/digg-interiors",
            temp_password="abc123",
            plan="growth",
            trial_ends="2026-06-25",
        )

    assert result is True
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    payload = call_kwargs.kwargs["json"]
    assert payload["to"] == ["judy@digg.co.za"]
    assert "Judy" in payload["subject"]
    assert "digg-interiors" in payload["html"]
    assert "abc123" in payload["html"]


@pytest.mark.asyncio
async def test_send_team_alert_calls_resend():
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "em_xyz"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch("vula.api.email.settings") as mock_settings,
        patch("vula.api.email.httpx.AsyncClient", return_value=mock_client),
    ):
        mock_settings.resend_api_key = "re_abc123"
        mock_settings.from_email = "Vula Group <hello@vula.ai>"
        mock_settings.team_email = "team@vula.ai"

        from vula.api.email import send_team_alert_email
        result = await send_team_alert_email(
            company_name="DIGG Interiors",
            contact_name="Judy Smith",
            email="judy@digg.co.za",
            whatsapp="+27821234567",
            plan="business",
            industry="Construction & Engineering",
            pain_points=["Invoicing", "Estimating"],
            workspace_url="https://app.vula.ai/digg-interiors",
        )

    assert result is True
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["to"] == ["team@vula.ai"]
    assert "DIGG Interiors" in payload["subject"]
    assert "business" in payload["subject"].lower()
    assert "Judy Smith" in payload["html"]


# ── HTML content checks ───────────────────────────────────────────────────────

def test_welcome_html_contains_required_elements():
    from vula.api.email import _welcome_html
    html = _welcome_html(
        first_name="Judy",
        company_name="DIGG Interiors",
        workspace_url="https://app.vula.ai/digg-interiors",
        temp_password="super-secret-pw",
        plan="growth",
        trial_ends="2026-06-25",
        payment_url=None,
    )
    assert "Judy" in html
    assert "DIGG Interiors" in html
    assert "super-secret-pw" in html
    assert "2026-06-25" in html
    assert "growth" in html
    assert "Open Your Workspace" in html
    # No PayFast button when no payment_url
    assert "PayFast" not in html


def test_welcome_html_includes_payfast_button_when_payment_url_given():
    from vula.api.email import _welcome_html
    html = _welcome_html(
        first_name="Judy",
        company_name="DIGG Interiors",
        workspace_url="https://app.vula.ai/digg-interiors",
        temp_password="pw",
        plan="growth",
        trial_ends="2026-06-25",
        payment_url="https://sandbox.payfast.co.za/eng/process?merchant_id=123",
    )
    assert "PayFast" in html
    assert "Complete Your Subscription" in html
    assert "sandbox.payfast.co.za" in html


def test_team_alert_html_shows_plan_badge():
    from vula.api.email import _team_alert_html
    html = _team_alert_html(
        company_name="Big Corp",
        contact_name="John Doe",
        email="john@bigcorp.co.za",
        whatsapp=None,
        plan="business",
        industry="Mining",
        pain_points=["Reporting", "Compliance"],
        workspace_url="https://app.vula.ai/big-corp",
    )
    assert "business" in html
    assert "D4A017" in html          # business tier gold colour
    assert "Not provided" in html    # no WhatsApp
    assert "Call John Doe within 24 hours" in html


def test_welcome_text_fallback():
    from vula.api.email import _welcome_text
    text = _welcome_text(
        first_name="Judy",
        company_name="DIGG Interiors",
        workspace_url="https://app.vula.ai/digg",
        temp_password="pw123",
        plan="starter",
        trial_ends="2026-07-01",
        payment_url="https://payfast.example/pay",
    )
    assert "Judy" in text
    assert "pw123" in text
    assert "payfast.example" in text
    assert "Vula Group" in text

"""Tests for vula.commerce.onboarding.handle_capture's request-shape guard (2026-09-18).

Real bug class (same as _maybe_allocate_pending_purpose/_maybe_helper_escalation_answer/
bank_review's answer handlers): with onboarding mid-flow, ANY reply used to be captured
unconditionally as the pending field — so a genuine question asked while, say,
collecting_address was active got silently stored as the customer's delivery address instead of
ever being answered.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce.onboarding import handle_capture, _looks_like_a_request

TID = "off-the-hook"
PHONE = "27827077080"


def _contact(state: str) -> dict:
    return {"phone": "27827077080", "onboarding_state": state, "name": None,
            "address": None, "email": None}


# ── _looks_like_a_request ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "What time do you close today?",
    "Can you tell me if you deliver to Rondebosch",
    "Give me the specials list",
    "Send me the menu",
])
def test_looks_like_a_request_true(text):
    assert _looks_like_a_request(text) is True


@pytest.mark.parametrize("text", [
    "John Smith",
    "22 Main Road, Cape Town",
    "john@example.com",
    "skip",
    "yes please sign me up",
])
def test_looks_like_a_request_false(text):
    assert _looks_like_a_request(text) is False


# ── handle_capture: a real question mid-flow is never captured as onboarding data ─

@pytest.mark.asyncio
@pytest.mark.parametrize("state,text", [
    ("collecting_name", "What's on the menu today?"),
    ("collecting_address", "What time do you close today?"),
    ("collecting_email", "Can you tell me about the daily specials"),
    ("confirming_optin", "Give me the price list"),
])
async def test_a_real_question_is_never_captured_as_onboarding_data(state, text):
    with (
        patch("vula.commerce.onboarding.active_capture", return_value=_contact(state)),
        patch("vula.commerce.onboarding._update") as mock_update,
    ):
        reply = await handle_capture(TID, PHONE, text)

    assert reply is None
    mock_update.assert_not_called()


# ── handle_capture: genuine onboarding data still gets captured ──────────────────

@pytest.mark.asyncio
async def test_a_real_name_still_advances_the_flow():
    with (
        patch("vula.commerce.onboarding.active_capture", return_value=_contact("collecting_name")),
        patch("vula.commerce.onboarding._update") as mock_update,
    ):
        reply = await handle_capture(TID, PHONE, "John Smith")

    assert reply is not None
    assert "delivery address" in reply
    mock_update.assert_called_once()
    assert mock_update.call_args[0][2]["name"] == "John Smith"


@pytest.mark.asyncio
async def test_a_real_address_still_advances_the_flow():
    with (
        patch("vula.commerce.onboarding.active_capture", return_value=_contact("collecting_address")),
        patch("vula.commerce.onboarding._update") as mock_update,
    ):
        reply = await handle_capture(TID, PHONE, "22 Main Road, Cape Town")

    assert reply is not None
    mock_update.assert_called_once()
    assert mock_update.call_args[0][2]["address"] == "22 Main Road, Cape Town"


@pytest.mark.asyncio
async def test_stop_still_opts_out_regardless_of_state():
    with (
        patch("vula.commerce.onboarding.active_capture", return_value=_contact("collecting_address")),
        patch("vula.commerce.onboarding._update") as mock_update,
        patch("vula.api.commerce.record_opt_out"),
    ):
        reply = await handle_capture(TID, PHONE, "STOP")

    assert "won't get marketing messages" in reply
    assert mock_update.call_args[0][2]["onboarding_state"] == "opted_out"


@pytest.mark.asyncio
async def test_no_active_capture_returns_none():
    with patch("vula.commerce.onboarding.active_capture", return_value=None):
        reply = await handle_capture(TID, PHONE, "hello")
    assert reply is None

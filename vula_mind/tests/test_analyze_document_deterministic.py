"""_analyze_document must take the deterministic payment-notice parser BEFORE any LLM call,
and reuse the caller's already-extracted text instead of re-parsing the file. 2026-09-11."""
from unittest.mock import AsyncMock, patch

import pytest

from vula.api.whatsapp import _analyze_document

FNB = """NOTIFICATION OF PAYMENT
First National Bank hereby confirms that the following payment instruction has been received:
Date Actioned
: 2026/07/20
Trace ID
: QFBQ1YMQ
Payment From
*AWEH BE LEKKER (PTY) LTD
Cur/Amount
ZAR44000.00
Recipient/Account no
: ..891598
Name
: Edison Maunganidze
Bank
: FIRST NATIONAL BANK
Branch Code
: 250655
Reference
: HPC GEYSER
END OF NOTIFICATION
"""


@pytest.mark.asyncio
async def test_fnb_notice_never_reaches_the_llm():
    # If the deterministic parser fires, litellm is never touched — a call is a test failure.
    with patch("litellm.acompletion", new=AsyncMock(side_effect=AssertionError(
            "the LLM must not be called for a machine-generated payment notice"))):
        r = await _analyze_document("digg-demo", "Payment Notification.pdf", "/tmp/x.pdf", text=FNB)

    assert r["category"] == "Proof of Payment"
    assert r["fields"]["amount_cents"] == 4_400_000
    assert r["fields"]["trace_id"] == "QFBQ1YMQ"
    assert r["fields"]["payee_name"] == "Edison Maunganidze"


@pytest.mark.asyncio
async def test_non_notice_text_still_falls_through_to_the_llm():
    invoice_text = "TAX INVOICE\nACME Supplies\nTotal Due: R1,200.00\nThank you for your business"
    fake_resp = type("R", (), {"choices": [type("C", (), {"message": type("M", (), {
        "content": '{"category": "Invoice", "summary": "ACME invoice.", '
                   '"fields": {"supplier": "ACME Supplies", "total_cents": 120000}}'})()})()]})()

    with (
        patch("core.llm_router.resolve_cheap_route",
              new=AsyncMock(return_value=("gpt-x", "k", "http://b"))),
        patch("core.llm_router.resolve_cloud_route", return_value=None),
        patch("litellm.acompletion", new=AsyncMock(return_value=fake_resp)) as mock_llm,
    ):
        r = await _analyze_document("digg-demo", "invoice.pdf", "/tmp/x.pdf", text=invoice_text)

    mock_llm.assert_awaited()          # the LLM path ran for a real invoice
    assert r["category"] == "Invoice"
    assert r["fields"]["total_cents"] == 120000

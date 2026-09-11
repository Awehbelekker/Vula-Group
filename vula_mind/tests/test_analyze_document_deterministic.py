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


def _fake_llm_response(content: str):
    return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {
        "content": content})()})()]})()


@pytest.mark.asyncio
async def test_docling_retry_recovers_a_total_fitz_text_scrambled():
    """A real invoice whose fitz-joined text is garbled (interleaved columns) — the cheap AND
    cloud passes both only ever see that same bad text, so both produce the same wrong-but-
    internally-consistent total (scan_quality_ok passes, but the figure isn't grounded). A
    third attempt on Docling's clean, reading-order-correct Markdown is what recovers it."""
    scrambled_text = "ACME CO invoice garbled column interleave xyz 12 34 56 nonsense"
    clean_markdown = ("ACME BUILDING SUPPLIES\nTax Invoice INV-4471\n\n"
                      "| Item | Total |\n|---|---|\n| Cement | R 1 250.00 |\n"
                      "| Rebar | R 480.00 |\n\nTotal Due R 1 989.50\n")
    # Internally consistent (798950 == 500000 + 298950) but not on the (scrambled) page.
    bad_json = ('{"category": "Invoice", "summary": "ACME invoice.", '
               '"fields": {"supplier": "ACME", "total_cents": 798950, '
               '"line_items": [{"total_cents": 500000}, {"total_cents": 298950}]}}')
    good_json = ('{"category": "Invoice", "summary": "ACME invoice.", '
                '"fields": {"supplier": "ACME", "total_cents": 198950, '
                '"line_items": [{"total_cents": 125000}, {"total_cents": 48000}]}}')

    calls = {"n": 0}

    async def fake_acompletion(**kwargs):
        calls["n"] += 1
        # Calls 1 (cheap) and 2 (cloud escalation) both only ever see the scrambled fitz text;
        # call 3 sees the Docling markdown and gets the real total.
        return _fake_llm_response(bad_json if calls["n"] <= 2 else good_json)

    with (
        patch("core.llm_router.resolve_cheap_route",
              new=AsyncMock(return_value=("gpt-x", "k", "http://b"))),
        patch("core.llm_router.resolve_cloud_route",
              return_value=("gpt-cloud", "k2", "http://c")),
        patch("litellm.acompletion", new=AsyncMock(side_effect=fake_acompletion)),
        patch("vula.ingestion.docling_extract.extract_markdown",
              new=AsyncMock(return_value=clean_markdown)),
    ):
        r = await _analyze_document("digg-demo", "invoice.pdf", "/tmp/x.pdf", text=scrambled_text)

    assert calls["n"] == 3          # cheap, cloud, docling-retry — no more, no fewer
    assert r["fields"]["total_cents"] == 198950
    assert "_unverified_figures" not in r["fields"]


ABSA_LIKE = """PROOF OF PAYMENT
ABSA Bank Limited confirms the following EFT has been processed.
Beneficiary Name: Edison Maunganidze
Beneficiary Reference: HPC GEYSER
Amount: R 44 000.00
Payment Date: 20 July 2026
"""


@pytest.mark.asyncio
async def test_unverified_bank_match_is_a_hint_not_a_fast_path():
    """A non-FNB bank match (payment_notice.py's UNVERIFIED generic matcher) must reach the
    LLM — as a hint in the prompt — never return directly the way FNB does."""
    good_json = ('{"category": "Proof of Payment", "summary": "Payment to Edison.", '
                '"fields": {"payee_name": "Edison Maunganidze", "amount_cents": 4400000, '
                '"reference": "HPC GEYSER"}}')
    seen_messages = {}

    async def fake_acompletion(**kwargs):
        seen_messages["msgs"] = kwargs["messages"]
        return _fake_llm_response(good_json)

    with (
        patch("core.llm_router.resolve_cheap_route",
              new=AsyncMock(return_value=("gpt-x", "k", "http://b"))),
        patch("core.llm_router.resolve_cloud_route", return_value=None),
        patch("litellm.acompletion", new=AsyncMock(side_effect=fake_acompletion)),
    ):
        r = await _analyze_document("digg-demo", "eft.pdf", "/tmp/x.pdf", text=ABSA_LIKE)

    # The LLM was actually called (not skipped the way a VERIFIED FNB match skips it) ...
    user_msg = seen_messages["msgs"][-1]["content"]
    assert "UNVERIFIED" in user_msg and "Edison Maunganidze" in user_msg   # ... with the hint
    assert r["fields"]["amount_cents"] == 4_400_000
    assert "_unverified_figures" not in r["fields"]     # the LLM's own figure IS grounded


@pytest.mark.asyncio
async def test_unverified_bank_hint_ignored_by_llm_still_gets_flagged():
    """If the LLM produces a figure that isn't actually on the page (whether or not it copied
    the hint), the usual grounding backstop still catches it — the hint changes nothing about
    that guarantee."""
    invented_json = ('{"category": "Proof of Payment", "summary": "Payment.", '
                     '"fields": {"payee_name": "Someone Else", "amount_cents": 999999}}')

    with (
        patch("core.llm_router.resolve_cheap_route",
              new=AsyncMock(return_value=("gpt-x", "k", "http://b"))),
        patch("core.llm_router.resolve_cloud_route",
              return_value=("gpt-cloud", "k2", "http://c")),
        patch("litellm.acompletion", new=AsyncMock(return_value=_fake_llm_response(invented_json))),
        patch("vula.ingestion.docling_extract.extract_markdown", new=AsyncMock(return_value=None)),
    ):
        r = await _analyze_document("digg-demo", "eft.pdf", "/tmp/x.pdf", text=ABSA_LIKE)

    assert r["fields"]["_unverified_figures"][0]["cents"] == 999999


@pytest.mark.asyncio
async def test_docling_unavailable_still_flags_unverified_instead_of_crashing():
    """Docling not installed/failing (extract_markdown -> None) must fall straight back to the
    existing behaviour: flag the ungrounded figure, don't book it, don't raise."""
    scrambled_text = "ACME CO invoice garbled column interleave xyz 12 34 56 nonsense"
    bad_json = ('{"category": "Invoice", "summary": "ACME invoice.", '
               '"fields": {"supplier": "ACME", "total_cents": 798950, '
               '"line_items": [{"total_cents": 500000}, {"total_cents": 298950}]}}')

    with (
        patch("core.llm_router.resolve_cheap_route",
              new=AsyncMock(return_value=("gpt-x", "k", "http://b"))),
        patch("core.llm_router.resolve_cloud_route",
              return_value=("gpt-cloud", "k2", "http://c")),
        patch("litellm.acompletion", new=AsyncMock(return_value=_fake_llm_response(bad_json))),
        patch("vula.ingestion.docling_extract.extract_markdown", new=AsyncMock(return_value=None)),
    ):
        r = await _analyze_document("digg-demo", "invoice.pdf", "/tmp/x.pdf", text=scrambled_text)

    assert r["fields"]["total_cents"] == 798950
    assert r["fields"]["_unverified_figures"][0]["cents"] == 798950

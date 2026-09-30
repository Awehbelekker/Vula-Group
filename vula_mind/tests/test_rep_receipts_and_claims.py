"""2026-09-28, gerflor: a sales rep sent 7 fuel slips with no caption. Each was described and
answered "save as a contact / log a meeting / file it against a project?" — nothing was booked
— and two replies said "I have filed the receipt" with no tool call. A rep's uncaptioned
receipt now goes to the books like a captioned one, and an action claimed without a
successful state-changing tool is replaced with an honest line.
"""
from unittest.mock import AsyncMock

import pytest

from core.skills.base import UNBACKED_ACTION_NOTE, substitute_if_unbacked_claim, tool_source
from vula.api import whatsapp as wa

TID = "gerflor"
PHONE = "27821112222"
REAL = ("I can see a receipt from AE R43 BOTRIVIER for a fuel purchase. The total amount paid "
        "was R871.50 for 33.061 Litres of ULP/LRP 95 at R26.36 per litre, paid via Visa Debit. "
        "I have filed the receipt.")


def test_the_real_false_claim_is_replaced():
    out = substitute_if_unbacked_claim(REAL, [], skill="commerce_admin", tenant_id=TID)
    assert "I have filed" not in out and out.endswith(UNBACKED_ACTION_NOTE)
    assert out.startswith("I can see a receipt from AE R43 BOTRIVIER")


def test_a_claim_after_a_successful_write_is_kept():
    src = [tool_source("add_expense", {"recorded": True, "amount": "R871.50"})]
    assert substitute_if_unbacked_claim(REAL, src, skill="commerce_admin") == REAL


def test_a_preview_or_a_failed_or_a_read_tool_does_not_back_a_claim():
    for src in ([tool_source("add_expense", {"preview": True, "amount": "R871.50"})],
                [tool_source("add_expense", {"error": "amount_rands must be positive"})],
                [tool_source("find_document", {"matches": [1]})]):
        assert substitute_if_unbacked_claim(REAL, src, skill="commerce_admin") != REAL


def test_ordinary_replies_are_untouched():
    for text in ("Would you like me to log this as an expense?",
                 "The invoice was sent on 12 September.",
                 "Stock for Hake is 12."):
        assert substitute_if_unbacked_claim(text, [], skill="commerce_admin") == text


@pytest.fixture()
def rep(monkeypatch):
    monkeypatch.setattr(wa, "_recently_asked_about_signature", lambda phone: False)
    monkeypatch.setattr(wa, "_sender_is_sales_rep", AsyncMock(return_value=True))
    monkeypatch.setattr(wa, "_handle_media", AsyncMock(return_value=False))   # not a contractor
    monkeypatch.setattr(wa, "_download_document", AsyncMock(return_value="/tmp/slip.jpg"))
    ingest, agent = AsyncMock(), AsyncMock(return_value=True)
    monkeypatch.setattr(wa, "_handle_document_ingest", ingest)
    monkeypatch.setattr(wa, "_run_commerce_admin", agent)
    monkeypatch.setattr(wa, "_describe_photo_for_rep", AsyncMock(return_value="a photo of a shopfront"))
    return ingest, agent


@pytest.mark.asyncio
async def test_a_reps_uncaptioned_slip_goes_to_the_books(rep, monkeypatch):
    ingest, agent = rep
    monkeypatch.setattr(wa, "_is_receipt_photo", AsyncMock(return_value=True))
    await wa._handle_image_or_video(PHONE, "image", "m1", "", "image/jpeg", "w1", "knowledge", TID, "sha")
    assert ingest.await_count == 1 and ingest.await_args.kwargs["route_tenant_id"] == TID
    agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_reps_other_photo_is_still_asked_about(rep, monkeypatch):
    ingest, agent = rep
    monkeypatch.setattr(wa, "_is_receipt_photo", AsyncMock(return_value=False))
    await wa._handle_image_or_video(PHONE, "image", "m1", "", "image/jpeg", "w1", "knowledge", TID, "sha")
    ingest.assert_not_awaited()
    prompt = agent.await_args.args[1]
    assert "log it as an expense" in prompt and "Never say something was saved" in prompt


@pytest.mark.asyncio
async def test_a_reps_spec_sheet_photo_is_kept_in_the_knowledge_base(rep, monkeypatch):
    """2026-09-22, gerflor: a photo of the Marmorette Acoustic / Elegance SD spec sheet was
    described, then lost. A product document goes into the knowledge base like a PDF."""
    ingest, agent = rep
    monkeypatch.setattr(wa, "_is_receipt_photo", AsyncMock(return_value=False))
    monkeypatch.setattr(wa, "_describe_photo_for_rep", AsyncMock(return_value=(
        "This document details two Gerflor flooring products: Linoleum Marmorette Acoustic and "
        "Elegance SD Shower System. It includes specifications like thickness, colour.")))
    await wa._handle_image_or_video(PHONE, "image", "m1", "", "image/jpeg", "w1", "knowledge", TID, "sha")
    assert ingest.await_count == 1 and ingest.await_args.args[2].startswith("product-")
    agent.assert_not_awaited()


@pytest.mark.parametrize("text", [
    "Done — order #1042 dispatched to Sea Point.",
    "✅ Saved to your expenses.",
    "Order 1042 is now marked paid.",
    "Successfully cancelled the booking for Tuesday.",
    "Sorted! The quote has been sent to Thabo.",
])
def test_non_first_person_claims_are_caught(text):
    assert substitute_if_unbacked_claim(text, [], skill="commerce_admin") != text


@pytest.mark.parametrize("text", [
    "Should I mark order 1042 as dispatched?",
    "Order 1042 was dispatched on 12 September.",
    "Once you confirm, I'll mark it paid.",
    "Done with the quote? Tell me who to send it to.",
])
def test_questions_and_history_are_not_claims(text):
    assert substitute_if_unbacked_claim(text, [], skill="commerce_admin") == text

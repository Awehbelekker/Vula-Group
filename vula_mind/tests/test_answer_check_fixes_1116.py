"""16 Nov 2026: what the first live answer checks and AI health check found.

- Off the Hook told a customer "We don't have a braai box in our catalogue" — the Braai Box is
  active, in stock, R350.00 (a guessed category hid it).
- DIGG's "Find the Solid Cape invoice for R7,571.44": find_document matched the amount exactly, the
  local model then quoted another figure, the price check dropped it, and the reply talked about a
  price list.
- Railway's MODEL_VISION still named Gemini (no zero-retention endpoint) and scans had no other
  model to fall back to."""
import asyncio
from unittest.mock import AsyncMock, patch

from config import settings
from core import llm_router
from core.skills import commerce_assistant as ca
from core.skills.email_admin import exact_amount_reply

CATALOGUE = [
    {"name": "Braai Box", "slug": "braai-box", "price_cents": 35000, "category": "fresh_fish"},
    {"name": "Braaipak (Frozen) — 8 pieces", "slug": "braaipak", "price_cents": 15000, "category": "frozen_chicken"},
    {"name": "Atlantic Mackerel Fillets", "slug": "mackerel", "price_cents": 8000, "category": "fresh_fish",
     "weight_grams": 500},
]


def test_the_question_names_the_product_it_asks_about():
    assert [p["name"] for p in ca.named_products(CATALOGUE, "What does the braai box cost?")] == ["Braai Box"]
    assert [p["name"] for p in ca.named_products(CATALOGUE, "Price of the Atlantic mackerel fillets please")] \
        == ["Atlantic Mackerel Fillets"]
    assert ca.named_products(CATALOGUE, "Do you deliver to Paarl?") == []


def test_saying_we_dont_have_a_listed_product_is_replaced_from_the_catalogue():
    fixed = ca.denied_but_listed("We don't have a braai box in our catalogue. Would you like to see "
                                 "our current selection of braai essentials?",
                                 "What does the braai box cost?", CATALOGUE)
    assert fixed.startswith("Yes, we have it:") and "Braai Box: R350.00" in fixed
    # a true "we don't have it" stands
    assert ca.denied_but_listed("Sorry, we don't sell crayfish.", "Do you have crayfish?", CATALOGUE) is None
    # a reply that doesn't deny anything is left alone
    assert ca.denied_but_listed("The Braai Box is R350.00.", "What does the braai box cost?", CATALOGUE) is None


def test_a_guessed_category_that_hides_the_product_widens_to_the_whole_catalogue():
    async def fake_list(tenant_id, category=None, **kw):
        return [p for p in CATALOGUE if not category or p["category"] == category]

    skill = ca.CommerceAssistantSkill.__new__(ca.CommerceAssistantSkill)
    with patch.object(ca.service, "list_products", side_effect=fake_list), \
         patch.object(ca.service, "product_choices", return_value=[]):
        out = asyncio.run(skill._exec_list_products("off-the-hook", {"category": "meat", "search": "braai box"}))
    assert [p["name"] for p in out] == ["Braai Box"] and out[0]["price"] == "R350.00"


def test_scans_always_have_a_model_that_reads_images_under_zero_retention(monkeypatch):
    monkeypatch.setattr(settings, "model_vision", "google/gemini-2.5-flash")
    msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:x"}}]}]
    chain = llm_router.cloud_fallback_chain("openrouter/google/gemini-2.5-flash", msgs)
    assert chain == ["openrouter/google/gemini-2.5-flash", f"openrouter/{llm_router.VISION_FALLBACK}"]


FOUND = {"status": "found", "matched_by": "amount R7,571.44 (exact, or the same amount with/without VAT)",
         "matches": [{"party": "SOLID CAPE", "category": "Invoice", "amount": 7571.44,
                      "filed_at": "2026-07-14T09:00:00+00:00", "filename": "INV-1042.pdf"}]}


def test_a_document_found_by_its_exact_amount_is_answered_from_the_record():
    reply = exact_amount_reply("Find the Solid Cape invoice for R7,571.44", FOUND)
    assert reply.startswith("Found it:") and "R7,571.44" in reply and "SOLID CAPE" in reply and "INV-1042.pdf" in reply
    # not an exact-amount match → the usual path
    assert exact_amount_reply("Find the Solid Cape invoice for R7,571.44", {**FOUND, "matched_by": "name"}) is None
    assert exact_amount_reply("Show me Solid Cape's invoices", FOUND) is None

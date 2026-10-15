"""Chat improvements from the 15 Oct 2026 review of SeedleMe's chats, on the real messages:
faster simple turns, fewer needless "double-check" warnings, the customer's own language,
context in the team ping, every message type in the turn record, and no Vula server link in
a customer welcome."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.lang import detect_language
from core.skills.commerce_assistant import needs_knowledge
from core.verification import nothing_to_check
from vula import escalation as esc


# ── 3. simple checkout replies skip the document search ─────────────────────

@pytest.mark.parametrize("msg", ["Yes please", "22 Porterfield,Table view, Cape Town", "EFT", "2",
                                 "1.5kg", "confirm", "Thanks!"])
def test_checkout_replies_skip_the_document_search(msg):
    assert not needs_knowledge(msg)


@pytest.mark.parametrize("msg", ["Do you do round basins", "How long does delivery take?",
                                 "2 kg hake please", "12 basins for my hotel, what discount?"])
def test_real_questions_still_search(msg):
    assert needs_knowledge(msg)


# ── 4. no second-opinion warning when there's nothing to check ──────────────

def test_figures_from_the_records_need_no_warning():
    tool = [{"type": "tool", "text": '{"total": "R0.00", "paid_orders": 0}'}]
    assert nothing_to_check("Today's sales are R0.00 from 0 paid orders.", tool) == "figures_from_tools"
    assert nothing_to_check("Please provide the customer's name or phone number.", []) == "no_figures_short"


def test_unsupported_figures_and_long_answers_are_still_checked():
    tool = [{"type": "tool", "text": "received R2,300,710.14"}]
    assert nothing_to_check("HPC profit is R142,203.17 on R2,300,710.14 received.", tool) == ""
    long = ("The Countertop Butler basin comes in Ivory and Sandstone Namibian Dream. There is also "
            "a Terracotta Exposed Aggregate Countertop Butler Basin and many other lovely options for "
            "your bathroom or kitchen today, ask me anything about them.")
    assert nothing_to_check(long, []) == ""


# ── 5. the customer's language ──────────────────────────────────────────────

def test_plain_english_questions_are_english():
    assert detect_language("Do you do round basins") == "en"
    assert detect_language("Contact Richard Downing on this number") == "en"
    # Afrikaans and isiZulu still win where they're written
    assert detect_language("Kan jy my help") == "af"
    assert detect_language("Is dit vandag oop?") == "af"
    assert detect_language("Sawubona, ngifuna isinki") == "zu"


# ── 6. the team ping says who is asking and what they were doing ────────────

class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a): return self
    def eq(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def execute(self): return SimpleNamespace(data=self.rows)


@pytest.mark.asyncio
async def test_team_ping_has_the_customer_cart_and_last_messages(monkeypatch):
    from vula.commerce import onboarding, service
    cart = {"commerce_cart_items": [{"quantity": 1, "unit_price_cents": 169900, "commerce_products": {
        "name": "The Emerald Dream Bespoke Charcoal Concrete Sink"}}]}
    msgs = [  # newest first, as the query returns them
        {"role": "user", "content": "I'd like to check out now"},
        {"role": "assistant", "content": "✅ Got your basket — 1 item from the shop."},
        {"role": "user", "content": "Hi! I'd like to order basket GXAMCN"},
    ]
    monkeypatch.setattr(onboarding, "get_contact", lambda t, p: {"name": "Richard"})
    monkeypatch.setattr(service, "get_or_create_cart", AsyncMock(return_value=cart))
    monkeypatch.setattr(service, "get_or_create_session", AsyncMock(return_value={"id": "s1"}))
    monkeypatch.setattr(service, "_client", lambda: SimpleNamespace(table=lambda n: _Q(msgs)))
    ctx = await esc.customer_context("seedleme", "27645755210", exclude="I'd like to check out now")
    assert "👤 Richard · +27645755210" in ctx
    assert "🛒 1 × The Emerald Dream Bespoke Charcoal Concrete Sink — R1,699.00" in ctx
    assert "• Customer: Hi! I'd like to order basket GXAMCN" in ctx
    assert "check out now" not in ctx                       # the question itself isn't repeated
    ping = esc.helper_prompt({}, "How long does delivery take?", context=ctx)
    assert ping.startswith("❓ A customer asked:") and "👤 Richard" in ping
    assert ping.rstrip().endswith("I'll send it to them and remember it.")


# ── 7. every kind of message lands in the turn record ───────────────────────

@pytest.mark.asyncio
async def test_lists_link_buttons_images_and_templates_are_recorded(monkeypatch):
    from core import dry_run
    from vula import turns
    from vula.api import whatsapp as wa
    seen = []
    monkeypatch.setattr(turns, "reply", lambda text, kind="text", to=None: seen.append((kind, text)))
    monkeypatch.setattr(dry_run, "record_send", lambda *a: True)
    creds = {"token": "t", "phone_id": "p"}
    await wa._send_wa_list(creds, "27645755210", "SeedleMe", "What are you looking for?", "", "View products",
                           [{"title": "Browse", "rows": [{"id": "cat_sinks", "title": "Sinks"},
                                                        {"id": "cat_basins", "title": "Basins"}]}])
    await wa._send_wa_cta_url(creds, "27645755210", "Welcome to SeedleMe!", "View products", "https://x")
    await wa._send_wa_image(creds, "27645755210", "https://img", "Round Drop-In Basin")
    await wa._send_wa_template("seedleme", "27645755210", "order_update", "SED-1", "packed")
    kinds = dict(seen)
    assert kinds["list"].endswith("[list: Sinks | Basins]")
    assert kinds["cta_url"].endswith("[link button: View products]")
    assert kinds["image"] == "[image] Round Drop-In Basin"
    assert kinds["template"] == "[template order_update] SED-1 | packed"

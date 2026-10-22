"""Customer prices must come from a lookup (2026-10-22, EZContact brief rule 7).

unverified_prices() used to return [] whenever no grounding tool ran that turn, so a customer
reply like "Hake fillets are R180/kg" with nothing behind it was never checked — and the
supervisor's figure check is off for customers. Customer replies now use strict=True.

Also: the business profile's opening hours come from the hours set in Settings, so the KB and
check_business_hours can't give a customer two different answers.
"""
import asyncio

import pytest

from core.skills.base import unverified_prices
from core.skills import commerce_assistant as ca
from vula.commerce import business_profile as bp
from vula.commerce.hours import describe

GROUNDING = {"kb", "list_products", "get_daily_catch"}


# ── strict price grounding ────────────────────────────────────────────────────

def test_a_price_with_no_lookup_is_flagged_in_strict_mode():
    answer = "Hake fillets are R180.00/kg today."
    assert unverified_prices(answer, [], GROUNDING) == []                    # old behaviour kept
    assert unverified_prices(answer, [], GROUNDING, strict=True) == ["R180.00"]


def test_a_looked_up_price_is_kept():
    sources = [{"name": "list_products", "text": '[{"name": "Hake fillets", "price": "R180.00"}]'}]
    assert unverified_prices("Hake fillets are R180.00/kg.", sources, GROUNDING, strict=True) == []


def test_the_business_own_facts_the_customer_words_and_earlier_replies_are_trusted():
    facts = "R60.00 R500.00"                               # delivery fee, free delivery over
    assert unverified_prices("Delivery is R60, free over R500.", [], GROUNDING,
                             strict=True, also_trusted=facts) == []
    assert unverified_prices("Yes, the R150 one is the 1kg pack.", [], GROUNDING,
                             strict=True, also_trusted="I saw a pack for R150 on your site") == []
    history = "Customer: price of calamari?\nVula: Calamari is R160.00/kg."
    assert unverified_prices("So 2kg calamari comes to R320.00 — R160.00/kg.", [], GROUNDING,
                             strict=True, also_trusted=history) == ["R320.00"]


def test_no_price_in_the_reply_is_never_flagged():
    assert unverified_prices("We're open until 5 today.", [], GROUNDING, strict=True) == []


def test_price_facts_text_lists_only_the_money_settings(monkeypatch):
    monkeypatch.setattr("vula.commerce.order_workflow.get_order_settings",
                        lambda tid: {"delivery_fee_cents": 6000, "free_delivery_over_cents": 50000,
                                     "min_order_cents": None, "eft_details": "FNB 123"})
    assert ca._price_facts_text("off-the-hook") == "R60.00 R500.00"


# ── one source for opening hours ─────────────────────────────────────────────

WEEK = {**{d: {"open": "08:00", "close": "17:00"} for d in ("mon", "tue", "wed", "thu", "fri")},
        "sat": {"open": "09:00", "close": "13:00"}, "sun": None}


def test_describe_groups_days_and_says_closed():
    assert describe(WEEK) == "Mon–Fri 08:00–17:00 · Sat 09:00–13:00 · Sun closed"
    assert describe({"mon": {"open": "08:00", "close": "17:00"}}) == "Mon 08:00–17:00 · Tue–Sun closed"
    assert describe(None) == "" and describe({}) == ""


@pytest.fixture()
def profile(monkeypatch):
    data = {"oth": {"what_we_do": "Fresh seafood", "hours": "Mon–Fri 8–5 (old)"}}
    hours = {"oth": WEEK}
    monkeypatch.setattr(bp, "get_answers", lambda tid: dict(data.get(tid, {})))
    monkeypatch.setattr(bp, "_business_type", lambda tid: "food")
    monkeypatch.setattr(bp, "get_public_keys", lambda tid: ["what_we_do", "hours"])
    monkeypatch.setattr("vula.commerce.order_workflow.get_order_settings",
                        lambda tid: {"business_hours": hours.get(tid)})
    written = []

    async def _write(tid, answers, public):
        written.append(dict(answers))
        return True

    monkeypatch.setattr(bp, "_write_documents", _write)
    return data, hours, written


def test_hours_set_in_settings_replace_the_free_text_answer(profile):
    data, hours, written = profile
    assert bp.effective_answers("oth")["hours"] == "Mon–Fri 08:00–17:00 · Sat 09:00–13:00 · Sun closed"
    assert "hours" not in bp.status("oth")["missing"]
    asyncio.run(bp.refresh_documents("oth"))
    assert written[-1]["hours"].startswith("Mon–Fri 08:00–17:00")


def test_without_settings_hours_the_owner_answer_stays_and_is_still_asked_for(profile):
    data, hours, written = profile
    hours.clear()
    assert bp.effective_answers("oth")["hours"] == "Mon–Fri 8–5 (old)"
    data["oth"].pop("hours")
    assert "hours" in bp.status("oth")["missing"]

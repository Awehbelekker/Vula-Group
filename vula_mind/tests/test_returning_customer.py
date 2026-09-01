"""A repeat customer shouldn't be re-interrogated for details already on file.

2026-09-01, ahead of off-the-hook taking real WhatsApp orders: nothing reused a known
customer's name or delivery address, so every repeat order started from scratch — the friction
most likely to make someone abandon a WhatsApp order. These tests cover the profile lookup and
the prompt block it feeds, including the safety property that an address on file is OFFERED for
confirmation, never silently assumed (a stale address puts a real delivery at the wrong door).
"""
from unittest.mock import patch

import pytest

from core.skills.commerce_assistant import CommerceAssistantSkill
from vula.commerce import service

TENANT = "off-the-hook"


# ── prompt block ────────────────────────────────────────────────────────────────

def test_no_profile_adds_nothing_to_the_prompt():
    assert CommerceAssistantSkill._returning_customer_block(None) == ""
    assert CommerceAssistantSkill._returning_customer_block({}) == ""


def test_profile_with_no_usable_details_adds_nothing():
    block = CommerceAssistantSkill._returning_customer_block(
        {"name": None, "delivery_address": None, "delivery_slot": None, "order_count": 1})
    assert block == ""


def test_profile_block_names_the_customer_and_their_address():
    block = CommerceAssistantSkill._returning_customer_block({
        "name": "Richard Downing", "delivery_address": "Parklands",
        "delivery_slot": "morning", "last_order": "OFF-00001", "order_count": 3,
    })
    assert "Richard Downing" in block
    assert "Parklands" in block
    assert "morning" in block
    assert "OFF-00001" in block
    assert "3 orders" in block


def test_profile_block_always_offers_the_chance_to_change_details():
    """Ian, 2026-09-01: 'need to always ask if the client wants to change any details'."""
    block = CommerceAssistantSkill._returning_customer_block(
        {"name": "Staci", "delivery_address": "12 Beach Rd", "order_count": 2})
    low = block.lower()
    assert "want to change anything?" in low
    assert "always give them that chance to change something" in low
    assert "never just assume the old details still hold" in low


def test_profile_block_reads_details_back_before_ordering():
    block = CommerceAssistantSkill._returning_customer_block(
        {"name": "Staci", "delivery_address": "12 Beach Rd", "delivery_slot": "morning",
         "order_count": 2})
    assert "READ THE DETAILS ON FILE BACK TO THEM" in block
    # the example it's shown uses their real details, not a placeholder
    assert "Staci" in block and "12 Beach Rd" in block and "morning" in block


def test_singular_order_count_reads_naturally():
    block = CommerceAssistantSkill._returning_customer_block(
        {"name": "Sam", "delivery_address": "Parklands", "order_count": 1})
    assert "1 order," in block and "1 orders" not in block


def test_system_prompt_includes_the_returning_customer_block():
    skill = CommerceAssistantSkill()
    prompt = skill._system_prompt(
        TENANT, kb_context="",
        customer_profile={"name": "Richard Downing", "delivery_address": "Parklands",
                          "order_count": 2})
    assert "Richard Downing" in prompt
    assert "RETURNING CUSTOMER" in prompt


def test_system_prompt_without_a_profile_is_unchanged():
    skill = CommerceAssistantSkill()
    assert "RETURNING CUSTOMER" not in skill._system_prompt(TENANT, kb_context="")


# ── profile lookup ──────────────────────────────────────────────────────────────

class _Q:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    @property
    def not_(self):
        return self

    def in_(self, *a, **k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _client_with(rows):
    return type("C", (), {"table": lambda self, name: _Q(rows)})()


@pytest.mark.asyncio
async def test_profile_returns_last_order_details():
    rows = [{"display_id": "OFF-00007", "customer_name": "Staci", "customer_phone": "27645755210",
             "customer_email": None, "delivery_address": "Parklands", "delivery_slot": "morning",
             "created_at": "2026-08-30T10:00:00+00:00"}]
    with patch.object(service, "_client", lambda: _client_with(rows)):
        prof = await service.get_customer_profile(TENANT, "27645755210")
    assert prof["name"] == "Staci"
    assert prof["delivery_address"] == "Parklands"
    assert prof["last_order"] == "OFF-00007"
    assert prof["order_count"] == 1


@pytest.mark.asyncio
async def test_profile_matches_on_phone_suffix_despite_formatting():
    """Stored customer_phone formatting isn't consistent — a leading 0 vs 27 must still match."""
    rows = [{"display_id": "OFF-1", "customer_name": "Sam", "customer_phone": "+27 64 575 5210",
             "customer_email": None, "delivery_address": "Parklands", "delivery_slot": None,
             "created_at": "2026-08-30T10:00:00+00:00"}]
    with patch.object(service, "_client", lambda: _client_with(rows)):
        prof = await service.get_customer_profile(TENANT, "0645755210")
    assert prof is not None and prof["name"] == "Sam"


@pytest.mark.asyncio
async def test_new_customer_returns_none_rather_than_inventing_details():
    rows = [{"display_id": "OFF-1", "customer_name": "Someone Else", "customer_phone": "27111111111",
             "customer_email": None, "delivery_address": "Elsewhere", "delivery_slot": None,
             "created_at": "2026-08-30T10:00:00+00:00"}]
    with patch.object(service, "_client", lambda: _client_with(rows)):
        assert await service.get_customer_profile(TENANT, "27645755210") is None


@pytest.mark.asyncio
async def test_no_phone_returns_none():
    assert await service.get_customer_profile(TENANT, "") is None


@pytest.mark.asyncio
async def test_lookup_failure_never_blocks_the_order():
    def _boom():
        raise RuntimeError("db down")
    with patch.object(service, "_client", _boom):
        assert await service.get_customer_profile(TENANT, "27645755210") is None

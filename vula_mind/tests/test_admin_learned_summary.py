"""GET /{tenant_id}/admin/learned-summary — Tenant Mind Phase 2 (2026-09-15).

One surface for everything Vula has picked up about a business, pulled from three genuinely
independent mechanisms (voice/tone, learned answers, merchant categorisation) that previously
had no shared view. Purely a read-only aggregation — this test file locks the shape of what it
returns, not any new learning behavior (each mechanism's own tests already cover that).
"""
from unittest.mock import MagicMock, patch

import pytest

from vula.api.commerce import admin_learned_summary

TID = "off-the-hook"


def _db_with(table_rows: dict):
    """table_rows: {table_name: [rows]} — each queried the same way admin_learned_summary
    queries it (select().eq().order().limit().execute())."""
    db = MagicMock()

    def table(name):
        m = MagicMock()
        m.select.return_value.eq.return_value.order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=table_rows.get(name, []))
        return m

    db.table.side_effect = table
    return db


def _cfg(**kw):
    base = {"persona_prompt": "", "persona_prompt_suggested": "", "persona_prompt_suggested_at": None}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_voice_section_reflects_current_and_suggested():
    with (
        patch("vula.commerce.service._client", return_value=_db_with({})),
        patch("vula.api.tenants.get_config", return_value=_cfg(
            persona_prompt="Warm and casual.",
            persona_prompt_suggested="Even warmer, uses emojis.",
            persona_prompt_suggested_at="2026-09-15T10:00:00Z")),
    ):
        result = await admin_learned_summary(TID)

    assert result["voice"]["current"] == "Warm and casual."
    assert result["voice"]["suggested"] == "Even warmer, uses emojis."
    assert result["voice"]["suggested_at"] == "2026-09-15T10:00:00Z"


@pytest.mark.asyncio
async def test_learned_answers_split_pending_and_approved():
    rows = [
        {"id": "1", "question": "Q1", "answer": "A1", "status": "pending"},
        {"id": "2", "question": "Q2", "answer": "A2", "status": "approved"},
        {"id": "3", "question": "Q3", "answer": "A3", "status": "approved"},
        {"id": "4", "question": "Q4", "answer": "A4", "status": "rejected"},
    ]
    with (
        patch("vula.commerce.service._client",
              return_value=_db_with({"vula_learned_answers": rows})),
        patch("vula.api.tenants.get_config", return_value=_cfg()),
    ):
        result = await admin_learned_summary(TID)

    la = result["learned_answers"]
    assert la["pending_count"] == 1
    assert la["approved_count"] == 2
    assert [r["id"] for r in la["pending"]] == ["1"]
    assert {r["id"] for r in la["recent_approved"]} == {"2", "3"}


@pytest.mark.asyncio
async def test_merchant_profiles_split_decided_and_undecided():
    rows = [
        {"merchant_key": "crazy store", "account_code": "cost_of_sales", "confidence": "confident"},
        {"merchant_key": "unknown shop", "account_code": None, "confidence": "ambiguous"},
    ]
    with (
        patch("vula.commerce.service._client",
              return_value=_db_with({"commerce_merchant_profiles": rows})),
        patch("vula.api.tenants.get_config", return_value=_cfg()),
    ):
        result = await admin_learned_summary(TID)

    mp = result["merchant_profiles"]
    assert mp["decided_count"] == 1
    assert mp["total_count"] == 2


@pytest.mark.asyncio
async def test_fails_open_on_every_section_independently():
    """A migration not yet applied for one mechanism must not blank out the others."""
    db = MagicMock()

    def table(name):
        if name == "vula_learned_answers":
            raise Exception('relation "vula_learned_answers" does not exist')
        m = MagicMock()
        m.select.return_value.eq.return_value.order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=[])
        return m

    db.table.side_effect = table
    with (
        patch("vula.commerce.service._client", return_value=db),
        patch("vula.api.tenants.get_config", return_value=_cfg(persona_prompt="Set.")),
    ):
        result = await admin_learned_summary(TID)  # must not raise

    assert result["voice"]["current"] == "Set."
    assert result["learned_answers"]["pending_count"] == 0
    assert result["merchant_profiles"]["total_count"] == 0

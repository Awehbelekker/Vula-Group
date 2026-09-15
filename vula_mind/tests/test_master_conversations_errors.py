"""Tests for master's per-tenant conversation/error visibility (2026-09-15, Master Build Brief
section 6a item 2 — "full conversation/error-log visibility without needing direct Railway
access"). Direct-function-call style, matching test_master_impersonate.py: no FastAPI
TestClient, everything under this router already requires require_master (asserted separately
by the router-level dependency, not re-tested per endpoint here).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vula.api import master

TENANT = "off-the-hook"


def _chain(data):
    """A table().select().eq().gte().order().limit().execute() chain that returns `data`
    regardless of which filters were applied — these tests check what the endpoint DOES with
    the rows, not that Supabase's own filtering works (that's exercised live, not mocked)."""
    m = MagicMock()
    m.select.return_value = m
    m.eq.return_value = m
    m.gte.return_value = m
    m.order.return_value = m
    m.limit.return_value = m
    m.execute.return_value = SimpleNamespace(data=data)
    return m


def _fake_db(tables: dict) -> MagicMock:
    db = MagicMock()
    db.table.side_effect = lambda name: _chain(tables.get(name, []))
    return db


# ─── conversations (thread picker + one thread) ────────────────────────────────

@pytest.mark.asyncio
async def test_conversations_lists_threads_via_chat_history_db():
    fake_threads = [{"phone": "27821111111", "last_message": "hi", "last_role": "user",
                     "last_at": "2026-09-15T10:00:00+00:00"}]
    fake_db = MagicMock()
    fake_db.list_threads.return_value = fake_threads
    with patch("vula.chat.history.get_db", return_value=fake_db):
        out = await master.master_tenant_conversations(TENANT)
    assert out == {"tenant_id": TENANT, "threads": fake_threads}
    fake_db.list_threads.assert_called_once_with(TENANT, limit=30)


@pytest.mark.asyncio
async def test_conversations_caps_limit_at_100():
    fake_db = MagicMock()
    fake_db.list_threads.return_value = []
    with patch("vula.chat.history.get_db", return_value=fake_db):
        await master.master_tenant_conversations(TENANT, limit=9999)
    fake_db.list_threads.assert_called_once_with(TENANT, limit=100)


@pytest.mark.asyncio
async def test_conversation_messages_for_one_thread():
    from vula.chat.history import ChatMessage
    fake_msgs = [ChatMessage(role="user", text="hi", created_at="2026-09-15T10:00:00+00:00",
                             phone="27821111111", tenant_id=TENANT)]
    fake_db = MagicMock()
    fake_db.get.return_value = fake_msgs
    with patch("vula.chat.history.get_db", return_value=fake_db):
        out = await master.master_tenant_conversation_messages(TENANT, "27821111111")
    assert out["tenant_id"] == TENANT
    assert out["phone"] == "27821111111"
    assert out["messages"] == [{"role": "user", "text": "hi",
                                "created_at": "2026-09-15T10:00:00+00:00"}]
    # Support reproduction needs old conversations, not just the AI's own recent-context window.
    fake_db.get.assert_called_once_with(TENANT, phone="27821111111", limit=100, max_age_hours=None)


# ─── errors (per-tenant drill-down) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_errors_returns_webhook_failures_and_verification_flags():
    webhook_rows = [{"id": "w1", "phone": "278211", "msg_type": "document",
                     "error": "boom", "created_at": "2026-09-15T09:00:00+00:00"}]
    telemetry_rows = [
        {"task": "commerce_admin", "outcome": "defect_found", "escalated": True,
         "reason": None, "extra": {}, "created_at": "2026-09-15T09:05:00+00:00"},
        {"task": "reasoning", "outcome": "accepted", "escalated": False,
         "reason": None, "extra": {}, "created_at": "2026-09-15T09:06:00+00:00"},
    ]
    fake_db = _fake_db({"vula_webhook_failures": webhook_rows,
                        "vula_reasoning_telemetry": telemetry_rows})
    with patch("vula.api.master._client", return_value=fake_db):
        out = await master.master_tenant_errors(TENANT)
    assert out["tenant_id"] == TENANT
    assert out["webhook_failures"] == webhook_rows
    # "accepted" must NOT show up — only real defects/checker problems count as an error event.
    assert len(out["verification_flags"]) == 1
    assert out["verification_flags"][0]["outcome"] == "defect_found"


@pytest.mark.asyncio
async def test_errors_excludes_router_escalations_even_though_escalated_true():
    """A router event escalating to cloud for genuine complexity is healthy routing, not an
    error — must never appear here just because some other event elsewhere has escalated=True."""
    telemetry_rows = [
        {"task": "email_summary", "outcome": "cloud", "escalated": True,
         "reason": "local_unreachable", "extra": {}, "created_at": "2026-09-15T09:00:00+00:00"},
    ]
    fake_db = _fake_db({"vula_reasoning_telemetry": telemetry_rows})
    with patch("vula.api.master._client", return_value=fake_db):
        out = await master.master_tenant_errors(TENANT)
    assert out["verification_flags"] == []


@pytest.mark.asyncio
async def test_errors_respects_custom_window_hours():
    fake_db = _fake_db({})
    with patch("vula.api.master._client", return_value=fake_db):
        out = await master.master_tenant_errors(TENANT, hours=24)
    assert out["window_hours"] == 24


@pytest.mark.asyncio
async def test_errors_fails_open_per_source_on_query_error():
    """One table erroring must not take down the other half of the response."""
    fake_db = MagicMock()
    fake_db.table.side_effect = RuntimeError("db down")
    with patch("vula.api.master._client", return_value=fake_db):
        out = await master.master_tenant_errors(TENANT)
    assert "error" in out["webhook_failures"]
    assert "error" in out["verification_flags"]

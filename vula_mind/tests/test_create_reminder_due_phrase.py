"""Tests for create_reminder's due_phrase resolution and the system-prompt date-grounding fix.

2026-08-26 regression, found live: asked to set a reminder for "next Tuesday", the model
had no way to know today's real date and asked the user for it instead of resolving the
phrase itself. Two fixes: (1) create_reminder now takes a raw due_phrase resolved
deterministically via the same _resolve_due_at dateutil logic log_meeting's action items
already use (never LLM date arithmetic), (2) _system_prompt grounds the model in today's
real date so it can reason about relative phrases at all.
"""
from datetime import datetime, timedelta, timezone

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill, _resolve_due_at

TID = "test-tenant"
CTX = {"phone": "27821234567", "caller_name": "Ian"}


@pytest.fixture
def skill():
    return CommerceAdminSkill()


def test_resolve_due_at_handles_weekday_phrase():
    resolved = _resolve_due_at("next Tuesday")
    assert resolved is not None
    dt = datetime.fromisoformat(resolved)
    assert dt.weekday() == 1  # Tuesday


def test_resolve_due_at_returns_none_for_no_date_signal():
    assert _resolve_due_at("just a general note, nothing time-related") is None


def test_resolve_due_at_returns_none_for_empty():
    assert _resolve_due_at("") is None
    assert _resolve_due_at(None) is None


@pytest.mark.asyncio
async def test_create_reminder_resolves_due_phrase_deterministically(skill, monkeypatch):
    class _FakeQuery:
        def __init__(self, table):
            self._table = table
        def select(self, *a): return self
        def eq(self, *a): return self
        def ilike(self, *a): return self
        def limit(self, *a): return self
        def insert(self, row):
            self._row = row
            return self
        def execute(self):
            if hasattr(self, "_row"):
                return type("R", (), {"data": [{**self._row, "id": "r1"}]})()
            return type("R", (), {"data": []})()

    class _FakeClient:
        def table(self, name):
            return _FakeQuery(name)

    monkeypatch.setattr(ca.service, "_client", lambda: _FakeClient())

    res = await skill._create_reminder(TID, {"text": "Follow up", "due_phrase": "next Tuesday"}, CTX)
    assert res["created"] is True
    assert res["due_at"] is not None
    dt = datetime.fromisoformat(res["due_at"])
    assert dt.weekday() == 1


@pytest.mark.asyncio
async def test_create_reminder_without_due_phrase_has_no_due_at(skill, monkeypatch):
    class _FakeQuery:
        def select(self, *a): return self
        def eq(self, *a): return self
        def ilike(self, *a): return self
        def limit(self, *a): return self
        def insert(self, row):
            self._row = row
            return self
        def execute(self):
            if hasattr(self, "_row"):
                return type("R", (), {"data": [{**self._row, "id": "r1"}]})()
            return type("R", (), {"data": []})()

    class _FakeClient:
        def table(self, name):
            return _FakeQuery()

    monkeypatch.setattr(ca.service, "_client", lambda: _FakeClient())

    res = await skill._create_reminder(TID, {"text": "Just a note"}, CTX)
    assert res["created"] is True
    assert res["due_at"] is None


# ── system prompt date grounding ────────────────────────────────────────────────

def test_owner_system_prompt_includes_todays_date(skill):
    prompt = skill._system_prompt(TID, role=None, name="Test")
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    assert f"Today's date is {today}" in prompt


def test_sales_rep_system_prompt_includes_todays_date(skill):
    prompt = skill._system_prompt(TID, role="sales_rep", name="Test")
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    assert f"Today's date is {today}" in prompt

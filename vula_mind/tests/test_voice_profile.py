"""Tests for tenant voice-profile learning (migrations 119/120).

Uses a minimal in-memory fake Supabase client, matching the pattern in tests/test_flows.py —
good enough to prove the sample-size guard across all four sources (WhatsApp agent replies,
WhatsApp self-chat, meeting notes, email excerpts), PII redaction, and that a suggestion is
persisted, without needing a real database or model.
"""
import uuid
from datetime import datetime, timezone

import pytest

import vula.commerce.voice_profile as vp


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self._in_filter = None
        self._like_filter = None
        self._limit = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self.filters.append((key, val))
        return self

    def like(self, key, pattern):
        # Only the "prefix:%" shape used by voice_profile.py is needed here.
        self._like_filter = (key, pattern.rstrip("%"))
        return self

    def in_(self, key, values):
        self._in_filter = (key, set(values))
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        if not all(row.get(k) == v for k, v in self.filters):
            return False
        if self._like_filter:
            key, prefix = self._like_filter
            if not str(row.get(key) or "").startswith(prefix):
                return False
        if self._in_filter:
            key, values = self._in_filter
            if row.get(key) not in values:
                return False
        return True

    def execute(self):
        rows = [r for r in self.table.rows if self._matches(r)]
        if self._limit:
            rows = rows[: self._limit]
        return _Result(rows)

    def update(self, patch):
        self._patch = patch
        return self

    def insert(self, rows):
        rows = [rows] if isinstance(rows, dict) else rows
        out = []
        for row in rows:
            row = dict(row)
            row.setdefault("id", str(uuid.uuid4()))
            self.table.rows.append(row)
            out.append(row)
        return _ExecWrapper(out)


class _ExecWrapper:
    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return _Result(self._rows)


class _FakeQueryWithUpdate(_FakeQuery):
    def execute(self):
        rows = [r for r in self.table.rows if self._matches(r)]
        if hasattr(self, "_patch"):
            for r in rows:
                r.update(self._patch)
        if self._limit:
            rows = rows[: self._limit]
        return _Result(rows)


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, name, store):
        self.name = name
        self.rows = store.setdefault(name, [])


class _FakeClient:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _FakeQueryWithUpdate(_FakeTable(name, self.store))


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(vp, "_client", lambda: client)
    monkeypatch.setattr("vula.api.tenants.invalidate", lambda tid: None)
    return client


TID = "test-tenant"


def _seed_messages(client, n, role="agent", session_id=None,
                   text="Howzit, thanks so much! Ready whenever you are 🙏"):
    rows = client.store.setdefault("commerce_conversation_messages", [])
    for i in range(n):
        rows.append({
            "id": str(uuid.uuid4()), "tenant_id": TID, "role": role, "session_id": session_id,
            "content": f"{text} ({i})", "created_at": datetime.now(timezone.utc).isoformat(),
        })


def _seed_admin_session(client):
    sid = str(uuid.uuid4())
    client.store.setdefault("commerce_conversation_sessions", []).append(
        {"id": sid, "tenant_id": TID, "session_key": f"admin:27821234567"})
    return sid


def _seed_meeting_notes(client, n, text="Met with the client, they want the deck by Friday."):
    rows = client.store.setdefault("vula_filed_documents", [])
    for i in range(n):
        rows.append({
            "id": str(uuid.uuid4()), "tenant_id": TID, "category": "meeting_notes",
            "fields": {"raw_notes": f"{text} ({i})"},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })


def _seed_email_excerpts(client, n, text="Thanks for reaching out, happy to help with that."):
    rows = client.store.setdefault("vula_email_voice_samples", [])
    for i in range(n):
        rows.append({
            "id": str(uuid.uuid4()), "tenant_id": TID, "excerpt": f"{text} ({i})",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })


def _seed_tenant_config(client):
    client.store.setdefault("vula_tenant_config", []).append({"tenant_id": TID})


def _mock_llm(monkeypatch, content="Warm and casual, uses 'howzit', short replies, emojis welcome."):
    async def _fake_route(*a, **kw):
        return ("fake-model", None, None)
    monkeypatch.setattr("core.llm_router.resolve_generation_route", _fake_route)

    class _Msg:
        pass
    _Msg.content = content

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    async def _fake_completion(*a, **kw):
        return _Resp()
    monkeypatch.setattr("litellm.acompletion", _fake_completion)


@pytest.mark.asyncio
async def test_analyze_voice_rejects_too_few_samples(fake_client):
    _seed_messages(fake_client, 5)
    result = await vp.analyze_voice(TID)
    assert "error" in result
    assert "Not enough data" in result["error"]


@pytest.mark.asyncio
async def test_analyze_voice_ignores_non_agent_non_admin_roles(fake_client):
    _seed_messages(fake_client, 20, role="assistant")
    result = await vp.analyze_voice(TID)
    assert "error" in result
    assert "Not enough data" in result["error"]


@pytest.mark.asyncio
async def test_analyze_voice_success_stores_suggestion(fake_client, monkeypatch):
    _seed_messages(fake_client, 20)
    _seed_tenant_config(fake_client)
    _mock_llm(monkeypatch)

    result = await vp.analyze_voice(TID)
    assert "error" not in result
    assert result["suggested"] == "Warm and casual, uses 'howzit', short replies, emojis welcome."
    assert result["sample_count"] == 20

    row = fake_client.store["vula_tenant_config"][0]
    assert row["persona_prompt_suggested"] == result["suggested"]
    assert row["persona_prompt_suggested_at"]


@pytest.mark.asyncio
async def test_sample_count_counts_agent_rows_only(fake_client):
    _seed_messages(fake_client, 12, role="agent")
    _seed_messages(fake_client, 8, role="assistant")
    assert await vp.sample_count(TID) == 12


@pytest.mark.asyncio
async def test_whatsapp_self_chat_counted_via_admin_session(fake_client):
    sid = _seed_admin_session(fake_client)
    _seed_messages(fake_client, 5, role="user", session_id=sid)
    # A normal storefront session's role='user' messages must NOT count — no session_id set,
    # so they don't match the admin-session id filter.
    _seed_messages(fake_client, 5, role="user", session_id=None)
    assert await vp.sample_count(TID) == 5


@pytest.mark.asyncio
async def test_all_four_sources_combine(fake_client, monkeypatch):
    sid = _seed_admin_session(fake_client)
    _seed_messages(fake_client, 5, role="agent")
    _seed_messages(fake_client, 5, role="user", session_id=sid)
    _seed_meeting_notes(fake_client, 3)
    _seed_email_excerpts(fake_client, 3)
    _seed_tenant_config(fake_client)
    _mock_llm(monkeypatch)

    result = await vp.analyze_voice(TID)
    assert "error" not in result
    assert result["sample_count"] == 16


@pytest.mark.asyncio
async def test_redacts_email_and_phone_from_samples(fake_client, monkeypatch):
    _seed_meeting_notes(fake_client, 15, text="Call the client on 082 555 1234 or client@example.com")
    _seed_tenant_config(fake_client)

    captured = {}

    async def _fake_route(*a, **kw):
        return ("fake-model", None, None)
    monkeypatch.setattr("core.llm_router.resolve_generation_route", _fake_route)

    class _Msg:
        content = "Direct and to the point."

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    async def _fake_completion(*a, **kw):
        captured["prompt"] = kw["messages"][0]["content"]
        return _Resp()
    monkeypatch.setattr("litellm.acompletion", _fake_completion)

    await vp.analyze_voice(TID)
    assert "082 555 1234" not in captured["prompt"]
    assert "client@example.com" not in captured["prompt"]
    assert "[phone]" in captured["prompt"]
    assert "[email]" in captured["prompt"]

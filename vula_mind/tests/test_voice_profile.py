"""Tests for tenant voice-profile learning (migration 119).

Uses a minimal in-memory fake Supabase client, matching the pattern in tests/test_flows.py —
good enough to prove the sample-size guard, the analysis happy path (LLM mocked), and that a
suggestion is persisted, without needing a real database or model.
"""
import uuid
from datetime import datetime, timezone

import pytest

import vula.commerce.voice_profile as vp


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self._limit = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self.filters.append((key, val))
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        return all(row.get(k) == v for k, v in self.filters)

    def execute(self):
        rows = [r for r in self.table.rows if self._matches(r)]
        if self._limit:
            rows = rows[: self._limit]
        return _Result(rows)

    def update(self, patch):
        self._patch = patch
        return self


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


def _seed_messages(client, n, role="agent", text="Howzit, thanks so much! Ready whenever you are 🙏"):
    rows = client.store.setdefault("commerce_conversation_messages", [])
    for i in range(n):
        rows.append({
            "id": str(uuid.uuid4()), "tenant_id": TID, "role": role,
            "content": f"{text} ({i})", "created_at": datetime.now(timezone.utc).isoformat(),
        })


def _seed_tenant_config(client):
    client.store.setdefault("vula_tenant_config", []).append({"tenant_id": TID})


@pytest.mark.asyncio
async def test_analyze_voice_rejects_too_few_samples(fake_client):
    _seed_messages(fake_client, 5)
    result = await vp.analyze_voice(TID)
    assert "error" in result
    assert "Not enough data" in result["error"]


@pytest.mark.asyncio
async def test_analyze_voice_ignores_non_agent_roles(fake_client):
    _seed_messages(fake_client, 20, role="assistant")
    result = await vp.analyze_voice(TID)
    assert "error" in result
    assert "Not enough data" in result["error"]


@pytest.mark.asyncio
async def test_analyze_voice_success_stores_suggestion(fake_client, monkeypatch):
    _seed_messages(fake_client, 20)
    _seed_tenant_config(fake_client)

    async def _fake_route(*a, **kw):
        return ("fake-model", None, None)
    monkeypatch.setattr("core.llm_router.resolve_generation_route", _fake_route)

    class _Msg:
        content = "Warm and casual, uses 'howzit', short replies, emojis welcome."

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    async def _fake_completion(*a, **kw):
        return _Resp()
    monkeypatch.setattr("litellm.acompletion", _fake_completion)

    result = await vp.analyze_voice(TID)
    assert "error" not in result
    assert result["suggested"] == "Warm and casual, uses 'howzit', short replies, emojis welcome."
    assert result["sample_count"] == 20

    row = fake_client.store["vula_tenant_config"][0]
    assert row["persona_prompt_suggested"] == result["suggested"]
    assert row["persona_prompt_suggested_at"]


@pytest.mark.asyncio
async def test_sample_count_reports_agent_rows_only(fake_client):
    _seed_messages(fake_client, 12, role="agent")
    _seed_messages(fake_client, 8, role="assistant")
    assert await vp.sample_count(TID) == 12

"""Tests for the owner-configurable WhatsApp conversation flow builder (migration 118).

Uses a minimal in-memory fake Supabase client (table -> list of dict rows) supporting only the
exact chainable operations vula/commerce/flows.py issues — good enough to prove the runtime
logic (trigger match, step advance, branching, idle abandon, terminal action dispatch) without
needing a real database.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import vula.commerce.flows as flows_mod


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self._order = None
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

    def insert(self, row):
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("fire_count", 0)
        row.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        self.table.rows.append(row)
        return _ExecWrapper([row])

    def update(self, patch):
        self._patch = patch
        return self

    def delete(self):
        self._delete = True
        return self


class _ExecWrapper:
    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return _Result(self._rows)


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


class _FakeQueryWithUpdate(_FakeQuery):
    """Extends _FakeQuery so update()/delete() actually mutate matching rows on execute()."""

    def execute(self):
        rows = [r for r in self.table.rows if self._matches(r)]
        if hasattr(self, "_patch"):
            for r in rows:
                r.update(self._patch)
        if hasattr(self, "_delete"):
            for r in rows:
                self.table.rows.remove(r)
        if self._limit:
            rows = rows[: self._limit]
        return _Result(rows)


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(flows_mod, "_client", lambda: client)
    return client


TID = "test-tenant"
PHONE = "27821234567"

BOOK_A_DEMO_FLOW = {
    "tenant_id": TID,
    "name": "Book a Demo",
    "trigger_phrases": ["book a demo"],
    "enabled": True,
    "steps": [
        {"prompt": "Which location?", "reply_type": "multiple_choice",
         "options": ["Cape Town", "Durban"], "save_as": "location", "branches": {}, "default_next": 1},
        {"prompt": "What date?", "reply_type": "free_text",
         "save_as": "date", "branches": {}, "default_next": None},
    ],
    "end_action": {"tool": "create_booking",
                   "arg_map": {"start": "{{date}}", "service": "Demo — {{location}}"},
                   "closing_message": "Booked for {{date}} at {{location}}!"},
    "fire_count": 0,
}


def _seed_flow(client, flow=BOOK_A_DEMO_FLOW):
    row = dict(flow)
    row["id"] = str(uuid.uuid4())
    row["created_at"] = datetime.now(timezone.utc).isoformat()
    client.store.setdefault("commerce_flows", []).append(row)
    return row


@pytest.mark.asyncio
async def test_maybe_start_flow_matches_trigger_phrase(fake_client):
    _seed_flow(fake_client)
    reply = await flows_mod.maybe_start_flow(TID, PHONE, "I'd like to book a demo please")
    assert reply is not None
    assert "Which location" in reply
    sessions = fake_client.store.get("commerce_flow_sessions", [])
    assert len(sessions) == 1
    assert sessions[0]["current_step_index"] == 0
    assert sessions[0]["status"] == "active"


@pytest.mark.asyncio
async def test_maybe_start_flow_no_match_returns_none(fake_client):
    _seed_flow(fake_client)
    reply = await flows_mod.maybe_start_flow(TID, PHONE, "hello there")
    assert reply is None
    assert fake_client.store.get("commerce_flow_sessions", []) == []


@pytest.mark.asyncio
async def test_maybe_continue_flow_advances_and_completes(fake_client, monkeypatch):
    flow = _seed_flow(fake_client)
    await flows_mod.maybe_start_flow(TID, PHONE, "book a demo")

    # Step 0 answer (multiple_choice) -> advances to step 1.
    reply = await flows_mod.maybe_continue_flow(TID, PHONE, "Cape Town")
    assert reply == "What date?"
    session = fake_client.store["commerce_flow_sessions"][0]
    assert session["current_step_index"] == 1
    assert session["collected_answers"]["location"] == "Cape Town"

    # Step 1 answer -> last step, default_next=None -> flow completes, end_action dispatched.
    captured = {}

    class _FakeSkill:
        async def _dispatch_tool(self, name, args, ctx):
            captured["name"] = name
            captured["args"] = args
            captured["ctx"] = ctx
            return {"booked": True}

    monkeypatch.setattr("core.skills.loader.get_skill", lambda n: _FakeSkill())

    reply2 = await flows_mod.maybe_continue_flow(TID, PHONE, "2026-08-05")
    assert reply2 == "Booked for 2026-08-05 at Cape Town!"
    assert captured["name"] == "create_booking"
    assert captured["args"]["start"] == "2026-08-05"
    assert captured["args"]["service"] == "Demo — Cape Town"

    session = fake_client.store["commerce_flow_sessions"][0]
    assert session["status"] == "completed"


@pytest.mark.asyncio
async def test_maybe_continue_flow_no_active_session_returns_none(fake_client):
    reply = await flows_mod.maybe_continue_flow(TID, PHONE, "anything")
    assert reply is None


@pytest.mark.asyncio
async def test_idle_session_auto_abandons(fake_client):
    flow = _seed_flow(fake_client)
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    fake_client.store["commerce_flow_sessions"] = [{
        "id": str(uuid.uuid4()), "tenant_id": TID, "phone": PHONE, "flow_id": flow["id"],
        "current_step_index": 0, "collected_answers": {}, "status": "active",
        "updated_at": old,
    }]
    reply = await flows_mod.maybe_continue_flow(TID, PHONE, "still here?")
    assert reply is None
    session = fake_client.store["commerce_flow_sessions"][0]
    assert session["status"] == "abandoned"


# ── Validation ──────────────────────────────────────────────────────────────

def test_create_flow_rejects_missing_default_next_with_branches(fake_client):
    body = {
        "name": "Bad flow", "trigger_phrases": ["hi there"],
        "steps": [{"prompt": "Yes or no?", "reply_type": "yes_no",
                  "branches": {"yes": 1}, "default_next": None}],
        "end_action": {"tool": "notify_team"},
    }
    with pytest.raises(ValueError, match="default_next"):
        flows_mod.create_flow(TID, body)


def test_create_flow_rejects_unknown_end_action_tool(fake_client):
    body = {
        "name": "Bad flow", "trigger_phrases": ["hi there"],
        "steps": [{"prompt": "Q1", "reply_type": "free_text", "save_as": "a"}],
        "end_action": {"tool": "delete_everything"},
    }
    with pytest.raises(ValueError, match="end_action.tool"):
        flows_mod.create_flow(TID, body)


def test_create_flow_success(fake_client):
    body = {
        "name": "Simple flow", "trigger_phrases": ["quote please"],
        "steps": [{"prompt": "What's your budget?", "reply_type": "free_text", "save_as": "budget"}],
        "end_action": {"tool": "create_reminder", "arg_map": {"text": "Follow up on {{budget}} quote"},
                      "closing_message": "Thanks!"},
    }
    row = flows_mod.create_flow(TID, body)
    assert row.get("name") == "Simple flow"
    assert row.get("trigger_phrases") == ["quote please"]

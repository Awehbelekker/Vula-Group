"""Tests for vula/commerce/automations.py's approval gate (migration 137) and conversational
rule authoring ("teaching mode"). The core safety property under test: a trigger match must
never send a WhatsApp message on its own — it stages a pending firing, and only approve_firing
(an explicit owner action) can actually call _run_action / send anything.
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import automations

TID = "test-tenant"


class _FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self._filters = []
        self._op = None
        self._payload = None

    def select(self, *a):
        self._op = self._op or "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, k, v):
        self._filters.append((k, v))
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        return self.db._execute(self.name, self._op, self._payload, self._filters)


class _FakeDB:
    def __init__(self, data=None):
        self.data = {k: list(v) for k, v in (data or {}).items()}
        self.inserted = []

    def table(self, name):
        return _FakeTable(self, name)

    def _execute(self, table, op, payload, filters):
        rows = self.data.get(table, [])
        if op == "select":
            result = [r for r in rows if all(r.get(k) == v for k, v in filters)]
            return type("R", (), {"data": result})()
        if op == "insert":
            new_row = dict(payload)
            new_row.setdefault("id", f"{table}-{len(rows) + 1}")
            new_row.setdefault("status", "pending")
            rows.append(new_row)
            self.data[table] = rows
            self.inserted.append((table, new_row))
            return type("R", (), {"data": [new_row]})()
        if op == "update":
            matched = [r for r in rows if all(r.get(k) == v for k, v in filters)]
            for r in matched:
                r.update(payload)
            return type("R", (), {"data": matched})()
        if op == "delete":
            matched = [r for r in rows if all(r.get(k) == v for k, v in filters)]
            self.data[table] = [r for r in rows if r not in matched]
            return type("R", (), {"data": matched})()
        return type("R", (), {"data": []})()


# ── _stage_firing: a match never sends, only stages ──────────────────────────────

def test_stage_firing_inserts_pending_row_and_never_sends():
    db = _FakeDB()
    automation = {"id": "auto-1", "action_type": "whatsapp_customer",
                  "action_config": {"message": "Hi {{customer_name}}, your order is {{status}}"}}
    ctx = {"customer_phone": "27821234567", "customer_name": "Jane", "status": "dispatched"}
    with patch("vula.commerce.service._client", return_value=db), \
         patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_send:
        result = automations._stage_firing(TID, automation, ctx)
    assert result is True
    mock_send.assert_not_called()
    assert len(db.data["commerce_automation_firings"]) == 1
    row = db.data["commerce_automation_firings"][0]
    assert row["message"] == "Hi Jane, your order is dispatched"
    assert row["automation_id"] == "auto-1"


def test_stage_firing_returns_false_on_empty_template():
    db = _FakeDB()
    automation = {"id": "auto-1", "action_type": "whatsapp_customer", "action_config": {"message": ""}}
    with patch("vula.commerce.service._client", return_value=db):
        assert automations._stage_firing(TID, automation, {}) is False
    assert db.data.get("commerce_automation_firings", []) == []


# ── approve_firing / reject_firing ────────────────────────────────────────────────

def _seed_pending_firing(db, **overrides):
    row = {"id": "f1", "tenant_id": TID, "automation_id": "auto-1", "status": "pending",
           "action_type": "whatsapp_customer",
           "action_config": {"message": "Hi {{customer_name}}"},
           "trigger_context": {"customer_phone": "27821234567", "customer_name": "Jane"},
           "message": "Hi Jane"}
    row.update(overrides)
    db.data["commerce_automation_firings"] = [row]
    return row


@pytest.mark.asyncio
async def test_approve_firing_sends_and_marks_approved():
    db = _FakeDB()
    _seed_pending_firing(db)
    with patch("vula.commerce.service._client", return_value=db), \
         patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send:
        result = await automations.approve_firing(TID, "f1")
    mock_send.assert_awaited_once_with("27821234567", "Hi Jane", tenant_id=TID)
    assert result["status"] == "approved"


@pytest.mark.asyncio
async def test_approve_firing_is_idempotent_no_double_send():
    db = _FakeDB()
    _seed_pending_firing(db, status="approved")  # already decided
    with patch("vula.commerce.service._client", return_value=db), \
         patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send:
        result = await automations.approve_firing(TID, "f1")
    mock_send.assert_not_awaited()
    assert result["status"] == "approved"


@pytest.mark.asyncio
async def test_approve_firing_missing_row_returns_error():
    db = _FakeDB()
    with patch("vula.commerce.service._client", return_value=db):
        result = await automations.approve_firing(TID, "nope")
    assert "error" in result


def test_reject_firing_never_sends_and_marks_rejected():
    db = _FakeDB()
    _seed_pending_firing(db)
    with patch("vula.commerce.service._client", return_value=db), \
         patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_send:
        result = automations.reject_firing(TID, "f1")
    mock_send.assert_not_called()
    assert result["status"] == "rejected"


# ── list_pending_firings ──────────────────────────────────────────────────────────

def test_list_pending_firings_filters_by_tenant_and_status():
    db = _FakeDB()
    db.data["commerce_automation_firings"] = [
        {"id": "f1", "tenant_id": TID, "status": "pending"},
        {"id": "f2", "tenant_id": TID, "status": "approved"},
        {"id": "f3", "tenant_id": "other-tenant", "status": "pending"},
    ]
    with patch("vula.commerce.service._client", return_value=db):
        result = automations.list_pending_firings(TID)
    assert [r["id"] for r in result] == ["f1"]


# ── _validate_parsed_rule: whitelist enforcement on the LLM's raw output ──────────

def test_validate_parsed_rule_rejects_unknown_trigger_type():
    result = automations._validate_parsed_rule(
        {"trigger_type": "email_received", "action_type": "whatsapp_customer",
         "action_config": {"message": "hi"}})
    assert "error" in result


def test_validate_parsed_rule_rejects_unknown_action_type():
    result = automations._validate_parsed_rule(
        {"trigger_type": "order_status", "trigger_config": {"to_status": "paid"},
         "action_type": "send_email", "action_config": {"message": "hi"}})
    assert "error" in result


def test_validate_parsed_rule_rejects_whatsapp_customer_on_low_stock():
    result = automations._validate_parsed_rule(
        {"trigger_type": "low_stock", "action_type": "whatsapp_customer",
         "action_config": {"message": "hi"}})
    assert "error" in result


def test_validate_parsed_rule_rejects_invalid_order_status():
    result = automations._validate_parsed_rule(
        {"trigger_type": "order_status", "trigger_config": {"to_status": "teleported"},
         "action_type": "whatsapp_team", "action_config": {"message": "hi"}})
    assert "error" in result


def test_validate_parsed_rule_rejects_missing_message():
    result = automations._validate_parsed_rule(
        {"trigger_type": "low_stock", "action_type": "whatsapp_team", "action_config": {}})
    assert "error" in result


def test_validate_parsed_rule_accepts_clean_valid_input():
    result = automations._validate_parsed_rule(
        {"name": "Notify on dispatch", "trigger_type": "order_status",
         "trigger_config": {"to_status": "dispatched"}, "action_type": "whatsapp_customer",
         "action_config": {"message": "Your order {{order_id}} is on its way!"}})
    assert result == {
        "name": "Notify on dispatch", "trigger_type": "order_status",
        "trigger_config": {"to_status": "dispatched"}, "action_type": "whatsapp_customer",
        "action_config": {"message": "Your order {{order_id}} is on its way!"},
    }


def test_validate_parsed_rule_passes_through_llm_reported_error():
    result = automations._validate_parsed_rule({"error": "that's not something I can automate"})
    assert result == {"error": "that's not something I can automate"}


# ── parse_rule_from_text: end-to-end (mocked LLM) ─────────────────────────────────

def _mock_llm_response(content: str):
    msg = type("M", (), {"content": content})()
    choice = type("C", (), {"message": msg})()
    return type("R", (), {"choices": [choice]})()


@pytest.mark.asyncio
async def test_parse_rule_from_text_creates_automation_on_valid_response():
    db = _FakeDB()
    valid_json = ('{"name": "Dispatch notice", "trigger_type": "order_status", '
                  '"trigger_config": {"to_status": "dispatched"}, '
                  '"action_type": "whatsapp_customer", '
                  '"action_config": {"message": "On its way!"}}')
    with patch("vula.commerce.service._client", return_value=db), \
         patch("core.llm_router.resolve_generation_route",
               new=AsyncMock(return_value=("ollama/x", None, None))), \
         patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm_response(valid_json))):
        result = await automations.parse_rule_from_text(
            TID, "when an order is dispatched, message the customer")
    assert "error" not in result
    assert result["trigger_type"] == "order_status"
    assert result["created_from"] == "conversation"
    assert len(db.inserted) == 1


@pytest.mark.asyncio
async def test_parse_rule_from_text_rejects_out_of_whitelist_response_without_creating():
    db = _FakeDB()
    bad_json = ('{"trigger_type": "email_received", "action_type": "send_email", '
                '"action_config": {"message": "hi"}}')
    with patch("vula.commerce.service._client", return_value=db), \
         patch("core.llm_router.resolve_generation_route",
               new=AsyncMock(return_value=("ollama/x", None, None))), \
         patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm_response(bad_json))):
        result = await automations.parse_rule_from_text(TID, "email me when something happens")
    assert "error" in result
    assert db.inserted == []


@pytest.mark.asyncio
async def test_parse_rule_from_text_handles_malformed_json_gracefully():
    db = _FakeDB()
    with patch("vula.commerce.service._client", return_value=db), \
         patch("core.llm_router.resolve_generation_route",
               new=AsyncMock(return_value=("ollama/x", None, None))), \
         patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm_response("not json at all"))):
        result = await automations.parse_rule_from_text(TID, "do the thing")
    assert "error" in result
    assert db.inserted == []


@pytest.mark.asyncio
async def test_parse_rule_from_text_empty_description_is_an_error():
    result = await automations.parse_rule_from_text(TID, "   ")
    assert "error" in result

"""Tests for vula/commerce/call_sheet.py — the persistent, editable per-rep weekly call sheet
(migration 138). Uses the same in-memory fake Supabase client pattern as
tests/test_purchase_orders.py.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import vula.commerce.call_sheet as cs_mod

TID = "test-tenant"


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self._limit = None
        self._patch = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self.filters.append((key, val))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        return all(row.get(k) == v for k, v in self.filters)

    def insert(self, row):
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("created_at", "2026-08-25T00:00:00Z")
        self.table.rows.append(row)
        return _ExecWrapper([row])

    def update(self, patch_dict):
        self._patch = patch_dict
        return self

    def execute(self):
        rows = [r for r in self.table.rows if self._matches(r)]
        if self._patch is not None:
            for r in rows:
                r.update(self._patch)
        if self._limit:
            rows = rows[: self._limit]
        return _Result(rows)


class _ExecWrapper:
    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return _Result(self._rows)


class _FakeTable:
    def __init__(self, name, store):
        self.rows = store.setdefault(name, [])


class _FakeClient:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _FakeQuery(_FakeTable(name, self.store))


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(cs_mod, "_client", lambda: client)
    return client


def _rep(**over):
    row = {"id": str(uuid.uuid4()), "tenant_id": TID, "whatsapp": "27821234567", "name": "Ian",
           "role": "sales_rep", "active": True,
           "call_sheet_recipient_email": "sarah@gerflor.co.za", "call_sheet_recipient_phone": None,
           "call_sheet_channel": "email", "call_sheet_day_of_week": 4,
           "call_sheet_hour": 17, "call_sheet_minute": 0, "call_sheet_last_sent_at": None}
    row.update(over)
    return row


# ── get_or_create_open_call_sheet / append_entry ─────────────────────────────────

def test_append_entry_creates_open_sheet_and_appends(fake_client):
    row = cs_mod.append_entry(TID, "27821234567", "log_meeting", "Met Dick about flooring")
    assert row["status"] == "open"
    assert len(row["entries"]) == 1
    assert row["entries"][0]["text"] == "Met Dick about flooring"
    assert row["entries"][0]["source"] == "log_meeting"


def test_second_append_reuses_same_open_sheet(fake_client):
    cs_mod.append_entry(TID, "27821234567", "log_meeting", "First meeting")
    row = cs_mod.append_entry(TID, "27821234567", "log_meeting", "Second meeting")
    assert len(row["entries"]) == 2
    open_rows = [r for r in fake_client.store["vula_call_sheets"]
                 if r["tenant_id"] == TID and r["status"] == "open"]
    assert len(open_rows) == 1


def test_different_reps_get_separate_open_sheets(fake_client):
    cs_mod.append_entry(TID, "27821111111", "log_meeting", "Rep A meeting")
    cs_mod.append_entry(TID, "27822222222", "log_meeting", "Rep B meeting")
    a = cs_mod.get_or_create_open_call_sheet(TID, "27821111111")
    b = cs_mod.get_or_create_open_call_sheet(TID, "27822222222")
    assert a["id"] != b["id"]
    assert len(a["entries"]) == 1 and len(b["entries"]) == 1


# ── apply_edit ────────────────────────────────────────────────────────────────────

def test_apply_edit_add(fake_client):
    row = cs_mod.apply_edit(TID, "27821234567", "add", None, "Sarah wants a Q4 review")
    assert len(row["entries"]) == 1
    assert row["entries"][0]["source"] == "manual"
    assert row["entries"][0]["text"] == "Sarah wants a Q4 review"


def test_apply_edit_edit_updates_matching_entry(fake_client):
    row = cs_mod.append_entry(TID, "27821234567", "log_meeting", "Wrong summary")
    entry_id = row["entries"][0]["id"]
    updated = cs_mod.apply_edit(TID, "27821234567", "edit", entry_id, "Correct summary")
    assert updated["entries"][0]["text"] == "Correct summary"
    assert len(updated["entries"]) == 1


def test_apply_edit_remove_deletes_entry(fake_client):
    row = cs_mod.append_entry(TID, "27821234567", "log_meeting", "To be removed")
    entry_id = row["entries"][0]["id"]
    updated = cs_mod.apply_edit(TID, "27821234567", "remove", entry_id, None)
    assert updated["entries"] == []


# ── format_call_sheet ─────────────────────────────────────────────────────────────

def test_format_call_sheet_empty():
    text = cs_mod.format_call_sheet("Ian", [])
    assert "No entries" in text


def test_format_call_sheet_lists_entries_numbered():
    entries = [{"text": "Met Dick", "created_at": "2026-08-20T10:00:00+00:00"},
               {"text": "Met Sarah", "created_at": "2026-08-22T10:00:00+00:00"}]
    text = cs_mod.format_call_sheet("Ian", entries)
    assert "1. " in text and "2. " in text
    assert "Met Dick" in text and "Met Sarah" in text
    assert "2 entries" in text


# ── is_due ────────────────────────────────────────────────────────────────────────

def test_is_due_true_on_configured_day_and_hour():
    rep = _rep(call_sheet_day_of_week=4, call_sheet_hour=17)  # Friday 17:00
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)  # a Friday, past 17:00
    assert now.weekday() == 4
    assert cs_mod.is_due(rep, now) is True


def test_is_due_false_on_wrong_day():
    rep = _rep(call_sheet_day_of_week=4, call_sheet_hour=17)
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)  # Thursday
    assert cs_mod.is_due(rep, now) is False


def test_is_due_false_before_configured_hour():
    rep = _rep(call_sheet_day_of_week=4, call_sheet_hour=17)
    now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)  # Friday, but only 10:00
    assert cs_mod.is_due(rep, now) is False


def test_is_due_false_if_already_sent_within_24h():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    rep = _rep(call_sheet_day_of_week=4, call_sheet_hour=17,
               call_sheet_last_sent_at=(now - timedelta(hours=2)).isoformat())
    assert cs_mod.is_due(rep, now) is False


def test_is_due_true_if_last_sent_over_a_week_ago():
    now = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
    rep = _rep(call_sheet_day_of_week=4, call_sheet_hour=17,
               call_sheet_last_sent_at=(now - timedelta(days=8)).isoformat())
    assert cs_mod.is_due(rep, now) is True


# ── send_call_sheet ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_call_sheet_email_only_success(fake_client, monkeypatch):
    cs_mod.append_entry(TID, "27821234567", "log_meeting", "Met Dick about flooring")
    monkeypatch.setattr("vula.email_imap.credentials.get_email_creds", lambda tid: {"email": "rep@gerflor.co.za"})

    sent = {}

    async def _fake_send(creds, to, subject, body):
        sent["to"] = to
        sent["body"] = body
        return {"sent": True}
    monkeypatch.setattr("vula.email_imap.service.send", _fake_send)

    rep = _rep()
    result = await cs_mod.send_call_sheet(TID, rep)
    assert result["email"] is True
    assert result["whatsapp"] is None  # not configured, not attempted
    assert result["meeting_count"] == 1
    assert sent["to"] == "sarah@gerflor.co.za"
    assert "Met Dick about flooring" in sent["body"]


@pytest.mark.asyncio
async def test_send_call_sheet_no_connected_email_degrades(fake_client, monkeypatch):
    cs_mod.append_entry(TID, "27821234567", "log_meeting", "Some meeting")
    monkeypatch.setattr("vula.email_imap.credentials.get_email_creds", lambda tid: None)
    rep = _rep()
    result = await cs_mod.send_call_sheet(TID, rep)
    assert result["email"] is False


@pytest.mark.asyncio
async def test_send_call_sheet_whatsapp_leg_no_template_degrades_without_raising(fake_client, monkeypatch):
    async def _fake_wa_template(*a, **kw):
        return False  # template doesn't exist / isn't approved
    monkeypatch.setattr("vula.api.whatsapp._send_wa_template", _fake_wa_template)

    rep = _rep(call_sheet_channel="whatsapp", call_sheet_recipient_email=None,
               call_sheet_recipient_phone="27821234567")
    result = await cs_mod.send_call_sheet(TID, rep)
    assert result["whatsapp"] is False
    assert result["email"] is None  # channel wasn't email/both


@pytest.mark.asyncio
async def test_send_call_sheet_both_channels_one_fails_other_still_attempted(fake_client, monkeypatch):
    monkeypatch.setattr("vula.email_imap.credentials.get_email_creds", lambda tid: {"email": "rep@gerflor.co.za"})

    async def _fake_send(creds, to, subject, body):
        return {"sent": True}
    monkeypatch.setattr("vula.email_imap.service.send", _fake_send)

    async def _fake_wa_template(*a, **kw):
        return False
    monkeypatch.setattr("vula.api.whatsapp._send_wa_template", _fake_wa_template)

    rep = _rep(call_sheet_channel="both", call_sheet_recipient_phone="27821234567")
    result = await cs_mod.send_call_sheet(TID, rep)
    assert result["email"] is True
    assert result["whatsapp"] is False


# ── run_weekly_call_sheets ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_weekly_call_sheets_skips_rep_with_no_recipient(fake_client):
    fake_client.store["vula_team_members"] = [_rep(call_sheet_recipient_email=None, call_sheet_recipient_phone=None)]
    sent = await cs_mod.run_weekly_call_sheets()
    assert sent == 0


@pytest.mark.asyncio
async def test_run_weekly_call_sheets_skips_rep_not_due(fake_client, monkeypatch):
    monkeypatch.setattr(cs_mod, "is_due", lambda rep, now: False)
    fake_client.store["vula_team_members"] = [_rep()]
    called = {"n": 0}

    async def _fake_send(*a, **kw):
        called["n"] += 1
        return {}
    monkeypatch.setattr(cs_mod, "send_call_sheet", _fake_send)
    await cs_mod.run_weekly_call_sheets()
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_run_weekly_call_sheets_sends_and_stamps_when_due(fake_client, monkeypatch):
    monkeypatch.setattr(cs_mod, "is_due", lambda rep, now: True)
    rep_row = _rep()
    fake_client.store["vula_team_members"] = [rep_row]
    sheet_row = cs_mod.get_or_create_open_call_sheet(TID, rep_row["whatsapp"])

    async def _fake_send(tenant_id, rep):
        return {"email": True, "whatsapp": None, "meeting_count": 0, "sheet_id": sheet_row["id"]}
    monkeypatch.setattr(cs_mod, "send_call_sheet", _fake_send)

    sent = await cs_mod.run_weekly_call_sheets()
    assert sent == 1
    updated_rep = [r for r in fake_client.store["vula_team_members"] if r["id"] == rep_row["id"]][0]
    assert updated_rep["call_sheet_last_sent_at"] is not None
    updated_sheet = [r for r in fake_client.store["vula_call_sheets"] if r["id"] == sheet_row["id"]][0]
    assert updated_sheet["status"] == "sent"


@pytest.mark.asyncio
async def test_run_weekly_call_sheets_empty_digest_still_stamps_due_cycle(fake_client, monkeypatch):
    # An explicit day/time is a calendar slot, not a rolling "n days since last content" — a due
    # cycle with nothing logged still counts as handled, so the rep isn't re-checked hourly.
    monkeypatch.setattr(cs_mod, "is_due", lambda rep, now: True)
    rep_row = _rep()
    fake_client.store["vula_team_members"] = [rep_row]
    sheet_row = cs_mod.get_or_create_open_call_sheet(TID, rep_row["whatsapp"])

    async def _fake_send(tenant_id, rep):
        return {"email": False, "whatsapp": None, "meeting_count": 0, "sheet_id": sheet_row["id"]}
    monkeypatch.setattr(cs_mod, "send_call_sheet", _fake_send)

    await cs_mod.run_weekly_call_sheets()
    updated_rep = [r for r in fake_client.store["vula_team_members"] if r["id"] == rep_row["id"]][0]
    assert updated_rep["call_sheet_last_sent_at"] is not None


@pytest.mark.asyncio
async def test_run_weekly_call_sheets_one_rep_failure_does_not_block_another(fake_client, monkeypatch):
    monkeypatch.setattr(cs_mod, "is_due", lambda rep, now: True)
    rep_a = _rep(id=str(uuid.uuid4()), whatsapp="27821111111")
    rep_b = _rep(id=str(uuid.uuid4()), whatsapp="27822222222")
    fake_client.store["vula_team_members"] = [rep_a, rep_b]
    sheet_b = cs_mod.get_or_create_open_call_sheet(TID, rep_b["whatsapp"])

    async def _fake_send(tenant_id, rep):
        if rep["whatsapp"] == "27821111111":
            raise RuntimeError("boom")
        return {"email": True, "whatsapp": None, "meeting_count": 0, "sheet_id": sheet_b["id"]}
    monkeypatch.setattr(cs_mod, "send_call_sheet", _fake_send)

    sent = await cs_mod.run_weekly_call_sheets()
    assert sent == 1
    updated_b = [r for r in fake_client.store["vula_team_members"] if r["whatsapp"] == "27822222222"][0]
    assert updated_b["call_sheet_last_sent_at"] is not None
    updated_a = [r for r in fake_client.store["vula_team_members"] if r["whatsapp"] == "27821111111"][0]
    assert updated_a["call_sheet_last_sent_at"] is None


# ── parse_update_instruction ──────────────────────────────────────────────────────

def _resp(content):
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


async def _fake_route(*a, **kw):
    return ("openrouter/test", "k", None)


@pytest.mark.asyncio
async def test_parse_update_instruction_add():
    async def fake_completion(*a, **kw):
        return _resp('{"action": "add", "text": "Sarah wants a Q4 review"}')

    with (
        patch("core.llm_router.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=fake_completion),
    ):
        result = await cs_mod.parse_update_instruction([], "add a note that Sarah wants a Q4 review")
    assert result == {"action": "add", "entry_id": None, "text": "Sarah wants a Q4 review"}


@pytest.mark.asyncio
async def test_parse_update_instruction_edit_requires_valid_entry_id():
    entries = [{"id": "abc123", "text": "Met Dick about HBC"}]

    async def fake_completion(*a, **kw):
        return _resp('{"action": "edit", "entry_id": "does-not-exist", "text": "corrected"}')

    with (
        patch("core.llm_router.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=fake_completion),
    ):
        result = await cs_mod.parse_update_instruction(entries, "fix the Dick meeting")
    assert "error" in result


@pytest.mark.asyncio
async def test_parse_update_instruction_rejects_out_of_vocabulary_action():
    async def fake_completion(*a, **kw):
        return _resp('{"action": "delete_everything", "text": "x"}')

    with (
        patch("core.llm_router.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=fake_completion),
    ):
        result = await cs_mod.parse_update_instruction([], "wipe it all")
    assert "error" in result


@pytest.mark.asyncio
async def test_parse_update_instruction_passes_through_llm_error():
    async def fake_completion(*a, **kw):
        return _resp('{"error": "too vague to act on"}')

    with (
        patch("core.llm_router.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=fake_completion),
    ):
        result = await cs_mod.parse_update_instruction([], "do the thing")
    assert result == {"error": "too vague to act on"}

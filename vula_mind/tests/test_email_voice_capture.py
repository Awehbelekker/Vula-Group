"""Tests for capturing Sent-folder voice samples (migration 120).

vula/email_imap/sync.py fetches Sent-folder bodies to build contacts/resolve follow-ups, then
(as of this feature) also keeps a short, redacted excerpt of genuinely human-composed sent mail
for voice_profile.py to learn from — AI-composed mail (tagged via _build()'s X-Vula-Sent header)
must never be captured, and quoted thread history must be stripped first.
"""
import pytest

from vula.email_imap.service import _build
from vula.email_imap.sync import _capture_voice_samples, _strip_quoted


def test_build_tags_every_composed_message_as_vula_sent():
    creds = {"email": "owner@example.com"}
    msg = _build(creds, "customer@example.com", "Re: Order", "Thanks for your order!")
    assert msg["X-Vula-Sent"] == "1"


def test_strip_quoted_removes_reply_chain():
    body = (
        "Thanks so much, that works for me!\n\n"
        "On Mon, 3 Aug 2026 at 10:00, Supplier <supplier@example.com> wrote:\n"
        "> Can you confirm the delivery date?\n"
    )
    assert _strip_quoted(body) == "Thanks so much, that works for me!"


def test_strip_quoted_leaves_body_unchanged_when_no_quote_marker():
    body = "All good, see you Friday!"
    assert _strip_quoted(body) == body


class _FakeInsert:
    def __init__(self, table):
        self.table = table

    def execute(self):
        return self

    def __call__(self, rows):
        self.table.rows.extend(rows)
        return self


class _FakeTable:
    def __init__(self):
        self.rows = []

    def insert(self, rows):
        self.rows.extend(rows)
        return self

    def execute(self):
        return self


class _FakeDb:
    def __init__(self):
        self._table = _FakeTable()

    def table(self, name):
        assert name == "vula_email_voice_samples"
        return self._table


@pytest.mark.asyncio
async def test_capture_voice_samples_excludes_ai_sent_and_short_bodies():
    db = _FakeDb()
    emails = [
        {"body": "Thanks so much, appreciate the quick turnaround!", "vula_sent": False},
        {"body": "AI-composed reply the owner auto-sent.", "vula_sent": True},
        {"body": "Hi", "vula_sent": False},  # too short, dropped
    ]
    await _capture_voice_samples(db, "test-tenant", emails)
    assert len(db._table.rows) == 1
    assert db._table.rows[0]["tenant_id"] == "test-tenant"
    assert "Thanks so much" in db._table.rows[0]["excerpt"]


@pytest.mark.asyncio
async def test_capture_voice_samples_strips_quotes_before_storing():
    db = _FakeDb()
    emails = [{
        "body": "Sounds good, see you then!\n\nOn Tue wrote:\n> original message here",
        "vula_sent": False,
    }]
    await _capture_voice_samples(db, "test-tenant", emails)
    assert db._table.rows[0]["excerpt"] == "Sounds good, see you then!"


@pytest.mark.asyncio
async def test_capture_voice_samples_noop_on_no_candidates():
    db = _FakeDb()
    await _capture_voice_samples(db, "test-tenant", [{"body": "AI text", "vula_sent": True}])
    assert db._table.rows == []

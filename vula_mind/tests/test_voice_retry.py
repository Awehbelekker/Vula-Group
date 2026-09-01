"""Never lose a voice order to a transcription outage.

Real telemetry (2026-09-01): 6 of 23 voice notes ever received — 26% — were lost to a bare 530
from the local Whisper tunnel (the SA GPU unreachable, never an actual transcription failure).
The customer was told "please type it out", which for a food order often means no order at all.
The deliberate design choice is to retry LOCALLY, so customer audio never leaves Vula's own
infrastructure and an outage costs a delay rather than an order.
"""
import base64
from unittest.mock import patch

from vula import voice_retry


class _Q:
    def __init__(self, store, rows=None, raise_on_insert=None):
        self.store = store
        self._rows = rows if rows is not None else []
        self._raise = raise_on_insert

    def insert(self, row):
        if self._raise:
            raise self._raise
        self.store.setdefault("inserted", []).append(row)
        return self

    def update(self, patch_):
        self.store.setdefault("updated", []).append(patch_)
        return self

    def delete(self):
        self.store["deleted"] = True
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _db(store, rows=None, raise_on_insert=None):
    return type("C", (), {"table": lambda self, n: _Q(store, rows, raise_on_insert)})()


# ── enqueue ─────────────────────────────────────────────────────────────────────

def test_enqueue_stores_the_audio_for_a_later_retry():
    store = {}
    with patch.object(voice_retry, "_client", lambda: _db(store)):
        assert voice_retry.enqueue("off-the-hook", "27645755210", b"OggS-audio",
                                   msg_id="wamid.1") is True
    row = store["inserted"][0]
    assert row["tenant_id"] == "off-the-hook"
    assert row["status"] == "pending"
    assert base64.b64decode(row["audio_b64"]) == b"OggS-audio"


def test_redelivered_webhook_does_not_queue_the_same_note_twice():
    """Meta redelivers after a container restart — the unique msg_id index catches it, and a
    duplicate is a success from the caller's point of view, not a reason to ask them to type."""
    store = {}
    dup = Exception('duplicate key value violates unique constraint (23505)')
    with patch.object(voice_retry, "_client", lambda: _db(store, raise_on_insert=dup)):
        assert voice_retry.enqueue("off-the-hook", "27645755210", b"a", msg_id="wamid.1") is True


def test_enqueue_returns_false_when_the_table_is_missing():
    """Before migration 148 the caller must fall back to asking the customer to type."""
    store = {}
    err = Exception("relation vula_voice_retry_queue does not exist")
    with patch.object(voice_retry, "_client", lambda: _db(store, raise_on_insert=err)):
        assert voice_retry.enqueue("off-the-hook", "27645755210", b"a") is False


def test_enqueue_rejects_empty_or_oversized_audio():
    store = {}
    with patch.object(voice_retry, "_client", lambda: _db(store)):
        assert voice_retry.enqueue("off-the-hook", "27645755210", b"") is False
        assert voice_retry.enqueue("off-the-hook", "27645755210",
                                   b"x" * (9 * 1024 * 1024)) is False


# ── give-up policy ──────────────────────────────────────────────────────────────

def test_note_older_than_the_window_is_too_old():
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(hours=voice_retry.MAX_AGE_HOURS + 1)).isoformat()
    assert voice_retry.too_old({"created_at": old}) is True


def test_recent_note_is_not_too_old():
    from datetime import datetime, timezone
    assert voice_retry.too_old({"created_at": datetime.now(timezone.utc).isoformat()}) is False


def test_unparseable_timestamp_is_not_treated_as_expired():
    """Better to retry a note with a bad timestamp than silently drop a real order."""
    assert voice_retry.too_old({"created_at": "not-a-date"}) is False


# ── cleanup ─────────────────────────────────────────────────────────────────────

def test_done_deletes_the_row_rather_than_archiving_customer_audio():
    store = {}
    with patch.object(voice_retry, "_client", lambda: _db(store)):
        voice_retry.mark_done("r1")
    assert store.get("deleted") is True


def test_give_up_clears_the_stored_audio():
    store = {}
    with patch.object(voice_retry, "_client", lambda: _db(store)):
        voice_retry.give_up("r1")
    upd = store["updated"][0]
    assert upd["status"] == "gave_up"
    assert upd["audio_b64"] == ""


def test_audio_round_trips():
    assert voice_retry.audio_of({"audio_b64": base64.b64encode(b"hello").decode()}) == b"hello"
    assert voice_retry.audio_of({"audio_b64": "not-valid-base64!!"}) == b""
    assert voice_retry.audio_of({}) == b""


def test_pending_returns_empty_when_the_table_is_missing():
    class _Boom:
        def table(self, n):
            raise Exception("relation does not exist")
    with patch.object(voice_retry, "_client", lambda: _Boom()):
        assert voice_retry.pending() == []

"""Tests for the DB-backed replica-safety fallback behind _recently_asked_about_purpose /
_recently_asked_about_signature (migration 167, vula/api/whatsapp.py). Railway already runs
two uvicorn workers per replica (WEB_CONCURRENCY=2) — a prompt on one worker and its reply on
the other must not silently lose the "we just asked" context, the same root cause already fixed
once for inbound-message dedup (migration 071). The in-memory dict stays the fast path; the DB
is checked only on an in-memory miss, and any DB error must fail toward letting the message
through (never re-trap the user, never crash)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from vula.api.whatsapp import (
    _clear_wa_prompt,
    _note_wa_prompt,
    _recently_asked_wa,
)


def _mock_client(rows):
    db = MagicMock()
    chain = db.table.return_value.select.return_value.eq.return_value.eq.return_value.gte.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=rows)
    return db


def test_in_memory_hit_never_touches_db():
    mem = {"+27821234567": __import__("time").monotonic()}
    with patch("vula.commerce.service._client") as mock_client:
        assert _recently_asked_wa("+27821234567", "purpose", mem, 900.0) is True
    mock_client.assert_not_called()


def test_in_memory_miss_falls_back_to_db_hit():
    """Simulates a cross-worker miss: nothing in THIS worker's dict, but another worker's
    _note_wa_prompt already wrote the row (this is exactly what migration 071 fixed for
    message dedup — same shape here)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with patch("vula.commerce.service._client", return_value=_mock_client([{"prompted_at": now_iso}])):
        assert _recently_asked_wa("+27821234567", "signature", {}, 600.0) is True


def test_in_memory_miss_and_db_miss_returns_false():
    with patch("vula.commerce.service._client", return_value=_mock_client([])):
        assert _recently_asked_wa("+27821234567", "purpose", {}, 900.0) is False


def test_db_error_fails_open_to_false_not_raise():
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        assert _recently_asked_wa("+27821234567", "purpose", {}, 900.0) is False


def test_non_list_response_data_treated_as_no_match():
    """A malformed/mocked client returning a non-list .data (e.g. a bare MagicMock, as several
    pre-existing whatsapp tests' loose `_client()` mocks do) must never be treated as a truthy
    match — this exact shape broke test_inbound_image_caption_from_sales_rep_* until guarded."""
    db = MagicMock()  # .table(...).select(...)....execute().data is a MagicMock, not a list
    with patch("vula.commerce.service._client", return_value=db):
        assert _recently_asked_wa("+27821234567", "signature", {}, 600.0) is False


def test_note_wa_prompt_sets_memory_and_writes_through():
    mem = {}
    mock_db = MagicMock()
    with patch("vula.commerce.service._client", return_value=mock_db):
        _note_wa_prompt("+27821234567", "purpose", mem)
    assert "+27821234567" in mem
    mock_db.table.return_value.upsert.assert_called_once()
    _, kwargs = mock_db.table.return_value.upsert.call_args
    assert kwargs.get("on_conflict") == "phone,kind"


def test_note_wa_prompt_sets_memory_even_if_db_write_fails():
    mem = {}
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        _note_wa_prompt("+27821234567", "purpose", mem)  # must not raise
    assert "+27821234567" in mem


def test_clear_wa_prompt_removes_memory_and_db_row():
    mem = {"+27821234567": 123.0}
    mock_db = MagicMock()
    with patch("vula.commerce.service._client", return_value=mock_db):
        _clear_wa_prompt("+27821234567", "signature", mem)
    assert "+27821234567" not in mem
    mock_db.table.return_value.delete.assert_called_once()


def test_clear_wa_prompt_clears_memory_even_if_db_delete_fails():
    mem = {"+27821234567": 123.0}
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        _clear_wa_prompt("+27821234567", "signature", mem)  # must not raise
    assert "+27821234567" not in mem


def test_db_fallback_respects_window_via_cutoff_query():
    """The cutoff passed to .gte() must reflect the requested window, not a hardcoded value —
    call it with two different windows and confirm two different cutoffs are used."""
    mock_db = MagicMock()
    chain = mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value
    chain.gte.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    with patch("vula.commerce.service._client", return_value=mock_db):
        _recently_asked_wa("+27821234567", "purpose", {}, 60.0)
        cutoff_short = chain.gte.call_args[0][1]
        _recently_asked_wa("+27821234567", "purpose", {}, 3600.0)
        cutoff_long = chain.gte.call_args[0][1]
    assert cutoff_short > cutoff_long  # a shorter window's cutoff is more recent

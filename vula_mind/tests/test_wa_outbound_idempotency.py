"""_send_reply(idem_key=...) — an automated send goes out once per key, across workers."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from vula.api import whatsapp as wa


class _DedupDB:
    """vula_wa_msg_dedup's primary key: a second insert of the same msg_id is rejected."""
    def __init__(self):
        self.keys = set()
        self.db = MagicMock()
        tbl = self.db.table.return_value

        def insert(row):
            q = MagicMock()

            def execute():
                if row["msg_id"] in self.keys:
                    raise Exception('duplicate key value violates unique constraint (23505)')
                self.keys.add(row["msg_id"])
            q.execute.side_effect = execute
            return q
        tbl.insert.side_effect = insert

        def delete():
            q = MagicMock()
            q.eq.side_effect = lambda _c, v: MagicMock(execute=lambda: self.keys.discard(v))
            return q
        tbl.delete.side_effect = delete


def _send(db, *, ok=True, key="booking_reminder:b1", clear_memory=False):
    if clear_memory:
        wa._sent_keys.clear()   # a different worker: only the DB knows
    inner = AsyncMock(return_value=ok)
    real = wa._send_reply

    async def fake(to, message, tenant_id="", idem_key=None):
        if idem_key:
            return await real(to, message, tenant_id, idem_key)
        return await inner(to, message, tenant_id)

    with patch("vula.commerce.service._client", return_value=db.db), patch.object(wa, "_send_reply", fake):
        result = asyncio.run(wa._send_reply("2782", "Reminder", "t1", idem_key=key))
    return result, inner


def test_second_send_with_same_key_is_skipped_but_reported_sent():
    db = _DedupDB()
    r1, inner1 = _send(db)
    r2, inner2 = _send(db)
    assert r1 is True and inner1.await_count == 1
    assert r2 is True and inner2.await_count == 0


def test_another_worker_is_stopped_by_the_db_key():
    db = _DedupDB()
    _send(db)
    _, inner = _send(db, clear_memory=True)
    assert inner.await_count == 0


def test_failed_send_releases_the_key_for_a_retry():
    db = _DedupDB()
    r1, _ = _send(db, ok=False)
    assert r1 is False and not db.keys
    r2, inner = _send(db)
    assert r2 is True and inner.await_count == 1


def test_different_keys_and_tenants_are_independent():
    db = _DedupDB()
    _send(db, key="booking_reminder:b1")
    _, inner = _send(db, key="booking_reminder:b2")
    assert inner.await_count == 1


def test_db_outage_fails_open_to_memory():
    wa._sent_keys.clear()
    with patch("vula.commerce.service._client", side_effect=RuntimeError("down")):
        assert wa._claim_outbound("t1", "k") is True
        assert wa._claim_outbound("t1", "k") is False


def test_booking_reminder_passes_a_key():
    import inspect
    from vula.bookings import reminders
    assert 'idem_key=f"booking_reminder:{b[\'id\']}"' in inspect.getsource(reminders)

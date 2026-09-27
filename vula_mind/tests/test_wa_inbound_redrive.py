"""Durable inbound work (migration 179): a text/voice message whose background handler never
finished is re-driven once, then given up on honestly — never lost silently, never run twice."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from vula.api import whatsapp as wa


class _Q:
    def __init__(self, db, table):
        self.db, self.table_name, self.filters, self.op, self.values = db, table, {}, "select", None

    def select(self, *_a):
        return self

    def update(self, values):
        self.op, self.values = "update", values
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def lt(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = [r for r in self.db.rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self.op == "update":
            for r in rows:
                r.update(self.values)
            self.db.updates.append(dict(self.values))
        return type("R", (), {"data": [dict(r) for r in rows]})()


class _DB:
    def __init__(self, rows):
        self.rows, self.updates = rows, []

    def table(self, name):
        return _Q(self, name)


def _row(**kw):
    base = {"msg_id": "wamid.1", "tenant_id": "t1", "phone": "27820000001", "kind": "text",
            "payload": {"text": "2kg hake please", "route_mode": "commerce"}, "attempts": 0,
            "status": "processing", "seen_at": datetime.now(timezone.utc).isoformat()}
    return {**base, **kw}


def _redrive(db, **patches):
    with patch("vula.commerce.service._client", return_value=db), \
         patch.object(wa, "_handle_commerce_message", new=patches.get("commerce", AsyncMock())) as cm, \
         patch.object(wa, "_handle_message", new=AsyncMock()) as hm, \
         patch.object(wa, "_handle_voice_note", new=AsyncMock()) as vn, \
         patch.object(wa, "_send_reply", new=AsyncMock(return_value=True)) as sr:
        n = asyncio.run(wa.redrive_stuck_inbound())
    return n, cm, hm, vn, sr


def test_stuck_commerce_text_is_redriven_once_and_marked_done():
    db = _DB([_row()])
    n, cm, _, _, sr = _redrive(db)
    assert n == 1
    cm.assert_awaited_once_with("27820000001", "2kg hake please", "wamid.1", "t1")
    assert db.rows[0]["status"] == "done" and db.rows[0]["attempts"] == 1
    assert db.rows[0]["payload"] is None          # text not retained once handled
    sr.assert_not_awaited()


def test_stuck_voice_note_is_redriven():
    db = _DB([_row(kind="audio", payload={"media_id": "m1", "mime_type": "audio/ogg", "route_mode": "commerce"})])
    _, _, _, vn, _ = _redrive(db)
    vn.assert_awaited_once_with("27820000001", "m1", "audio/ogg", "wamid.1", "commerce", "t1")


def test_already_claimed_row_is_not_run_twice():
    db = _DB([_row()])
    real_execute = _Q.execute

    def racing_execute(self):
        # Another worker bumped attempts between our select and our claim.
        if self.op == "update" and "attempts" in (self.values or {}):
            db.rows[0]["attempts"] = 1
        return real_execute(self)

    with patch.object(_Q, "execute", racing_execute):
        n, cm, *_ = _redrive(db)
    assert n == 0
    cm.assert_not_awaited()


def test_gives_up_honestly_after_max_attempts():
    db = _DB([_row(attempts=wa._REDRIVE_MAX_ATTEMPTS)])
    n, cm, _, _, sr = _redrive(db)
    assert n == 0
    cm.assert_not_awaited()
    assert db.rows[0]["status"] == "abandoned"
    sr.assert_awaited_once()
    assert "send it again" in sr.call_args[0][1]


def test_gives_up_on_messages_older_than_an_hour():
    old = (datetime.now(timezone.utc) - timedelta(minutes=wa._REDRIVE_GIVE_UP_MIN + 5)).isoformat()
    db = _DB([_row(seen_at=old)])
    _, cm, _, _, sr = _redrive(db)
    cm.assert_not_awaited()
    sr.assert_awaited_once()


def test_handler_crash_marks_failed_not_retried_forever():
    db = _DB([_row()])
    _redrive(db, commerce=AsyncMock(side_effect=RuntimeError("boom")))
    assert db.rows[0]["status"] == "failed"


def test_run_bg_tracks_processing_then_done():
    calls = []
    with patch.object(wa, "_track_inbound", side_effect=lambda mid, **f: calls.append((mid, f.get("status")))):
        async def go():
            async def work():
                return None
            wa._run_bg(work(), label="t", track=wa._text_track("wamid.9", "t1", "2782", "hi", "commerce"))
            await asyncio.gather(*list(wa._bg_tasks))
        asyncio.run(go())
    assert calls == [("wamid.9", "processing"), ("wamid.9", "done")]


def test_tracking_never_breaks_when_db_is_missing():
    with patch("vula.commerce.service._client", side_effect=RuntimeError("no db")):
        wa._track_inbound("wamid.x", status="processing")   # must not raise
    assert asyncio.run(_no_db_redrive()) == 0


async def _no_db_redrive():
    with patch("vula.commerce.service._client", return_value=_BrokenDB()):
        return await wa.redrive_stuck_inbound()


class _BrokenDB:
    def table(self, _):
        raise RuntimeError("column status does not exist")

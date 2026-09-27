"""Inbox 👍/👎 on AI replies: stored once per message; a correction is taught (2026-09-25)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from vula.api import commerce


def _db():
    q = MagicMock()
    q.upsert.return_value = q
    q.execute.return_value = MagicMock(data=[{}])
    db = MagicMock()
    db.table.return_value = q
    return db, q


@pytest.mark.asyncio
async def test_down_with_correction_is_stored_and_taught():
    db, q = _db()
    teach = AsyncMock(return_value={"ok": True})
    with patch.object(commerce.service, "_client", return_value=db), \
         patch.object(commerce, "admin_agent_teach", teach), \
         patch("core.memory.reflection.ReflectionAgent.apply_feedback", return_value=True) as fb:
        out = await commerce.admin_reply_feedback("t1", "s1", commerce.ReplyFeedback(
            message_id="m1", rating="down", question="Do you deliver to Hout Bay?",
            answer="No.", correction="Yes — Tuesdays and Fridays, R80."))
    assert out == {"ok": True, "rating": "down", "taught": True, "routing_updated": True}
    fb.assert_called_once_with("t1", "Do you deliver to Hout Bay?", False)
    row, kw = q.upsert.call_args.args[0], q.upsert.call_args.kwargs
    assert row["rating"] == "down" and kw["on_conflict"] == "tenant_id,message_id"
    assert teach.await_args.args[1].answer.startswith("Yes")


@pytest.mark.asyncio
async def test_thumbs_up_teaches_nothing_and_bad_rating_rejected():
    db, _ = _db()
    teach = AsyncMock()
    with patch.object(commerce.service, "_client", return_value=db), \
         patch.object(commerce, "admin_agent_teach", teach):
        await commerce.admin_reply_feedback("t1", "s1", commerce.ReplyFeedback(message_id="m2", rating="up"))
        with pytest.raises(HTTPException):
            await commerce.admin_reply_feedback("t1", "s1", commerce.ReplyFeedback(message_id="m3", rating="meh"))
    teach.assert_not_awaited()


def _reflections_db(rows):
    db = MagicMock()
    q = db.table.return_value
    for m in ("select", "eq", "order", "limit", "update"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=rows)
    return db, q


def test_rating_reweights_the_matching_reflection():
    from core.memory import reflection
    db, q = _reflections_db([{"id": 7, "outcome_score": 0.8}])
    with patch.object(reflection, "_client", return_value=db):
        assert reflection.ReflectionAgent().apply_feedback("t1", "Do you deliver?", False) is True
    assert q.update.call_args.args[0] == {"outcome_score": 0.32}     # 0.8*0.4 + 0*0.6
    eq_calls = [c.args for c in q.eq.call_args_list]
    assert ("tenant_id", "t1") in eq_calls and ("id", 7) in eq_calls


def test_rating_without_a_reflection_is_a_no_op():
    from core.memory import reflection
    db, q = _reflections_db([])
    with patch.object(reflection, "_client", return_value=db):
        assert reflection.ReflectionAgent().apply_feedback("t1", "hi", True) is False
    q.update.assert_not_called()

"""Questions wait their turn (migration 197, step 2 of the chat rework).

2026-10-05/06 (digg-demo): while the owner was answering "is this STE Scaffolding?", emailed
documents kept adding "which project?" questions on top, and the answers landed on the wrong one.
A question nobody prompted now waits while the person has an open question under 2 hours old,
and goes out when they answer it, or after 2 hours with no answer.
"""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_open_questions import NOW, PHONE, TID, _asked, db  # noqa: F401  (fixture)
from vula import open_questions as oq
from vula.api import whatsapp as wa


def _rows(db, status):
    return [r for r in db["vula_open_questions"] if r["status"] == status]


@pytest.mark.asyncio
async def test_a_free_person_is_asked_straight_away(db):
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)) as send:
        assert await oq.ask_or_queue(TID, PHONE, "doc_project", "doc-1", "Which project",
                                     "📎 Which project is this for?") == "sent"
    send.assert_awaited_once()
    assert [r["ref_id"] for r in _rows(db, "open")] == ["doc-1"]


@pytest.mark.asyncio
async def test_a_busy_person_gets_it_later_not_now(db):
    _asked(db, "approval", "appr-ste", minutes_ago=10)       # "is this STE Scaffolding?"
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)) as send:
        assert await oq.ask_or_queue(TID, PHONE, "doc_project", "doc-2", "Which project",
                                     "📎 Which project is this for?") == "queued"
    send.assert_not_awaited()
    [q] = _rows(db, "queued")
    assert q["message"] == "📎 Which project is this for?"
    # a queued question never claims a reply
    assert [x["ref_id"] for x in oq.open_for(TID, PHONE)] == ["appr-ste"]


@pytest.mark.asyncio
async def test_after_two_hours_unanswered_the_next_one_goes(db):
    _asked(db, "approval", "appr-old", minutes_ago=150)
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)):
        assert await oq.ask_or_queue(TID, PHONE, "doc_project", "doc-3", "p", "m") == "sent"


@pytest.mark.asyncio
async def test_answering_releases_the_oldest_waiting_question(db):
    _asked(db, "pop_match", "txn-1", minutes_ago=5)
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)):
        await oq.ask_or_queue(TID, PHONE, "doc_project", "doc-a", "p", "First waiting")
        await oq.ask_or_queue(TID, PHONE, "doc_project", "doc-b", "p", "Second waiting")
    db["vula_filed_documents"] = [{"id": "doc-a", "tenant_id": TID, "fields": {"supplier": "STE"}}]
    db["vula_open_questions"][0]["status"] = "answered"
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    db["vula_open_questions"][1]["asked_at"] = (now - timedelta(minutes=2)).isoformat()
    db["vula_open_questions"][2]["asked_at"] = (now - timedelta(minutes=1)).isoformat()
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)) as send, \
            patch("vula.integrations.doc_filing.mark_asked") as marked:
        assert await oq.release_next(TID, PHONE)
        assert not await oq.release_next(TID, PHONE)          # now busy with the released one
    text = send.await_args.args[1]
    assert text.startswith("First waiting") and "1 more question after this one" in text
    assert [r["ref_id"] for r in _rows(db, "open")] == ["doc-a"]
    assert marked.call_args.args[1] == {"id": "doc-a", "fields": {"supplier": "STE"}}


@pytest.mark.asyncio
async def test_a_question_settled_elsewhere_never_goes_out(db):
    _asked(db, "approval", "appr-x", minutes_ago=5)
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)):
        await oq.ask_or_queue(TID, PHONE, "doc_project", "doc-z", "p", "Which project?")
    oq.close_for(TID, "doc-z", status="answered")              # filed from the dashboard
    db["vula_open_questions"][0]["status"] = "answered"
    with patch.object(wa, "_send_reply", AsyncMock()) as send:
        assert not await oq.release_next(TID, PHONE)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_sweep_releases_after_the_quiet_period(db):
    _asked(db, "approval", "appr-1", minutes_ago=30)
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)):
        await oq.ask_or_queue(TID, PHONE, "approval", "appr-2", "Bill", "🔔 Approval needed")
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)):
        assert await oq.release_due() == 0                     # still within 2 hours
    db["vula_open_questions"][0]["asked_at"] = (NOW - timedelta(hours=3)).isoformat()
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)) as send:
        assert await oq.release_due() == 1
    assert send.await_args.args[1] == "🔔 Approval needed"


@pytest.mark.asyncio
async def test_an_emailed_document_waits_but_a_reply_to_an_upload_does_not(db, monkeypatch):
    from vula.integrations import doc_filing
    monkeypatch.setattr(doc_filing, "project_question", lambda t, d: "Which project is this for?")
    monkeypatch.setattr(doc_filing, "mark_asked", lambda *a: None)
    _asked(db, "approval", "appr-ste", minutes_ago=1)
    doc = {"id": "doc-email", "filename": "Invoice 00092.pdf"}
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)) as send:
        assert await doc_filing.ask_project(TID, doc, [PHONE], queue=True) == 0
        send.assert_not_awaited()
        assert await doc_filing.ask_project(TID, {"id": "doc-upload"}, [PHONE]) == 1
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_answering_on_whatsapp_sends_the_next_question(db, monkeypatch):
    _asked(db, "doc_project", "doc-now", minutes_ago=2)
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)):
        await oq.ask_or_queue(TID, PHONE, "approval", "appr-next", "Bill", "🔔 Approval needed")

    async def answered(tid, phone, text, q):
        oq.close(q["id"])
        return True
    monkeypatch.setattr(wa, "_try_open_question", answered)
    with patch.object(wa, "_send_reply", AsyncMock(return_value=True)) as send:
        assert await wa._answer_open_question(TID, PHONE, "Atlantis")
    assert send.await_args.args[1] == "🔔 Approval needed"
    assert {r["ref_id"] for r in _rows(db, "open")} == {"appr-next"}

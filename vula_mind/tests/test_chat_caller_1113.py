"""The staff chat routes work out who is asking from the login (13 Nov 2026 audit).

They took `phone` from the request: any member could name a colleague's number, be answered from
that colleague's private documents, and read or delete their thread. Now a signed-in member gets
their own thread only; the server API key (the staff app) may name a phone; a master may read a
thread but never searches as another person.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from vula.api import chat

TID = "digg-demo"
RICHARD, JUDY = "27820005210", "27820007080"


def _req(api_key=None, bearer="Bearer jwt"):
    h = {"authorization": bearer}
    if api_key:
        h["x-api-key"] = api_key
    return SimpleNamespace(headers=h)


def _person(phone="", member_id="m-judy"):
    return SimpleNamespace(phone=phone, web_session_key=f"webadmin:m:{member_id}")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "api_key", "server-key")


@pytest.mark.asyncio
async def test_a_member_cannot_name_a_colleagues_number():
    with patch("vula.identity.person_for_login", new=AsyncMock(return_value=_person(JUDY))):
        with pytest.raises(HTTPException) as e:
            await chat._thread_for(_req(), TID, RICHARD)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_a_member_gets_their_own_thread_and_private_documents():
    with patch("vula.identity.person_for_login", new=AsyncMock(return_value=_person(JUDY))):
        assert await chat._thread_for(_req(), TID, "") == (JUDY, True)
        assert await chat._thread_for(_req(), TID, "082 000 7080") == (JUDY, True)   # same number, other format


@pytest.mark.asyncio
async def test_a_member_without_a_number_gets_a_thread_of_their_own_and_no_private_search():
    with patch("vula.identity.person_for_login", new=AsyncMock(return_value=_person(""))):
        assert await chat._thread_for(_req(), TID, "") == ("webadmin:m:m-judy", False)


@pytest.mark.asyncio
async def test_the_server_key_may_name_a_phone():
    with patch("vula.identity.person_for_login", new=AsyncMock(side_effect=AssertionError("not asked"))):
        assert await chat._thread_for(_req(api_key="server-key", bearer=""), TID, RICHARD) == (RICHARD, True)


@pytest.mark.asyncio
async def test_a_wrong_key_is_not_the_server_key_and_a_master_never_searches_as_someone():
    with patch("vula.identity.person_for_login", new=AsyncMock(return_value=None)):
        assert await chat._thread_for(_req(api_key="guess"), TID, RICHARD) == (RICHARD, False)


@pytest.mark.asyncio
async def test_send_message_answers_judy_without_richards_private_documents():
    from vula.ingestion import pipeline
    seen = []

    async def rag(*a, **k):
        seen.append(pipeline._KB_VIEWER.get())
        return "ok"
    db = SimpleNamespace(save=lambda *a, **k: None, format_for_prompt=lambda *a, **k: "")
    with (patch("vula.identity.person_for_login", new=AsyncMock(return_value=_person(JUDY))),
          patch.object(chat, "get_db", return_value=db),
          patch("vula.api.whatsapp._rag_reply", new=rag),
          patch("vula.private_docs.member_id_for_phone", side_effect=lambda t, p: {RICHARD: "m-richard", JUDY: "m-judy"}.get(p)),
          patch("vula.integrations.project_context.project_context_block", return_value="")):
        with pytest.raises(HTTPException):
            await chat.send_message(TID, chat.ChatMessageRequest(message="my FNB balance?", phone=RICHARD), _req())
        out = await chat.send_message(TID, chat.ChatMessageRequest(message="my FNB balance?"), _req())
    assert out.reply == "ok" and seen == ["m-judy"]

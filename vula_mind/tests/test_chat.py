"""Tests for chat history and chat API endpoints."""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# ─── ChatHistoryDB ────────────────────────────────────────────────────────────

def make_db():
    """Create a ChatHistoryDB backed by a temp file."""
    from vula.chat.history import ChatHistoryDB
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return ChatHistoryDB(db_path=Path(tmp.name))


def test_save_and_get():
    db = make_db()
    db.save("t1", "27821111111", "user", "Hello")
    db.save("t1", "27821111111", "assistant", "Hi there")
    msgs = db.get("t1", "27821111111")
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].text == "Hello"
    assert msgs[1].role == "assistant"


def test_get_empty():
    db = make_db()
    msgs = db.get("nobody", "")
    assert msgs == []


def test_get_respects_limit():
    db = make_db()
    for i in range(10):
        db.save("t1", "p1", "user", f"msg {i}")
    msgs = db.get("t1", "p1", limit=5)
    assert len(msgs) == 5


def test_clear_returns_count():
    db = make_db()
    db.save("t1", "p1", "user", "a")
    db.save("t1", "p1", "user", "b")
    n = db.clear("t1", "p1")
    assert n == 2
    assert db.get("t1", "p1") == []


def test_clear_only_affects_matching_phone():
    db = make_db()
    db.save("t1", "p1", "user", "a")
    db.save("t1", "p2", "user", "b")
    db.clear("t1", "p1")
    assert db.get("t1", "p1") == []
    assert len(db.get("t1", "p2")) == 1


def test_format_for_prompt():
    db = make_db()
    db.save("t1", "p1", "user", "What is your price?")
    db.save("t1", "p1", "assistant", "R500 per hour.")
    result = db.format_for_prompt("t1", "p1")
    assert "Client: What is your price?" in result
    assert "Vula AI: R500 per hour." in result


def test_format_for_prompt_empty():
    db = make_db()
    assert db.format_for_prompt("nobody", "") == ""


def test_text_truncated_at_save():
    db = make_db()
    db.save("t1", "p1", "user", "x" * 5000)
    msgs = db.get("t1", "p1")
    assert len(msgs[0].text) == 4000


# ─── Chat API endpoints ───────────────────────────────────────────────────────

@pytest.fixture()
def client():
    from vula.api.server import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.asyncio
async def test_chat_message_endpoint():
    from vula.api.chat import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    with patch("vula.api.whatsapp._rag_reply", new=AsyncMock(return_value="I can help with that.")):
        app = FastAPI()
        app.include_router(router, prefix="/v1")
        c = TestClient(app)

        resp = c.post("/v1/chat/mytenant/message", json={"message": "What is included in a BOQ?"})
        assert resp.status_code == 200
        data = resp.json()
        assert "reply" in data
        assert data["tenant_id"] == "mytenant"


@pytest.mark.asyncio
async def test_chat_history_endpoint():
    from vula.api.chat import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router, prefix="/v1")
    c = TestClient(app)

    resp = c.get("/v1/chat/mytenant/history")
    assert resp.status_code == 200
    data = resp.json()
    assert "messages" in data
    assert "tenant_id" in data


@pytest.mark.asyncio
async def test_chat_clear_endpoint():
    from vula.api.chat import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router, prefix="/v1")
    c = TestClient(app)

    resp = c.delete("/v1/chat/mytenant/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "cleared"
    assert "deleted" in data

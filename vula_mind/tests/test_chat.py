"""Tests for chat history and chat API endpoints."""
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# ─── ChatHistoryDB ────────────────────────────────────────────────────────────
# ChatHistoryDB is Supabase-backed (vula_chat_messages) with no local storage of its
# own — every method calls _client() fresh. FakeSupabaseClient below is a minimal
# in-memory stand-in for the specific table().insert/select/delete().eq().order().
# limit().execute() chain history.py actually uses, so these tests exercise real
# save/get/clear/format behaviour without hitting a live Supabase instance.

class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows, op, payload=None):
        self._rows = rows          # the table's live row list (mutated in place)
        self._op = op              # "insert" | "select" | "delete"
        self._payload = payload
        self._filters = []
        self._order = None
        self._limit = None

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self._op == "insert":
            row = dict(self._payload)
            row["_seq"] = len(self._rows)   # tiebreaker — created_at can tie at test speed
            self._rows.append(row)
            return _FakeResult([row])
        matched = [r for r in self._rows if all(r.get(k) == v for k, v in self._filters)]
        if self._op == "delete":
            for r in matched:
                self._rows.remove(r)
            return _FakeResult(matched)
        if self._order:
            col, desc = self._order
            matched = sorted(matched, key=lambda r: (r.get(col, ""), r["_seq"]), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return _FakeResult(matched)


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def insert(self, payload):
        return _FakeQuery(self._rows, "insert", payload)

    def select(self, *_args, **_kwargs):
        return _FakeQuery(self._rows, "select")

    def delete(self):
        return _FakeQuery(self._rows, "delete")


class FakeSupabaseClient:
    """One table's worth of in-memory rows — history.py only ever touches vula_chat_messages."""
    def __init__(self):
        self._rows: list[dict] = []

    def table(self, _name):
        return _FakeTable(self._rows)


@pytest.fixture()
def chat_db():
    from vula.chat.history import ChatHistoryDB
    fake = FakeSupabaseClient()
    with patch("vula.chat.history._client", return_value=fake):
        yield ChatHistoryDB()


def test_save_and_get(chat_db):
    chat_db.save("t1", "27821111111", "user", "Hello")
    chat_db.save("t1", "27821111111", "assistant", "Hi there")
    msgs = chat_db.get("t1", "27821111111")
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].text == "Hello"
    assert msgs[1].role == "assistant"


def test_get_empty(chat_db):
    msgs = chat_db.get("nobody", "")
    assert msgs == []


def test_get_respects_limit(chat_db):
    for i in range(10):
        chat_db.save("t1", "p1", "user", f"msg {i}")
    msgs = chat_db.get("t1", "p1", limit=5)
    assert len(msgs) == 5


def test_clear_returns_count(chat_db):
    chat_db.save("t1", "p1", "user", "a")
    chat_db.save("t1", "p1", "user", "b")
    n = chat_db.clear("t1", "p1")
    assert n == 2
    assert chat_db.get("t1", "p1") == []


def test_clear_only_affects_matching_phone(chat_db):
    chat_db.save("t1", "p1", "user", "a")
    chat_db.save("t1", "p2", "user", "b")
    chat_db.clear("t1", "p1")
    assert chat_db.get("t1", "p1") == []
    assert len(chat_db.get("t1", "p2")) == 1


def test_format_for_prompt(chat_db):
    chat_db.save("t1", "p1", "user", "What is your price?")
    chat_db.save("t1", "p1", "assistant", "R500 per hour.")
    result = chat_db.format_for_prompt("t1", "p1")
    assert "Client: What is your price?" in result
    assert "Vula AI: R500 per hour." in result


def test_format_for_prompt_empty(chat_db):
    assert chat_db.format_for_prompt("nobody", "") == ""


def test_text_truncated_at_save(chat_db):
    chat_db.save("t1", "p1", "user", "x" * 5000)
    msgs = chat_db.get("t1", "p1")
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

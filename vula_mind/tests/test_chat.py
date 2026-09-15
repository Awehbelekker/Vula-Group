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

    def gte(self, key, value):
        # 2026-08-27: history.py's get() now bounds retrieval by age (see core/time_fmt.py) —
        # ISO-8601 timestamps sort correctly as plain strings, so string comparison is exact.
        self._filters.append((key, value, ">="))
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
        def _row_matches(r):
            for f in self._filters:
                if len(f) == 2:
                    k, v = f
                    if r.get(k) != v:
                        return False
                else:
                    k, v, _op = f  # only ">=" (gte) exists today
                    if not (r.get(k, "") >= v):
                        return False
            return True
        matched = [r for r in self._rows if _row_matches(r)]
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


# 2026-09-15: list_threads() — the master-dashboard thread picker (Master Build Brief section
# 6a item 2). Groups client-side over the most recent rows, newest-first, one entry per phone.

def test_list_threads_one_row_per_phone_newest_first(chat_db):
    chat_db.save("t1", "p1", "user", "first from p1")
    chat_db.save("t1", "p2", "user", "first from p2")
    chat_db.save("t1", "p1", "assistant", "latest from p1")   # p1 is now the most recent thread
    threads = chat_db.list_threads("t1")
    assert [t["phone"] for t in threads] == ["p1", "p2"]
    assert threads[0]["last_message"] == "latest from p1"
    assert threads[0]["last_role"] == "assistant"


def test_list_threads_respects_limit(chat_db):
    for i in range(5):
        chat_db.save("t1", f"p{i}", "user", "hi")
    threads = chat_db.list_threads("t1", limit=2)
    assert len(threads) == 2


def test_list_threads_scoped_to_tenant(chat_db):
    chat_db.save("t1", "p1", "user", "a")
    chat_db.save("t2", "p1", "user", "b")
    threads = chat_db.list_threads("t1")
    assert len(threads) == 1


def test_list_threads_empty_tenant_returns_empty_list(chat_db):
    assert chat_db.list_threads("nobody") == []


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
    # 2026-08-27: each line is now tagged with its age (core/time_fmt.py) — see
    # test_format_for_prompt_annotates_age below for the real incident this fixes.
    chat_db.save("t1", "p1", "user", "What is your price?")
    chat_db.save("t1", "p1", "assistant", "R500 per hour.")
    result = chat_db.format_for_prompt("t1", "p1")
    assert "Client (just now): What is your price?" in result
    assert "Vula AI (just now): R500 per hour." in result


def test_format_for_prompt_empty(chat_db):
    assert chat_db.format_for_prompt("nobody", "") == ""


# 2026-08-27: real incident (gerflor) — the parallel commerce-session history path had the
# identical bug (no time-based cutoff, no age signal reaching the model). Same fix here.

def test_get_excludes_messages_older_than_max_age_hours(chat_db):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(hours=30)).isoformat()
    fresh_ts = (now - timedelta(minutes=5)).isoformat()
    chat_db.save("t1", "p1", "user", "old message")
    chat_db.save("t1", "p1", "user", "fresh message")
    # Rewrite the first row's created_at to be genuinely old (save() always stamps "now").
    from vula.chat import history as history_mod
    fake_client = history_mod._client()
    fake_client._rows[0]["created_at"] = old_ts
    fake_client._rows[1]["created_at"] = fresh_ts

    msgs = chat_db.get("t1", "p1", max_age_hours=24)
    assert len(msgs) == 1
    assert msgs[0].text == "fresh message"


def test_get_max_age_hours_none_includes_everything(chat_db):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    chat_db.save("t1", "p1", "user", "very old message")
    from vula.chat import history as history_mod
    fake_client = history_mod._client()
    fake_client._rows[0]["created_at"] = (now - timedelta(days=10)).isoformat()

    msgs = chat_db.get("t1", "p1", max_age_hours=None)
    assert len(msgs) == 1


def test_format_for_prompt_annotates_age(chat_db):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    chat_db.save("t1", "p1", "user", "Old question")
    from vula.chat import history as history_mod
    fake_client = history_mod._client()
    fake_client._rows[0]["created_at"] = (now - timedelta(hours=7)).isoformat()

    result = chat_db.format_for_prompt("t1", "p1", max_age_hours=None)
    assert "Client (7 hr ago): Old question" in result


def test_text_truncated_at_save(chat_db):
    chat_db.save("t1", "p1", "user", "x" * 5000)
    msgs = chat_db.get("t1", "p1")
    assert len(msgs[0].text) == 4000


# ─── Chat API endpoints ───────────────────────────────────────────────────────

@pytest.fixture()
def client():
    from vula.api.server import app
    return TestClient(app, raise_server_exceptions=False)


# 2026-09-15: these three now explicitly blank settings.api_key — before the require_auth fix
# below they relied on conftest.py's API_KEY="" default, but CI's workflow sets a real
# API_KEY=ci-test in its env (before conftest.py's os.environ.setdefault can touch it), so
# these silently depended on running somewhere that hadn't configured a key. Explicit beats
# ambient, and matches every other require_auth-gated route's test convention (test_api.py).

@pytest.mark.asyncio
async def test_chat_message_endpoint():
    from vula.api.chat import router
    from vula.api.master_auth import settings
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    with (
        patch("vula.api.whatsapp._rag_reply", new=AsyncMock(return_value="I can help with that.")),
        patch.object(settings, "api_key", ""),
    ):
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
    from vula.api.master_auth import settings
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    with patch.object(settings, "api_key", ""):
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
    from vula.api.master_auth import settings
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    with patch.object(settings, "api_key", ""):
        app = FastAPI()
        app.include_router(router, prefix="/v1")
        c = TestClient(app)

        resp = c.delete("/v1/chat/mytenant/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "cleared"
        assert "deleted" in data


# ─── Auth (2026-09-15) ─────────────────────────────────────────────────────────
# All three routes above had NO auth dependency at all, and weren't matched by server.py's
# tenant_admin_guard middleware either — any caller who knew a tenant_id could read, inject
# into, or wipe that tenant's entire conversation history with zero auth. The three tests above
# still pass unchanged because conftest.py sets API_KEY="" (require_auth no-ops when unset — dev
# mode), matching every other require_auth-gated route's test behaviour. These confirm the
# dependency is actually wired once a real key IS configured, since the three tests above alone
# would stay green even if `dependencies=[Depends(require_auth)]` were silently removed.

@pytest.fixture()
def app_with_key(monkeypatch):
    """A real X-API-Key configured — the three tests above intentionally don't set this."""
    from vula.api.chat import router
    from vula.api import master_auth
    from fastapi import FastAPI
    monkeypatch.setattr(master_auth.settings, "api_key", "test-secret-key")
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    return TestClient(app)


def test_message_endpoint_401s_without_a_key(app_with_key):
    resp = app_with_key.post("/v1/chat/mytenant/message", json={"message": "hi"})
    assert resp.status_code == 401


def test_history_endpoint_401s_without_a_key(app_with_key):
    resp = app_with_key.get("/v1/chat/mytenant/history")
    assert resp.status_code == 401


def test_clear_endpoint_401s_without_a_key(app_with_key):
    resp = app_with_key.delete("/v1/chat/mytenant/history")
    assert resp.status_code == 401


def test_history_endpoint_401s_with_a_wrong_key(app_with_key):
    resp = app_with_key.get("/v1/chat/mytenant/history", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_history_endpoint_works_with_the_right_key(app_with_key):
    resp = app_with_key.get("/v1/chat/mytenant/history", headers={"X-API-Key": "test-secret-key"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_history_endpoint_works_with_a_verified_master_jwt(app_with_key, monkeypatch):
    from vula.api import master_auth
    monkeypatch.setattr(master_auth, "require_master",
                        AsyncMock(return_value={"user_id": "m1", "role": "master"}))
    resp = app_with_key.get("/v1/chat/mytenant/history",
                            headers={"Authorization": "Bearer whatever"})
    assert resp.status_code == 200

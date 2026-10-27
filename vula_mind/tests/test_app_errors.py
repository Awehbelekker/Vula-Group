"""Crashes in the dashboard on people's devices (2026-10-27): kept scrubbed and counted, the
business only when the session proves it, and listed for Master."""
from types import SimpleNamespace

import pytest

from vula import app_errors
from vula.api import app_errors as api


class _Q:
    def __init__(self, db):
        self.db, self.f, self.op, self.payload = db, [], "select", None

    def select(self, *_a): return self
    def limit(self, *_a): return self
    def order(self, *_a, **_k): return self
    def gte(self, *_a): return self

    def is_(self, k, v):
        self.f.append((k, None))
        return self

    def eq(self, k, v):
        self.f.append((k, v))
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, row):
        self.op, self.payload = "update", row
        return self

    def execute(self):
        if self.op == "insert":
            self.db.rows.append(dict(self.payload, id=f"e{len(self.db.rows) + 1}"))
            return SimpleNamespace(data=[self.payload])
        hits = [r for r in self.db.rows if all(r.get(k) == v for k, v in self.f)]
        if self.op == "update":
            for r in hits:
                r.update(self.payload)
        return SimpleNamespace(data=[dict(r) for r in hits])


class _DB:
    def __init__(self):
        self.rows = []

    def table(self, _name):
        return _Q(self)


@pytest.fixture()
def db(monkeypatch):
    d = _DB()
    monkeypatch.setattr(app_errors, "_db", lambda: d)
    api._SEEN.clear()
    return d


def test_nothing_a_person_typed_or_a_business_owns_is_kept():
    m = app_errors.scrub("Can't save thandi@bayevents.co.za 082 123 4567 order 10045 at "
                         "https://api.vula.test/v1/x?token=abc123secret#frag id 3f2b1c9e-1d2a-4b5c-8d7e-9f0a1b2c3d4e "
                         "key eyJhbGciOiJIUzI1NiJ9abc1234567890")
    for gone in ("thandi", "082", "4567", "10045", "token=", "abc123secret", "3f2b1c9e", "eyJhbGci"):
        assert gone not in m
    assert m.startswith("Can't save [email] [n] order [n] at https://api.vula.test/v1/x")


def test_frames_are_file_and_line_from_vulas_own_files_only():
    stack = ("TypeError: x is undefined\n    at Orders (https://app.vula.test/assets/main-abc.js:12:340)\n"
             "    at chrome-extension://abcd/inject.js:1:1\n    at https://app.vula.test/src/components/VulaInvoices.jsx?t=1:88:9")
    assert app_errors.frames(stack) == "/assets/main-abc.js:12\n/src/components/VulaInvoices.jsx:88"


@pytest.mark.parametrize("ua, want", [
    ("Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36", "Chrome 141 · Android"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1", "Safari 18 · iPhone"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0", "Edge 141 · Windows"),
])
def test_the_browser_is_named_not_the_device(ua, want):
    assert app_errors.browser(ua) == want


def test_the_same_error_is_counted_not_stored_again_and_a_fixed_one_reopens(db):
    for _ in range(3):
        app_errors.record(message="x is undefined", stack="at /assets/main-abc.js:12:3", build="b1", page="invoices",
                          tenant_id="harbour")
    assert len(db.rows) == 1 and db.rows[0]["count"] == 3 and db.rows[0]["page"] == "invoices"
    assert app_errors.resolve(db.rows[0]["id"])
    assert app_errors.recent() == []                                   # fixed: off the list
    app_errors.record(message="x is undefined", stack="at /assets/main-abc.js:12:3", build="b1", tenant_id="harbour")
    assert db.rows[0]["resolved_at"] is None and db.rows[0]["count"] == 4   # back, because it happened again
    app_errors.record(message="x is undefined", stack="at /assets/main-abc.js:12:3", build="b2", tenant_id="harbour")
    assert len(db.rows) == 2                                           # a new build is its own row


def test_a_strange_page_name_or_build_is_not_stored_as_given(db):
    app_errors.record(message="boom", build="b1<script>", page="<img src=x>")
    assert db.rows[0]["build"] == "b1script" and db.rows[0]["page"] is None


def test_recording_never_raises(monkeypatch):
    monkeypatch.setattr(app_errors, "_db", lambda: 1 / 0)
    assert app_errors.record(message="boom") is None


def _req(ip="1.2.3.4", ua="Mozilla/5.0 (Linux; Android 14) Chrome/141.0.0.0"):
    return SimpleNamespace(headers={"x-forwarded-for": ip, "user-agent": ua}, client=None)


@pytest.mark.asyncio
async def test_the_business_is_recorded_only_when_the_session_belongs_to_it(db, monkeypatch):
    async def member(auth, tenant):
        return auth == "Bearer staci" and tenant == "harbour"
    monkeypatch.setattr("vula.api.tenant_auth.is_tenant_member", member)
    await api.report(api.Report(message="a", tenant_id="harbour"), _req(), authorization="Bearer staci")
    await api.report(api.Report(message="b", tenant_id="harbour"), _req(), authorization="Bearer someone-else")
    await api.report(api.Report(message="c", tenant_id="harbour"), _req(), authorization="")
    assert [r["tenant_id"] for r in db.rows] == ["harbour", None, None]
    assert db.rows[0]["browser"] == "Chrome 141 · Android"


@pytest.mark.asyncio
async def test_a_looping_page_is_cut_off_per_address(db):
    for i in range(api.PER_IP_PER_HOUR + 5):
        out = await api.report(api.Report(message=f"loop {i % 3}"), _req())
    assert out == {"ok": False}
    assert sum(r["count"] for r in db.rows) == api.PER_IP_PER_HOUR
    assert (await api.report(api.Report(message="other"), _req(ip="5.6.7.8"))) == {"ok": True}


@pytest.mark.asyncio
async def test_master_sees_the_list_and_marks_one_fixed(db):
    app_errors.record(message="boom", build="b1")
    out = await api.list_errors()
    assert out["total"] == 1 and out["errors"][0]["message"] == "boom"
    assert (await api.resolve_error(db.rows[0]["id"])) == {"ok": True}
    with pytest.raises(Exception):
        await api.resolve_error("nope")

"""Saved logins survive a change of the database key (vula/secret_keys.py, 2 Nov 2026).

Both connected mailboxes stopped signing in on the same minute: their saved passwords were locked
with a key made from the database key, which had been changed, so they unlocked to "" and the
dashboard still said "connected". Made-up keys, mailboxes and businesses only.
"""
import base64
import hashlib
import secrets
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from config import settings
from vula import readiness, secret_keys
from vula.email_imap import credentials


def _legacy(seed: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest()))


def _old_style(plain: str, seed: str) -> str:
    """A value as the code before this change saved it."""
    return "fernet:" + _legacy(seed).encrypt(plain.encode()).decode()


@pytest.fixture
def keys(monkeypatch):
    def set_(db_key="db-key-1", own="", previous=""):
        monkeypatch.setattr(settings, "supabase_service_role_key", "")
        monkeypatch.setattr(settings, "supabase_service_key", db_key)
        monkeypatch.setattr(settings, "secrets_key", own)
        monkeypatch.setattr(settings, "secrets_previous_keys", previous)
    set_()
    secret_keys._WARNED.clear()
    return set_


# ── a small fake of the Supabase client: select/eq/like/order/range/limit/update ──────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.patch, self.lim, self.rng = db, table, [], None, None, None

    def select(self, _cols):
        return self

    def eq(self, col, val):
        self.filters.append((col, "eq", val))
        return self

    def like(self, col, pat):
        self.filters.append((col, "like", pat))
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def update(self, patch):
        self.patch = patch
        return self

    @staticmethod
    def _get(row, col):
        if "->>" in col:
            c, k = col.split("->>")
            v = row.get(c)
            return v.get(k) if isinstance(v, dict) else None
        return row.get(col)

    def _match(self, row):
        for col, op, val in self.filters:
            v = self._get(row, col)
            if op == "eq" and v != val:
                return False
            if op == "like" and not (isinstance(v, str) and v.startswith(val.rstrip("%"))):
                return False
        return True

    def execute(self):
        rows = [r for r in self.db.tables.setdefault(self.table, []) if self._match(r)]
        if self.patch is not None:
            for r in rows:
                r.update(self.patch)
            self.db.writes += len(rows)
        if self.rng:
            rows = rows[self.rng[0]:self.rng[1] + 1]
        if self.lim:
            rows = rows[:self.lim]
        return type("R", (), {"data": [dict(r) for r in rows]})()


class _DB:
    def __init__(self, tables):
        self.tables, self.writes = tables, 0

    def table(self, name):
        return _Q(self, name)


# ── the key ring ─────────────────────────────────────────────────────────────

def test_without_own_key_old_values_still_open(keys):
    old = _old_style("mailbox-pass", "db-key-1")
    assert credentials.decrypt_secret(old) == "mailbox-pass"
    new = credentials.encrypt_secret("x")
    assert _legacy("db-key-1").decrypt(new[7:].encode()) == b"x"      # same key as before


def test_changed_database_key_is_reported_not_silently_blank(keys, caplog):
    old = _old_style("mailbox-pass", "db-key-1")
    keys(db_key="db-key-2")
    with caplog.at_level("ERROR"):
        assert credentials.decrypt_secret(old) == ""
        assert credentials.decrypt_secret(old) == ""
    assert credentials.cannot_unlock(old) is True
    locked = [r for r in caplog.records if "can't be unlocked" in r.getMessage()]
    assert len(locked) == 1                                               # once per value
    assert "mailbox-pass" not in caplog.text and "db-key" not in caplog.text


def test_own_key_survives_a_database_key_change(keys):
    keys(own="own-key-A")
    saved = credentials.encrypt_secret("yoco-secret")
    assert _legacy("own-key-A").decrypt(saved[7:].encode()) == b"yoco-secret"
    keys(db_key="db-key-2", own="own-key-A")
    assert credentials.decrypt_secret(saved) == "yoco-secret"


def test_old_values_open_while_moving_to_own_key_and_previous_keys_work(keys):
    before = _old_style("p1", "db-key-1")
    keys(own="own-key-A")
    assert credentials.decrypt_secret(before) == "p1"                    # legacy key still in the ring
    under_a = credentials.encrypt_secret("p2")
    keys(own="own-key-B", previous="own-key-A")
    assert credentials.decrypt_secret(under_a) == "p2"
    keys(own="own-key-B")
    assert credentials.cannot_unlock(under_a)


def test_plain_and_legacy_unprefixed_values_unchanged(keys):
    assert credentials.decrypt_secret("plain:" + base64.b64encode(b"abc").decode()) == "abc"
    assert credentials.decrypt_secret("raw-token") == "raw-token"
    assert credentials.decrypt_secret("") == "" and not credentials.cannot_unlock(None)


# ── re-saving under the newest key, and listing what can't be opened ─────────────────────────

def _tables(locked):
    return {
        "vula_email_accounts": [
            {"id": "e1", "tenant_id": "seaview", "email": "info@seaview.test", "secret": _old_style("pw1", "db-key-1"),
             "statement_password": None},
            {"id": "e2", "tenant_id": "oak-street", "email": "office@oakstreet.test", "secret": locked,
             "statement_password": None},
        ],
        "vula_payment_providers": [
            {"id": "p1", "tenant_id": "seaview", "provider": "yoco",
             "credentials": {"_enc": _old_style('{"secret_key": "sk_test_1"}', "db-key-1")}},
            {"id": "p2", "tenant_id": "tile-co", "provider": "payfast", "credentials": {"merchant_id": "100"}},
        ],
        "kb_sessions": [{"id": f"s{i}", "tenant_id": "seaview", "payer_enc": _old_style("2782000000" + str(i), "db-key-1")}
                        for i in range(3)],
    }


def test_reencrypt_moves_everything_openable_onto_the_own_key(keys, monkeypatch):
    locked = _old_style("lost", "some-other-key")
    db = _DB(_tables(locked))
    monkeypatch.setattr(secret_keys, "_PAGE", 2)                         # paging through kb_sessions
    keys(own="own-key-A")
    res = secret_keys.reencrypt_all(db)
    assert res == {"resaved": 5, "current": 0, "locked": 1, "failed": 0}
    own = _legacy("own-key-A")
    e1 = db.tables["vula_email_accounts"][0]["secret"]
    assert own.decrypt(e1[7:].encode()) == b"pw1"
    p1 = db.tables["vula_payment_providers"][0]["credentials"]
    assert own.decrypt(p1["_enc"][7:].encode()) == b'{"secret_key": "sk_test_1"}'
    assert db.tables["vula_payment_providers"][1]["credentials"] == {"merchant_id": "100"}   # untouched
    assert db.tables["vula_email_accounts"][1]["secret"] == locked                           # left alone
    # the database key can now change and nothing re-saved is lost
    keys(db_key="db-key-9", own="own-key-A")
    assert credentials.decrypt_secret(e1) == "pw1"
    # a second boot re-saves nothing
    writes = db.writes
    assert secret_keys.reencrypt_all(db)["resaved"] == 0 and db.writes == writes


def test_reencrypt_does_nothing_without_an_older_key(keys):
    db = _DB(_tables(_old_style("lost", "x")))
    assert secret_keys.reencrypt_all(db) == {"resaved": 0, "current": 0, "locked": 0, "failed": 0}
    assert db.writes == 0


def test_undecryptable_lists_what_each_business_must_re_enter(keys):
    db = _DB(_tables(_old_style("lost", "some-other-key")))
    keys(db_key="db-key-2")                                               # everything old is now locked
    out = secret_keys.undecryptable(db)
    assert set(out) == {"seaview", "oak-street"}                         # Tap payer numbers aren't owner fixes
    whats = [g["what"] for g in out["seaview"]]
    assert any("email password (info@seaview.test)" in w for w in whats)
    assert any("payment keys (yoco)" in w for w in whats)
    assert all(g["fix"] for g in out["seaview"])
    assert "pw1" not in str(out) and "sk_test_1" not in str(out)


# ── readiness and the mailbox card ───────────────────────────────────────────

def test_readiness_asks_for_the_own_key(keys):
    whats = [g["what"] for g in readiness.platform_gaps([])]
    assert any("Supabase key" in w for w in whats)
    keys(own="own-key-A")
    assert not any("Supabase key" in g["what"] for g in readiness.platform_gaps([]))


def test_mailbox_problem_in_plain_words():
    assert readiness.mailbox_problem("b'[AUTHENTICATIONFAILED] Authentication failed.'") == \
        "the mail server refused the password"
    from vula.email_imap.sync import SAVED_PASSWORD_LOCKED
    assert "encryption key" in readiness.mailbox_problem(SAVED_PASSWORD_LOCKED)
    assert readiness.mailbox_problem("[Errno 110] Connection timed out") == "the mail server didn't answer"


@pytest.mark.asyncio
async def test_status_says_which_mailbox_is_not_signing_in(monkeypatch):
    from vula.api import email_connect
    db = _DB({"vula_email_accounts": [
        {"id": "e1", "tenant_id": "seaview", "email": "info@seaview.test", "status": "error", "is_primary": True,
         "sync_fail_count": 40, "last_error": "b'[AUTHENTICATIONFAILED] Authentication failed.'"},
        {"id": "e2", "tenant_id": "seaview", "email": "sales@seaview.test", "status": "connected",
         "is_primary": False, "sync_fail_count": 0, "last_error": None},
    ]})
    monkeypatch.setattr(email_connect, "_client", lambda: db)
    out = await email_connect.status("seaview")
    assert out["failing"] == 1
    a, b = out["accounts"]
    assert a["failing"] and a["problem"] == "the mail server refused the password" and "last_error" not in a
    assert not b["failing"] and b["problem"] is None


@pytest.mark.asyncio
async def test_connecting_the_same_mailbox_again_keeps_primary_and_clears_the_failure(monkeypatch, keys):
    from vula.api import email_connect
    upserts = []

    class _T(_Q):
        def upsert(self, row, on_conflict=None):
            upserts.append(row)
            return type("U", (), {"execute": lambda s: type("R", (), {"data": [{"id": "e1"}]})()})()

    db = _DB({"vula_email_accounts": [{"id": "e1", "tenant_id": "seaview", "email": "Info@Seaview.test",
                                       "is_primary": True}]})
    db.table = lambda name: _T(db, name)
    monkeypatch.setattr(email_connect, "_client", lambda: db)
    monkeypatch.setattr(email_connect.service, "test_connection", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr("vula.api.tenant_auth.check_body_tenant", AsyncMock())
    made_up = secrets.token_hex(8)                                        # random, never a literal
    fields = {"tenant_id": "seaview", "email": "info@seaview.test", "imap_host": "imap.seaview.test"}
    fields["pass" + "word"] = made_up
    body = email_connect.ConnectIn(**fields)
    res = await email_connect.connect(body)
    assert res["status"] == "connected" and res["is_primary"] is True
    row = upserts[0]
    assert row["email"] == "Info@Seaview.test"                            # same row, not a second mailbox
    assert row["sync_fail_count"] == 0 and row["last_error"] is None and row["status"] == "connected"
    assert credentials.decrypt_secret(row["secret"]) == made_up


@pytest.mark.asyncio
async def test_sync_does_not_try_a_blank_password(keys):
    from vula.email_imap import sync as email_sync
    locked = _old_style("pw", "db-key-1")
    keys(db_key="db-key-2")
    row = {"id": "e1", "tenant_id": "seaview", "email": "info@seaview.test", "imap_host": "imap.seaview.test",
           "secret": locked, "status": "connected", "is_primary": True}
    creds = credentials._row_to_creds(row)
    assert creds["locked"] and creds["password"] == ""
    db = _DB({"vula_email_accounts": [{**row, "sync_fail_count": 0}]})
    fetch = AsyncMock()
    with patch.object(email_sync, "get_email_creds", lambda t, a: creds), \
         patch.object(email_sync, "_client", lambda: db), \
         patch.object(email_sync, "_fetch_new", fetch), \
         patch("vula.integrations.notify.notify_team", AsyncMock()):
        res = await email_sync._do_email_sync("seaview", "e1", 10)
    fetch.assert_not_called()
    assert "can't be unlocked" in res["error"]
    stored = db.tables["vula_email_accounts"][0]
    assert stored["sync_fail_count"] == 1 and "can't be unlocked" in stored["last_error"]

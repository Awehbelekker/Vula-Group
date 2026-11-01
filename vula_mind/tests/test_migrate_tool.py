"""tools/migrate.py — the migration runner, against a fake connection (no real Postgres)."""
import importlib.util
import sys
from pathlib import Path

import pytest

from vula import migrations_files

_spec = importlib.util.spec_from_file_location(
    "migrate_tool", Path(__file__).resolve().parents[1] / "tools" / "migrate.py")
migrate = importlib.util.module_from_spec(_spec)
sys.modules["migrate_tool"] = migrate
_spec.loader.exec_module(migrate)

_FAKE_PW = "s3cr3t" + "-pw"   # a made-up password: the tests prove it is never printed


def _dsn(user: str, host: str, port: int = 5432) -> str:
    """A connection string built at run time, so no credential-shaped literal sits in the file."""
    return "postgresql://" + user + ":" + _FAKE_PW + "@" + host + f":{port}/postgres"


STAGING_DSN = _dsn("postgres.stagingref", "aws-0-eu.pooler.supabase.com")
PROD_DSN = _dsn("postgres", "db.prodref123.supabase.co")
ENV = {"DATABASE_URL": STAGING_DSN, "SUPABASE_URL": "https://prodref123.supabase.co",
       "VULA_ENV": "production"}


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        c = self.conn
        if "to_regclass" in sql:
            self._result = [(c.has_table,)]
            return
        if sql.startswith("select filename"):
            self._result = [(n, r["number"], r["sha256"], None, r["applied_by"], r["baseline"])
                            for n, r in c.rows.items()]
            return
        if sql.startswith("insert into vula_schema_migrations"):
            name, num, sha, by, base = params
            c.staged_rows[name] = {"number": num, "sha256": sha, "applied_by": by, "baseline": base}
            return
        if "create table if not exists vula_schema_migrations" in sql:
            c.staged_table = True
        if "FAIL_ME" in sql:
            raise RuntimeError("syntax error at FAIL_ME")
        c.staged_sql.append(sql)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeConn:
    def __init__(self, rows=None, has_table=True):
        self.rows = dict(rows or {})
        self.has_table = has_table
        self.executed = []        # migration SQL that was committed
        self.staged_sql, self.staged_rows, self.staged_table = [], {}, False
        self.commits = self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.commits += 1
        self.executed += self.staged_sql
        self.rows.update(self.staged_rows)
        self.has_table = self.has_table or self.staged_table
        self.staged_sql, self.staged_rows, self.staged_table = [], {}, False

    def rollback(self):
        self.rollbacks += 1
        self.staged_sql, self.staged_rows, self.staged_table = [], {}, False

    def close(self):
        self.closed = True


@pytest.fixture
def mdir(tmp_path):
    (tmp_path / "001_first.sql").write_text("create table if not exists a (id int);\n")
    (tmp_path / "010_tenth.sql").write_text("create table if not exists b (id int);\n")
    (tmp_path / "002_second.sql").write_text("create table if not exists c (id int);\n")
    (tmp_path / "_APPLY_2026-09-10_combined.sql").write_text("-- bundle\n")
    (tmp_path / "notes.sql").write_text("-- not a migration\n")
    (tmp_path / "240_schema_migrations.sql").write_text(
        (migrations_files.MIGRATIONS_DIR / "240_schema_migrations.sql").read_text())
    return tmp_path


def _run(argv, conn, env=ENV):
    lines = []
    code = migrate.main(argv, connect_fn=lambda dsn: conn, env=env, out=lines.append)
    return code, "\n".join(lines)


def _row(f, baseline=False):
    return {"number": f.number, "sha256": f.sha256, "applied_by": "x", "baseline": baseline}


# ── file listing (shared with master.py) ─────────────────────────────────────────────────

def test_listing_is_numeric_order_and_skips_bundles(mdir):
    names = [f.name for f in migrations_files.list_migrations(mdir)]
    assert names == ["001_first.sql", "002_second.sql", "010_tenth.sql", "240_schema_migrations.sql"]


def test_real_migrations_dir_skips_the_apply_bundles():
    names = [f.name for f in migrations_files.list_migrations()]
    assert not any(n.startswith("_") for n in names)
    assert names.index("239_forms.sql") < names.index("240_schema_migrations.sql")
    nums = [f.number for f in migrations_files.list_migrations()]
    assert nums == sorted(nums)


def test_master_health_uses_the_same_listing():
    from vula.api import master
    assert [p.name for p in master._migration_files(["179", "176", "x"])] == [
        "176_reply_feedback.sql", "179_wa_inbound_tracking.sql"]


def test_hash_ignores_crlf(tmp_path):
    a, b = tmp_path / "001_a.sql", tmp_path / "002_b.sql"
    a.write_bytes(b"select 1;\nselect 2;\n")
    b.write_bytes(b"select 1;\r\nselect 2;\r\n")
    assert migrations_files.file_sha256(a) == migrations_files.file_sha256(b)


# ── status ───────────────────────────────────────────────────────────────────────────────

def test_status_reports_applied_pending_and_changed(mdir):
    files = migrations_files.list_migrations(mdir)
    rows = {"001_first.sql": _row(files[0]),
            "002_second.sql": {**_row(files[1]), "sha256": "old-hash"},
            "099_gone.sql": {"number": 99, "sha256": "h", "applied_by": "x", "baseline": True}}
    st = migrate.compute_status(files, rows)
    assert [f.name for f in st.applied] == ["001_first.sql"]
    assert [f.name for f in st.changed] == ["002_second.sql"]
    assert [f.name for f in st.pending] == ["010_tenth.sql", "240_schema_migrations.sql"]
    assert st.unknown == ["099_gone.sql"]
    code, out = _run(["status", "--target", "staging", "--migrations-dir", str(mdir)], FakeConn(rows))
    assert code == 0 and "CHANGED  002_second.sql" in out and "PENDING  010_tenth.sql" in out
    assert "UNKNOWN  099_gone.sql" in out


def test_status_without_the_table_says_so_and_writes_nothing(mdir):
    conn = FakeConn(has_table=False)
    code, out = _run(["status", "--target", "staging", "--migrations-dir", str(mdir)], conn)
    assert code == 0 and "doesn't exist yet" in out and "baseline" in out
    assert conn.executed == [] and conn.rows == {} and not conn.has_table


# ── apply ────────────────────────────────────────────────────────────────────────────────

def test_apply_default_is_a_dry_run_that_executes_nothing(mdir):
    conn = FakeConn(has_table=False)
    code, out = _run(["apply", "--target", "staging", "--migrations-dir", str(mdir)], conn)
    assert code == 0 and "dry run" in out
    assert out.index("001_first.sql") < out.index("002_second.sql") < out.index("010_tenth.sql")
    assert conn.executed == [] and conn.rows == {} and conn.commits == 0 and not conn.has_table


def test_apply_runs_pending_in_order_and_records_each(mdir):
    files = migrations_files.list_migrations(mdir)
    conn = FakeConn({"001_first.sql": _row(files[0])})
    code, out = _run(["apply", "--target", "staging", "--yes", "--migrations-dir", str(mdir)], conn)
    assert code == 0, out
    ran = [s for s in conn.executed if "create table if not exists" in s]
    assert ["c (id" in ran[0], "b (id" in ran[1]] == [True, True]   # 002 before 010
    assert set(conn.rows) == {f.name for f in files}
    assert conn.rows["010_tenth.sql"]["sha256"] == files[2].sha256
    assert conn.rows["010_tenth.sql"]["baseline"] is False
    assert not any("table if not exists a" in s for s in conn.executed)  # 001 not re-run
    assert conn.closed


def test_apply_bootstraps_the_table_from_migration_240(mdir):
    conn = FakeConn(has_table=False)
    code, out = _run(["apply", "--target", "staging", "--yes", "--migrations-dir", str(mdir)], conn)
    assert code == 0 and "created vula_schema_migrations" in out and conn.has_table
    assert "240_schema_migrations.sql" in conn.rows


def test_apply_stops_at_the_first_failure_and_keeps_nothing_from_it(mdir):
    (mdir / "002_second.sql").write_text("create table c (id int); FAIL_ME;\n")
    conn = FakeConn(has_table=True)
    code, out = _run(["apply", "--target", "staging", "--yes", "--migrations-dir", str(mdir)], conn)
    assert code == 1 and "FAILED 002_second.sql" in out and "stopped: 1 applied" in out
    assert list(conn.rows) == ["001_first.sql"]           # only the one before it is recorded
    assert not any("table c" in s for s in conn.executed)  # its SQL was rolled back
    assert not any("table if not exists b" in s for s in conn.executed)  # 010 never attempted


def test_apply_only_runs_one_file_and_refuses_an_applied_one(mdir):
    files = migrations_files.list_migrations(mdir)
    conn = FakeConn({"001_first.sql": _row(files[0])})
    code, out = _run(["apply", "--target", "staging", "--yes", "--only", "10",
                      "--migrations-dir", str(mdir)], conn)
    assert code == 0 and list(conn.rows) == ["001_first.sql", "010_tenth.sql"]
    code, out = _run(["apply", "--target", "staging", "--yes", "--only", "1",
                      "--migrations-dir", str(mdir)], conn)
    assert "already applied" in out and conn.commits == 1
    code, out = _run(["apply", "--target", "staging", "--only", "77", "--migrations-dir", str(mdir)], conn)
    assert code == 1 and "no migration file numbered 77" in out


def test_changed_files_are_not_rerun_unless_asked(mdir):
    files = migrations_files.list_migrations(mdir)
    rows = {f.name: _row(f) for f in files}
    rows["002_second.sql"]["sha256"] = "old"
    conn = FakeConn(rows)
    code, out = _run(["apply", "--target", "staging", "--yes", "--migrations-dir", str(mdir)], conn)
    assert "nothing to apply" in out and "1 changed file(s) are NOT re-run" in out and conn.executed == []
    code, out = _run(["apply", "--target", "staging", "--yes", "--only", "2",
                      "--migrations-dir", str(mdir)], conn)
    assert code == 0 and conn.rows["002_second.sql"]["sha256"] == files[1].sha256


# ── baseline ─────────────────────────────────────────────────────────────────────────────

def test_baseline_records_without_running(mdir):
    conn = FakeConn(has_table=False)
    code, out = _run(["baseline", "--target", "staging", "--upto", "2", "--migrations-dir", str(mdir)], conn)
    assert code == 0 and "dry run" in out and conn.rows == {} and not conn.has_table
    code, out = _run(["baseline", "--target", "staging", "--upto", "2", "--yes",
                      "--migrations-dir", str(mdir)], conn)
    assert code == 0 and set(conn.rows) == {"001_first.sql", "002_second.sql"}
    assert all(r["baseline"] for r in conn.rows.values())
    # only the bootstrap DDL (migration 240) ran — not 001/002
    assert not any("table if not exists a" in s or "table if not exists c" in s for s in conn.executed)
    code, out = _run(["apply", "--target", "staging", "--migrations-dir", str(mdir)], conn)
    assert "010_tenth.sql" in out and "001_first.sql" not in out


def test_baseline_needs_upto(mdir):
    code, out = _run(["baseline", "--target", "staging", "--migrations-dir", str(mdir)], FakeConn())
    assert code == 2 and "--upto" in out


# ── production guard / secrets ───────────────────────────────────────────────────────────

def test_production_writes_need_i_mean_production(mdir):
    env = {**ENV, "DATABASE_URL": PROD_DSN}
    conn = FakeConn()
    code, out = _run(["apply", "--target", "production", "--yes", "--migrations-dir", str(mdir)], conn, env)
    assert code == 2 and "--i-mean-production" in out and conn.commits == 0
    # read-only status and a dry run are allowed
    assert _run(["status", "--target", "production", "--migrations-dir", str(mdir)], conn, env)[0] == 0


def test_staging_target_refused_when_url_is_the_production_project(mdir):
    env = {**ENV, "DATABASE_URL": PROD_DSN}
    connected = []
    lines = []
    code = migrate.main(["status", "--target", "staging", "--migrations-dir", str(mdir)],
                        connect_fn=lambda d: connected.append(d), env=env, out=lines.append)
    assert code == 2 and "prodref123" in "\n".join(lines) and connected == []
    # the same URL is fine when this machine says it is staging
    assert migrate.check_target("staging", PROD_DSN, writes=True, i_mean_production=False,
                                supabase_url="https://prodref123.supabase.co", vula_env="staging") is None
    # the pooler form (user postgres.<ref>) is caught too
    pooler = _dsn("postgres.prodref123", "aws-0-eu.pooler.supabase.com", 6543)
    assert migrate.check_target("staging", pooler, writes=False, i_mean_production=False,
                                supabase_url="https://prodref123.supabase.co", vula_env="production")


def test_unbaselined_production_apply_is_refused(mdir):
    env = {**ENV, "DATABASE_URL": PROD_DSN}
    conn = FakeConn(has_table=False)
    code, out = _run(["apply", "--target", "production", "--i-mean-production", "--yes",
                      "--migrations-dir", str(mdir)], conn, env)
    assert code == 2 and "baseline" in out and conn.executed == [] and conn.rows == {}


def test_password_is_never_printed(mdir):
    code, out = _run(["status", "--target", "staging", "--migrations-dir", str(mdir)], FakeConn())
    assert _FAKE_PW not in out and "pooler.supabase.com" in out
    assert migrate.mask_dsn(STAGING_DSN) == "postgres.stagingref@aws-0-eu.pooler.supabase.com:5432/postgres"

    def boom(dsn):
        raise RuntimeError(f"connection to {dsn} failed: bad password {_FAKE_PW}")
    lines = []
    code = migrate.main(["status", "--target", "staging"], connect_fn=boom, env=ENV, out=lines.append)
    assert code == 1 and _FAKE_PW not in "\n".join(lines)


def test_no_database_url_is_refused():
    code, out = _run(["status", "--target", "staging"], FakeConn(), env={})
    assert code == 2 and "DATABASE_URL is not set" in out

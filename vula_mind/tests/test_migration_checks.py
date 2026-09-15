"""tools/check_migrations_rls.py — CI guardrails on migrations/*.sql.

Had zero test coverage before this. 2026-09-15 migration audit found migration number 112
genuinely claimed by two unrelated files (112_rls_credential_tables.sql and, until fixed,
112_team_member_window_tracking.sql — itself a prior renumbering of a collision with
108_tenants_paid_column.sql). Nothing had ever checked for this, so it happened twice. These
tests cover the new duplicate-number guard directly, plus the pre-existing RLS guard (also
previously untested), against small controlled fixture directories rather than the real
migrations/ tree.
"""
from tools.check_migrations_rls import _check_duplicate_numbers, _check_rls


def _write(tmp_path, name: str, sql: str):
    (tmp_path / name).write_text(sql, encoding="utf-8")


# ── duplicate migration numbers ──────────────────────────────────────────────────────────

def test_no_duplicates_passes(tmp_path, capsys):
    _write(tmp_path, "001_a.sql", "create table a (id int);")
    _write(tmp_path, "002_b.sql", "create table b (id int);")
    assert _check_duplicate_numbers(str(tmp_path)) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_two_files_sharing_a_number_fails(tmp_path, capsys):
    """The exact real-world shape: two unrelated migrations both claiming "112"."""
    _write(tmp_path, "112_rls_credential_tables.sql", "alter table x enable row level security;")
    _write(tmp_path, "112_team_member_window_tracking.sql", "alter table vula_team_members "
                                                              "add column if not exists x text;")
    assert _check_duplicate_numbers(str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "112" in out
    assert "112_rls_credential_tables.sql" in out
    assert "112_team_member_window_tracking.sql" in out


def test_unnumbered_files_are_ignored():
    """A combined/catch-up convenience file (e.g. _APPLY_2026-09-10_combined.sql) has no
    leading digits — must not be treated as claiming some number, or false-flagged at all."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        _write(tmp_path, "_APPLY_combined.sql", "create table a (id int);")
        _write(tmp_path, "001_a.sql", "create table a (id int);")
        assert _check_duplicate_numbers(str(tmp_path)) == 0


def test_three_files_sharing_a_number_still_fails(tmp_path):
    for name in ("005_a.sql", "005_b.sql", "005_c.sql"):
        _write(tmp_path, name, "create table t (id int);")
    assert _check_duplicate_numbers(str(tmp_path)) == 1


# ── RLS coverage (pre-existing behavior, previously untested) ────────────────────────────

def test_rls_enabled_passes(tmp_path, capsys):
    _write(tmp_path, "001_a.sql", "create table a (id int);\n"
                                   "alter table a enable row level security;")
    assert _check_rls(str(tmp_path)) == 0
    assert "OK" in capsys.readouterr().out


def test_missing_rls_fails(tmp_path, capsys):
    _write(tmp_path, "001_a.sql", "create table a (id int);")
    assert _check_rls(str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "a" in out


def test_rls_check_ignores_commented_out_statements(tmp_path):
    """A migration's own prose mentioning CREATE TABLE / ENABLE ROW LEVEL SECURITY inside a
    -- comment must never count as the real statement."""
    _write(tmp_path, "001_a.sql",
           "-- create table ghost (id int);\n"
           "create table real_table (id int);\n"
           "alter table real_table enable row level security;")
    assert _check_rls(str(tmp_path)) == 0

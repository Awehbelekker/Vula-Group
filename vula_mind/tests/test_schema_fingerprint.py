"""tools/schema_fingerprint.py: the check CI runs after replaying every migration onto a fresh
database. Before 3 Nov 2026 nothing compared the files with production, and a fresh database
stopped at migration 102."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import schema_fingerprint as sf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_round_trip():
    s = sf.from_rows([("b", "x", "text"), ("a", "id", "uuid"), ("a", "name", "text")])
    assert sf.parse(sf.render(s)) == s


def test_reports_the_drift_that_broke_fresh_databases():
    prod = sf.parse("vula_tenants: tenant_id:text, company_name:text\ncommerce_reviews: id:uuid\n")
    fresh = sf.parse("vula_tenants: tenant_id:uuid, company_name:text, id:int8\n")
    problems = sf.diff(prod, fresh)
    assert "missing table commerce_reviews" in problems
    assert "vula_tenants.tenant_id: type is uuid, production has text" in problems
    assert "vula_tenants: extra column id (int8)" in problems


def test_one_off_backup_tables_are_ignored():
    rows = [("ops_digg_self_supplier_backup_20261026", "id", "text"),
            ("vula_cleanup_log_20261002", "id", "int8"), ("vula_tenants", "tenant_id", "text")]
    assert list(sf.from_rows(rows)) == ["vula_tenants"]


def test_check_exit_codes(tmp_path, monkeypatch):
    snap = tmp_path / "s.txt"
    snap.write_text("t: a:text\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost/db")
    assert sf.main(["check", str(snap)], fetch=lambda dsn: [("t", "a", "text")]) == 0
    assert sf.main(["check", str(snap)], fetch=lambda dsn: [("t", "a", "uuid")]) == 1


def test_snapshot_has_the_tables_that_fresh_databases_used_to_lack():
    snap = sf.parse((ROOT / "migrations" / "production_schema.txt").read_text(encoding="utf-8"))
    for table in ("commerce_reviews", "vula_queries", "vula_subscriptions", "vula_users",
                  "vula_wa_prompt_state"):
        assert table in snap
    assert "tenant_id:text" in snap["vula_tenants"]
    assert "id:text" in snap["commerce_suppliers"]


def test_migration_242_closes_the_open_policies():
    sql = (ROOT / "migrations" / "242_production_parity.sql").read_text(encoding="utf-8")
    for table in ("vula_tenants", "vula_tenant_phones", "vula_subscriptions", "vula_users",
                  "vula_documents", "vula_queries"):
        assert f"'{table}'" in sql
    assert "for all to service_role" in sql
    assert "to public" not in sql.lower().replace("`to public using (true)`", "")


def test_no_migration_opens_a_table_to_every_role():
    # Production's six open tables (closed by 242) had `for all using (true)` with no `to`
    # clause, which means every role, anon included. Only service_role may get using (true).
    import re
    pat = re.compile(r"create\s+policy\s+(\"[^\"]+\"|\S+)\s+on\s+(\S+)(.*?);", re.I | re.S)
    bad = []
    for f in sorted((ROOT / "migrations").glob("*.sql")):
        sql = re.sub(r"--[^\n]*", "", f.read_text(encoding="utf-8"))
        for m in pat.finditer(sql):
            body = m.group(3).lower()
            if re.search(r"(using|with\s+check)\s*\(\s*true\s*\)", body) and "to service_role" not in body:
                bad.append(f"{f.name}: {m.group(1)} on {m.group(2)}")
    assert not bad, bad

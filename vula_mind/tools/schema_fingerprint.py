"""
tools/schema_fingerprint.py — do the migration files build the database production actually has?

3 Nov 2026. Production's schema grew partly by hand (the SQL editor, the old db/migrations and
vula_mind/supabase/migrations folders), so replaying migrations/*.sql onto a fresh database
did not give production's schema: it stopped at 102, and once past that, tenant ids were UUIDs
instead of text and five tables were missing (fixed in 001, 009 and 242). This keeps them
together:

    python tools/schema_fingerprint.py dump                         # print this database's tables
    python tools/schema_fingerprint.py check migrations/production_schema.txt

`check` compares every public table's columns (name and type) with the snapshot and exits 1 on
any difference. CI runs it on a fresh database after replaying every migration
(.github/workflows/ci.yml, "Fresh database"). When a migration adds or changes a column, the
snapshot changes in the same commit: `dump > migrations/production_schema.txt`.

Leftover one-off backup tables on production (ops_*, *_backup_YYYYMMDD, *_log_YYYYMMDD) are
not part of the schema and are ignored on both sides.

Connects with DATABASE_URL (read-only queries). The password is never printed.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

_SQL = ("select table_name, column_name, udt_name from information_schema.columns "
        "where table_schema = 'public' order by table_name, column_name")
_IGNORED = re.compile(r"^ops_|_backup_\d{8}$|_log_\d{8}$")

Schema = Dict[str, List[str]]


def ignored(table: str) -> bool:
    return bool(_IGNORED.search(table))


def from_rows(rows) -> Schema:
    out: Schema = {}
    for table, column, udt in rows:
        if not ignored(table):
            out.setdefault(table, []).append(f"{column}:{udt}")
    return {t: sorted(c) for t, c in sorted(out.items())}


def render(schema: Schema) -> str:
    return "".join(f"{t}: {', '.join(cols)}\n" for t, cols in schema.items())


def parse(text: str) -> Schema:
    out: Schema = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        table, _, cols = line.partition(": ")
        if not ignored(table):
            out[table] = sorted(c.strip() for c in cols.split(",") if c.strip())
    return out


def diff(expected: Schema, actual: Schema) -> List[str]:
    """Plain-language differences, empty when the schemas match."""
    out = []
    for t in sorted(set(expected) - set(actual)):
        out.append(f"missing table {t}")
    for t in sorted(set(actual) - set(expected)):
        out.append(f"extra table {t} (add it to the snapshot if a migration created it)")
    for t in sorted(set(expected) & set(actual)):
        e, a = dict(c.split(":", 1) for c in expected[t]), dict(c.split(":", 1) for c in actual[t])
        for c in sorted(set(e) - set(a)):
            out.append(f"{t}: missing column {c} ({e[c]})")
        for c in sorted(set(a) - set(e)):
            out.append(f"{t}: extra column {c} ({a[c]})")
        for c in sorted(set(e) & set(a)):
            if e[c] != a[c]:
                out.append(f"{t}.{c}: type is {a[c]}, production has {e[c]}")
    return out


def _fetch(dsn: str):
    try:
        import psycopg
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(_SQL)
            return cur.fetchall()
    except ImportError:
        import psycopg2
        conn = psycopg2.connect(dsn)
        try:
            cur = conn.cursor()
            cur.execute(_SQL)
            return cur.fetchall()
        finally:
            conn.close()


def main(argv: Optional[List[str]] = None, fetch: Callable = _fetch) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("dump", "check") or (argv[0] == "check" and len(argv) < 2):
        print(__doc__.strip().splitlines()[0])
        print("usage: schema_fingerprint.py dump | check <snapshot file>")
        return 2
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("DATABASE_URL is not set.")
        return 2
    actual = from_rows(fetch(dsn))
    if argv[0] == "dump":
        sys.stdout.write(render(actual))
        return 0
    problems = diff(parse(Path(argv[1]).read_text(encoding="utf-8")), actual)
    if problems:
        print(f"The database built from the migrations differs from {argv[1]}:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"OK: {len(actual)} tables match {argv[1]}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

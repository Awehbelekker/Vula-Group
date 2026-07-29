"""
tools/check_migrations_rls.py — CI guardrail (security remediation Phase 1d).

Fails (non-zero exit) if any table created in migrations/*.sql never gets
`ENABLE ROW LEVEL SECURITY` in the migration history. Run from vula_mind/:

    python tools/check_migrations_rls.py

A table with RLS enabled but a deliberately empty policy (shared/internal infra,
e.g. vula_scheduler_lock — see migration 115) still counts as covered: the point of
this check is "was RLS turned on," not "does every table have a permissive policy."
"""
from __future__ import annotations

import glob
import os
import re
import sys

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")

_CREATE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_RLS_RE = re.compile(
    r"alter\s+table\s+(?:only\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s+enable\s+row\s+level\s+security",
    re.IGNORECASE)


def main() -> int:
    created: set[str] = set()
    rls_enabled: set[str] = set()

    for path in sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql"))):
        with open(path, encoding="utf-8", errors="ignore") as f:
            sql = f.read()
        # Strip `-- ...` comment lines first — a migration's own prose (e.g. "CREATE TABLE vs
        # ENABLE ROW LEVEL SECURITY") would otherwise false-positive as a real statement.
        sql = re.sub(r"--[^\n]*", "", sql)
        created |= {m.group(1).lower() for m in _CREATE_RE.finditer(sql)}
        rls_enabled |= {m.group(1).lower() for m in _RLS_RE.finditer(sql)}

    missing = sorted(created - rls_enabled)
    if missing:
        print(f"FAIL: {len(missing)} table(s) created without RLS ever being enabled:")
        for name in missing:
            print(f"  - {name}")
        print("\nAdd `ALTER TABLE <name> ENABLE ROW LEVEL SECURITY;` (with a tenant_isolation "
             "policy, or an explicit no-policy comment for genuinely tenant-less tables — see "
             "migrations/115_rls_shared_infra_tables.sql for that pattern) before merging.")
        return 1

    print(f"OK: all {len(created)} tables have RLS enabled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

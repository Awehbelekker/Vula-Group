"""
tools/check_migrations_rls.py — CI guardrail (security remediation Phase 1d + migration numbering).

Fails (non-zero exit) if:
  1. Any table created in migrations/*.sql never gets `ENABLE ROW LEVEL SECURITY` in the
     migration history. A table with RLS enabled but a deliberately empty policy (shared/
     internal infra, e.g. vula_scheduler_lock — see migration 115) still counts as covered: the
     point of this check is "was RLS turned on," not "does every table have a permissive
     policy."
  2. Two migration files share the same leading number. 2026-09-15 audit found this had
     genuinely happened twice — migration 112 collided once (108_tenants_paid_column.sql vs the
     original 108_team_member_window_tracking.sql, fixed by renumbering to 112), and the FIX
     ITSELF collided again with 112_rls_credential_tables.sql (fixed by renumbering to 161) —
     because nothing was ever checking for this. There is no schema_migrations table (migrations
     are applied by hand — see vula/startup_checks.py's own docstring), so the filename number
     is the only ordering signal a human has; two files silently sharing one is exactly the kind
     of thing that looks fine in a diff and only bites someone reading migration history later.

Run from vula_mind/:

    python tools/check_migrations_rls.py
"""
from __future__ import annotations

import glob
import os
import re
import sys
from collections import defaultdict

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")

_CREATE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_RLS_RE = re.compile(
    r"alter\s+table\s+(?:only\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s+enable\s+row\s+level\s+security",
    re.IGNORECASE)
_PREFIX_RE = re.compile(r"^(\d+)_")


def _check_duplicate_numbers(migrations_dir: str = MIGRATIONS_DIR) -> int:
    by_number: dict[str, list[str]] = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(migrations_dir, "*.sql"))):
        name = os.path.basename(path)
        m = _PREFIX_RE.match(name)
        if m:
            by_number[m.group(1)].append(name)

    dupes = {num: names for num, names in by_number.items() if len(names) > 1}
    if not dupes:
        return 0

    print(f"FAIL: {len(dupes)} migration number(s) claimed by more than one file:")
    for num, names in sorted(dupes.items(), key=lambda kv: int(kv[0])):
        print(f"  - {num}: {', '.join(names)}")
    print("\nRenumber one of each pair to the next unused number (git mv, then update its own "
         "header comment noting the rename — see migrations/161_team_member_window_tracking.sql "
         "for the pattern). Already-applied migrations don't need re-running: there's no "
         "schema_migrations table, so a filename rename is bookkeeping only.")
    return 1


def _check_rls(migrations_dir: str = MIGRATIONS_DIR) -> int:
    created: set[str] = set()
    rls_enabled: set[str] = set()

    for path in sorted(glob.glob(os.path.join(migrations_dir, "*.sql"))):
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


def main() -> int:
    # Run both checks and report everything at once rather than stopping at the first failure —
    # a migration PR is much cheaper to fix once, seeing every problem, than round-tripping CI.
    rls_result = _check_rls()
    dup_result = _check_duplicate_numbers()
    return 1 if (rls_result or dup_result) else 0


if __name__ == "__main__":
    sys.exit(main())

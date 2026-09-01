"""Remove test-run rows from the production telemetry sink.

Found 2026-09-01: the test suite was writing real rows into vula_reasoning_telemetry, under
fixture tenant ids. At least 1000 rows spanning 2026-07-27 to 2026-09-01 — 826 of them
'verified-reasoning' — which is precisely the data used to judge the checker_error rate, the
defect rate and the degenerate-output count. Those numbers were being read this session to make
decisions, and they were wrong.

The source is fixed (core/reasoning_telemetry.py now no-ops under PYTEST_CURRENT_TEST); this
clears the history so the remaining numbers describe real traffic only.

Deletes ONLY rows whose tenant_id is a known test fixture name — never a null tenant_id, which
belongs to legitimate untenanted platform events. Dry-run by default.

Run:  PYTHONPATH=. railway run python scripts/purge_test_telemetry.py [--confirm]
"""
import sys
from collections import Counter

from vula.commerce import service

# Fixture tenant ids used across the test suite. Deliberately an explicit allow-list rather than
# a pattern: a real tenant must never be caught by a wildcard.
TEST_TENANTS = ["test-tenant", "tenant-abc", "tenant-xyz", "t1", "acme"]


def main() -> int:
    db = service._client()
    confirm = "--confirm" in sys.argv

    total = 0
    per_tenant = Counter()
    for t in TEST_TENANTS:
        try:
            rows = (db.table("vula_reasoning_telemetry").select("id", count="exact")
                    .eq("tenant_id", t).limit(1).execute())
            n = rows.count if rows.count is not None else len(rows.data or [])
        except Exception as exc:
            print(f"  count failed for {t}: {str(exc)[:100]}")
            continue
        if n:
            per_tenant[t] = n
            total += n

    if not total:
        print("No test-tenant telemetry rows found — already clean.")
        return 0

    print("Test-run rows in the production telemetry sink:")
    for t, n in per_tenant.most_common():
        print(f"  {t:<14} {n:>6}")
    print(f"  {'TOTAL':<14} {total:>6}")

    if not confirm:
        print("\nDry run. Re-run with --confirm to delete these rows.")
        print("Real tenants and untenanted (null) platform events are never touched.")
        return 0

    for t in per_tenant:
        db.table("vula_reasoning_telemetry").delete().eq("tenant_id", t).execute()
        print(f"deleted rows for {t}")
    print(f"\nDone — {total} test row(s) removed. Remaining telemetry is real traffic only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

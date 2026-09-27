"""
tools/check_migration_sentinels.py — CI guardrail: every new migration can be seen from prod.

Migrations are applied by hand in the Supabase SQL editor (there is no runner), so the only way
anyone learns one was missed is the boot schema check (vula/startup_checks.py::_SENTINELS) and
Master › Health, whose migration probes are generated from that same list. A migration with no
sentinel is invisible to both. From FIRST_CHECKED on, every migration must either have a
sentinel or be listed in NO_PROBE with the reason nothing selectable changes.

Run from vula_mind/:

    python tools/check_migration_sentinels.py
"""
from __future__ import annotations

import glob
import os
import re
import sys

FIRST_CHECKED = 175

# Migrations whose effect a `select` can't observe (constraint/index/backfill-only).
NO_PROBE = {
    "175": "relaxes NOT NULLs and backfills rows — nothing a select can probe",
}


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, here)
    from vula.startup_checks import _SENTINELS
    probed = {num for num, _table, _col in _SENTINELS}
    missing = []
    for path in sorted(glob.glob(os.path.join(here, "migrations", "*.sql"))):
        m = re.match(r"(\d+)_", os.path.basename(path))
        if not m or int(m.group(1)) < FIRST_CHECKED:
            continue
        num = m.group(1)
        if num not in probed and num not in NO_PROBE:
            missing.append(os.path.basename(path))
    if missing:
        print("Migrations with no startup_checks._SENTINELS entry (Master › Health can't see if "
              "they were applied). Add a sentinel, or list them in NO_PROBE with a reason:")
        for f in missing:
            print(f"  {f}")
        return 1
    print(f"OK: every migration from {FIRST_CHECKED} on has a sentinel or a NO_PROBE reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

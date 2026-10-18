"""Every status the expense code writes must be allowed by the latest commerce_expenses CHECK.

The 060 CHECK never listed 'unconfirmed' (written by record_claim(confirmed=False) since #115),
so the database rejected every unconfirmed receipt. Migration 216 adds it; this keeps the code
and the newest migration from drifting apart again.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _latest_allowed() -> set[str]:
    allowed = None
    for f in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql")):
        m = re.search(r"commerce_expenses_status_check\s+check\s*\(status in \(([^)]*)\)\)",
                      f.read_text(encoding="utf-8"), re.I)
        if m:
            allowed = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert allowed, "no commerce_expenses_status_check found in migrations"
    return allowed


def _written_statuses() -> set[str]:
    src = (ROOT / "vula" / "commerce" / "expenses.py").read_text(encoding="utf-8")
    found = set(re.findall(r'"status":\s*"([a-z_]+)"', src))
    found |= set(re.findall(r'"status":\s*"[a-z_]+"\s+if\s+\w+\s+else\s+"([a-z_]+)"', src))
    return found


def test_unconfirmed_is_allowed():
    assert "unconfirmed" in _latest_allowed()


def test_every_written_status_is_allowed():
    written = _written_statuses()
    assert {"submitted", "unconfirmed"} <= written
    assert written <= _latest_allowed(), written - _latest_allowed()


def test_216_keeps_every_060_status():
    old = {"pending", "submitted", "approved", "paid", "reimbursed", "rejected", "cancelled"}
    assert old <= _latest_allowed()

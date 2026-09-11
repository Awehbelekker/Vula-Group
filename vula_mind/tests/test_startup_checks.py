"""vula/startup_checks.py — the boot-time "did the migrations actually get applied" probe.
2026-09-11: audited and extended _SENTINELS from 17 to 51 entries (every migration 100+ that
adds a table/column a live code path actually reads), as part of the pre-go-live brief's
"only 17/157 migrations checked" finding. No test file existed for this mechanism at all
before now."""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from vula.startup_checks import _SENTINELS, check_schema

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def test_sentinel_list_has_no_duplicate_entries():
    assert len(_SENTINELS) == len(set(_SENTINELS))


def test_every_sentinel_migration_number_has_a_real_migration_file():
    """Catches a typo'd migration number before it misleads whoever reads the list."""
    existing = {re.match(r"(\d+)_", p.name).group(1)
               for p in MIGRATIONS_DIR.glob("*.sql") if re.match(r"\d+_", p.name)}
    for migration, _table, _column in _SENTINELS:
        assert migration in existing, f"sentinel cites migration {migration}, no such file"


def test_no_two_sentinels_are_identical_table_and_column():
    """Same (table, column) checked twice is either a mistake or truly redundant — the
    migration number is the only thing that would differ, and that alone isn't useful twice."""
    pairs = [(t, c) for _m, t, c in _SENTINELS]
    assert len(pairs) == len(set(pairs))


def _client_with_missing(table_to_fail: str):
    client = MagicMock()

    def table(name):
        m = MagicMock()
        if name == table_to_fail:
            m.select.return_value.limit.return_value.execute.side_effect = Exception(
                f'relation "{name}" does not exist')
        else:
            m.select.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        return m

    client.table.side_effect = table
    return client


def test_check_schema_reports_a_genuinely_missing_table():
    missing_table = _SENTINELS[0][1]
    with patch("vula.commerce.service._client", return_value=_client_with_missing(missing_table)):
        missing = check_schema()
    assert any(missing_table in m for m in missing)


def test_check_schema_clean_when_everything_present():
    client = MagicMock()
    client.table.return_value.select.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=[])
    with patch("vula.commerce.service._client", return_value=client):
        assert check_schema() == []


def test_check_schema_fails_open_with_no_db_client():
    with patch("vula.commerce.service._client", side_effect=RuntimeError("no client")):
        assert check_schema() == []

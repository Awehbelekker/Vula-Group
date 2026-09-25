"""Every new tenant gets a slug-keyed billing row (2026-09-25 review)."""
from unittest.mock import MagicMock, patch

from vula.api import tenants


def _db(existing):
    q = MagicMock()
    for m in ("select", "eq", "limit", "insert"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=existing)
    db = MagicMock()
    db.table.return_value = q
    return db, q


def test_billing_row_created_once_keyed_by_slug():
    db, q = _db([])
    with patch.object(tenants, "_client", return_value=db):
        tenants.ensure_billing_row("new-shop", "New Shop", email="o@x.co", trial_days=30)
    row = q.insert.call_args.args[0]
    assert row["workspace_slug"] == "new-shop" and row["email"] == "o@x.co" and "trial_ends" in row

    db, q = _db([{"id": 1}])
    with patch.object(tenants, "_client", return_value=db):
        tenants.ensure_billing_row("new-shop", "New Shop")
    q.insert.assert_not_called()


def test_master_created_row_has_no_trial():
    db, q = _db([])
    with patch.object(tenants, "_client", return_value=db):
        tenants.ensure_billing_row("digg-2", "DIGG 2")
    row = q.insert.call_args.args[0]
    assert "trial_ends" not in row and "email" not in row

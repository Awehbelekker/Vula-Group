"""Tests for master's tenant-impersonation audit endpoint (2026-09-15).

2026-09-15 audit found "Open as tenant" already fully worked — is_tenant_member already lets
a master JWT through tenant_admin_guard for any tenant (vula/api/tenant_auth.py), and any WRITE
master makes while impersonating already gets attributed to their real identity via
require_tenant_actor + merchant_audit. What was actually missing was a dedicated, clean record
of WHO looked at WHICH tenant's real data, WHEN, and WHY — this endpoint is that record, logged
the moment the dashboard's "Open as tenant" action fires. Dual-written to both audit trails,
matching the established pattern every other tenant-affecting master action already uses (see
master_mark_paid/master_cancel_subscription in vula/api/master.py).
"""
from unittest.mock import MagicMock, patch

import pytest

from vula.api import master

_IDENTITY = {"user_id": "m1", "email": "master@vula.app", "role": "master"}


@pytest.mark.asyncio
async def test_impersonate_writes_to_both_audit_trails():
    with (
        patch("vula.api.master.audit") as mock_master_audit,
        patch("vula.api.merchant_audit.audit") as mock_merchant_audit,
    ):
        result = await master.master_impersonate_tenant(
            "off-the-hook", {"reason": "reproducing a customer's reported bug"}, identity=_IDENTITY)

    assert result == {"ok": True}
    mock_master_audit.assert_called_once_with(
        _IDENTITY, "master_impersonate_tenant", "off-the-hook",
        reason="reproducing a customer's reported bug")
    mock_merchant_audit.assert_called_once_with(
        "off-the-hook", _IDENTITY, "master_viewed_as_tenant",
        reason="reproducing a customer's reported bug")


@pytest.mark.asyncio
async def test_impersonate_reason_is_optional():
    with (
        patch("vula.api.master.audit") as mock_master_audit,
        patch("vula.api.merchant_audit.audit") as mock_merchant_audit,
    ):
        await master.master_impersonate_tenant("off-the-hook", {}, identity=_IDENTITY)

    assert mock_master_audit.call_args.kwargs["reason"] is None
    assert mock_merchant_audit.call_args.kwargs["reason"] is None


@pytest.mark.asyncio
async def test_impersonate_handles_missing_body():
    """The frontend always sends a body today, but a defensive None must not crash this."""
    with (
        patch("vula.api.master.audit") as mock_master_audit,
        patch("vula.api.merchant_audit.audit"),
    ):
        result = await master.master_impersonate_tenant("off-the-hook", None, identity=_IDENTITY)
    assert result == {"ok": True}
    assert mock_master_audit.call_args.kwargs["reason"] is None


@pytest.mark.asyncio
async def test_impersonate_strips_whitespace_only_reason_to_none():
    with (
        patch("vula.api.master.audit") as mock_master_audit,
        patch("vula.api.merchant_audit.audit"),
    ):
        await master.master_impersonate_tenant("off-the-hook", {"reason": "   "}, identity=_IDENTITY)
    assert mock_master_audit.call_args.kwargs["reason"] is None


@pytest.mark.asyncio
async def test_impersonate_never_raises_when_the_audit_writer_itself_fails():
    """audit()/merchant_audit.audit() are already individually fail-open (see their own
    docstrings) — confirm this endpoint doesn't add a NEW way for a DB hiccup to turn a
    routine "open as tenant" click into a 500."""
    mock_db = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.side_effect = RuntimeError("db down")
    with patch("vula.api.master._client", return_value=mock_db):
        result = await master.master_impersonate_tenant(
            "off-the-hook", {"reason": "x"}, identity=_IDENTITY)
    assert result == {"ok": True}

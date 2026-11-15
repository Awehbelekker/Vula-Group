"""Access fixes from the 13 Nov 2026 audit.

Removing a team member deleted their team row but left their login, and a staff login with no
team row is given full access, so "Remove" upgraded a rep who could only see orders to
everything. The login (email or WhatsApp sign-in) is now pulled before the row goes.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api import team


def _db(row):
    db = MagicMock()
    tbl = MagicMock()
    db.table.return_value = tbl
    tbl.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [row]
    return db, tbl


@pytest.mark.asyncio
async def test_removing_a_member_pulls_their_email_and_whatsapp_logins_first():
    db, tbl = _db({"email": "rep@shop.co.za", "whatsapp": "082 000 1111"})
    order = []
    tbl.delete.side_effect = lambda: order.append("delete row") or MagicMock()
    users = {"rep@shop.co.za": "u-email", "wa-27820001111@users.vula-ai.com": "u-wa"}

    async def find(e):
        return users.get(e)

    async def remove(tenant, user_id, identity=None):
        order.append(f"revoke {user_id}")
    with (patch.object(team, "_client", return_value=db),
          patch("vula.api.users._find_user_id", new=find),
          patch("vula.api.users.remove_access", new=remove),
          patch.object(team.merchant_audit, "audit") as audit,
          patch("vula.personal_memory.erase_member")):
        out = await team.remove_member("shop", "m1", identity={"user_id": "owner"})
    assert out["login_revoked"] is True
    assert order == ["revoke u-email", "revoke u-wa", "delete row"]
    assert audit.call_args.kwargs["login_revoked"] is True


@pytest.mark.asyncio
async def test_a_failed_removal_says_so_instead_of_reporting_removed():
    with (patch.object(team, "_client", side_effect=RuntimeError("db down")),
          patch.object(team, "_revoke_matching_login", new=AsyncMock(return_value=False)),
          patch("vula.personal_memory.erase_member")):
        with pytest.raises(team.HTTPException) as e:
            await team.remove_member("shop", "m1", identity={"user_id": "owner"})
    assert e.value.status_code == 500


def test_xero_and_shopify_secrets_are_re_saved_on_a_key_change():
    """13 Nov 2026 audit: encrypted but missing from FIELDS, so a key rotation left them behind."""
    from vula.secret_keys import FIELDS
    have = {(f.table, f.col) for f in FIELDS}
    assert {("vula_xero_accounts", "access_token"), ("vula_xero_accounts", "refresh_token"),
            ("vula_shopify_accounts", "client_secret")} <= have


@pytest.mark.asyncio
async def test_the_legacy_payfast_notify_is_closed_while_payfast_is_off(monkeypatch):
    from types import SimpleNamespace
    from vula.api import onboarding
    monkeypatch.setattr(onboarding.settings, "payfast_merchant_id", "")
    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="1.2.3.4"))
    with pytest.raises(onboarding.HTTPException) as e:
        await onboarding.payfast_notify(req)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_the_legacy_payfast_notify_reads_the_real_caller_and_checks_the_merchant(monkeypatch):
    from types import SimpleNamespace
    from vula.api import onboarding
    monkeypatch.setattr(onboarding.settings, "payfast_merchant_id", "10000100")
    monkeypatch.setattr(onboarding.settings, "debug", False)
    spoof = SimpleNamespace(headers={"x-forwarded-for": "197.97.145.144, 9.9.9.9"},
                            client=SimpleNamespace(host="10.0.0.1"))
    with pytest.raises(onboarding.HTTPException) as e:          # the caller-sent hop doesn't count
        await onboarding.payfast_notify(spoof)
    assert e.value.status_code == 403

    async def form():
        return {"merchant_id": "99999", "payment_status": "COMPLETE", "m_payment_id": "acme"}
    real = SimpleNamespace(headers={"x-forwarded-for": "9.9.9.9, 197.97.145.144"},
                           client=SimpleNamespace(host="10.0.0.1"), form=form)
    assert await onboarding.payfast_notify(real) == {"status": "ignored"}

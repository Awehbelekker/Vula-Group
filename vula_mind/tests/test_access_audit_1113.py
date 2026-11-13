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

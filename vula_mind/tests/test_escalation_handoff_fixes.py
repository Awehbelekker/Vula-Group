"""Escalation answers stay on the tenant they were given on; failed inbox sends are reported;
stale-handoff pings go to the assignee (2026-09-25 review)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def test_open_escalation_is_scoped_to_the_replying_tenant():
    from vula import escalation as esc
    q = MagicMock()
    for m in ("select", "eq", "order", "limit"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=[])
    db = MagicMock()
    db.table.return_value = q
    with patch.object(esc, "_client", return_value=db):
        esc.open_escalation_for_helper("27821112222", "digg-demo")
    assert ("tenant_id", "digg-demo") in [c.args for c in q.eq.call_args_list]


@pytest.mark.asyncio
async def test_inbox_reply_reports_undelivered_send():
    from vula.api import commerce
    with patch.object(commerce.service, "get_conversation_thread",
                      AsyncMock(return_value={"phone": "27821112222", "paused": True})), \
         patch("vula.api.whatsapp._send_reply", AsyncMock(return_value=False)), \
         patch.object(commerce.service, "append_message", AsyncMock()) as append:
        with pytest.raises(HTTPException) as exc:
            await commerce.admin_reply("t1", "s1", commerce.AgentReplyRequest(message="hi"))
    assert exc.value.status_code == 502
    append.assert_not_awaited()   # never logged as sent


def test_assigned_member_lookup():
    from vula.api import server
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.execute.return_value = MagicMock(data=[{"name": "Stacy", "email": "s@x.co", "whatsapp": "27737815979"}])
    db = MagicMock()
    db.table.return_value = q
    with patch("vula.commerce.service._client", return_value=db):
        assert server._assigned_member("t", "stacy")["whatsapp"] == "27737815979"
        assert server._assigned_member("t", "+27 73 781 5979")["name"] == "Stacy"
        assert server._assigned_member("t", "") is None
        assert server._assigned_member("t", "someone else") is None

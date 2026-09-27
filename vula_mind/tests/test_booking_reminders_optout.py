"""Booking reminders honour STOP — the opt-out reply promises no more reminder messages."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from vula.bookings import reminders


def test_opted_out_customer_gets_no_reminder_but_others_do():
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    rows = [{"id": "b1", "customer_name": "Ann", "customer_phone": "0821111111", "service_name": "Cut",
             "start_at": start, "reminder_sent": False, "status": "confirmed"},
            {"id": "b2", "customer_name": "Ben", "customer_phone": "27822222222", "service_name": "Cut",
             "start_at": start, "reminder_sent": False, "status": "confirmed"}]
    db = MagicMock()
    q = db.table.return_value
    for m in ("select", "eq", "gte", "lte", "update"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=rows)
    send = AsyncMock(return_value=True)
    with patch.object(reminders.bs, "get_settings", new=AsyncMock(return_value={"timezone": "Africa/Johannesburg",
                                                                               "reminder_hours": 24})), \
         patch.object(reminders.bs, "_client", return_value=db), \
         patch("vula.api.commerce._suppressed_phones", return_value={"27821111111"}), \
         patch("vula.api.tenants.get_config", return_value={"display_name": "Salon"}), \
         patch("vula.api.whatsapp._send_reply", new=send):
        sent = asyncio.run(reminders.send_due_reminders("salon"))
    assert sent == 1
    assert [c.args[0] for c in send.await_args_list] == ["27822222222"]

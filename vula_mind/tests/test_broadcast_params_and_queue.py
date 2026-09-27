"""Broadcast templates get their body parameters; big audiences send in the background."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api import commerce


def test_body_params_fill_first_name_and_static_values():
    assert commerce._template_body_params({"param_count": 0}, {"name": "Jo"}, []) == []
    assert commerce._template_body_params({"param_count": 1}, {"name": "Jo Smith"}, []) == \
        [{"type": "text", "text": "Jo"}]
    assert commerce._template_body_params({"param_count": 1}, {"name": ""}, []) == \
        [{"type": "text", "text": "there"}]
    assert [p["text"] for p in commerce._template_body_params(
        {"param_count": 2}, {"name": "Jo"}, ["20% off", "Friday"])] == ["20% off", "Friday"]


@pytest.mark.asyncio
async def test_large_audience_is_queued_not_sent_inline():
    people = [{"name": f"C{i}", "phone": f"2782000{i:04d}", "total_spent_cents": 0} for i in range(60)]
    db = MagicMock()
    q = db.table.return_value
    for m in ("select", "eq", "limit", "insert", "update", "in_"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=[{"header_type": None, "buttons": None, "param_count": 1}])
    ran = []
    with patch.object(commerce.service, "_client", return_value=db), \
         patch.object(commerce, "_aggregate_customers", AsyncMock(return_value=people)), \
         patch.object(commerce, "_filter_audience", side_effect=lambda rows, aud: rows), \
         patch.object(commerce, "_suppressed_phones", return_value=set()), \
         patch("vula.api.whatsapp._get_tenant_wa_creds", AsyncMock(return_value={"phone_id": "1", "token": "t"})), \
         patch("vula.commerce.background_tasks.run_background",
               side_effect=lambda t, label, coro: (ran.append(label), coro.close())):
        out = await commerce.admin_send_broadcast("t1", {"template_name": "promo", "dry_run": False})
    assert out["queued"] is True and out["recipient_count"] == 60 and ran == ["broadcast_send"]

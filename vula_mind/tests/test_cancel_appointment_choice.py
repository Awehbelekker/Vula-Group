"""With more than one upcoming booking, cancel asks which instead of cancelling the earliest."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.commerce_assistant import CommerceAssistantSkill

B = [{"id": "b2", "service_name": "Colour", "start_at": "2026-10-03T10:00:00+00:00"},
     {"id": "b1", "service_name": "Cut", "start_at": "2026-10-01T09:00:00+00:00"}]


@pytest.mark.asyncio
async def test_asks_then_cancels_the_chosen_one():
    skill = CommerceAssistantSkill()
    set_status = AsyncMock()
    with patch("vula.bookings.service.list_bookings", AsyncMock(return_value=list(B))), \
         patch("vula.bookings.service.set_status", set_status), \
         patch("vula.bookings.service._now_utc"):
        ask = await skill._exec_cancel_appointment("t", "2782", {})
        assert ask["status"] == "need_info" and "1. Cut" in ask["message"] and "2. Colour" in ask["message"]
        set_status.assert_not_awaited()
        done = await skill._exec_cancel_appointment("t", "2782", {"choice": 2})
    set_status.assert_awaited_once_with("t", "b2", "cancelled")
    assert done["cancelled"] is True


@pytest.mark.asyncio
async def test_single_booking_cancels_directly():
    skill = CommerceAssistantSkill()
    set_status = AsyncMock()
    with patch("vula.bookings.service.list_bookings", AsyncMock(return_value=[B[1]])), \
         patch("vula.bookings.service.set_status", set_status), \
         patch("vula.bookings.service._now_utc"):
        await skill._exec_cancel_appointment("t", "2782", {})
    set_status.assert_awaited_once_with("t", "b1", "cancelled")

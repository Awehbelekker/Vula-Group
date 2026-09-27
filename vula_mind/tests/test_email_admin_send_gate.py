"""email_admin in send mode only sends after an explicit confirm (2026-09-25 review — the rule
was prompt-only, so a model that skipped it sent real email)."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.base import need_info_message
from core.skills.email_admin import EmailAdminSkill


@pytest.mark.asyncio
async def test_send_mode_previews_first_then_sends_on_confirm():
    skill = EmailAdminSkill()
    creds = {"send_mode": "send"}
    send = AsyncMock(return_value={"sent": True})
    args = {"to": "jo@example.com", "subject": "Quote", "body": "Hi Jo, attached."}
    import core.skills.email_admin as ea
    with patch.object(ea.service, "send", send):
        first = await skill._dispatch("email_draft", args, "t1", creds)
        assert "jo@example.com" in need_info_message(first) and "YES" in need_info_message(first)
        send.assert_not_awaited()
        await skill._dispatch("email_draft", {**args, "confirm": True}, "t1", creds)
    send.assert_awaited_once()

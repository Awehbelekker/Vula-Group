"""Connecting a mailbox says why it failed, in words (8 Nov 2026: Off the Hook's reconnect was
turned away while signed out, and the card blamed the password every time)."""
from unittest.mock import AsyncMock, patch

import pytest

from vula.api import email_connect

BODY = email_connect.ConnectIn(tenant_id="t-1", email="info@shop.example.test", password="x",
                               imap_host="mail.shop.example.test")


async def _connect_with(error: str) -> dict:
    with patch("vula.api.tenant_auth.check_body_tenant", AsyncMock(return_value=None)), \
         patch.object(email_connect.service, "test_connection", AsyncMock(return_value={"ok": False, "error": error})):
        return await email_connect.connect(BODY, authorization="Bearer t")


@pytest.mark.asyncio
@pytest.mark.parametrize("raw, words", [
    ("b'[AUTHENTICATIONFAILED] Authentication failed.'", "The mail server refused the password."),
    ("[Errno -2] Name or service not known", "The mail server address can't be found."),
    ("timed out", "The mail server didn't answer."),
    ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", "A secure connection to the mail server failed."),
])
async def test_a_failed_connect_says_why(raw, words):
    out = await _connect_with(raw)
    assert out["status"] == "error"
    assert out["error"] == words
    assert out["detail"] == raw          # the raw error stays for support


@pytest.mark.asyncio
async def test_an_unknown_failure_still_comes_back():
    out = await _connect_with("something odd")
    assert out == {"status": "error", "error": "Something odd.", "detail": "something odd"}

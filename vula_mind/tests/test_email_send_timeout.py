"""Test for the wall-clock timeout wrapping vula/email_imap/service.py's send() — confirmed
live 2026-09-20: smtplib's own timeout= only bounds each individual socket operation, not the
whole call. A hung login() that raises after its own timeout can still cost a SECOND full
timeout when the `with` block's implicit quit() cleanup (in __exit__) hits the same dead
connection — nearly doubling the worst case. Live symptom: a digg-demo alert email blocked its
caller (a WhatsApp webhook handler) for ~41s instead of failing in ~20s. asyncio.wait_for here
bounds the true worst case regardless of what smtplib does internally."""
import asyncio
import time
from unittest.mock import patch

import pytest

from vula.email_imap.service import send

CREDS = {"smtp_host": "smtp.example.com", "smtp_port": 465, "email": "a@example.com", "password": "x"}


@pytest.mark.asyncio
async def test_send_times_out_well_under_smtplibs_own_worst_case():
    """Simulates the confirmed-live failure: _send blocks far longer than a single smtplib
    timeout (e.g. two stacked 20s timeouts = ~40s). send() must raise once its own ceiling is
    hit rather than waiting out whatever smtplib does internally. Timeout patched down to keep
    this test fast — the mechanism (wait_for wrapping the blocking call) is what's under test,
    not the specific 25s production value."""
    def _hangs(*args, **kwargs):
        time.sleep(2.0)  # longer than the patched-down ceiling below

    with (
        patch("vula.email_imap.service._send", side_effect=_hangs),
        patch("vula.email_imap.service._SEND_WALL_CLOCK_TIMEOUT_S", 0.2),
    ):
        start = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await send(CREDS, "to@example.com", "subject", "body")
        elapsed = time.monotonic() - start

    assert elapsed < 1.0  # bounded by the ceiling, not by how long _send actually hangs


@pytest.mark.asyncio
async def test_send_returns_normally_when_fast():
    with patch("vula.email_imap.service._send", return_value={"sent": True, "to": "to@example.com"}):
        result = await send(CREDS, "to@example.com", "subject", "body")
    assert result == {"sent": True, "to": "to@example.com"}

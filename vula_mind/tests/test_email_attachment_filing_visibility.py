"""Real incident, 2026-09-22 (DIGG tenant): a client was certain an invoice had been emailed to
Judy's synced mailbox, but Vula never had it. Root cause: every exception in the email-attachment
filing pipeline (vula/email_imap/sync.py::_file_attachment, plus its caller's per-attachment loop
in _do_email_sync) was caught and logged at DEBUG — and vula/api/server.py's logging.basicConfig()
runs at INFO in production (DEBUG only when settings.debug), so a failed attachment vanished with
zero trace: no log line, no WhatsApp notification, nothing in the dashboard. Worse, the sync
cursor (last_sync_uid) still advances past the email regardless of per-attachment success, so a
failed attachment is never retried either — permanent, silent data loss.

The fix funnels both catch sites through _report_filing_failure(), which logs at WARNING (visible
in production) and sends a WhatsApp nudge via notify_team so a human learns the document needs
manual attention instead of it simply disappearing.
"""
import inspect
import logging
from unittest.mock import AsyncMock, patch

import pytest

from vula.email_imap import sync as email_sync


@pytest.mark.asyncio
async def test_report_filing_failure_logs_at_warning_not_debug(caplog):
    em = {"from": "supplier@example.co.za", "subject": "Invoice"}
    att = {"name": "Invoice 123.pdf"}
    with patch("vula.integrations.notify.notify_team", AsyncMock()):
        with caplog.at_level(logging.WARNING, logger="vula.email_imap.sync"):
            await email_sync._report_filing_failure(
                "digg-demo", em, att, RuntimeError("boom"), "filed_documents record failed")
    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "a filing failure must be visible at WARNING — production runs at INFO and silently "
        "drops anything logged at DEBUG (vula/api/server.py's logging.basicConfig)")
    assert "Invoice 123.pdf" in caplog.text


@pytest.mark.asyncio
async def test_report_filing_failure_notifies_the_team_with_filename_and_sender():
    em = {"from": "supplier@example.co.za", "subject": "Invoice"}
    att = {"name": "Invoice 123.pdf"}
    notify = AsyncMock()
    with patch("vula.integrations.notify.notify_team", notify):
        await email_sync._report_filing_failure(
            "digg-demo", em, att, RuntimeError("pdf parse failed"), "filed_documents record failed")
    notify.assert_awaited_once()
    args = notify.await_args.args
    assert args[0] == "digg-demo"
    assert args[1] == "attachment_filing_failed"
    assert "Invoice 123.pdf" in args[2]
    assert "supplier@example.co.za" in args[2]


@pytest.mark.asyncio
async def test_report_filing_failure_never_raises_when_notify_itself_fails():
    """notify_team failing must not compound the original failure into an unhandled exception —
    this runs inside a best-effort background sync loop with no caller to catch it."""
    em, att = {"from": "x@y.com"}, {"name": "f.pdf"}
    with patch("vula.integrations.notify.notify_team", AsyncMock(side_effect=RuntimeError("wa down"))):
        await email_sync._report_filing_failure("digg-demo", em, att, RuntimeError("boom"), "x")


def test_both_swallow_sites_route_through_report_filing_failure():
    """Regression guard: neither catch site that used to log at DEBUG (and vanish in production)
    may silently revert to it. Both must route through the WARNING+notify helper."""
    do_sync_src = inspect.getsource(email_sync._do_email_sync)
    assert "_report_filing_failure" in do_sync_src, (
        "the per-attachment loop's except-block must call _report_filing_failure, not swallow "
        "at logger.debug (that's exactly what made the 2026-09-22 incident invisible)")

    file_attachment_src = inspect.getsource(email_sync._file_attachment)
    assert "_report_filing_failure" in file_attachment_src, (
        "the outer try/except wrapping match_project/file_document/commit_inbound_document must "
        "call _report_filing_failure, not swallow at logger.debug")

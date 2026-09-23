"""2026-09-23: a uvicorn worker on Railway died every ~3.5 min, ~20-30 s after "Ingesting:
HPC01_JORDAAN_STREET_DRAWING_PACK_COUNCIL_SUBMISSION_REV_4_230926.pdf" (digg-demo, emailed).
PyMuPDF ran on the web worker and held the GIL past uvicorn's 5 s health check; email sync only
saved its cursor after the whole batch, so the same email was replayed and the worker killed on
every sweep — mail sync for every tenant stalled from 2026-09-22 11:40 UTC.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.email_imap import sync as email_sync
from vula.ingestion.pipeline import DocumentParser


class _Proc:
    def __init__(self, returncode=0, hang=False, out=b"", err=b""):
        self.returncode, self._hang, self._out, self._err = returncode, hang, out, err
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(3600)
        return self._out, self._err

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _no_fallbacks():
    return (patch("pdfplumber.open", side_effect=AssertionError("pdfplumber must not run")),
            patch("pdf2image.convert_from_path", side_effect=AssertionError("poppler must not run")))


@pytest.mark.asyncio
async def test_a_pdf_that_hangs_extraction_is_killed_and_filed_by_name(tmp_path):
    proc = _Proc(hang=True)
    parser = DocumentParser()
    p1, p2 = _no_fallbacks()
    with (patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
          patch("vula.ingestion.pipeline.settings.pdf_extract_timeout_s", 0.05), p1, p2):
        pages = await parser._parse_pdf(tmp_path / "DRAWING_PACK.pdf")
    assert proc.killed
    assert pages and "DRAWING_PACK.pdf" in pages[0][1] and "couldn't be extracted" in pages[0][1]


@pytest.mark.asyncio
async def test_an_extractor_killed_by_a_signal_is_not_retried_in_process(tmp_path):
    parser = DocumentParser()
    p1, p2 = _no_fallbacks()
    with (patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(returncode=-11))),
          p1, p2):
        pages = await parser._parse_pdf(tmp_path / "x.pdf")
    assert "couldn't be extracted" in pages[0][1]


@pytest.mark.asyncio
async def test_an_ordinary_extractor_error_still_falls_back_to_pdfplumber(tmp_path):
    parser = DocumentParser()
    with (patch("asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=_Proc(returncode=1, err=b"RuntimeError: cannot open"))),
          patch.object(parser, "_parse_pdf_native", new=AsyncMock(return_value=[(1, "plumber text")]))):
        assert await parser._parse_pdf(tmp_path / "x.pdf") == [(1, "plumber text")]


def test_drawing_sheets_skip_the_table_finder(tmp_path):
    import pymupdf
    from vula.ingestion.pdf_extract import extract
    pdf = tmp_path / "sheet.pdf"
    doc = pymupdf.open()
    doc.new_page(width=2384, height=1684).insert_text((72, 72), "A1 GENERAL ARRANGEMENT " * 5)  # A1
    doc.new_page().insert_text((72, 72), "TAX INVOICE 123 total R100.00 " * 3)                 # A4
    doc.save(str(pdf)); doc.close()
    calls = []
    real = pymupdf.Page.find_tables

    def spy(self, *a, **k):
        calls.append(self.number)
        return real(self, *a, **k)

    with patch.object(pymupdf.Page, "find_tables", spy):
        pages = extract(str(pdf), str(tmp_path))
    assert [p[0] for p in pages] == [1, 2]
    assert calls == [1]  # only the A4 page


def test_the_email_cursor_is_saved_before_an_emails_attachments_are_filed():
    db = MagicMock()
    q = db.table.return_value.update.return_value.eq.return_value.lt.return_value
    email_sync._advance_cursor(db, "acc1", {"uid": 2470, "is_sent": False}, None)
    db.table.return_value.update.assert_called_once_with({"last_sync_uid": 2470})
    db.table.return_value.update.return_value.eq.return_value.lt.assert_called_once_with(
        "last_sync_uid", 2470)  # never backwards
    q.execute.assert_called_once()


def test_sent_cursor_only_moves_when_a_sent_folder_was_found():
    db = MagicMock()
    email_sync._advance_cursor(db, "acc1", {"uid": 580, "is_sent": True}, None)
    db.table.assert_not_called()
    email_sync._advance_cursor(db, "acc1", {"uid": 580, "is_sent": True}, "Sent")
    db.table.return_value.update.assert_called_once_with({"last_sync_uid_sent": 580})


@pytest.mark.asyncio
async def test_sync_advances_the_cursor_before_filing_and_not_during_backfill():
    order = []
    em = {"uid": 2470, "when": "2026-09-22T11:41:00+00:00", "people": [], "is_sent": False,
          "attachments": [{"name": "pack.pdf", "data": b"%PDF"}], "subject": "s", "from": "a@b.c",
          "body": "", "bulk": False}
    fetched = {"emails": [em], "max_uid": 2470, "folder": "INBOX", "oversized": []}
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute \
        .return_value.data = [{"last_sync_uid": 2463, "auto_sync": True}]

    async def run(from_uid):
        order.clear()
        with (patch.object(email_sync, "get_email_creds", return_value={"email": "j@digg.co.za"}),
              patch.object(email_sync, "_client", return_value=db),
              patch.object(email_sync, "_fetch_new",
                           side_effect=[dict(fetched, emails=[dict(em)]),
                                        {"emails": [], "max_uid": 0, "folder": None, "oversized": []}]),
              patch.object(email_sync, "_advance_cursor", side_effect=lambda *a: order.append("cursor")),
              patch.object(email_sync, "_file_attachment",
                           new=AsyncMock(side_effect=lambda *a, **k: order.append("file"))),
              patch.object(email_sync, "_resolve_replied_followups", new=AsyncMock(return_value=0)),
              patch.object(email_sync, "_capture_voice_samples", new=AsyncMock()),
              patch.object(email_sync, "_needs_reply", return_value=None)):
            await email_sync._do_email_sync("digg-demo", "acc1", 20, from_uid=from_uid)
        return list(order)

    assert await run(None) == ["cursor", "file"]
    assert await run(100) == ["file"]

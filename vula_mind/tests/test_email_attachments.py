"""Tests for vula/email_imap/service.py's attachment support (added for the monthly expense
sheet feature, migration 140) — purely additive to _build/_send/send, existing callers unaffected."""
from vula.email_imap.service import _build


def _creds():
    return {"email": "rep@gerflor.co.za", "from_name": "Richard"}


def test_build_without_attachments_is_unaffected():
    msg = _build(_creds(), "accounts@gerflor.co.za", "Subject", "Body text")
    assert msg.is_multipart() is False
    assert msg.get_content().strip() == "Body text"


def test_build_with_one_attachment_produces_multipart_with_it():
    msg = _build(_creds(), "accounts@gerflor.co.za", "Subject", "Body text", attachments=[
        {"filename": "claim.xlsx", "content": b"fake-xlsx-bytes",
         "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ])
    assert msg.is_multipart() is True
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "claim.xlsx"
    assert attachments[0].get_content() == b"fake-xlsx-bytes"
    assert attachments[0].get_content_type() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_build_with_multiple_attachments():
    msg = _build(_creds(), "to@example.com", "Subject", "Body", attachments=[
        {"filename": "a.jpg", "content": b"aaa", "mimetype": "image/jpeg"},
        {"filename": "b.png", "content": b"bbb", "mimetype": "image/png"},
    ])
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 2
    assert {a.get_filename() for a in attachments} == {"a.jpg", "b.png"}


def test_build_attachment_defaults_mimetype_when_missing():
    msg = _build(_creds(), "to@example.com", "Subject", "Body", attachments=[
        {"filename": "file.bin", "content": b"xyz"},
    ])
    attachments = list(msg.iter_attachments())
    assert attachments[0].get_content_type() == "application/octet-stream"


def test_build_x_vula_sent_header_still_present_with_attachments():
    msg = _build(_creds(), "to@example.com", "Subject", "Body", attachments=[
        {"filename": "a.jpg", "content": b"aaa", "mimetype": "image/jpeg"},
    ])
    assert msg["X-Vula-Sent"] == "1"

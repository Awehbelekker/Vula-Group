"""Tests for real (not draft) Microsoft Graph email sending — added 2026-08-26 after a real M365
mailbox rejected IMAP basic auth ("AUTHENTICATE failed. Provided authentication mechanism is not
supported."). Covers vula/microsoft/service.py::send_mail, the credentials._refresh scope-drift
fix, and vula/commerce/mail_router.py's IMAP-first-then-Graph fallback."""
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.microsoft import service as ms_service
from vula.microsoft.credentials import _refresh
from vula.microsoft.service import MicrosoftNotConnected


# ── service.send_mail ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_mail_success_no_attachments():
    resp = MagicMock(status_code=202)
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=resp)

    with (
        patch("vula.microsoft.service._token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await ms_service.send_mail("gerflor", "to@example.com", "Subject", "Body")

    assert result == {"sent": True, "to": "to@example.com", "subject": "Subject"}
    call = mock_client.post.call_args
    assert call.args[0].endswith("/me/sendMail")
    assert call.kwargs["json"]["message"]["toRecipients"][0]["emailAddress"]["address"] == "to@example.com"
    assert "attachments" not in call.kwargs["json"]["message"]


@pytest.mark.asyncio
async def test_send_mail_encodes_attachments_as_base64():
    resp = MagicMock(status_code=202)
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=resp)

    with (
        patch("vula.microsoft.service._token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.httpx.AsyncClient", return_value=mock_client),
    ):
        await ms_service.send_mail("gerflor", "to@example.com", "Subject", "Body", attachments=[
            {"filename": "claim.xlsx", "content": b"fake-bytes",
             "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        ])

    att = mock_client.post.call_args.kwargs["json"]["message"]["attachments"][0]
    assert att["name"] == "claim.xlsx"
    assert att["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert base64.b64decode(att["contentBytes"]) == b"fake-bytes"


@pytest.mark.asyncio
async def test_send_mail_non_202_returns_error_not_raise():
    resp = MagicMock(status_code=403, text="Forbidden — Mail.Send scope missing")
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=resp)

    with (
        patch("vula.microsoft.service._token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await ms_service.send_mail("gerflor", "to@example.com", "Subject", "Body")

    assert "error" in result
    assert "403" in result["error"]


@pytest.mark.asyncio
async def test_send_mail_not_connected_returns_error_not_raise():
    with patch("vula.microsoft.service._token", new=AsyncMock(side_effect=MicrosoftNotConnected("gerflor"))):
        result = await ms_service.send_mail("gerflor", "to@example.com", "Subject", "Body")
    assert "error" in result


# ── credentials._refresh: scope-drift fix ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_requests_the_real_current_scopes_not_a_stale_copy():
    """Real bug found 2026-08-26: _refresh() used to hardcode its own copy of the scope string,
    independent of service.SCOPES — adding Mail.Send there silently never reached a refreshed
    token, since Microsoft's refresh grant is itself scoped by what's requested here."""
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"access_token": "new-tok", "expires_in": 3600}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=resp)

    with (
        patch("vula.microsoft.credentials.httpx.AsyncClient", return_value=mock_client),
        patch("vula.microsoft.credentials._client") as mock_db,
    ):
        await _refresh("gerflor", "refresh-tok")

    requested_scope = mock_client.post.call_args.kwargs["data"]["scope"]
    assert requested_scope == " ".join(ms_service.SCOPES)
    assert "Mail.Send" in requested_scope


# ── mail_router.send_tenant_email ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_router_uses_imap_when_connected_never_tries_graph():
    from vula.commerce.mail_router import send_tenant_email
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.send", new=AsyncMock(return_value={"sent": True})),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock()) as mock_graph,
    ):
        ok = await send_tenant_email("gerflor", "to@example.com", "Subject", "Body")
    assert ok is True
    mock_graph.assert_not_called()


@pytest.mark.asyncio
async def test_router_falls_back_to_graph_when_no_imap_mailbox():
    from vula.commerce.mail_router import send_tenant_email
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value={"access_token": "tok"})),
        patch("vula.microsoft.service.send_mail", new=AsyncMock(return_value={"sent": True})) as mock_send,
    ):
        ok = await send_tenant_email("gerflor", "to@example.com", "Subject", "Body")
    assert ok is True
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_router_falls_back_to_graph_when_imap_send_fails():
    from vula.commerce.mail_router import send_tenant_email
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.send", new=AsyncMock(return_value={"error": "auth failed"})),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value={"access_token": "tok"})),
        patch("vula.microsoft.service.send_mail", new=AsyncMock(return_value={"sent": True})),
    ):
        ok = await send_tenant_email("gerflor", "to@example.com", "Subject", "Body")
    assert ok is True


@pytest.mark.asyncio
async def test_router_returns_false_when_neither_is_connected():
    from vula.commerce.mail_router import send_tenant_email
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value=None)),
    ):
        ok = await send_tenant_email("gerflor", "to@example.com", "Subject", "Body")
    assert ok is False


@pytest.mark.asyncio
async def test_router_never_raises_when_imap_send_itself_raises():
    from vula.commerce.mail_router import send_tenant_email
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.send", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value=None)),
    ):
        ok = await send_tenant_email("gerflor", "to@example.com", "Subject", "Body")
    assert ok is False


@pytest.mark.asyncio
async def test_router_never_raises_when_graph_raises():
    from vula.commerce.mail_router import send_tenant_email
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        ok = await send_tenant_email("gerflor", "to@example.com", "Subject", "Body")
    assert ok is False


@pytest.mark.asyncio
async def test_router_passes_attachments_through_to_whichever_path_sends():
    from vula.commerce.mail_router import send_tenant_email
    captured = {}

    async def _fake_imap_send(creds, to, subject, body, attachments=None):
        captured["attachments"] = attachments
        return {"sent": True}

    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.send", new=_fake_imap_send),
    ):
        await send_tenant_email("gerflor", "to@example.com", "Subject", "Body",
                                attachments=[{"filename": "x.xlsx", "content": b"y"}])
    assert captured["attachments"] == [{"filename": "x.xlsx", "content": b"y"}]

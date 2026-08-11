"""Tests for the Dynamics 365 (Dataverse) connector — token storage/refresh, client call
shape, and tool gating when not connected. Mirrors vula/microsoft's shape (Gmail/Outlook),
just with an org-specific resource scope instead of one universal Graph endpoint."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.dynamics365 import client as d365_client
from vula.dynamics365.client import Dynamics365NotConnected
from vula.dynamics365.credentials import get_access_token, store_connection


# ── credentials: get_access_token ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_access_token_none_when_not_connected():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.dynamics365.credentials._client", return_value=mock_db):
        result = await get_access_token("gerflor")
    assert result is None


@pytest.mark.asyncio
async def test_get_access_token_returns_unexpired_token_without_refresh():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    row = {"org_url": "https://gerflor.crm4.dynamics.com", "access_token": "plaintext-token",
           "refresh_token": "plaintext-refresh", "token_expiry": future, "email": "ian@gerflor.co.za"}
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[row])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.dynamics365.credentials._client", return_value=mock_db),
        patch("vula.dynamics365.credentials._refresh", new=AsyncMock()) as mock_refresh,
    ):
        result = await get_access_token("gerflor")
    mock_refresh.assert_not_called()
    assert result == {"access_token": "plaintext-token",
                      "org_url": "https://gerflor.crm4.dynamics.com", "email": "ian@gerflor.co.za"}


@pytest.mark.asyncio
async def test_get_access_token_refreshes_when_expired():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    row = {"org_url": "https://gerflor.crm4.dynamics.com", "access_token": "stale-token",
           "refresh_token": "plaintext-refresh", "token_expiry": past, "email": "ian@gerflor.co.za"}
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[row])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.dynamics365.credentials._client", return_value=mock_db),
        patch("vula.dynamics365.credentials._refresh", new=AsyncMock(return_value="fresh-token")) as mock_refresh,
    ):
        result = await get_access_token("gerflor")
    mock_refresh.assert_awaited_once_with("gerflor", "https://gerflor.crm4.dynamics.com", "plaintext-refresh")
    assert result["access_token"] == "fresh-token"


def test_store_connection_encrypts_tokens_and_strips_trailing_slash():
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with (
        patch("vula.dynamics365.credentials._client", return_value=mock_db),
        patch("vula.email_imap.credentials.encrypt_secret", side_effect=lambda s: f"enc:{s}"),
    ):
        store_connection("gerflor", org_url="https://gerflor.crm4.dynamics.com/",
                         access_token="tok", refresh_token="ref", expires_in=3600,
                         email="ian@gerflor.co.za", scopes="offline_access")
    row = mock_table.upsert.call_args.args[0]
    assert row["org_url"] == "https://gerflor.crm4.dynamics.com"
    assert row["access_token"] == "enc:tok"
    assert row["refresh_token"] == "enc:ref"


# ── client: Dataverse lookups ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_contacts_not_connected_raises():
    with patch("vula.dynamics365.client.get_access_token", new=AsyncMock(return_value=None)):
        with pytest.raises(Dynamics365NotConnected):
            await d365_client.search_contacts("gerflor", "John")


@pytest.mark.asyncio
async def test_search_contacts_builds_correct_request():
    mock_response = MagicMock()
    mock_response.json.return_value = {"value": [
        {"fullname": "John Smith", "telephone1": "0821234567",
         "emailaddress1": "john@example.com", "jobtitle": "Buyer"}]}
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with (
        patch("vula.dynamics365.client.get_access_token",
              new=AsyncMock(return_value={"access_token": "tok", "org_url": "https://gerflor.crm4.dynamics.com"})),
        patch("vula.dynamics365.client.httpx.AsyncClient", return_value=mock_client),
    ):
        results = await d365_client.search_contacts("gerflor", "John")

    assert results == [{"name": "John Smith", "phone": "0821234567",
                        "email": "john@example.com", "title": "Buyer"}]
    call = mock_client.get.call_args
    assert call.args[0] == "https://gerflor.crm4.dynamics.com/api/data/v9.2/contacts"
    assert "John" in call.kwargs["params"]["$filter"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_exchange_code_requests_org_scoped_token():
    token_response = MagicMock()
    token_response.raise_for_status = MagicMock()
    token_response.json.return_value = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "scope": "s"}
    who_response = MagicMock()
    who_response.json.return_value = {"UserId": "u1"}
    me_response = MagicMock()
    me_response.json.return_value = {"internalemailaddress": "ian@gerflor.co.za"}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=token_response)
    mock_client.get = AsyncMock(side_effect=[who_response, me_response])

    with patch("vula.dynamics365.client.httpx.AsyncClient", return_value=mock_client):
        result = await d365_client.exchange_code("code123", "https://cb", "https://gerflor.crm4.dynamics.com")

    assert result["access_token"] == "at"
    assert result["email"] == "ian@gerflor.co.za"
    post_call = mock_client.post.call_args
    assert "https://gerflor.crm4.dynamics.com/.default" in post_call.kwargs["data"]["scope"]


# ── commerce_admin.py: dynamics_lookup tool gating ───────────────────────────

@pytest.mark.asyncio
async def test_dynamics_lookup_tool_returns_clear_error_when_not_connected():
    from core.skills.commerce_admin import CommerceAdminSkill
    from vula.dynamics365.client import Dynamics365NotConnected
    skill = CommerceAdminSkill()
    with patch("vula.dynamics365.client.search_contacts", new=AsyncMock(side_effect=Dynamics365NotConnected("gerflor"))):
        result = await skill._dynamics_lookup("gerflor", "John", "contact")
    assert "error" in result
    assert "connect it from the dashboard" in result["error"]


@pytest.mark.asyncio
async def test_dynamics_lookup_tool_returns_results_when_connected():
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()
    with patch("vula.dynamics365.client.search_accounts",
              new=AsyncMock(return_value=[{"name": "Gerflor Cape Town", "phone": "0211234567",
                                            "email": "", "city": "Cape Town"}])):
        result = await skill._dynamics_lookup("gerflor", "Gerflor", "account")
    assert result["kind"] == "account"
    assert result["results"][0]["name"] == "Gerflor Cape Town"


@pytest.mark.asyncio
async def test_dynamics_lookup_tool_requires_query():
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()
    result = await skill._dynamics_lookup("gerflor", "", "contact")
    assert "error" in result


# ── vula/api/dynamics365.py: connect flow ────────────────────────────────────

@pytest.mark.asyncio
async def test_authorize_url_encodes_tenant_and_org_in_state():
    from vula.api.dynamics365 import authorize_url, _decode_state
    with patch("vula.api.dynamics365.settings") as mock_settings:
        mock_settings.microsoft_client_id = "client123"
        mock_settings.public_base_url = "https://vula-group-production.up.railway.app"
        result = await authorize_url("gerflor", "https://gerflor.crm4.dynamics.com/")
    assert "url" in result
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(result["url"]).query)
    tenant_id, org_url = _decode_state(qs["state"][0])
    assert tenant_id == "gerflor"
    assert org_url == "https://gerflor.crm4.dynamics.com"  # trailing slash stripped


@pytest.mark.asyncio
async def test_authorize_url_requires_org_url():
    from vula.api.dynamics365 import authorize_url
    with patch("vula.api.dynamics365.settings") as mock_settings:
        mock_settings.microsoft_client_id = "client123"
        result = await authorize_url("gerflor", "")
    assert "error" in result


@pytest.mark.asyncio
async def test_oauth_callback_stores_connection_on_success():
    from vula.api.dynamics365 import oauth_callback, _encode_state
    state = _encode_state("gerflor", "https://gerflor.crm4.dynamics.com")
    with (
        patch("vula.api.dynamics365.client.exchange_code",
              new=AsyncMock(return_value={"access_token": "at", "refresh_token": "rt",
                                          "expires_in": 3600, "scope": "s", "email": "ian@gerflor.co.za"})),
        patch("vula.api.dynamics365.store_connection") as mock_store,
    ):
        resp = await oauth_callback(code="code123", state=state)
    assert "connected" in resp.body.decode().lower()
    mock_store.assert_called_once()
    assert mock_store.call_args.kwargs["org_url"] == "https://gerflor.crm4.dynamics.com"


@pytest.mark.asyncio
async def test_status_not_connected_when_no_row():
    from vula.api.dynamics365 import status
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.api.dynamics365._client", return_value=mock_db):
        result = await status("gerflor")
    assert result == {"tenant_id": "gerflor", "status": "not_connected"}

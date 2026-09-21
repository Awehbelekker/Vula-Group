"""Tests for the 2026-09-18 fix to customer photo/document misrouting.

Real bug: _handle_document_ingest hardcoded role="admin" for ANY sender on a tenant's
dedicated line (route_tenant_id set), with no check of who actually sent it. A real
customer's photo or forwarded document on a commerce-mode tenant's line got filed into the
tenant's knowledge base as if an admin uploaded it, and the customer got an admin-flavoured
reply ("I'll book it straight into your books..."). commerce_assistant.py has zero inbound
vision handling, so a genuine customer's photo had no legitimate destination in the system
at all before this fix.
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.api.whatsapp import _handle_document_ingest, _handle_image_or_video

TID = "off-the-hook"
STAFF_PHONE = "27827077080"
CUSTOMER_PHONE = "27645755210"


# ── _handle_document_ingest ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_commerce_line_staff_sender_still_treated_as_admin():
    """A staff sender must clear the new role gate and reach the (unchanged) mime-type check
    below it — proven here by an unsupported mime type producing THAT function's own decline
    message, not the new "not able to file documents" one, without touching real ingest IO."""
    with (
        patch("vula.api.whatsapp._is_tenant_owner", return_value=True),
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_reply,
    ):
        await _handle_document_ingest(
            STAFF_PHONE, "media1", "photo.heic", "image/heic", route_tenant_id=TID,
            route_mode="commerce")

    mock_reply.assert_awaited_once()
    assert "can't ingest" in mock_reply.call_args.args[1]


@pytest.mark.asyncio
async def test_commerce_line_real_customer_declined_not_filed_as_admin():
    with (
        patch("vula.api.whatsapp._is_tenant_owner", return_value=False),
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_reply,
    ):
        await _handle_document_ingest(
            CUSTOMER_PHONE, "media1", "photo.jpg", "image/jpeg", route_tenant_id=TID,
            route_mode="commerce")

    mock_reply.assert_awaited_once()
    args = mock_reply.call_args.args
    assert "not able to file documents" in args[1]
    assert args[2] == TID


@pytest.mark.asyncio
async def test_knowledge_line_anyone_still_treated_as_admin_unchanged():
    """route_mode == 'knowledge' keeps the original 'anyone may upload' behaviour — no
    customer concept exists on a dedicated knowledge-mode line."""
    with (
        patch("vula.api.whatsapp._is_tenant_owner") as mock_owner,
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock()) as mock_rep,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_reply,
    ):
        await _handle_document_ingest(
            CUSTOMER_PHONE, "media1", "photo.heic", "image/heic", route_tenant_id=TID,
            route_mode="knowledge")

    mock_owner.assert_not_called()
    mock_rep.assert_not_called()
    mock_reply.assert_awaited_once()
    assert "can't ingest" in mock_reply.call_args.args[1]


@pytest.mark.asyncio
async def test_no_route_tenant_id_unchanged_admin_lookup_path():
    """route_tenant_id=None (a non-dedicated number) keeps the original DB-lookup path
    entirely untouched by this fix."""
    with (
        patch("vula.models.tenants.get_tenant_db") as mock_get_db,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_reply,
    ):
        mock_get_db.return_value.lookup_by_phone_with_role.return_value = None
        await _handle_document_ingest(CUSTOMER_PHONE, "media1", "photo.jpg", "image/jpeg")

    mock_reply.assert_awaited_once()
    assert "couldn't find a Vula account" in mock_reply.call_args.args[1]


# ── _handle_image_or_video ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customer_uncaptioned_photo_routes_to_commerce_assistant_not_admin_ingest():
    with (
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._handle_media", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._is_tenant_owner", return_value=False),
        patch("vula.api.whatsapp._describe_photo_for_rep", new=AsyncMock(return_value="A grey snapper on ice.")),
        patch("vula.api.whatsapp._run_commerce_assistant", new=AsyncMock(return_value=True)) as mock_assistant,
        patch("vula.api.whatsapp._handle_document_ingest", new=AsyncMock()) as mock_ingest,
    ):
        await _handle_image_or_video(
            CUSTOMER_PHONE, "image", "media1", "", "image/jpeg", "msg1",
            route_mode="commerce", route_tenant=TID, content_sha=None)

    mock_assistant.assert_awaited_once()
    assert mock_assistant.call_args.args[0] == CUSTOMER_PHONE
    assert "grey snapper" in mock_assistant.call_args.args[1]
    assert mock_assistant.call_args.args[2] == TID
    mock_ingest.assert_not_called()


@pytest.mark.asyncio
async def test_customer_photo_declined_when_commerce_assistant_cannot_answer():
    with (
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._handle_media", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._is_tenant_owner", return_value=False),
        patch("vula.api.whatsapp._describe_photo_for_rep", new=AsyncMock(return_value="")),
        patch("vula.api.whatsapp._run_commerce_assistant", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._handle_document_ingest", new=AsyncMock()) as mock_ingest,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_reply,
    ):
        await _handle_image_or_video(
            CUSTOMER_PHONE, "image", "media1", "", "image/jpeg", "msg1",
            route_mode="commerce", route_tenant=TID, content_sha=None)

    mock_ingest.assert_not_called()
    mock_reply.assert_awaited_once()
    assert "not able to file documents" in mock_reply.call_args.args[1]


@pytest.mark.asyncio
async def test_staff_photo_on_commerce_line_still_reaches_document_ingest():
    """A genuine owner/staff sender (not a sales_rep, e.g. the shop owner) must keep working
    exactly as before — no regression for the legitimate admin-ingest path."""
    with (
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._handle_media", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._is_tenant_owner", return_value=True),
        patch("vula.api.whatsapp._run_commerce_assistant", new=AsyncMock()) as mock_assistant,
        patch("vula.api.whatsapp._handle_document_ingest", new=AsyncMock()) as mock_ingest,
    ):
        await _handle_image_or_video(
            STAFF_PHONE, "image", "media1", "restock photo", "image/jpeg", "msg1",
            route_mode="commerce", route_tenant=TID, content_sha=None)

    mock_assistant.assert_not_called()
    mock_ingest.assert_awaited_once()
    assert mock_ingest.call_args.kwargs["route_mode"] == "commerce"


@pytest.mark.asyncio
async def test_knowledge_mode_photo_unaffected_by_customer_check():
    """route_mode='knowledge' must never invoke the new staff-check branch at all — same as
    the _handle_document_ingest-level test above, for symmetry."""
    with (
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._handle_media", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._is_tenant_owner") as mock_owner,
        patch("vula.api.whatsapp._handle_document_ingest", new=AsyncMock()) as mock_ingest,
    ):
        await _handle_image_or_video(
            CUSTOMER_PHONE, "image", "media1", "", "image/jpeg", "msg1",
            route_mode="knowledge", route_tenant=TID, content_sha=None)

    mock_owner.assert_not_called()
    mock_ingest.assert_awaited_once()

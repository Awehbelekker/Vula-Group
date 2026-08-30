"""Tests for scripts/sync_meta_catalog.py — pushes commerce_products into a tenant's connected
Meta Commerce Manager catalog. No tenant has a catalog connected yet (confirmed live,
2026-08-25), so this is deterministic/mocked only — nothing here can be live-verified against
a real catalog until one exists. See vula/api/whatsapp.py's _handle_native_order for the
receiving side.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.sync_meta_catalog import _catalog_item, sync_catalog


def test_catalog_item_shape():
    product = {"slug": "hake-fillets", "name": "Hake Fillets", "description": "Fresh hake",
               "price_cents": 16000, "in_stock": True, "stock_quantity": 12,
               "image_url": "https://example.com/hake.jpg", "images": []}
    item = _catalog_item(product, store_url="https://oth.vula.site")

    assert item["method"] == "UPDATE"
    data = item["data"]
    assert data["id"] == "hake-fillets"
    assert data["name"] == "Hake Fillets"
    assert data["price"] == "160.00 ZAR"
    assert data["currency"] == "ZAR"
    assert data["availability"] == "in stock"
    assert data["image"] == [{"url": "https://example.com/hake.jpg"}]
    assert data["url"] == "https://oth.vula.site/products/hake-fillets"


def test_catalog_item_out_of_stock_when_quantity_zero():
    product = {"slug": "calamari", "name": "Calamari", "price_cents": 12000,
               "in_stock": True, "stock_quantity": 0}
    assert _catalog_item(product, None)["data"]["availability"] == "out of stock"


def test_catalog_item_out_of_stock_when_in_stock_flag_false():
    product = {"slug": "prawns", "name": "Prawns", "price_cents": 20000,
               "in_stock": False, "stock_quantity": 50}
    assert _catalog_item(product, None)["data"]["availability"] == "out of stock"


def test_catalog_item_prefers_sale_price():
    product = {"slug": "mackerel", "name": "Mackerel", "price_cents": 8000,
               "sale_price_cents": 6000, "in_stock": True, "stock_quantity": 5}
    assert _catalog_item(product, None)["data"]["price"] == "60.00 ZAR"


def test_catalog_item_falls_back_to_images_list_when_no_image_url():
    product = {"slug": "sole", "name": "Sole", "price_cents": 9000,
               "in_stock": True, "stock_quantity": 3, "images": ["https://example.com/sole.jpg"]}
    assert _catalog_item(product, None)["data"]["image"] == [{"url": "https://example.com/sole.jpg"}]


def test_catalog_item_omits_url_when_no_store_url_configured():
    product = {"slug": "trout", "name": "Trout", "price_cents": 7000,
               "in_stock": True, "stock_quantity": 2}
    assert "url" not in _catalog_item(product, None)["data"]


@pytest.mark.asyncio
async def test_sync_catalog_reports_no_catalog_id_when_unset():
    # sync_catalog() imports `service` locally (from vula.commerce import service) — patch the
    # real module's attribute, not scripts.sync_meta_catalog's (nonexistent) module-level name.
    with patch("vula.commerce.service._client") as mock_client:
        mock_client.return_value.table.return_value.select.return_value \
            .eq.return_value.limit.return_value.execute.return_value = MagicMock(
                data=[{"meta_catalog_id": None, "store_url": None}])
        result = await sync_catalog("off-the-hook")

    assert result["status"] == "no_catalog_id"


@pytest.mark.asyncio
async def test_sync_catalog_posts_items_batch_when_configured():
    mock_client_fn = MagicMock()
    cfg_chain = mock_client_fn.return_value.table.return_value.select.return_value.eq.return_value.limit.return_value
    cfg_chain.execute.return_value = MagicMock(
        data=[{"meta_catalog_id": "cat123", "store_url": "https://oth.vula.site"}])
    products_chain = mock_client_fn.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value
    products_chain.execute.return_value = MagicMock(data=[
        {"slug": "hake", "name": "Hake", "price_cents": 16000, "in_stock": True, "stock_quantity": 5},
    ])

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"handles": ["h1"]}
    mock_http_client = AsyncMock()
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)
    mock_http_client.post = AsyncMock(return_value=mock_response)

    with (
        patch("vula.commerce.service._client", mock_client_fn),
        patch("vula.api.whatsapp._get_tenant_wa_creds", new=AsyncMock(
            return_value={"phone_id": "1", "token": "tok"})),
        patch("scripts.sync_meta_catalog.httpx.AsyncClient", return_value=mock_http_client),
    ):
        result = await sync_catalog("off-the-hook")

    assert result["status"] == "success"
    assert result["synced"] == 1
    call = mock_http_client.post.call_args
    assert call.args[0] == "https://graph.facebook.com/v19.0/cat123/items_batch"
    assert call.kwargs["json"]["item_type"] == "PRODUCT_ITEM"
    assert call.kwargs["json"]["requests"][0]["data"]["id"] == "hake"

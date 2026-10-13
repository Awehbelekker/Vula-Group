"""WhatsApp's own catalogue (vula/commerce/meta_catalog.py). Customers browse, pick a colour and
build a cart inside WhatsApp; the cart comes back as an "order" webhook whose items carry each
product's slug (vula/api/whatsapp.py::_handle_native_order). Catalogue feed field names
(title, link, image_link, item_group_id, color) for POST /{catalog_id}/items_batch."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import meta_catalog


def _p(name, slug, cents=149900, **k):
    return {"name": name, "slug": slug, "price_cents": cents, "in_stock": True, "stock_quantity": None,
            "image_url": f"https://cdn/{slug}.jpg", "description": "Hand-cast concrete.", **k}


def test_a_product_is_a_catalogue_feed_item():
    item = meta_catalog._item(_p("Hake Fillets", "hake-fillets", 16000, stock_quantity=12,
                                 images=["https://cdn/hake-fillets.jpg", "https://cdn/hake-2.jpg"]),
                              "https://oth.vula.site", "Off the Hook")
    assert item["method"] == "UPDATE"
    assert item["data"] == {
        "id": "hake-fillets", "title": "Hake Fillets", "description": "Hand-cast concrete.",
        "availability": "in stock", "condition": "new", "price": "160.00 ZAR",
        "link": "https://oth.vula.site/products/hake-fillets", "image_link": "https://cdn/hake-fillets.jpg",
        "additional_image_link": ["https://cdn/hake-2.jpg"], "brand": "Off the Hook"}


def test_untracked_stock_is_available_a_counted_zero_is_not():
    assert meta_catalog._item(_p("A", "a"), "https://x")["data"]["availability"] == "in stock"
    assert meta_catalog._item(_p("B", "b", stock_quantity=0), "https://x")["data"]["availability"] == "out of stock"
    assert meta_catalog._item(_p("C", "c", in_stock=False), "https://x")["data"]["availability"] == "out of stock"


def test_a_sale_price_wins():
    assert meta_catalog._item(_p("M", "m", 8000, sale_price_cents=6000), "https://x")["data"]["price"] == "60.00 ZAR"


def test_colours_of_a_range_share_a_group_and_each_names_its_colour():
    items = meta_catalog.catalog_items([
        _p("Round Concave Concrete Basin – Black – 42 x 14cm", "rcb-black", 169900),
        _p("Round Concave Concrete Basin – Ivory – 42 x 14cm", "rcb-ivory", 169900),
        _p("Amazon Green Bespoke Bath 175 x 80 x 50cm", "bath", 2700000)], "https://seedleme.com", "SeedleMe")
    data = {i["data"]["id"]: i["data"] for i in items}
    assert data["rcb-black"]["item_group_id"] == data["rcb-ivory"]["item_group_id"]
    assert (data["rcb-black"]["color"], data["rcb-ivory"]["color"]) == ("Black", "Ivory")
    assert "item_group_id" not in data["bath"] and "color" not in data["bath"]   # a one-off isn't a group
    assert data["bath"]["link"] == "https://seedleme.com/products/bath"


@pytest.fixture()
def tenant(monkeypatch):
    cfg = {"meta_catalog_id": None, "store_url": "https://seedleme.com", "display_name": "SeedleMe"}
    monkeypatch.setattr(meta_catalog, "_config", lambda tid: dict(cfg))
    monkeypatch.setattr("vula.api.whatsapp._get_tenant_wa_creds", AsyncMock(return_value={"phone_id": "1", "token": "tok"}))

    async def products(tid, **k):
        return [_p(f"Calypso {c} Basin 56 x 29.5 x 15cm", f"calypso-{c.lower()}", 199900) for c in ("Black", "Grey")]
    monkeypatch.setattr("vula.commerce.service.list_products", products)
    return cfg


@pytest.mark.asyncio
async def test_nothing_is_sent_until_a_catalogue_is_connected(tenant):
    assert (await meta_catalog.sync("seedleme"))["status"] == "no_catalog_id"


def _http(status=200, body=None):
    resp = MagicMock(status_code=status, text=str(body))
    resp.json.return_value = body if body is not None else {"handles": ["h1"]}
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_a_connected_catalogue_gets_every_product_with_its_colour_group(tenant):
    tenant["meta_catalog_id"] = "cat123"
    client = _http()
    with patch("vula.commerce.meta_catalog.httpx.AsyncClient", return_value=client):
        res = await meta_catalog.sync("seedleme")
    assert res == {"status": "success", "synced": 2, "colour_groups": 1, "catalog_id": "cat123", "handles": ["h1"]}
    call = client.post.call_args
    assert call.args[0] == "https://graph.facebook.com/v19.0/cat123/items_batch"
    assert call.kwargs["json"]["item_type"] == "PRODUCT_ITEM"
    assert [r["data"]["color"] for r in call.kwargs["json"]["requests"]] == ["Black", "Grey"]


@pytest.mark.asyncio
async def test_metas_refusal_is_reported_with_what_to_do(tenant):
    client = _http(403, {"error": {"message": "(#10) Application does not have permission for this action"}})
    with patch("vula.commerce.meta_catalog.httpx.AsyncClient", return_value=client):
        res = await meta_catalog.sync("seedleme", catalog_id="cat123")
    assert res["status"] == "failed" and "assign the catalogue" in res["error"]


@pytest.mark.asyncio
async def test_after_an_import_the_catalogue_is_resynced_only_when_there_is_one(tenant, monkeypatch):
    calls = []

    async def fake_sync(tid, catalog_id=None):
        calls.append(tid)
        return {"status": "success"}
    monkeypatch.setattr(meta_catalog, "sync", fake_sync)
    await meta_catalog.sync_if_connected("seedleme")
    assert calls == []
    tenant["meta_catalog_id"] = "cat123"
    await meta_catalog.sync_if_connected("seedleme")
    assert calls == ["seedleme"]


def test_the_command_line_script_is_a_wrapper():
    import scripts.sync_meta_catalog as script
    assert SimpleNamespace(main=script._main).main is not None

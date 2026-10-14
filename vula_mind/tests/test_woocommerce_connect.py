"""Connect WooCommerce (2026-10-13, Ian: "WooCommerce will come up too"). Same shape as Shopify:
the whole catalogue with variations and stock, WooCommerce calls Vula back on changes (with
Read/Write keys), keys encrypted and never shown, and WooCommerce's calls only believed when
signed with the secret Vula gave it."""
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from tests.test_shopify_connect import _DB, env as shopify_env  # noqa: F401  (fixture reuse)
from vula.integrations import woocommerce as woo

KEY, SECRET = "ck_test123", "cs_test456"
URL = "https://shop.seedleme.co.za"


def _product(pid=11, slug="aquatina-basin", typ="variable", status="publish", **k):
    return {"id": pid, "slug": slug, "name": "Aquatina Concrete Basin &amp; Plug", "type": typ,
            "status": status, "permalink": f"{URL}/product/{slug}/", "price": "2099",
            "description": "<p>Hand-cast concrete.</p>", "categories": [{"name": "Basins"}],
            "images": [{"src": f"{URL}/a.jpg"}], "stock_status": "instock", "manage_stock": typ == "simple",
            "stock_quantity": 4 if typ == "simple" else None,
            "variations": [21, 22] if typ == "variable" else [], **k}


VARIATIONS = [
    {"id": 21, "sku": "AQ-BL", "price": "2099", "stock_status": "instock", "manage_stock": True, "stock_quantity": 3,
     "attributes": [{"name": "Colour", "option": "Blue"}], "image": {"src": f"{URL}/blue.jpg"}},
    {"id": 22, "sku": "AQ-CH", "price": "2299", "stock_status": "outofstock", "manage_stock": True, "stock_quantity": 0,
     "attributes": [{"name": "Colour", "option": "Charcoal"}], "image": {"src": f"{URL}/a.jpg"}}]


class _R:
    def __init__(self, status, body, headers=None):
        self.status_code, self._b, self.headers = status, body, headers or {"x-wp-totalpages": "1"}

    def json(self):
        return self._b


class FakeWoo:
    def __init__(self, write=True, products=None):
        self.write, self.products, self.hooks, self.calls = write, products or [_product()], [], []

    async def request(self, method, url, key, secret, json=None, params=None):
        self.calls.append((method, url))
        if (key, secret) != (KEY, SECRET):
            return _R(401, {"message": "Sorry, you cannot list resources."})
        path = url.split("/wp-json/wc/v3")[1]
        if method == "POST" and path == "/webhooks":
            if not self.write:
                return _R(401, {"message": "no write"})
            self.hooks.append(json)
            return _R(201, {"id": len(self.hooks)})
        if path == "/products":
            return _R(200, self.products)
        if path.endswith("/variations"):
            return _R(200, VARIATIONS)
        if path.startswith("/products/"):
            pid = int(path.split("/")[2])
            if pid in (21, 22):
                return _R(200, {"id": pid, "parent_id": 11})
            return _R(200, next(p for p in self.products if p["id"] == pid))
        if path == "/webhooks":
            return _R(200, [{"id": 1, "delivery_url": woo.webhook_url("seedleme")}])
        return _R(200, {})


@pytest.fixture()
def wenv(shopify_env, monkeypatch):
    fake = FakeWoo()
    monkeypatch.setattr(woo, "_request", fake.request)
    monkeypatch.setattr(woo, "_db", lambda: shopify_env.db)

    async def name(url):
        return "SeedleMe"
    monkeypatch.setattr(woo, "_store_name", name)
    shopify_env.woo = fake
    return shopify_env


def test_the_store_address_must_be_https_and_loses_shop_paths():
    assert woo.normalise_url("shop.seedleme.co.za/shop/") == URL
    assert woo.normalise_url("https://Shop.SeedleMe.co.za/wp-admin/edit.php") == URL
    with pytest.raises(woo.WooError, match="https"):
        woo.normalise_url("http://shop.seedleme.co.za")


def test_a_variable_product_comes_with_its_colours_stock_and_photos():
    p = woo.map_product(_product(), VARIATIONS, URL)
    assert (p["name"], p["slug"], p["price_cents"], p["status"], p["woo_id"]) == (
        "Aquatina Concrete Basin & Plug", "aquatina-basin", 209900, "active", "11")
    assert p["images"] == [f"{URL}/a.jpg", f"{URL}/blue.jpg"] and p["category_label"] == "Basins"
    assert [(v["option_values"], v["stock"], v["in_stock"], v["woo_id"]) for v in p["variants"]] == [
        ({"Colour": "Blue"}, 3, True, "21"), ({"Colour": "Charcoal"}, 0, False, "22")]
    assert p["in_stock"] is True and p["stock"] is None


def test_a_simple_draft_keeps_its_stock_and_stays_a_draft():
    p = woo.map_product(_product(typ="simple", status="draft"), [], URL)
    assert (p["status"], p["stock"], p["variants"]) == ("draft", 4, [])


@pytest.mark.asyncio
async def test_connecting_checks_the_keys_stores_them_encrypted_and_asks_for_live_updates(wenv):
    view = await woo.connect("seedleme", "shop.seedleme.co.za", KEY, SECRET)
    row = wenv.db.tables["vula_woocommerce_accounts"][0]
    assert row["store_url"] == URL and row["store_name"] == "SeedleMe" and row["permissions"] == "read_write"
    assert KEY not in json.dumps(row) and SECRET not in json.dumps(row)            # encrypted at rest
    assert [h["topic"] for h in wenv.woo.hooks] == list(woo.WEBHOOK_TOPICS)
    assert wenv.woo.hooks[0]["delivery_url"].endswith("/v1/woocommerce/webhook/seedleme")
    assert view["connected"] and view["live_updates"]
    assert not any(k in json.dumps(view) for k in (KEY, SECRET, "consumer_", "webhook_secret"))


@pytest.mark.asyncio
async def test_read_only_keys_connect_without_live_updates(wenv):
    wenv.woo.write = False
    view = await woo.connect("seedleme", URL, KEY, SECRET)
    assert view["connected"] and not view["live_updates"] and view["permissions"] == "read"


@pytest.mark.asyncio
async def test_wrong_keys_and_anothers_store_are_refused(wenv):
    with pytest.raises(woo.WooError, match="consumer key"):
        await woo.connect("seedleme", URL, "abc", "def")
    with pytest.raises(woo.WooError, match="refused the keys"):
        await woo.connect("seedleme", URL, "ck_wrong", "cs_wrong")
    wenv.db.tables["vula_woocommerce_accounts"] = [{"tenant_id": "other", "store_url": URL, "status": "connected"}]
    with pytest.raises(woo.WooError, match="another Vula business"):
        await woo.connect("seedleme", URL, KEY, SECRET)


@pytest.mark.asyncio
async def test_a_sync_saves_every_product_with_its_store_id_and_stock(wenv):
    wenv.woo.products = [_product(typ="simple", pid=31, slug="silverland-bath")]
    await woo.connect("seedleme", URL, KEY, SECRET)
    st = await woo.sync("seedleme")
    assert st["step"] == "done" and st["products_found"] == 1 and st["stock_changes"] == 1
    assert wenv.products["silverland-bath"]["woo_id"] == "31"
    assert wenv.moves == [("p-silverland-bath", 4, "woocommerce")]


def _signed(body: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_the_webhook_answers_pings_and_believes_only_signed_calls(wenv, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vula.api import woocommerce_connect as api
    import asyncio
    asyncio.run(woo.connect("seedleme", URL, KEY, SECRET))
    hook_secret = woo._dec(wenv.db.tables["vula_woocommerce_accounts"][0]["webhook_secret"])
    ran = []
    monkeypatch.setattr("vula.commerce.background_tasks.run_background", lambda tid, name, coro: (ran.append(name), coro.close()))
    app = FastAPI()
    app.include_router(api.webhook_router, prefix="/v1/woocommerce")
    c = TestClient(app)
    assert c.post("/v1/woocommerce/webhook/seedleme", content=b"webhook_id=5").json()["ping"] is True
    body = json.dumps({"id": 11}).encode()
    assert c.post("/v1/woocommerce/webhook/seedleme", content=body,
                  headers={"x-wc-webhook-signature": "nope", "x-wc-webhook-topic": "product.updated"}).status_code == 401
    ok = c.post("/v1/woocommerce/webhook/seedleme", content=body,
                headers={"x-wc-webhook-signature": _signed(body, hook_secret),
                         "x-wc-webhook-topic": "product.updated", "x-wc-webhook-delivery-id": "d1"})
    assert ok.json() == {"ok": True} and ran == ["woocommerce_webhook:product.updated"]


@pytest.mark.asyncio
async def test_a_variation_change_rereads_its_product_and_a_delete_archives(wenv):
    await woo.connect("seedleme", URL, KEY, SECRET)
    assert await woo.handle_webhook("seedleme", "product.updated", {"id": 21, "parent_id": 11}) == "saved"
    assert wenv.products["aquatina-basin"]["woo_id"] == "11"
    assert await woo.handle_webhook("seedleme", "product.deleted", {"id": 11}) == "archived"
    assert wenv.products["aquatina-basin"]["status"] == "archived"


@pytest.mark.asyncio
async def test_disconnecting_forgets_the_keys_and_removes_vulas_webhooks(wenv):
    await woo.connect("seedleme", URL, KEY, SECRET)
    await woo.disconnect("seedleme")
    row = wenv.db.tables["vula_woocommerce_accounts"][0]
    assert row["status"] == "disconnected" and row["consumer_key"] is None and row["webhook_secret"] is None
    assert ("DELETE", f"{URL}/wp-json/wc/v3/webhooks/1") in wenv.woo.calls

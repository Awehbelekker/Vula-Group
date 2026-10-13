"""Connect Shopify (2026-10-13, SeedleMe — ~290 handmade basins and sinks on a Shopify store).
A connected store gives Vula the whole catalogue, every photo and live stock, and Shopify calls
back when something changes. Credentials are encrypted, never shown, and Shopify's calls are
only believed when they carry the store's own signature."""
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from vula.integrations import shopify

SHOP = "seedleme.myshopify.com"
SECRET = "shpss_test_secret"


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _node(handle="round-basin", status="ACTIVE", stock=7, variants=None):
    return {
        "id": f"gid://shopify/Product/{abs(hash(handle)) % 10**6}", "handle": handle,
        "title": "Round Concrete Basin – Charcoal – 40 x 12cm", "descriptionHtml": "<p>Hand-cast.</p>",
        "productType": "Basins", "status": status, "tags": ["basin"], "onlineStoreUrl": None,
        "options": [{"name": "Title"}],
        "media": {"nodes": [{"image": {"url": "https://cdn.shopify.com/a.jpg"}},
                            {"image": {"url": "https://cdn.shopify.com/b.jpg"}}, {}]},
        "variants": {"nodes": variants or [
            {"id": "gid://shopify/ProductVariant/1", "title": "Default Title", "sku": "RB-C",
             "price": "1499.00", "availableForSale": True, "inventoryQuantity": stock,
             "selectedOptions": [{"name": "Title", "value": "Default Title"}]}]},
    }


class FakeShopify:
    """Shopify's token endpoint and Admin GraphQL, enough for these tests."""

    def __init__(self, scopes=("read_products", "read_inventory", "read_orders"), nodes=None):
        self.scopes, self.nodes = list(scopes), nodes or [_node()]
        self.calls, self.exchanges, self.hooks = [], 0, []

    async def post(self, url, *, json=None, data=None, headers=None):
        self.calls.append((url, json, data, headers))
        if url.endswith("/admin/oauth/access_token"):
            if data.get("client_secret") != SECRET:
                return _Resp(400, {"error": "invalid_client"})
            self.exchanges += 1
            return _Resp(200, {"access_token": f"tok{self.exchanges}", "expires_in": 86399,
                               "scope": ",".join(self.scopes)})
        q = json["query"]
        if "currentAppInstallation" in q:
            return _Resp(200, {"data": {"shop": {"name": "SeedleMe", "myshopifyDomain": SHOP},
                                        "currentAppInstallation": {"accessScopes": [{"handle": s} for s in self.scopes]}}})
        if "webhookSubscriptionCreate" in q:
            self.hooks.append(json["variables"])
            return _Resp(200, {"data": {"webhookSubscriptionCreate": {"webhookSubscription": {"id": "w"}, "userErrors": []}}})
        if "products(first" in q:
            return _Resp(200, {"data": {"products": {"pageInfo": {"hasNextPage": False}, "nodes": self.nodes}}})
        if "inventoryItem(" in q:
            return _Resp(200, {"data": {"inventoryItem": {"variant": {"product": self.nodes[0]}}}})
        if "product(id" in q:
            return _Resp(200, {"data": {"product": self.nodes[0]}})
        return _Resp(200, {"data": {}})


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.nf, self.op, self.payload = db, table, [], [], "select", None

    def select(self, *_a): return self
    def limit(self, *_a): return self

    def eq(self, k, v):
        self.f.append((k, v))
        return self

    def neq(self, k, v):
        self.nf.append((k, v))
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload, self.key = "upsert", payload, on_conflict
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            for r in rows:
                if r.get(self.key) == self.payload.get(self.key):
                    r.update(self.payload)
                    break
            else:
                rows.append(dict(self.payload))
            return SimpleNamespace(data=[self.payload])
        hit = [r for r in rows if all(r.get(k) == v for k, v in self.f)
               and all(r.get(k) != v for k, v in self.nf)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return SimpleNamespace(data=hit)


class _DB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Q(self, name)


@pytest.fixture()
def env(monkeypatch):
    db, fake = _DB(), FakeShopify()
    products, moves, patches = {}, [], []

    async def save_products(tid, items, st):
        for p in items:
            if p["price_cents"] <= 0:
                continue
            status = "updated" if p["slug"] in products else "created"
            products.setdefault(p["slug"], {"id": f"p-{p['slug']}", "slug": p["slug"], "stock_quantity": None,
                                            "status": "active", "tenant_id": tid})
            products[p["slug"]].update(name=p["name"], images=p.get("images"),
                                       status=p.get("status") or "active")
            st[f"products_{status}"] += 1

    async def list_products(tid, **_k):
        return list(products.values())

    async def update_product(tid, pid, patch):
        patches.append((pid, patch))
        row = next(p for p in products.values() if p["id"] == pid)
        row.update(patch)
        db.tables.setdefault("commerce_products", [])
        db.tables["commerce_products"] = [dict(r, tenant_id=tid) for r in products.values()]
        return row

    async def adjust_stock(tid, pid, *, set_to=None, **k):
        moves.append((pid, set_to, k.get("actor")))
        next(p for p in products.values() if p["id"] == pid)["stock_quantity"] = set_to
        return set_to

    async def list_variants(tid, pid):
        return []

    class _Ingest:
        def __init__(self, tenant_id): pass

        async def ingest_text(self, **k):
            return SimpleNamespace(status="success")

    monkeypatch.setattr(shopify, "_post", fake.post)
    monkeypatch.setattr(shopify, "_db", lambda: db)
    monkeypatch.setattr(shopify, "_RETRY_WAITS", (0.0, 0.0, 0.0))
    monkeypatch.setattr("vula.commerce.site_import._save_products", save_products)
    monkeypatch.setattr("vula.commerce.site_import._db", lambda: db)
    monkeypatch.setattr("vula.commerce.service.list_products", list_products)
    monkeypatch.setattr("vula.commerce.service.update_product", update_product)
    monkeypatch.setattr("vula.commerce.service.adjust_stock", adjust_stock)
    monkeypatch.setattr("vula.commerce.service.list_variants", list_variants)
    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline", _Ingest)
    shopify._TOKENS.clear()
    shopify._SEEN.clear()
    return SimpleNamespace(db=db, shop=fake, products=products, moves=moves, patches=patches)


def test_the_store_address_is_the_myshopify_one():
    assert shopify.normalise_domain("https://SeedleMe.myshopify.com/admin") == SHOP
    assert shopify.normalise_domain("seedleme") == SHOP
    with pytest.raises(shopify.ShopifyError, match="myshopify.com"):
        shopify.normalise_domain("seedleme.com")


@pytest.mark.asyncio
async def test_connecting_stores_secrets_encrypted_and_asks_shopify_for_live_updates(env):
    view = await shopify.connect("seedleme", "seedleme", client_id="cid", client_secret=SECRET)
    row = env.db.tables["vula_shopify_accounts"][0]
    assert row["shop_domain"] == SHOP and row["shop_name"] == "SeedleMe"
    assert SECRET not in json.dumps(row) and "tok1" not in json.dumps(row)     # encrypted at rest
    assert shopify._dec(row["client_secret"]) == SECRET
    assert [h["topic"] for h in env.shop.hooks] == list(shopify.WEBHOOK_TOPICS)
    assert env.shop.hooks[0]["sub"]["uri"].endswith("/v1/shopify/webhook/seedleme")
    assert view["connected"] and view["live_updates"]
    assert not any(k in json.dumps(view) for k in (SECRET, "tok1", "client_secret", "access_token"))


@pytest.mark.asyncio
async def test_wrong_credentials_or_no_product_access_are_refused_with_what_to_do(env):
    with pytest.raises(shopify.ShopifyError, match="Client ID and secret"):
        await shopify.connect("seedleme", SHOP, client_id="cid", client_secret="wrong")
    env.shop.scopes = ["read_orders"]
    with pytest.raises(shopify.ShopifyError, match="read_products"):
        await shopify.connect("seedleme", SHOP, client_id="cid", client_secret=SECRET)
    assert not env.db.tables.get("vula_shopify_accounts")


@pytest.mark.asyncio
async def test_a_store_already_linked_to_another_business_is_refused(env):
    env.db.tables["vula_shopify_accounts"] = [{"tenant_id": "someone-else", "shop_domain": SHOP,
                                               "status": "connected"}]
    with pytest.raises(shopify.ShopifyError, match="another Vula business"):
        await shopify.connect("seedleme", SHOP, client_id="cid", client_secret=SECRET)


@pytest.mark.asyncio
async def test_the_24_hour_token_is_reused_then_renewed(env, monkeypatch):
    await shopify.connect("seedleme", SHOP, client_id="cid", client_secret=SECRET)
    acct = shopify.get_account("seedleme")
    assert await shopify.access_token(acct) == "tok1" and env.shop.exchanges == 1
    clock = [1_000_000.0]
    monkeypatch.setattr(shopify.time, "monotonic", lambda: clock[0])
    shopify._TOKENS["seedleme"] = ("tok1", clock[0] + 100)          # 100s left: inside the renew window
    acct["token_expires_at"] = None
    assert await shopify.access_token(acct) == "tok2" and env.shop.exchanges == 2


def test_a_shopify_product_maps_with_every_photo_its_status_and_stock():
    p = shopify.map_product(_node(status="DRAFT", stock=4), f"https://{SHOP}")
    assert p["slug"] == "round-basin" and p["price_cents"] == 149_900
    assert p["images"] == ["https://cdn.shopify.com/a.jpg", "https://cdn.shopify.com/b.jpg"]
    assert p["image_url"] == "https://cdn.shopify.com/a.jpg"
    assert p["status"] == "draft" and p["stock"] == 4 and p["variants"] == []
    assert p["shopify_id"].startswith("gid://shopify/Product/")
    two = shopify.map_product(_node(variants=[
        {"id": "gid://shopify/ProductVariant/1", "title": "Charcoal", "price": "1499.00",
         "availableForSale": True, "inventoryQuantity": 2, "selectedOptions": [{"name": "Colour", "value": "Charcoal"}]},
        {"id": "gid://shopify/ProductVariant/2", "title": "Ivory", "price": "1599.00",
         "availableForSale": False, "inventoryQuantity": 0, "selectedOptions": [{"name": "Colour", "value": "Ivory"}]},
    ]), f"https://{SHOP}")
    assert two["price_cents"] == 149_900 and len(two["variants"]) == 2 and two["stock"] is None
    assert two["variants"][1]["option_values"] == {"Colour": "Ivory"} and two["variants"][1]["stock"] == 0


@pytest.mark.asyncio
async def test_a_sync_saves_the_catalogue_and_records_stock_only_when_it_changed(env):
    await shopify.connect("seedleme", SHOP, client_id="cid", client_secret=SECRET)
    st = await shopify.sync("seedleme", "SeedleMe")
    assert st["step"] == "done" and st["products_found"] == 1 and st["products_created"] == 1
    row = env.products["round-basin"]
    assert row["stock_quantity"] == 7 and env.moves == [("p-round-basin", 7, "shopify")]
    assert row["shopify_id"].startswith("gid://shopify/Product/")
    assert env.db.tables["vula_shopify_accounts"][0]["last_sync"]["products_found"] == 1
    await shopify.sync("seedleme")
    assert len(env.moves) == 1                                       # unchanged stock: no new movement


def _signed(body: bytes, secret=SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


@pytest.fixture()
def client(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from vula.api import shopify_connect
    ran = []

    def run_now(tenant_id, label, coro, *a, **k):
        ran.append(label)
        coro.close()
    monkeypatch.setattr("vula.commerce.background_tasks.run_background", run_now)
    app = FastAPI()
    app.include_router(shopify_connect.webhook_router, prefix="/v1/shopify")
    return SimpleNamespace(http=TestClient(app), ran=ran)


@pytest.mark.asyncio
async def test_only_a_delivery_signed_with_the_stores_secret_is_believed(env, client):
    await shopify.connect("seedleme", SHOP, client_id="cid", client_secret=SECRET)
    body = json.dumps({"id": 42, "admin_graphql_api_id": "gid://shopify/Product/42"}).encode()
    headers = {"X-Shopify-Topic": "products/update", "X-Shopify-Shop-Domain": SHOP,
               "X-Shopify-Webhook-Id": "w-1"}
    bad = client.http.post("/v1/shopify/webhook/seedleme", content=body,
                           headers={**headers, "X-Shopify-Hmac-Sha256": _signed(body, "not-the-secret")})
    assert bad.status_code == 401 and client.ran == []
    good = client.http.post("/v1/shopify/webhook/seedleme", content=body,
                            headers={**headers, "X-Shopify-Hmac-Sha256": _signed(body)})
    assert good.status_code == 200 and client.ran == ["shopify_webhook:products/update"]
    again = client.http.post("/v1/shopify/webhook/seedleme", content=body,
                             headers={**headers, "X-Shopify-Hmac-Sha256": _signed(body)})
    assert again.json().get("duplicate") is True and len(client.ran) == 1
    other = client.http.post("/v1/shopify/webhook/someone-else", content=body,
                             headers={**headers, "X-Shopify-Webhook-Id": "w-2",
                                      "X-Shopify-Hmac-Sha256": _signed(body)})
    assert other.status_code == 401                                  # no store connected there


@pytest.mark.asyncio
async def test_webhooks_update_stock_and_archive_a_deleted_product(env):
    await shopify.connect("seedleme", SHOP, client_id="cid", client_secret=SECRET)
    await shopify.sync("seedleme")
    env.shop.nodes = [_node(stock=3)]
    assert await shopify.handle_webhook("seedleme", "inventory_levels/update",
                                        {"inventory_item_id": 99, "available": 3}) == "saved"
    assert env.products["round-basin"]["stock_quantity"] == 3
    assert any('"gid://shopify/InventoryItem/99"' in json.dumps(c[1]) for c in env.shop.calls if c[1])
    gid = env.products["round-basin"]["shopify_id"]
    assert await shopify.handle_webhook("seedleme", "products/delete", {"id": int(gid.rsplit("/", 1)[1])}) == "archived"
    assert env.products["round-basin"]["status"] == "archived"


@pytest.mark.asyncio
async def test_disconnecting_forgets_the_credentials(env):
    await shopify.connect("seedleme", SHOP, client_id="cid", client_secret=SECRET)
    shopify.disconnect("seedleme")
    row = env.db.tables["vula_shopify_accounts"][0]
    assert row["status"] == "disconnected" and row["client_secret"] is None and row["access_token"] is None
    assert shopify.get_account("seedleme") is None

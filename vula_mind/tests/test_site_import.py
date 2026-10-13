"""Import from your website (2026-10-12, onboarding SeedleMe — handmade concrete basins, sinks
and baths on a Shopify store). A new shop's catalogue and the pages its customers ask about are
read from its own site: products from the platform's catalogue feed (never prices guessed off a
page), options as variants, pages from the sitemap into the knowledge base. Re-running updates."""
import json

import pytest

from vula.commerce import site_import

ROOT = "https://seedleme.com"

PRODUCTS = {"products": [
    {"title": "Amazon Green Bespoke Bath", "handle": "amazon-green-bespoke-bath", "product_type": "Baths",
     "body_html": "<p>Hand-cast concrete bath.</p><ul><li>Sealed</li><li>Any colour</li></ul>",
     "options": [{"name": "Title"}],
     "variants": [{"title": "Default Title", "option1": "Default Title", "price": "27000.00",
                   "sku": "BATH-AG", "available": True}],
     "images": [{"src": "https://cdn.shopify.com/bath.jpg"}, {"src": "https://cdn.shopify.com/bath-side.jpg"}],
     "tags": ["bath"]},
    {"title": "Round Vessel Basin", "handle": "round-vessel-basin", "product_type": "Basins",
     "body_html": "<p>A round basin.</p>", "options": [{"name": "Colour"}, {"name": "Size"}],
     "variants": [
         {"title": "Charcoal / 400mm", "option1": "Charcoal", "option2": "400mm", "price": "2850.00",
          "sku": "RVB-C-400", "available": True},
         {"title": "White / 450mm", "option1": "White", "option2": "450mm", "price": "3150.50",
          "sku": "RVB-W-450", "available": False}],
     "images": []},
    {"title": "Custom Kitchen Sink", "handle": "custom-kitchen-sink", "product_type": "Sinks",
     "body_html": "<p>Made to measure — ask for a quote.</p>", "options": [{"name": "Title"}],
     "variants": [{"title": "Default Title", "price": "0.00", "available": True}], "images": []},
]}

SITEMAP = f"""<?xml version="1.0"?><sitemapindex>
<sitemap><loc>{ROOT}/sitemap_pages_1.xml</loc></sitemap>
<sitemap><loc>{ROOT}/sitemap_products_1.xml?from=1&amp;to=9</loc></sitemap>
<sitemap><loc>https://other.example/sitemap.xml</loc></sitemap></sitemapindex>"""
PAGES_SM = f"""<urlset><url><loc>{ROOT}/</loc></url><url><loc>{ROOT}/pages/about-us</loc></url>
<url><loc>{ROOT}/pages/contact-us</loc></url><url><loc>{ROOT}/cart</loc></url>
<url><loc>{ROOT}/blogs/seedleme-blog/basins</loc></url></urlset>"""
PRODUCTS_SM = f"<urlset><url><loc>{ROOT}/products/round-vessel-basin</loc></url></urlset>"

LONG = "We hand-craft every concrete basin, sink and bath in Cape Town. " * 8


def _page(title, body):
    return f"<html><head><title>{title}</title></head><body><p>{body}</p></body></html>".encode()


SITE = {
    f"{ROOT}/products.json?limit=25&page=1": json.dumps(PRODUCTS).encode(),
    f"{ROOT}/sitemap.xml": SITEMAP.encode(),
    f"{ROOT}/sitemap_pages_1.xml": PAGES_SM.encode(),
    f"{ROOT}/sitemap_products_1.xml?from=1&to=9": PRODUCTS_SM.encode(),
    f"{ROOT}/": _page("Seedleme - Handmade Concrete Basins", LONG),
    f"{ROOT}/pages/about-us": _page("About us", "Over 67 years of experience. " + LONG),
    f"{ROOT}/pages/contact-us": _page("Contact", "5 Korhaan Close, Joostenbergvlakte."),   # too short
    f"{ROOT}/blogs/seedleme-blog/basins": _page("Divine design", LONG),
    f"{ROOT}/policies/refund-policy": _page("Refund policy",
                                            "Unused products in original packaging can be returned within 7 days. " * 4),
}


class _Res:
    def __init__(self, status="success"):
        self.status = status


class FakePipeline:
    docs = {}

    def __init__(self, tenant_id):
        self.tenant_id = tenant_id

    async def ingest_text(self, content, filename, doc_id=None, source_type="document", category=None):
        FakePipeline.docs[doc_id] = {"content": content, "filename": filename, "source_type": source_type}
        return _Res()


class _JobsDB:
    """vula_background_jobs, in memory: what both uvicorn workers read."""
    def __init__(self):
        self.rows = {}

    def table(self, name):
        assert name == "vula_background_jobs"
        return _JobsQ(self)


class _JobsQ:
    def __init__(self, db):
        self.db, self.f, self.payload = db, {}, None

    def select(self, *_a): return self
    def limit(self, *_a): return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def upsert(self, payload, on_conflict=None):
        self.payload = payload
        return self

    def execute(self):
        from types import SimpleNamespace
        if self.payload is not None:
            self.db.rows[(self.payload["tenant_id"], self.payload["kind"])] = dict(self.payload)
            return SimpleNamespace(data=[self.payload])
        row = self.db.rows.get((self.f.get("tenant_id"), self.f.get("kind")))
        return SimpleNamespace(data=[row] if row else [])


@pytest.fixture()
def site(monkeypatch):
    fetched = []

    async def fetch(url, max_bytes=None):
        fetched.append(url)
        if url not in SITE:
            raise RuntimeError("404")
        return SITE[url]

    imported, variants = [], []
    products_db = {}

    async def import_products(tid, body):
        out = []
        for r in body["rows"]:
            status = "updated" if r["slug"] in products_db else "created"
            products_db[r["slug"]] = {"id": f"p-{r['slug']}", "slug": r["slug"], **r}
            out.append({"slug": r["slug"], "status": status})
        imported.append(body["rows"])
        return {"results": out}

    async def list_products(tid, **_k):
        return list(products_db.values())

    async def list_variants(tid, pid):
        return [v for v in variants if v["product_id"] == pid]

    async def create_variant(tid, pid, data):
        variants.append({"id": f"v{len(variants)}", "product_id": pid, **data})
        return variants[-1]

    async def update_variant(tid, vid, data):
        next(v for v in variants if v["id"] == vid).update(data)
        return {}

    monkeypatch.setattr(site_import, "_fetch", fetch)
    monkeypatch.setattr(site_import, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(site_import, "_RETRY_WAITS", (0.0, 0.0, 0.0))
    jobs = _JobsDB()
    monkeypatch.setattr(site_import, "_db", lambda: jobs)
    site_import._STATUS.clear()
    monkeypatch.setattr("vula.api.commerce.admin_import_products", import_products)
    monkeypatch.setattr("vula.commerce.service.list_products", list_products)
    monkeypatch.setattr("vula.commerce.service.list_variants", list_variants)
    monkeypatch.setattr("vula.commerce.service.create_variant", create_variant)
    monkeypatch.setattr("vula.commerce.service.update_variant", update_variant)
    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline", FakePipeline)
    FakePipeline.docs = {}
    return {"fetched": fetched, "imported": imported, "variants": variants, "jobs": jobs}


def test_the_address_is_normalised_to_the_site_root():
    assert site_import.normalise_root("seedleme.com") == ROOT
    assert site_import.normalise_root("https://SeedleMe.com/collections/all?page=3") == ROOT
    with pytest.raises(ValueError):
        site_import.normalise_root("not a site")


@pytest.mark.asyncio
async def test_products_come_from_the_catalogue_feed_with_options_as_variants(site):
    st = await site_import.import_site("seedleme", "seedleme.com", "SeedleMe")
    assert st["step"] == "done" and st["platform"] == "shopify" and st["products_found"] == 3
    rows = {r["slug"]: r for r in site["imported"][0]}
    assert rows["amazon-green-bespoke-bath"]["price_cents"] == 2_700_000
    assert rows["amazon-green-bespoke-bath"]["category"] == "baths"
    assert rows["amazon-green-bespoke-bath"]["image_url"] == "https://cdn.shopify.com/bath.jpg"
    assert rows["amazon-green-bespoke-bath"]["images"] == ["https://cdn.shopify.com/bath.jpg",
                                                           "https://cdn.shopify.com/bath-side.jpg"]
    assert "• Sealed" in rows["amazon-green-bespoke-bath"]["description"]
    assert rows["round-vessel-basin"]["price_cents"] == 285_000          # the lowest option
    assert "custom-kitchen-sink" not in rows and st["products_on_request"] == 1
    assert st["products_created"] == 2 and st["variants"] == 2
    white = next(v for v in site["variants"] if v["sku"] == "RVB-W-450")
    assert white["option_values"] == {"Colour": "White", "Size": "450mm"}
    assert white["price_cents"] == 315_050 and white["in_stock"] is False


@pytest.mark.asyncio
async def test_pages_and_the_catalogue_go_into_the_knowledge_base(site):
    st = await site_import.import_site("seedleme", ROOT, "SeedleMe")
    texts = {d["filename"]: d for d in FakePipeline.docs.values()}
    assert all(d["source_type"] == "website" for d in texts.values())
    cat = texts["Website — product catalogue (seedleme.com)"]["content"]
    assert "Amazon Green Bespoke Bath — R27,000.00" in cat and "Custom Kitchen Sink — price on request" in cat
    assert "Charcoal / 400mm R2,850.00" in cat
    assert any("About us" in f for f in texts) and any("Refund policy" in f for f in texts)
    assert "Source: https://seedleme.com/pages/about-us" in next(
        d["content"] for f, d in texts.items() if "About us" in f)
    # the cart, the other host, product pages (already in the feed) are never fetched
    assert f"{ROOT}/cart" not in site["fetched"] and "https://other.example/sitemap.xml" not in site["fetched"]
    assert f"{ROOT}/products/round-vessel-basin" not in site["fetched"]
    assert st["skipped_pages"] == 1                     # the contact page is too short to use
    assert st["kb_documents"] == 1 + 4                  # catalogue + home, about, blog, refund policy


@pytest.mark.asyncio
async def test_running_it_again_updates_instead_of_duplicating(site):
    await site_import.import_site("seedleme", ROOT)
    n_docs = len(FakePipeline.docs)
    st = await site_import.import_site("seedleme", ROOT)
    assert st["products_created"] == 0 and st["products_updated"] == 2
    assert len(site["variants"]) == 2 and len(FakePipeline.docs) == n_docs


@pytest.mark.asyncio
async def test_a_site_without_a_shop_feed_still_gives_its_pages(site, monkeypatch):
    for k in [k for k in SITE if "products.json" in k]:
        monkeypatch.delitem(SITE, k)
    st = await site_import.import_site("seedleme", ROOT)
    assert st["platform"] == "" and st["products_found"] == 0 and st["kb_documents"] >= 3
    assert f"{ROOT}/products/round-vessel-basin" in site["fetched"]    # no feed → product pages are read


def test_prices_are_whole_cents():
    assert site_import._cents("3150.50") == 315_050
    assert site_import._cents("2700000", minor_unit=2) == 2_700_000      # WooCommerce Store API
    assert site_import._cents("") == 0 and site_import._cents("abc") == 0


# 2026-10-13: SeedleMe's real first run — no products, About and Contact lost, "&ndash;" titles.

ROUND_PAGE = ('<html><head><title>Round Vessel Basin &ndash; Seedleme</title>'
              '<script type="application/ld+json">{"@context":"http://schema.org/","@type":"Product",'
              '"name":"Round Vessel Basin","description":"&lt;p&gt;A round basin.&lt;/p&gt;",'
              '"image":["//cdn.shopify.com/round.jpg","https://cdn.shopify.com/round-top.jpg"],'
              '"offers":[{"@type":"Offer","price":"2850.00","priceCurrency":"ZAR",'
              '"availability":"http://schema.org/InStock"}]}</script></head><body>x</body></html>').encode()


@pytest.mark.asyncio
async def test_a_refused_feed_falls_back_to_each_product_and_says_why(site, monkeypatch):
    import httpx
    for k in [k for k in SITE if "products.json" in k]:
        monkeypatch.delitem(SITE, k)
    monkeypatch.setitem(SITE, f"{ROOT}/products/round-vessel-basin.json",
                        json.dumps({"product": PRODUCTS["products"][1]}).encode())
    real = site_import._fetch

    async def refuse_feed(url, max_bytes=None):
        if "products.json" in url:
            req = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req))
        return await real(url, max_bytes)
    monkeypatch.setattr(site_import, "_fetch", refuse_feed)
    st = await site_import.import_site("seedleme", ROOT, "SeedleMe")
    assert st["platform"] == "shopify" and st["products_found"] == 1 and st["products_created"] == 1
    assert "Shopify product feed: HTTP 403" in st["catalogue_note"]
    rows = {r["slug"]: r for r in site["imported"][0]}
    assert rows["round-vessel-basin"]["price_cents"] == 285_000 and st["variants"] == 2


@pytest.mark.asyncio
async def test_without_any_json_the_product_pages_own_schema_gives_the_price(site, monkeypatch):
    for k in [k for k in SITE if "products.json" in k]:
        monkeypatch.delitem(SITE, k)
    monkeypatch.setitem(SITE, f"{ROOT}/products/round-vessel-basin", ROUND_PAGE)
    st = await site_import.import_site("seedleme", ROOT)
    row = site["imported"][0][0]
    assert st["platform"] == "website" and row["slug"] == "round-vessel-basin"
    assert row["price_cents"] == 285_000 and row["image_url"] == "https://cdn.shopify.com/round.jpg"
    assert row["description"] == "A round basin."
    assert row["images"] == ["https://cdn.shopify.com/round.jpg", "https://cdn.shopify.com/round-top.jpg"]


def test_page_titles_are_plain_text_and_html_sitemaps_are_not_read():
    title, _ = site_import.page_text(b"<html><head><title>About us &ndash; Seedleme</title></head></html>")
    assert title == "About us \u2013 Seedleme"
    assert site_import._SKIP_PATH.search("/pages/html-sitemap-products")
    assert not site_import._SKIP_PATH.search("/pages/about-us")


@pytest.mark.asyncio
async def test_a_new_knowledge_base_created_by_two_pages_at_once_loses_neither(monkeypatch):
    """Qdrant refuses the second create with "already exists" — that is the collection we want."""
    import asyncio

    from vula.ingestion import pipeline
    state = {"exists": False, "creates": 0}

    class _R:
        def __init__(self, code, text=""):
            self.status_code, self.text = code, text

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url):
            return _R(200 if state["exists"] else 404)

        async def put(self, url, json=None):
            if url.endswith("/index"):
                return _R(200)
            state["creates"] += 1
            if state["exists"]:
                return _R(409, '{"status":{"error":"Wrong input: Collection `vula_seedleme` already exists!"}}')
            await asyncio.sleep(0)
            state["exists"] = True
            return _R(200)

    monkeypatch.setattr(pipeline.httpx, "AsyncClient", _Client)
    store = pipeline.QdrantStore("http://qdrant.test")
    await asyncio.gather(*(store.ensure_collection("seedleme", 1536) for _ in range(4)))
    assert state["creates"] == 1                     # the per-collection lock: one create, no race
    state["exists"] = False                          # another process won between our GET and PUT
    calls = []
    orig_get = _Client.get

    async def get_then_lose(self, url):
        r = await orig_get(self, url)
        state["exists"] = True
        calls.append(url)
        return r
    monkeypatch.setattr(_Client, "get", get_then_lose)
    await store.ensure_collection("seedleme", 1536)  # "already exists" → no error


# 2026-10-13, the second run: the owner pressed Import four times because the progress lived in
# one worker's memory; four runs at once got 429/503 from Shopify and 52 of ~290 products landed.

def _refused(url, code=429):
    import httpx
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(str(code), request=req, response=httpx.Response(code, request=req))


@pytest.mark.asyncio
async def test_the_other_worker_sees_the_run_and_its_result(site):
    await site_import.import_site("seedleme", ROOT, "SeedleMe")
    site_import._STATUS.clear()                      # a different worker: nothing in its memory
    st = site_import.status("seedleme")
    assert st["running"] is False and st["step"] == "done" and st["products_created"] == 2
    row = site["jobs"].rows[("seedleme", "site_import")]
    assert row["running"] is False and "running" not in row["status"]


def test_a_run_in_progress_on_either_worker_blocks_another_until_it_goes_stale(site):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    site["jobs"].rows[("seedleme", "site_import")] = {
        "running": True, "status": {"step": "reading product pages (40 of 290)"},
        "updated_at": now.isoformat()}
    assert site_import.status("seedleme")["running"] is True
    site["jobs"].rows[("seedleme", "site_import")]["updated_at"] = (now - timedelta(minutes=20)).isoformat()
    st = site_import.status("seedleme")
    assert st["running"] is False and "run it again" in st["error"]


@pytest.mark.asyncio
async def test_a_slow_down_answer_is_waited_out_and_tried_again(site, monkeypatch):
    real, tries = site_import._fetch, []

    async def busy_once(url, max_bytes=None):
        tries.append(url)
        if "products.json" in url and tries.count(url) == 1:
            raise _refused(url, 429)
        return await real(url, max_bytes)
    monkeypatch.setattr(site_import, "_fetch", busy_once)
    st = await site_import.import_site("seedleme", ROOT)
    assert st["products_found"] == 3 and "catalogue_note" not in st
    assert tries.count(f"{ROOT}/products.json?limit=25&page=1") == 2


@pytest.mark.asyncio
async def test_a_feed_that_stops_part_way_is_finished_from_the_product_pages(site, monkeypatch):
    first = {"products": PRODUCTS["products"][:1] * 25}            # a full first page of 25
    first["products"] = [dict(p, handle=f"bath-{i}", title=f"Bath {i}") for i, p in enumerate(first["products"])]
    monkeypatch.setitem(SITE, f"{ROOT}/products.json?limit=25&page=1", json.dumps(first).encode())
    monkeypatch.setitem(SITE, f"{ROOT}/products/round-vessel-basin.json",
                        json.dumps({"product": PRODUCTS["products"][1]}).encode())
    real = site_import._fetch

    async def page2_refused(url, max_bytes=None):
        if url.endswith("page=2"):
            raise _refused(url, 429)
        return await real(url, max_bytes)
    monkeypatch.setattr(site_import, "_fetch", page2_refused)
    st = await site_import.import_site("seedleme", ROOT)
    slugs = {r["slug"] for r in site["imported"][0]}
    assert len(slugs) == 26 and "round-vessel-basin" in slugs
    assert "Shopify product feed page 2: HTTP 429" in st["catalogue_note"]


@pytest.mark.asyncio
async def test_the_import_endpoint_keeps_the_whole_gallery(monkeypatch):
    from vula.api import commerce
    saved = {}

    async def list_products(tid, **_k):
        return []

    async def create_product(tid, payload):
        saved.update(payload)
        return {"id": "p1", **payload}

    class _NoCats:
        def table(self, *_a): raise RuntimeError("offline")
    monkeypatch.setattr(commerce.service, "list_products", list_products)
    monkeypatch.setattr(commerce.service, "create_product", create_product)
    monkeypatch.setattr(commerce.service, "_client", lambda: _NoCats())
    res = await commerce.admin_import_products("seedleme", {"rows": [{
        "name": "Round Basin", "price_cents": 149900,
        "images": "https://cdn.x/a.jpg|not a url|https://cdn.x/b.jpg|https://cdn.x/a.jpg"}]})
    assert res["created"] == 1
    assert saved["images"] == ["https://cdn.x/a.jpg", "https://cdn.x/b.jpg"]
    assert saved["image_url"] == "https://cdn.x/a.jpg"

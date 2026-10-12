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
     "images": [{"src": "https://cdn.shopify.com/bath.jpg"}], "tags": ["bath"]},
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
    f"{ROOT}/products.json?limit=100&page=1": json.dumps(PRODUCTS).encode(),
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


@pytest.fixture()
def site(monkeypatch):
    fetched = []

    async def fetch(url):
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
    monkeypatch.setattr("vula.api.commerce.admin_import_products", import_products)
    monkeypatch.setattr("vula.commerce.service.list_products", list_products)
    monkeypatch.setattr("vula.commerce.service.list_variants", list_variants)
    monkeypatch.setattr("vula.commerce.service.create_variant", create_variant)
    monkeypatch.setattr("vula.commerce.service.update_variant", update_variant)
    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline", FakePipeline)
    FakePipeline.docs = {}
    return {"fetched": fetched, "imported": imported, "variants": variants}


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

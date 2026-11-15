"""The website import reads more, and reads it exactly (12 Nov 2026 — Ian: "how strong is our
website scrape … everything from products, images and descriptions to terms and privacy").

Sites here are made up, shaped like the kinds of small-business sites Vula meets: a WordPress
brochure site with no /sitemap.xml, a Wix-style shop, a site built in the browser, a Shopify
shop (tests/test_site_import.py)."""
import json
from unittest.mock import AsyncMock

import pytest

from tests.test_site_import import _JobsDB
from vula.commerce import site_facts, site_import

ROOT = "https://harbourvine.example"

NAV = ('<header><nav><a href="/">Home</a> <a href="/about-us/">About</a> <a href="/menu/">Menu</a> '
       '<a href="/blog/">Blog</a> Free delivery over R500!</nav></header>')
FOOTER = ('<footer><p>Harbour &amp; Vine Deli · 12 Beach Road, Sea Point, Cape Town · '
          '<a href="tel:+27215550123">021 555 0123</a> · <a href="mailto:hello@harbourvine.example">hello@harbourvine.example</a></p>'
          '<p>Mon–Fri 07:30–17:00, Sat 08:00–14:00. Closed Sundays.</p>'
          '<a href="/terms-and-conditions/">Terms</a> <a href="/privacy-policy/">Privacy</a> '
          '<a href="/contact/">Contact</a> <a href="https://instagram.com/harbourvine">Instagram</a></footer>')
JSONLD = json.dumps({"@context": "https://schema.org", "@graph": [
    {"@type": "WebSite", "name": "Harbour & Vine"},
    {"@type": ["Restaurant", "LocalBusiness"], "name": "Harbour & Vine Deli", "telephone": "+27 21 555 0123",
     "email": "hello@harbourvine.example",
     "address": {"@type": "PostalAddress", "streetAddress": "12 Beach Road", "addressLocality": "Sea Point",
                 "addressRegion": "Cape Town", "postalCode": "8005"},
     "openingHoursSpecification": [
         {"dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], "opens": "07:30", "closes": "17:00"},
         {"dayOfWeek": "https://schema.org/Saturday", "opens": "08:00:00", "closes": "14:00:00"}],
     "areaServed": ["Sea Point", {"@type": "Place", "name": "Green Point"}],
     "sameAs": ["https://www.facebook.com/harbourvine"]}]})

BODY = "We bake sourdough every morning and cater for events across the Atlantic Seaboard. " * 6


def page(title, body, main=True, jsonld=False, extra=""):
    content = f"<main><h1>{title}</h1><p>{body}</p>{extra}</main>" if main else f"<div><h1>{title}</h1><p>{body}</p>{extra}</div>"
    ld = f'<script type="application/ld+json">{JSONLD}</script>' if jsonld else ""
    return (f"<html><head><title>{title}</title>{ld}<script>var x=1;</script></head>"
            f'<body><div class="cookie-banner">We use cookies. Accept?</div>{NAV}{content}{FOOTER}</body></html>').encode()


BLOG_POSTS = {f"{ROOT}/blog/post-{i}/": page(f"Post {i}", "A story about delivery and our bread. " * 10)
              for i in range(8)}
WP_SITE = {
    f"{ROOT}/": page("Harbour & Vine Deli", BODY, jsonld=True,
                     extra='<a href="/blog/">Blog</a> <a href="/wp-content/uploads/Menu-2026.pdf">Our menu (PDF)</a> '
                           '<a href="/wp-content/uploads/team-photo.pdf">Team photo</a>'),
    f"{ROOT}/about-us/": page("About us", "Family run since 1998. " + BODY),
    f"{ROOT}/menu/": page("Menu", "Sourdough loaf, croissants, platters and coffee. " * 8),
    f"{ROOT}/contact/": page("Contact us", "Visit us or phone during trading hours. " * 8),
    f"{ROOT}/terms-and-conditions/": page("Terms and conditions",
                                          "Orders may be cancelled up to 24 hours before delivery for a full refund. " * 5),
    f"{ROOT}/privacy-policy/": page("Privacy policy", "We process personal information under POPIA. " * 8),
    f"{ROOT}/blog/": page("Blog", " ".join(f'<a href="/blog/post-{i}/">Post {i}</a>' for i in range(8)) + " " + BODY),
    **BLOG_POSTS,
    f"{ROOT}/wp-content/uploads/Menu-2026.pdf": b"%PDF-1.4 menu",
}


class _Res:
    def __init__(self, status="success"):
        self.status = status


class _Store:
    deleted = []

    async def delete_document(self, tenant_id, doc_id):
        _Store.deleted.append(doc_id)


class FakePipeline:
    docs = {}

    def __init__(self, tenant_id):
        self.tenant_id = tenant_id
        self.store = _Store()

    async def ingest_text(self, content, filename, doc_id=None, source_type="document", category=None):
        FakePipeline.docs[doc_id] = {"content": content, "filename": filename}
        return _Res()


@pytest.fixture()
def world(monkeypatch, tmp_path):
    sites = {}
    fetched = []

    async def fetch(url, max_bytes=None):
        fetched.append(url)
        if url not in sites:
            raise RuntimeError("404")
        return sites[url]

    monkeypatch.setattr(site_import, "_fetch", fetch)
    monkeypatch.setattr(site_import, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(site_import, "_RETRY_WAITS", (0.0, 0.0, 0.0))
    jobs = _JobsDB()
    monkeypatch.setattr(site_import, "_db", lambda: jobs)
    site_import._STATUS.clear()
    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline", FakePipeline)
    FakePipeline.docs, _Store.deleted = {}, []
    drafted, facts, pdf_runs, products = [], [], [], []

    async def draft_and_save(tid, read):
        drafted.append([u for u, _t, _x in read])
        return 2

    monkeypatch.setattr("vula.commerce.profile_from_site.draft_and_save", draft_and_save)
    monkeypatch.setattr("vula.commerce.business_profile.save_drafts", lambda tid, d: facts.append(d) or len(d))

    async def import_products(tid, body):
        products.extend(body["rows"])
        return {"results": [{"slug": r["slug"], "status": "created"} for r in body["rows"]]}

    monkeypatch.setattr("vula.api.commerce.admin_import_products", import_products)
    monkeypatch.setattr("vula.commerce.service.list_products", AsyncMock(return_value=[]))
    monkeypatch.setattr("vula.commerce.meta_catalog.sync_if_connected", AsyncMock())
    from vula.api import server as srv
    monkeypatch.setattr(srv, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr("vula.commerce.profile_from_docs.already_running", lambda t: False)
    monkeypatch.setattr("vula.commerce.background_tasks.run_background",
                        lambda tid, label, coro: (pdf_runs.append(label), coro.close()))
    return {"sites": sites, "fetched": fetched, "drafted": drafted, "facts": facts, "pdf_runs": pdf_runs,
            "products": products, "tmp": tmp_path}


# ── the pure pieces ─────────────────────────────────────────────────────────────────────────

def test_main_text_keeps_the_page_and_drops_menu_footer_and_banners():
    raw = page("About us", "Family run since 1998.").decode()
    text = site_facts.main_text(raw)
    assert "Family run since 1998." in text
    for noise in ("Free delivery over R500", "We use cookies", "021 555 0123", "var x"):
        assert noise not in text
    no_main = page("About us", "Family run since 1998.", main=False).decode()
    text = site_facts.main_text(no_main)
    assert "Family run since 1998." in text and "Free delivery over R500" not in text and "021 555 0123" not in text


def test_the_footer_is_read_on_its_own():
    text = site_facts.footer_text(page("Home", BODY).decode())
    assert "12 Beach Road" in text and "Mon–Fri 07:30–17:00" in text
    assert site_facts.footer_text("<html><body>no footer</body></html>") == ""


def test_links_are_same_site_pages_footer_first():
    got = site_facts.links(WP_SITE[f"{ROOT}/"].decode(), f"{ROOT}/", ROOT)
    assert got[:3] == [f"{ROOT}/terms-and-conditions/", f"{ROOT}/privacy-policy/", f"{ROOT}/contact/"]
    assert f"{ROOT}/about-us/" in got and f"{ROOT}/blog/" in got
    assert not any("instagram" in u or u.endswith(".pdf") or "tel:" in u for u in got)


def test_only_pdfs_worth_reading_are_picked():
    got = site_facts.pdf_links(WP_SITE[f"{ROOT}/"].decode(), f"{ROOT}/", ROOT)
    assert got == [(f"{ROOT}/wp-content/uploads/Menu-2026.pdf", "Our menu (PDF)")]


def test_exact_facts_from_schema_org_and_links():
    facts = site_facts.business_facts([(f"{ROOT}/", WP_SITE[f"{ROOT}/"].decode())])
    v = {k: f["value"] for k, f in facts.items()}
    assert v["phone"] == "+27 21 555 0123" and v["email"] == "hello@harbourvine.example"
    assert v["address"] == "12 Beach Road, Sea Point, Cape Town, 8005"
    assert v["hours"] == "Monday, Tuesday, Wednesday, Thursday, Friday 07:30–17:00; Saturday 08:00–14:00"
    assert v["area"] == "Sea Point, Green Point" and v["socials"] == "https://www.facebook.com/harbourvine"
    assert all(f["url"] == f"{ROOT}/" for f in facts.values())


def test_without_schema_org_the_phone_and_email_links_still_count():
    facts = site_facts.business_facts([(f"{ROOT}/contact/", WP_SITE[f"{ROOT}/contact/"].decode())])
    assert facts["phone"]["value"] == "+27215550123" and facts["email"]["value"] == "hello@harbourvine.example"
    assert "hours" not in facts and "instagram.com/harbourvine" in facts["socials"]["value"]


def test_facts_become_grounded_drafts():
    facts = site_facts.business_facts([(f"{ROOT}/", WP_SITE[f"{ROOT}/"].decode())])
    drafts = site_facts.facts_as_drafts(facts)
    assert set(drafts) == {"hours", "area", "contact"}
    assert drafts["contact"]["answer"] == ("Phone: +27 21 555 0123; Email: hello@harbourvine.example; "
                                           "Address: 12 Beach Road, Sea Point, Cape Town, 8005")
    assert all(d["quote"] and d["url"] == f"{ROOT}/" for d in drafts.values())
    assert site_facts.facts_as_drafts({}) == {}


def test_pages_are_sorted_into_topics_and_policy_kinds():
    assert site_facts.topic_of(f"{ROOT}/") == "contact"
    assert site_facts.topic_of(f"{ROOT}/terms-and-conditions/") == "policies"
    assert site_facts.topic_of(f"{ROOT}/pages/shipping") == "delivery"
    assert site_facts.topic_of(f"{ROOT}/faq") == "faq"
    assert site_facts.topic_of(f"{ROOT}/blog/post-1/") is None
    assert site_facts.policy_kind(f"{ROOT}/privacy-policy/") == "privacy"
    assert site_facts.policy_kind(f"{ROOT}/policies/refund-policy") == "returns"


def test_a_page_built_in_the_browser_is_recognised():
    spa = '<html><head><script src="/app.js"></script></head><body><div id="root"></div></body></html>'
    assert site_facts.builds_in_browser(spa, site_facts.main_text(spa))
    assert not site_facts.builds_in_browser(WP_SITE[f"{ROOT}/"].decode(), BODY)


def test_important_pages_rank_before_blog_posts_under_the_cap():
    import asyncio
    urls = [f"{ROOT}/blog/delivery-tips/", f"{ROOT}/gallery/", f"{ROOT}/privacy-policy/", f"{ROOT}/collections/all",
            f"{ROOT}/contact/", f"{ROOT}/", f"{ROOT}/terms-and-conditions/"]
    ranked = asyncio.run(site_import.sitemap_urls(ROOT, pages=urls))
    assert ranked[:4] == [f"{ROOT}/", f"{ROOT}/privacy-policy/", f"{ROOT}/terms-and-conditions/", f"{ROOT}/contact/"]
    assert ranked[-1] == f"{ROOT}/blog/delivery-tips/"         # a blog post, even one about delivery, is last


# ── a WordPress brochure site with no /sitemap.xml ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_site_without_a_sitemap_is_read_by_following_its_links(world):
    world["sites"].update(WP_SITE)
    st = await site_import.import_site("hv", ROOT, "Harbour & Vine", max_pages=8)
    assert st["step"] == "done" and st["found_by"] == "links"
    read = world["drafted"][0]
    # terms, privacy and contact made it in under an 8-page cap; blog posts were what got left out
    for must in ("/terms-and-conditions/", "/privacy-policy/", "/contact/", "/about-us/", "/menu/"):
        assert any(u.endswith(must) for u in read), must
    assert sum("/blog/post-" in u for u in read) <= 1
    cov = st["coverage"]
    assert cov["policies"]["terms"] == f"{ROOT}/terms-and-conditions/" and cov["policies"]["privacy"]
    assert cov["policies"]["returns"] is None                      # honestly not found
    assert cov["contact"] == {"phone": True, "email": True, "address": True, "hours": True, "socials": True}
    assert cov["pages"]["how"] == "links" and cov["pdfs"] == ["Our menu (PDF)"]


@pytest.mark.asyncio
async def test_pages_are_stored_without_the_menu_and_the_footer_is_stored_once(world):
    world["sites"].update(WP_SITE)
    await site_import.import_site("hv", ROOT, "Harbour & Vine", max_pages=8)
    docs = FakePipeline.docs.values()
    pages = [d for d in docs if "contact details" not in d["filename"]]
    assert pages and not any("021 555 0123" in d["content"] or "We use cookies" in d["content"] for d in pages)
    footer = [d for d in docs if "contact details" in d["filename"]]
    assert len(footer) == 1 and "12 Beach Road" in footer[0]["content"]


@pytest.mark.asyncio
async def test_exact_facts_are_saved_as_drafts_after_the_model_drafts(world):
    world["sites"].update(WP_SITE)
    st = await site_import.import_site("hv", ROOT, max_pages=8)
    assert len(world["drafted"]) == 1 and len(world["facts"]) == 1           # model first, then the exact facts
    assert set(world["facts"][0]) == {"hours", "area", "contact"}
    assert st["facts"]["phone"] == "+27 21 555 0123" and st["profile_facts"] == 3


@pytest.mark.asyncio
async def test_a_linked_menu_pdf_is_downloaded_and_handed_to_the_document_reader(world):
    world["sites"].update(WP_SITE)
    st = await site_import.import_site("hv", ROOT, max_pages=8)
    assert st["pdfs_read"] == ["Menu-2026.pdf"] and world["pdf_runs"] == ["site_import_pdfs"]
    assert (world["tmp"] / "hv" / "Menu-2026.pdf").read_bytes() == b"%PDF-1.4 menu"
    assert f"{ROOT}/wp-content/uploads/team-photo.pdf" not in world["fetched"]


@pytest.mark.asyncio
async def test_robots_txt_names_the_sitemap(world):
    world["sites"].update({
        f"{ROOT}/robots.txt": f"User-agent: *\nSitemap: {ROOT}/wp-sitemap.xml\nSitemap: https://elsewhere.example/s.xml".encode(),
        f"{ROOT}/wp-sitemap.xml": f"<urlset><url><loc>{ROOT}/</loc></url><url><loc>{ROOT}/privacy-policy/</loc></url></urlset>".encode(),
        f"{ROOT}/": WP_SITE[f"{ROOT}/"], f"{ROOT}/privacy-policy/": WP_SITE[f"{ROOT}/privacy-policy/"]})
    st = await site_import.import_site("hv", ROOT)
    assert st["found_by"] == "sitemap" and st["coverage"]["policies"]["privacy"] == f"{ROOT}/privacy-policy/"
    assert "https://elsewhere.example/s.xml" not in world["fetched"]


# ── a Wix-style shop: product pages with schema.org data, photos kept ───────────────────────

def _product_page(name, price, img):
    ld = json.dumps({"@context": "https://schema.org", "@type": "Product", "name": name,
                     "description": f"<p>{name}, hand made.</p>", "image": [img],
                     "offers": {"@type": "Offer", "price": price, "priceCurrency": "ZAR",
                                "availability": "https://schema.org/InStock"}})
    return (f'<html><head><title>{name}</title><script type="application/ld+json">{ld}</script></head>'
            f"<body><main><h1>{name}</h1><p>{name}, hand made in Cape Town. " * 3 + "</p></main></body></html>").encode()


@pytest.mark.asyncio
async def test_a_wix_style_shop_gives_its_products_and_keeps_their_photos(world, monkeypatch):
    shop = "https://potter.example"
    img = "https://static.wixstatic.example/media/bowl.jpg"
    world["sites"].update({
        f"{shop}/sitemap.xml": f"<urlset><url><loc>{shop}/</loc></url><url><loc>{shop}/product-page/blue-bowl</loc></url></urlset>".encode(),
        f"{shop}/": page("Potter", BODY),
        f"{shop}/product-page/blue-bowl": _product_page("Blue Bowl", "450.00", img),
        img: b"\xff\xd8\xff\xe0jpegdata",
    })
    stored = []
    monkeypatch.setattr(site_import, "_store_image",
                        lambda tid, slug, i, data, ct, ext: stored.append((tid, slug, i, ct, ext)) or f"https://store.example/{slug}-{i}{ext}")
    st = await site_import.import_site("potter", shop)
    assert st["platform"] == "website" and st["products_found"] == 1
    row = world["products"][0]
    assert (row["name"], row["price_cents"]) == ("Blue Bowl", 45000)
    assert row["image_url"] == "https://store.example/blue-bowl-0.jpg"            # our copy, not their CDN
    assert stored == [("potter", "blue-bowl", 0, "image/jpeg", ".jpg")] and st["images_kept"] == 1
    assert st["coverage"]["products"] == {"found": 1, "with_price": 1, "with_image": 1, "with_description": 1,
                                          "images_kept": 1}


@pytest.mark.asyncio
async def test_a_photo_that_cannot_be_kept_keeps_its_original_address(world, monkeypatch):
    shop = "https://potter.example"
    img = "https://static.wixstatic.example/media/missing.jpg"
    world["sites"].update({
        f"{shop}/sitemap.xml": f"<urlset><url><loc>{shop}/product-page/jug</loc></url></urlset>".encode(),
        f"{shop}/product-page/jug": _product_page("Jug", "300", img)})
    monkeypatch.setattr(site_import, "_store_image", lambda *a: (_ for _ in ()).throw(AssertionError("not called")))
    st = await site_import.import_site("potter", shop)
    assert world["products"][0]["image_url"] == img and st["images_not_kept"] == 1


# ── a site built in the browser ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_site_built_in_the_browser_says_why_so_little_was_found(world):
    spa = "https://react.example"
    world["sites"].update({f"{spa}/": b'<html><head><title>App</title><script src="/x.js"></script></head>'
                                      b'<body><div id="root"></div></body></html>'})
    st = await site_import.import_site("spa", spa)
    assert st["step"] == "done" and "builds its pages in the browser" in st["note"]


# ── re-running, and the dry run ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_page_gone_from_the_site_is_removed_on_the_next_clean_run(world):
    world["sites"].update(WP_SITE)
    await site_import.import_site("hv", ROOT, max_pages=8)
    about = site_import._doc_id("hv", f"{ROOT}/about-us/")
    del world["sites"][f"{ROOT}/about-us/"]
    st = await site_import.import_site("hv", ROOT, max_pages=8)
    assert about in _Store.deleted and st["pages_removed"] >= 1
    assert site_import._doc_id("hv", f"{ROOT}/privacy-policy/") not in _Store.deleted


@pytest.mark.asyncio
async def test_a_run_that_stops_removes_nothing(world, monkeypatch):
    world["sites"].update(WP_SITE)
    await site_import.import_site("hv", ROOT, max_pages=8)

    async def broken(*a, **k):
        raise RuntimeError("the site went away")
    monkeypatch.setattr(site_import, "fetch_products", broken)
    st = await site_import.import_site("hv", ROOT, max_pages=8)
    assert st["step"] == "stopped" and _Store.deleted == []
    assert site_import._doc_id("hv", f"{ROOT}/about-us/") in st["doc_ids"]       # still known for next time


@pytest.mark.asyncio
async def test_the_dry_run_reads_everything_and_writes_nothing(world):
    world["sites"].update(WP_SITE)
    st = await site_import.inspect_site(ROOT, max_pages=8)
    assert st["step"] == "done" and st["coverage"]["contact"]["phone"]
    assert FakePipeline.docs == {} and world["drafted"] == [] and world["facts"] == []
    assert world["pdf_runs"] == [] and world["products"] == []
    assert site_import.status("inspect") == {"running": False}


# ── the profile drafted one topic at a time ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_each_topic_is_asked_only_the_questions_it_can_answer(monkeypatch):
    from vula.commerce import profile_from_site as pfs
    calls = []

    async def fake(tid, pages, source="website", only_keys=None):
        calls.append(([u for u, _t, _x in pages], None if only_keys is None else set(only_keys)))
        if any("terms" in u for u, _t, _x in pages):
            return {"cancellations": {"answer": "24 hours' notice", "quote": "q", "url": pages[0][0]}}
        return {"hours": {"answer": "7:30 to 5", "quote": "q", "url": pages[0][0]}}

    monkeypatch.setattr(pfs, "draft_from_pages", fake)
    pages = [(f"{ROOT}/", "Home", "x"), (f"{ROOT}/terms-and-conditions/", "Terms", "x"),
             (f"{ROOT}/faq/", "FAQ", "x"), (f"{ROOT}/blog/post-1/", "Post", "x")]
    out = await pfs.draft_by_topic("hv", pages)
    by_first = {urls[0]: keys for urls, keys in calls}
    assert set(by_first) == {f"{ROOT}/", f"{ROOT}/terms-and-conditions/", f"{ROOT}/faq/"}    # no call for the blog
    assert "cancellations" in by_first[f"{ROOT}/terms-and-conditions/"] and "hours" not in by_first[f"{ROOT}/terms-and-conditions/"]
    assert by_first[f"{ROOT}/faq/"] is None                       # a FAQ can answer anything
    assert out["hours"]["url"] == f"{ROOT}/" and out["cancellations"]["url"].endswith("/terms-and-conditions/")


@pytest.mark.asyncio
async def test_pages_that_fit_no_topic_are_still_read(monkeypatch):
    from vula.commerce import profile_from_site as pfs
    seen = []

    async def fake(tid, pages, source="website", only_keys=None):
        seen.append(len(pages))
        return {}
    monkeypatch.setattr(pfs, "draft_from_pages", fake)
    await pfs.draft_by_topic("hv", [(f"{ROOT}/gallery/", "Gallery", "x"), (f"{ROOT}/blog/a/", "A", "x")])
    assert seen == [2]

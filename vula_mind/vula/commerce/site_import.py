"""
vula/commerce/site_import.py — "Import from your website": a new tenant's products and the
pages customers read (About, FAQ, delivery, returns, care, blog), straight from its own site.

2026-10-12 (Ian, onboarding SeedleMe — handmade concrete basins, sinks and baths on a Shopify
store): "do a full scrape on his website and products offering so we can feed SeedleMe's KB".
Until now a new shop's catalogue was typed in or CSV-imported, and its site's policies never
reached the knowledge base at all, so the first customer question about delivery or returns
was answered from the starter template.

One run, in the background, on the tenant's own site:
- products from the shop platform's public catalogue feed — Shopify's /products.json or
  WooCommerce's Store API — never by guessing prices off rendered HTML. Each product is upserted
  by slug (the products CSV import's own path, so re-running updates rather than duplicates),
  with its options as variants. A product with no price ("on request") is not listed for sale
  but is kept in the catalogue document so customers still hear about it.
- pages from the site's sitemap (and the shop's policy pages), each stored in the tenant's
  knowledge base as source_type "website", replaced on the next run (stable doc ids).
- one "product catalogue" document, so a knowledge question ("do you make round basins?")
  finds the range even when the shopping tools aren't the path that answers it.

12 Nov 2026 (Ian: "how strong is our website scrape … everything from products, images and
descriptions to terms and privacy"): pages are found from robots.txt and the usual sitemap names,
or by following the site's own links when it has no sitemap; terms, privacy, returns and contact
pages are read first; each page's own content is kept without the menu and footer, and the footer
is kept once; address, phone, hours and socials are read exactly from the page's schema.org data
and links (site_facts.py); the profile is drafted per topic; product photos are copied into the
business's own storage; menus and price lists linked as PDFs are read; pages gone from the site
are removed; and every run ends with a coverage report. `inspect_site` does all the reading and
none of the writing (tools/site_check.py).

Every fetch goes through reference_url.safe_fetch_bytes (scheme/DNS/IP/redirect/size checks):
the URL is typed in by a tenant. Same host only. Nothing is sent to anyone; prices are integer
cents from the platform's own figures.
"""
from __future__ import annotations

import asyncio
import hashlib
import html as _html
import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger(__name__)

MAX_PAGES = 60
# 2026-10-13 (SeedleMe's first run found no products): Shopify's feed at 100 products a page,
# each with its full description and every image, can pass the 2MB page cap — and the failure
# was silent. Smaller pages, a larger cap for this one JSON feed, and the reason kept.
SHOPIFY_PAGE_SIZE = 25
MAX_PRODUCT_PAGES = 40            # × 25 products per feed page
FEED_MAX_BYTES = 8 * 1024 * 1024
MAX_PRODUCT_URLS = 300            # product pages read one by one when there is no feed
_CONCURRENCY = 2
# 2026-10-13: four overlapping runs at 4 requests at a time made Shopify answer 429/503 to
# SeedleMe's feed and product pages. One run at a time (the shared status below), at most one
# request start per _MIN_INTERVAL per site, and a refused request waits and is tried again.
_MIN_INTERVAL = 0.4
_RETRY_WAITS = (2.0, 5.0, 12.0)
_RETRY_CODES = (429, 502, 503, 504)
_STALE_AFTER_S = 15 * 60          # a "running" row not touched for this long = a worker that died
_MIN_TEXT = 200                    # a page with less real text than this adds nothing

# Pages that never help a customer question: cart, account, search, checkout, login, tags, and
# HTML sitemaps (a page of links — SeedleMe's "html sitemap products" was refused by the embedder).
_SKIP_PATH = re.compile(r"/(cart|account|checkout|search|login|register|wp-admin|wp-login|my-account|"
                        r"tagged|feed)(/|$|\?)|sitemap", re.I)
# Shopify serves its policies here whether or not the sitemap lists them.
_SHOPIFY_POLICIES = ("/policies/refund-policy", "/policies/shipping-policy",
                     "/policies/terms-of-service", "/policies/privacy-policy")
# Most useful first when the page cap bites: the pages people ask about. Policies and contact
# first on every platform (on a non-Shopify site, terms and privacy used to rank with everything
# else and could be cut behind blog posts); blog posts last.
_PRIORITY = [re.compile(p, re.I) for p in (
    r"/policies/|terms|conditions|privacy|popia|paia|legal|refund|returns?|shipping|deliver|cancel",
    r"contact|about|faq|hours|find-?us|location|menu|pric|rates|services|warrant|guarantee|care|install",
    r"/pages/",
    r".",                               # everything else
    r"/collections?/|/categor",
    r"/blogs?/|/news/|/articles?/|/posts?/|/\d{4}/\d{2}/")]
# Where a site's sitemap may be, besides what robots.txt says.
_SITEMAP_NAMES = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap-index.xml")
CRAWL_DEPTH = 2
MAX_CRAWL = 120                   # pages fetched while following links (no sitemap)
MAX_PDFS = 5
PDF_MAX_BYTES = 15 * 1024 * 1024
IMAGES_PER_PRODUCT = 4
MAX_IMAGES = 300                  # per run
IMAGE_MAX_BYTES = 6 * 1024 * 1024
# A product page, on the platforms we know (Shopify, WooCommerce, Wix, Squarespace, custom).
_PRODUCT_PATH = re.compile(r"/(?:products?|product-page|items?)/[^/]+/?$|/(?:shop|store)/(?:p/)?[^/]+/?$", re.I)

_STATUS: Dict[str, Dict[str, Any]] = {}
_JOB = "site_import"


def _db():
    from vula.commerce import service
    return service._client()


def status(tenant_id: str, kind: str = _JOB) -> Dict[str, Any]:
    """The last or ongoing run — from vula_background_jobs (migration 206), so both uvicorn
    workers answer the same; this worker's memory when that table can't be read. `kind` lets
    other long jobs (the Shopify sync) share the table."""
    try:
        rows = (_db().table("vula_background_jobs").select("running,status,updated_at")
                .eq("tenant_id", tenant_id).eq("kind", kind).limit(1).execute().data or [])
    except Exception:
        rows = []
    if rows:
        st = dict(rows[0].get("status") or {})
        running = bool(rows[0].get("running"))
        if running and _age_s(rows[0].get("updated_at")) > _STALE_AFTER_S:
            running = False
            st.setdefault("step", "stopped")
            st.setdefault("error", "The import stopped part-way (the server restarted) — run it again.")
        st["running"] = running
        return st
    return dict(_STATUS.get(_key(tenant_id, kind)) or {"running": False})


def _key(tenant_id: str, kind: str) -> str:
    return tenant_id if kind == _JOB else f"{kind}:{tenant_id}"


def _age_s(ts: Any) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                ).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def _save(tenant_id: str, st: Dict[str, Any], kind: str = _JOB) -> None:
    """Write this run's progress where the other worker can see it. Fail-open."""
    _STATUS[_key(tenant_id, kind)] = st
    try:
        _db().table("vula_background_jobs").upsert({
            "tenant_id": tenant_id, "kind": kind, "running": bool(st.get("running")),
            "status": {k: v for k, v in st.items() if k != "running"},
            "started_at": st.get("started_at"), "updated_at": _now()},
            on_conflict="tenant_id,kind").execute()
    except Exception as exc:
        log.debug("site import status not saved for %s: %s", tenant_id, exc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise_root(url: str) -> str:
    """'seedleme.com' / 'https://seedleme.com/collections/all' → 'https://seedleme.com'."""
    u = (url or "").strip()
    if not u:
        raise ValueError("Give the website address, e.g. https://seedleme.com")
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    if not p.hostname or "." not in p.hostname:
        raise ValueError(f"'{url}' doesn't look like a website address")
    return f"{p.scheme.lower()}://{p.netloc.lower()}"


async def _fetch(url: str, max_bytes: Optional[int] = None) -> bytes:
    from vula.commerce.reference_url import safe_fetch_bytes
    return await (safe_fetch_bytes(url, max_bytes) if max_bytes else safe_fetch_bytes(url))


_last_start: Dict[str, float] = {}


async def _get(url: str, max_bytes: Optional[int] = None) -> bytes:
    """_fetch, politely: request starts to one site spaced by _MIN_INTERVAL, and a 429/5xx
    "slow down" answer waited out (its Retry-After, capped) and tried again."""
    host = urlparse(url).netloc
    loop = asyncio.get_running_loop()
    for attempt in range(len(_RETRY_WAITS) + 1):
        wait = _last_start.get(host, 0.0) + _MIN_INTERVAL - loop.time()
        _last_start[host] = loop.time() + max(wait, 0.0)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            return await (_fetch(url, max_bytes) if max_bytes else _fetch(url))
        except Exception as exc:
            resp = getattr(exc, "response", None)
            code = getattr(resp, "status_code", None)
            if code not in _RETRY_CODES or attempt == len(_RETRY_WAITS):
                raise
            try:
                after = float((resp.headers or {}).get("retry-after") or 0)
            except (TypeError, ValueError, AttributeError):
                after = 0.0
            await asyncio.sleep(min(max(after, _RETRY_WAITS[attempt]), 30.0))
    raise RuntimeError("unreachable")


def _why(exc: Exception) -> str:
    """A fetch failure in a few words for the import status ("HTTP 403", "Response too large")."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return f"HTTP {code}" if code else (str(exc) or type(exc).__name__)[:80]


def _cents(price: Any, minor_unit: Optional[int] = None) -> int:
    """Shopify's "27000.00" (rands) or WooCommerce's "2700000" with minor unit 2 → cents."""
    if price in (None, ""):
        return 0
    try:
        if minor_unit is not None:
            return int(Decimal(str(price)).quantize(Decimal(1))) * (10 ** max(0, 2 - int(minor_unit)))
        return int((Decimal(str(price)) * 100).quantize(Decimal(1)))
    except (InvalidOperation, ValueError):
        return 0


def html_to_text(fragment: str) -> str:
    """A product description's HTML → plain text with paragraph and list breaks kept."""
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment or "")
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", t)
    t = re.sub(r"(?i)<li[^>]*>", "• ", t)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", "\n".join(line.strip() for line in t.splitlines())).strip()


def _category_key(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    return key[:40] or "general"


# ── Products ──────────────────────────────────────────────────────────────────

def _from_shopify(p: Dict[str, Any], root: str) -> Dict[str, Any]:
    options = [o.get("name") for o in (p.get("options") or []) if o.get("name")]
    variants = []
    for v in p.get("variants") or []:
        values = {}
        for i, name in enumerate(options[:3]):
            val = v.get(f"option{i + 1}")
            if val and val != "Default Title":
                values[name] = val
        variants.append({"option_values": values, "sku": (v.get("sku") or "").strip() or None,
                         "price_cents": _cents(v.get("price")),
                         "in_stock": bool(v.get("available", True)),
                         "title": v.get("title") if v.get("title") != "Default Title" else None})
    priced = [v["price_cents"] for v in variants if v["price_cents"] > 0]
    images = p.get("images") or []
    return {
        "name": (p.get("title") or "").strip(), "slug": (p.get("handle") or "").strip(),
        "price_cents": min(priced) if priced else 0,
        "category_label": (p.get("product_type") or "").strip() or None,
        "description": html_to_text(p.get("body_html") or ""),
        "image_url": (images[0].get("src") if images and isinstance(images[0], dict) else None),
        "images": [i.get("src") for i in images if isinstance(i, dict) and i.get("src")],
        "in_stock": any(v["in_stock"] for v in variants) if variants else True,
        "variants": [v for v in variants if v["option_values"]] if len(variants) > 1 else [],
        "options": [n for n in options[:3] if any(n in v["option_values"] for v in variants)]
        if len(variants) > 1 else [],
        "url": f"{root}/products/{p.get('handle')}" if p.get("handle") else root,
        "tags": p.get("tags") if isinstance(p.get("tags"), list) else
                [t.strip() for t in str(p.get("tags") or "").split(",") if t.strip()],
    }


def _from_woo(p: Dict[str, Any], root: str) -> Dict[str, Any]:
    prices = p.get("prices") or {}
    minor = prices.get("currency_minor_unit", 2)
    cats = p.get("categories") or []
    images = p.get("images") or []
    return {
        "name": _html.unescape(p.get("name") or "").strip(), "slug": (p.get("slug") or "").strip(),
        "price_cents": _cents(prices.get("price"), minor),
        "category_label": (cats[0].get("name") if cats else None),
        "description": html_to_text(p.get("description") or p.get("short_description") or ""),
        "image_url": (images[0].get("src") if images else None),
        "images": [i.get("src") for i in images if isinstance(i, dict) and i.get("src")],
        "in_stock": bool(p.get("is_in_stock", True)), "variants": [],
        "url": p.get("permalink") or root, "tags": [t.get("name") for t in p.get("tags") or [] if t.get("name")],
    }


def _from_jsonld(page: str, url: str) -> Optional[Dict[str, Any]]:
    """A product page's schema.org Product (JSON-LD) → a product, priced from its own offers.
    The fallback when a shop's catalogue feed can't be read: never a price scraped off text."""
    for block in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page or ""):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else (data.get("@graph") or [data])
        for d in items:
            if not isinstance(d, dict) or "Product" not in str(d.get("@type")):
                continue
            offers = d.get("offers") or []
            offers = offers if isinstance(offers, list) else [offers]
            if offers and isinstance(offers[0], dict) and offers[0].get("offers"):   # AggregateOffer
                offers = offers[0]["offers"] if isinstance(offers[0]["offers"], list) else [offers[0]["offers"]]
            prices = [_cents(o.get("price") or o.get("lowPrice")) for o in offers if isinstance(o, dict)]
            prices = [c for c in prices if c > 0]
            raw = d.get("image")
            gallery = [(i.get("url") or i.get("contentUrl")) if isinstance(i, dict) else i    # Wix: contentUrl
                       for i in (raw if isinstance(raw, list) else [raw] if raw else [])]
            gallery = [_full_size(("https:" + i if i.startswith("//") else i))
                       for i in gallery if isinstance(i, str) and i]
            image = gallery[0] if gallery else None
            name = _html.unescape(str(d.get("name") or "")).strip()
            if not name:
                continue
            slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
            return {
                "name": name, "slug": slug, "price_cents": min(prices) if prices else 0,
                "category_label": (d.get("category") if isinstance(d.get("category"), str) else None),
                "description": html_to_text(_html.unescape(str(d.get("description") or ""))),
                "image_url": image, "images": gallery,
                "in_stock": not any("OutOfStock" in str(o.get("availability")) for o in offers
                                    if isinstance(o, dict)) if offers else True,
                "variants": [], "url": url, "tags": [],
            }
    return None


# What a site builder puts in a product's description until the owner writes one. Kept out of
# the catalogue: an empty description is better than an instruction to the shop owner.
_PLACEHOLDER = re.compile(
    r"(?i)^\s*(add your (own )?product description|write (your|a) (product )?description|"
    r"this is (a|your) product description|lorem ipsum|product description goes here)")


def _real_description(text: str) -> str:
    return "" if _PLACEHOLDER.search(text or "") else (text or "")


class _Microdata:
    """The schema.org items marked up in a page with itemscope/itemprop (microdata), as nested
    dicts: {"type": ".../Product", "props": {"name": ["…"], "offers": [{"type": …, "props": …}]}}.
    Values come from content/href/src, else the element's own text."""
    _VOID = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "col", "wbr"}

    def __init__(self, page: str):
        from html.parser import HTMLParser
        items: List[Dict[str, Any]] = []
        stack: List[Tuple[int, Optional[str], Dict[str, Any]]] = []   # (depth, itemprop, item)
        texts: List[Tuple[int, str, List[str]]] = []                   # (depth, itemprop, chunks)
        depth = [0]

        def add(prop: str, value: Any) -> None:
            if stack and prop:
                for name in prop.split():
                    stack[-1][2]["props"].setdefault(name, []).append(value)

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                a = {k: (v or "") for k, v in attrs}
                prop = a.get("itemprop")
                void = tag in _Microdata._VOID
                if "itemscope" in a:
                    stack.append((depth[0], prop, {"type": a.get("itemtype", ""), "props": {}}))
                    if void:          # an empty scope on a void tag closes at once
                        self._close()
                elif prop:
                    val = a.get("content") or a.get("href") or a.get("src")
                    if val or void:
                        add(prop, val)
                    else:
                        texts.append((depth[0], prop, []))
                if not void:
                    depth[0] += 1

            def _close(self):
                d, prop, item = stack.pop()
                if prop and stack:
                    add(prop, item)
                elif not prop:
                    items.append(item)

            def handle_endtag(self, tag):
                if tag in _Microdata._VOID:
                    return
                depth[0] -= 1
                while texts and texts[-1][0] >= depth[0]:
                    _d, prop, chunks = texts.pop()
                    add(prop, " ".join("".join(chunks).split()))
                while stack and stack[-1][0] >= depth[0]:
                    self._close()

            def handle_data(self, data):
                if texts:
                    texts[-1][2].append(data)

        try:
            P(convert_charrefs=True).feed(page or "")
        except Exception:  # noqa: BLE001 — a page we can't parse simply has no microdata
            pass
        while stack:
            d, prop, item = stack.pop()
            (stack[-1][2]["props"].setdefault(prop, []).append(item) if prop and stack else items.append(item))
        self.items = items


def _from_microdata(page: str, url: str) -> Optional[Dict[str, Any]]:
    """A product page's schema.org Product in microdata (itemprop="price" content="1299") → a
    product, priced from its own Offer. Site builders that write no JSON-LD (Kelp Board Bags' does
    this) still mark the product up this way. Never a price read off the visible text."""
    def first(props: Dict[str, Any], name: str) -> Any:
        v = props.get(name) or []
        return v[0] if v else None

    for item in _Microdata(page).items:
        if "Product" not in str(item.get("type")):
            continue
        props = item["props"]
        name = _html.unescape(str(first(props, "name") or "")).strip()
        if not name or isinstance(first(props, "name"), dict):
            continue
        offers = [o for o in props.get("offers") or [] if isinstance(o, dict)]
        inner = [x for o in offers for x in (o["props"].get("offers") or []) if isinstance(x, dict)]
        offers = inner or offers                                      # AggregateOffer → its Offers
        prices = [_cents(first(o["props"], "price") or first(o["props"], "lowPrice")) for o in offers]
        prices = [c for c in prices if c > 0]
        gallery = [("https:" + i if i.startswith("//") else i)
                   for i in props.get("image") or [] if isinstance(i, str) and i]
        category = next((c for c in props.get("category") or [] if isinstance(c, str) and c.strip()), None)
        slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        return {
            "name": name, "slug": slug, "price_cents": min(prices) if prices else 0,
            "category_label": category,
            "description": _real_description(html_to_text(_html.unescape(str(first(props, "description") or "")))),
            "image_url": gallery[0] if gallery else None, "images": gallery,
            "in_stock": not any("OutOfStock" in str(first(o["props"], "availability")) for o in offers)
            if offers else True,
            "variants": [], "url": url, "tags": [],
        }
    return None


def _full_size(src: str) -> str:
    """Wix serves every photo through a resize path (…~mv2.jpg/v1/fit/w_500,h_500,q_90/file.jpg);
    the original is the same address without it."""
    return re.sub(r"^(https://static\.wixstatic\.com/media/[^/]+)/v1/.*$", r"\1", src or "")


def _wix_rich_text(raw: str) -> str:
    """Wix's rich-content description ({"nodes": [{"type": "PARAGRAPH", …}]}) → plain text with
    paragraph and list breaks; anything else is treated as HTML."""
    try:
        doc = json.loads(raw) if (raw or "").lstrip().startswith("{") else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get("nodes"), list):
        return html_to_text(raw or "")
    lines: List[str] = []

    def text_of(n: Dict[str, Any]) -> str:
        own = ((n.get("textData") or {}).get("text") or "") if isinstance(n, dict) else ""
        return own + "".join(text_of(c) for c in n.get("nodes") or [] if isinstance(c, dict))

    def walk(nodes: List[Any], bullet: bool = False) -> None:
        for n in nodes:
            if not isinstance(n, dict):
                continue
            kind = str(n.get("type") or "").upper()
            if kind in ("BULLETED_LIST", "ORDERED_LIST", "LIST_ITEM"):
                walk(n.get("nodes") or [], bullet=True)
            elif kind in ("PARAGRAPH", "HEADING", "BLOCKQUOTE", "CODE_BLOCK"):
                t = " ".join(text_of(n).split())
                if t:
                    lines.append(("• " if bullet else "") + t)
            else:
                walk(n.get("nodes") or [], bullet)
    walk(doc["nodes"])
    return "\n".join(lines)


def _wix_product(page: str, slug: str) -> Optional[Dict[str, Any]]:
    """The product object in a Wix product page's own data (<script id="wix-warmup-data">)."""
    m = re.search(r'(?is)<script[^>]*id="wix-warmup-data"[^>]*>(.*?)</script>', page or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    found: List[Dict[str, Any]] = []

    def walk(x: Any, depth: int = 0) -> None:
        if depth > 12:
            return
        if isinstance(x, dict):
            if isinstance(x.get("productItems"), list) and isinstance(x.get("options"), list) and x.get("name"):
                found.append(x)
                return
            for v in x.values():
                walk(v, depth + 1)
        elif isinstance(x, list):
            for v in x:
                walk(v, depth + 1)
    walk(data)
    return next((p for p in found if str(p.get("urlPart") or "").lower() == slug.lower()), None) or \
        (found[0] if len(found) == 1 else None)


def _from_wix(page: str, url: str) -> Optional[Dict[str, Any]]:
    """A Wix Stores product → a product with every priced combination as a variant.

    13 Nov 2026 (Kelp Gear, kelpgearshop.com): the page's JSON-LD gives one price, the lowest
    ("Kelp Classic Day Bag" R1 200) though the 12' bag is R2 400, and its photos as contentUrl,
    which wasn't read. Wix's own data on the same page has each option, each priced combination
    (productItems: the selections, price, stock) and the photos. Options that don't change the
    price (colours, nose and tail styles) are listed in the description, by name."""
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    w = _wix_product(page, slug)
    if not w:
        return None
    opts = [o for o in w.get("options") or [] if isinstance(o, dict) and o.get("title")]

    def label(o: Dict[str, Any], s: Dict[str, Any]) -> str:
        # a COLOR option's value is a hex code; its name is the description
        v = s.get("description") if str(o.get("optionType")).upper() == "COLOR" else s.get("value")
        return str(v or s.get("value") or s.get("description") or "").strip()

    by_id = {s.get("id"): (o["title"], label(o, s)) for o in opts
             for s in o.get("selections") or [] if isinstance(s, dict)}
    priced_titles: List[str] = []
    variants = []
    for it in w.get("productItems") or []:
        if not isinstance(it, dict) or it.get("isVisible") is False:
            continue
        values = {}
        for sid in it.get("optionsSelections") or []:
            if sid in by_id:
                title, val = by_id[sid]
                values[title] = val
                if title not in priced_titles:
                    priced_titles.append(title)
        status = str((it.get("inventory") or {}).get("status") or "in_stock").lower()
        variants.append({"option_values": values, "sku": (str(it.get("sku") or "")).strip() or None,
                         "price_cents": _cents(it.get("price")), "in_stock": status != "out_of_stock",
                         "title": " / ".join(values.values()) or None})
    variants = [v for v in variants if v["option_values"]]
    prices = [v["price_cents"] for v in variants if v["price_cents"] > 0] or \
        [c for c in [_cents(w.get("discountedPrice") or w.get("price"))] if c > 0]
    choices = [f"{o['title']}: " + ", ".join(label(o, s) for s in o.get("selections") or [] if isinstance(s, dict))
               for o in opts if o["title"] not in priced_titles]
    ld = _from_jsonld(page, url) or {}
    description = _real_description(ld.get("description") or _wix_rich_text(str(w.get("description") or "")))
    if choices:
        description = (description + "\n" if description else "") + "Choices: " + "; ".join(choices)
    gallery = []
    for m in w.get("media") or []:
        if isinstance(m, dict) and str(m.get("mediaType") or "PHOTO").upper() == "PHOTO":
            src = m.get("fullUrl") or (f"https://static.wixstatic.com/media/{m['url']}" if m.get("url") else "")
            if src and _full_size(src) not in gallery:
                gallery.append(_full_size(src))
    crumbs = [c.get("name") for c in w.get("breadcrumbs") or [] if isinstance(c, dict) and c.get("name")]
    return {
        "name": _html.unescape(str(w.get("name") or "")).strip(), "slug": str(w.get("urlPart") or slug),
        "price_cents": min(prices) if prices else 0,
        "category_label": crumbs[0] if crumbs else None,
        "description": description, "image_url": gallery[0] if gallery else None, "images": gallery,
        "in_stock": (any(v["in_stock"] for v in variants) if variants else bool(w.get("isInStock", True))),
        "variants": variants if len(variants) > 1 else [],
        "options": priced_titles if len(variants) > 1 else [],
        "choices": [{"name": o["title"], "values": [label(o, s) for s in o.get("selections") or []
                                                    if isinstance(s, dict) and label(o, s)]}
                    for o in opts if o["title"] not in priced_titles],
        "url": url, "tags": [],
    }


def product_from_page(page: str, url: str) -> Optional[Dict[str, Any]]:
    """A product page's own product: Wix's own product data first (every priced option), then
    schema.org JSON-LD, then microdata."""
    p = _from_wix(page, url) or _from_jsonld(page, url) or _from_microdata(page, url)
    if p and not p.get("name"):
        return None
    if p:
        p["description"] = _real_description(p.get("description") or "")
    return p


async def _sitemap_starts(root: str) -> List[str]:
    """The sitemaps to read: what robots.txt names, then the usual addresses."""
    out: List[str] = []
    try:
        robots = (await _get(f"{root}/robots.txt")).decode("utf-8", errors="replace")
        out += [m.strip() for m in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)]
    except Exception:
        pass
    host = urlparse(root).netloc.lower()
    out = [u for u in out if urlparse(u).netloc.lower() == host]
    return list(dict.fromkeys(out + [root + n for n in _SITEMAP_NAMES]))


async def _sitemap_locs(root: str) -> List[str]:
    """Every same-host URL in the site's sitemap (one level of child sitemaps), in sitemap order.
    Reads robots.txt's Sitemap: lines and the usual names (/sitemap.xml, /sitemap_index.xml,
    /wp-sitemap.xml) — it used to read /sitemap.xml only, and a site without one gave its home
    page and nothing else."""
    host = urlparse(root).netloc
    found: List[str] = []
    seen = set()
    for start in await _sitemap_starts(root):
        more = await _read_sitemap(start, host, seen)
        found += more
        if found:
            break
    return found


async def _read_sitemap(start: str, host: str, seen: set) -> List[str]:
    found: List[str] = []
    queue = [start]
    for depth in range(2):
        nxt = []
        for sm in queue:
            try:
                xml = (await _get(sm)).decode("utf-8", errors="replace")
            except Exception:
                continue
            for loc in _LOC.findall(xml):
                loc = _html.unescape(loc)
                if urlparse(loc).netloc.lower() != host:
                    continue
                if loc.lower().split("?")[0].endswith(".xml"):
                    if depth == 0:
                        nxt.append(loc)
                elif loc not in seen:
                    seen.add(loc)
                    found.append(loc)
        queue = nxt
    return found


async def _page(url: str, cache: Optional[Dict[str, bytes]]) -> bytes:
    """A page's bytes — from this run's cache when it was already fetched (while crawling)."""
    if cache is not None and url in cache:
        return cache[url]
    raw = await _get(url)
    if cache is not None:
        cache[url] = raw
    return raw


async def crawl(root: str, cache: Dict[str, bytes], limit: int = MAX_CRAWL) -> List[str]:
    """A site with no sitemap: its pages, by following its own links from the home page,
    CRAWL_DEPTH levels deep, footer links first. Every page fetched is kept in `cache` so it is
    not fetched again."""
    from vula.commerce import site_facts
    start = root + "/"
    found: List[str] = [start]
    level = [start]
    for _ in range(CRAWL_DEPTH):
        nxt: List[str] = []
        for u in level:
            if len(cache) >= limit:
                break
            try:
                raw = await _page(u, cache)
            except Exception:
                continue
            for link in site_facts.links(raw.decode("utf-8", errors="replace"), u, root):
                if link in found or _SKIP_PATH.search(urlparse(link).path):
                    continue
                found.append(link)
                nxt.append(link)
        level = nxt
    return found


async def discover(root: str, cache: Dict[str, bytes]) -> Tuple[List[str], str]:
    """(every page URL, how they were found: "sitemap" or "links")."""
    locs = await _sitemap_locs(root)
    if locs:
        return locs, "sitemap"
    return await crawl(root, cache), "links"


def _slug_of(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


async def _products_from_pages(root: str, notes: List[str], skip_slugs: frozenset = frozenset(),
                               on_progress: Optional[Callable[[int, int], None]] = None,
                               pages: Optional[List[str]] = None,
                               cache: Optional[Dict[str, bytes]] = None
                               ) -> Tuple[str, List[Dict[str, Any]]]:
    """No readable feed (or one that stopped part-way): each product page not already in
    `skip_slugs` — Shopify's own per-product JSON first ("/products/<handle>.json", the feed's
    shape), else the page's schema.org Product (JSON-LD, or microdata). Product pages are recognised on Shopify,
    WooCommerce, Wix (/product-page/), Squarespace (/shop/p/) and custom stores."""
    pages = pages if pages is not None else await _sitemap_locs(root)
    urls = [u for u in pages if _PRODUCT_PATH.search(urlparse(u).path)
            and _slug_of(u) not in skip_slugs][:MAX_PRODUCT_URLS]
    if not urls:
        return "", []
    done = [0]
    sem = asyncio.Semaphore(_CONCURRENCY)
    got: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    failed: List[str] = []

    async def one(u: str) -> None:
        async with sem:
            try:
                await _one_product(u)
            finally:
                done[0] += 1
                if on_progress:
                    on_progress(done[0], len(urls))

    async def _one_product(u: str) -> None:
        base = u.split("?")[0].rstrip("/")
        if re.search(r"/products/[^/]+$", urlparse(base).path):      # Shopify's own product JSON
            try:
                data = json.loads(await _get(base + ".json"))
                if isinstance(data, dict) and isinstance(data.get("product"), dict):
                    got[u] = ("shopify", _from_shopify(data["product"], root))
                    return
            except Exception:
                pass
        try:
            p = product_from_page((await _page(u, cache)).decode("utf-8", errors="replace"), u)
        except Exception as exc:
            failed.append(f"{u}: {_why(exc)}")
            return
        if p:
            got[u] = ("page", p)

    await asyncio.gather(*(one(u) for u in urls))
    if failed:
        notes.append(f"{len(failed)} product page(s) couldn't be read, e.g. {failed[0]}")
    products = [got[u][1] for u in urls if u in got]
    kinds = {got[u][0] for u in got}
    return ("shopify" if "shopify" in kinds else "website" if products else ""), products


async def fetch_products(root: str, notes: Optional[List[str]] = None,
                         on_progress: Optional[Callable[[int, int], None]] = None,
                         pages: Optional[List[str]] = None,
                         cache: Optional[Dict[str, bytes]] = None
                         ) -> Tuple[str, List[Dict[str, Any]]]:
    """(platform, products) from the shop's public catalogue feed — or, when the feed can't be
    read, from its product pages; ("", []) when it has none. Why a feed failed goes in `notes`.
    A feed that stops part-way keeps what it gave and the rest is read page by page."""
    notes = notes if notes is not None else []
    out: List[Dict[str, Any]] = []
    for page in range(1, MAX_PRODUCT_PAGES + 1):
        try:
            data = json.loads(await _get(
                f"{root}/products.json?limit={SHOPIFY_PAGE_SIZE}&page={page}", max_bytes=FEED_MAX_BYTES))
        except Exception as exc:
            notes.append(f"Shopify product feed{f' page {page}' if page > 1 else ''}: {_why(exc)}")
            break
        items = data.get("products") if isinstance(data, dict) else None
        if not items:
            break
        out += [_from_shopify(p, root) for p in items]
        if len(items) < SHOPIFY_PAGE_SIZE:
            break
    if out:
        if notes:          # stopped part-way: the products it didn't reach, from their own pages
            _, more = await _products_from_pages(
                root, notes, frozenset(p["slug"] for p in out), on_progress, pages, cache)
            out += more
        return "shopify", out
    for page in range(1, MAX_PRODUCT_PAGES + 1):
        try:
            items = json.loads(await _get(f"{root}/wp-json/wc/store/v1/products?per_page=100&page={page}",
                                            max_bytes=FEED_MAX_BYTES))
        except Exception:
            break
        if not isinstance(items, list) or not items:
            break
        out += [_from_woo(p, root) for p in items]
        if len(items) < 100:
            break
    if out:
        return "woocommerce", out
    return await _products_from_pages(root, notes, on_progress=on_progress, pages=pages, cache=cache)


async def _save_products(tenant_id: str, products: List[Dict[str, Any]], st: Dict[str, Any]) -> None:
    from vula.api.commerce import admin_import_products
    from vula.commerce import service
    rows = [{"name": p["name"], "slug": p["slug"] or None, "price_cents": p["price_cents"],
             "category": _category_key(p["category_label"] or "general"),
             "description": p["description"][:4000], "image_url": p["image_url"] or "",
             "images": p.get("images") or [],
             "in_stock": "yes" if p["in_stock"] else "no", "status": p.get("status") or "active",
             # which options change the price, and the choices on the same price (267) —
             # only when there are any, so a shop without them writes nothing new
             **({"options": p["options"]} if p.get("options") and p.get("variants") else {}),
             **({"choices": p["choices"]} if p.get("choices") else {})}
            for p in products if p["name"] and p["price_cents"] > 0]
    st["products_on_request"] = sum(1 for p in products if p["name"] and p["price_cents"] <= 0)
    if not rows:
        return
    res = await admin_import_products(tenant_id, {"rows": rows})
    results = res.get("results") if isinstance(res, dict) else None
    for r in results or []:
        if r.get("status") == "created":
            st["products_created"] += 1
        elif r.get("status") == "updated":
            st["products_updated"] += 1
        else:
            st["errors"].append(f"{r.get('slug')}: {r.get('error') or r.get('status')}")
    by_slug = {p["slug"]: p for p in await service.list_products(tenant_id, in_stock_only=False,
                                                                 include_archived=True)}
    for p in products:
        prod = by_slug.get(p["slug"])
        if not prod or not p["variants"]:
            continue
        existing = await service.list_variants(tenant_id, prod["id"])
        for i, v in enumerate(p["variants"]):
            data = {"option_values": v["option_values"], "sku": v["sku"],
                    "price_cents": v["price_cents"] or None, "in_stock": v["in_stock"], "sort_order": i}
            match = next((e for e in existing if (v["sku"] and e.get("sku") == v["sku"])
                          or e.get("option_values") == v["option_values"]), None)
            try:
                if match:
                    await service.update_variant(tenant_id, match["id"], data)
                else:
                    await service.create_variant(tenant_id, prod["id"], data)
                st["variants"] += 1
            except Exception as exc:
                st["errors"].append(f"{p['slug']} option {v['option_values']}: {exc}")


def catalogue_text(business: str, root: str, products: List[Dict[str, Any]]) -> str:
    """The whole range as one readable document for the knowledge base."""
    lines = [f"# {business} — products (from {root})", ""]
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for p in products:
        by_cat.setdefault(p["category_label"] or "Products", []).append(p)
    for cat, items in sorted(by_cat.items()):
        lines.append(f"## {cat}")
        for p in sorted(items, key=lambda x: x["name"]):
            price = (f"from R{p['price_cents'] / 100:,.2f}" if len({v['price_cents'] for v in p['variants']} - {0}) > 1
                     else f"R{p['price_cents'] / 100:,.2f}") if p["price_cents"] > 0 else "price on request"
            lines.append(f"### {p['name']} — {price}" + ("" if p["in_stock"] else " (currently unavailable)"))
            if p["variants"]:
                opts = "; ".join(" / ".join(v["option_values"].values())
                                 + (f" R{v['price_cents'] / 100:,.2f}" if v["price_cents"] else "")
                                 for v in p["variants"])
                lines.append(f"Options: {opts}")
            if p["description"]:
                lines.append(p["description"][:1500])
            lines.append(f"Link: {p['url']}")
            lines.append("")
    return "\n".join(lines).strip()


# ── Pages ─────────────────────────────────────────────────────────────────────

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


async def sitemap_urls(root: str, platform: str = "", pages: Optional[List[str]] = None) -> List[str]:
    """Every page URL on this host (from the sitemap, or `pages` already found), plus the shop's
    policy pages, most useful first. Product pages are left out when the catalogue feed already
    has them."""
    found = list(pages) if pages is not None else await _sitemap_locs(root)
    seen = set(found)
    if platform == "shopify":
        found += [root + p for p in _SHOPIFY_POLICIES if root + p not in seen]
    if not found:
        found = [root + "/"]
    if platform:
        found = [u for u in found if not re.search(r"/products?/", urlparse(u).path)]
    found = [u for u in found if not _SKIP_PATH.search(urlparse(u).path)]

    def rank(u: str) -> int:
        path = urlparse(u).path or "/"
        if path in ("", "/"):
            return -1
        hits = [i for i, rx in enumerate(_PRIORITY) if rx.search(path)]
        # a blog post or category that merely mentions "delivery" is still a blog post
        if any(i >= 4 for i in hits):
            return max(hits)
        return min(hits) if hits else 3
    return sorted(dict.fromkeys(found), key=rank)


def page_text(raw: bytes) -> Tuple[str, str]:
    from vula.commerce.reference_url import _extract_title_and_text
    title, text = _extract_title_and_text(raw.decode("utf-8", errors="replace"))
    text = re.sub(r"\n\s*\n+", "\n\n", text or "").strip()
    return _html.unescape(title or "").strip(), text


def _doc_id(tenant_id: str, key: str) -> str:
    return "site_" + hashlib.md5(f"{tenant_id}:{key}".encode()).hexdigest()[:16]


# ── Product photos, kept ──────────────────────────────────────────────────────

_IMG_TYPES = {b"\xff\xd8\xff": ("image/jpeg", ".jpg"), b"\x89PNG": ("image/png", ".png"),
              b"RIFF": ("image/webp", ".webp"), b"GIF8": ("image/gif", ".gif")}


def _image_kind(data: bytes) -> Optional[Tuple[str, str]]:
    return next((v for k, v in _IMG_TYPES.items() if data.startswith(k)), None)


def _store_image(tenant_id: str, slug: str, i: int, data: bytes, ctype: str, ext: str) -> Optional[str]:
    """Into the business's own product-images bucket (the one uploaded product photos use)."""
    from vula.api.whatsapp import _upload_to_storage
    safe = re.sub(r"[^a-z0-9-]+", "-", (slug or "product").lower()).strip("-")[:60] or "product"
    return _upload_to_storage("product-images", f"{tenant_id}/site/{safe}-{i}{ext}", data, ctype)


async def keep_images(tenant_id: str, products: List[Dict[str, Any]], st: Dict[str, Any]) -> None:
    """Copy each product's photos into the business's own storage. Until now a product showed the
    shop's own CDN address, so its pictures disappeared when that site changed or closed. A photo
    that can't be fetched or stored keeps its original address; the count says how many."""
    from config import settings
    if not getattr(settings, "site_import_copy_images", True):
        return
    budget = MAX_IMAGES
    for p in products:
        if p.get("price_cents", 0) <= 0 or not p.get("images"):
            continue
        kept: List[str] = []
        for i, url in enumerate(p["images"][:IMAGES_PER_PRODUCT]):
            if budget <= 0:
                kept.append(url)
                continue
            budget -= 1
            try:
                data = await _get(url, max_bytes=IMAGE_MAX_BYTES)
                kind = _image_kind(data)
                stored = await asyncio.to_thread(_store_image, tenant_id, p["slug"], i, data, *kind) if kind else None
            except Exception:
                stored = None
            if stored:
                st["images_kept"] = st.get("images_kept", 0) + 1
            else:
                st["images_not_kept"] = st.get("images_not_kept", 0) + 1
            kept.append(stored or url)
        p["images"] = kept + p["images"][IMAGES_PER_PRODUCT:]
        p["image_url"] = p["images"][0] if p["images"] else p.get("image_url")


# ── The run ───────────────────────────────────────────────────────────────────

def _coverage(products: List[Dict[str, Any]], read: List[Tuple[str, str, str]], facts: Dict[str, Any],
              st: Dict[str, Any], how: str, pdfs: List[Tuple[str, str]]) -> Dict[str, Any]:
    """What this run found, by kind — for the owner ("Found your terms and privacy policy") and
    the operator. Counts and addresses only."""
    from vula.commerce import site_facts
    priced = [p for p in products if p.get("price_cents", 0) > 0]
    policies: Dict[str, Optional[str]] = {k: None for k in site_facts.POLICY_KINDS}
    for url, title, _text in read:
        kind = site_facts.policy_kind(url, title)
        if kind and not policies[kind]:
            policies[kind] = url
    return {
        "products": {"found": len(products), "with_price": len(priced),
                     "with_image": sum(1 for p in products if p.get("image_url")),
                     "with_description": sum(1 for p in products if (p.get("description") or "").strip()),
                     "images_kept": st.get("images_kept", 0)},
        "policies": policies,
        "contact": {k: bool(facts.get(k)) for k in ("phone", "email", "address", "hours", "socials")},
        "pages": {"found": st.get("pages_found", 0), "read": st.get("pages_read", 0),
                  "used": len(read), "skipped": st.get("skipped_pages", 0), "how": how},
        "pdfs": [label for _u, label in pdfs],
    }


async def _read_pdfs(tenant_id: str, pdfs: List[Tuple[str, str]], st: Dict[str, Any]) -> None:
    """Menus, price lists, brochures and terms linked as PDFs: downloaded and handed to the
    document set-up (profile_from_docs) — filed in the knowledge base and read for the profile."""
    from vula.api import server as srv
    from vula.commerce import profile_from_docs
    from vula.commerce.background_tasks import run_background
    from vula.uploads import safe_upload_path
    folder = srv.UPLOAD_DIR / tenant_id
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for url, label in pdfs:
        try:
            data = await _get(url, max_bytes=PDF_MAX_BYTES)
        except Exception as exc:
            st["errors"].append(f"{url}: {_why(exc)}")
            continue
        if not data.startswith(b"%PDF"):
            continue
        name = urlparse(url).path.rsplit("/", 1)[-1] or "document.pdf"
        path = safe_upload_path(folder, name)
        path.write_bytes(data)
        files.append({"name": name, "path": str(path), "mime": "application/pdf"})
    st["pdfs_read"] = [f["name"] for f in files]
    if files and not profile_from_docs.already_running(tenant_id):
        run_background(tenant_id, "site_import_pdfs",
                       profile_from_docs.run(tenant_id, files, file_it=profile_from_docs.file_into_kb))


async def _remove_stale(tenant_id: str, pipeline: Any, before: List[str], now: List[str]) -> int:
    """Pages the last run stored that this run didn't find: gone from the site, so gone from the
    knowledge base. Only called after a run that finished cleanly."""
    gone = [d for d in before or [] if d not in set(now)]
    store = getattr(pipeline, "store", None)
    if not gone or store is None:
        return 0
    n = 0
    for doc_id in gone:
        try:
            await store.delete_document(tenant_id, doc_id)
            n += 1
        except Exception as exc:
            log.debug("site import: stale page %s not removed: %s", doc_id, exc)
    return n


async def import_site(tenant_id: str, url: str, business: str = "", max_pages: int = MAX_PAGES,
                      dry_run: bool = False) -> Dict[str, Any]:
    """Read the site and, unless `dry_run`, save what it gives: products, pages, the footer, the
    business-profile drafts, linked PDFs. `dry_run` reads everything and writes nothing (no
    status, products, knowledge base, drafts or photos) — see inspect_site."""
    from vula.commerce import site_facts
    root = normalise_root(url)
    before = [] if dry_run else list(status(tenant_id).get("doc_ids") or [])
    st: Dict[str, Any] = {
        "running": True, "url": root, "step": "reading the product catalogue", "started_at": _now(),
        "platform": "", "products_found": 0, "products_created": 0, "products_updated": 0,
        "products_on_request": 0, "variants": 0, "pages_found": 0, "pages_read": 0,
        "kb_documents": 0, "skipped_pages": 0, "errors": [], "doc_ids": []}

    def save() -> None:
        if not dry_run:
            _save(tenant_id, st)
    save()

    def product_progress(done: int, total: int) -> None:
        st["step"] = f"reading product pages ({done} of {total})"
        if done % 20 == 0 or done == total:
            save()

    clean = False
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = None if dry_run else VulaIngestionPipeline(tenant_id=tenant_id)

        async def store_text(content: str, filename: str, key: str) -> bool:
            if dry_run:
                return True
            doc_id = _doc_id(tenant_id, key)
            res = await pipeline.ingest_text(content=content, filename=filename, doc_id=doc_id,
                                             source_type="website")
            if res.status == "success":
                st["kb_documents"] += 1
                st["doc_ids"].append(doc_id)
                return True
            return False

        cache: Dict[str, bytes] = {}
        st["step"] = "finding the website's pages"
        found, how = await discover(root, cache)
        st["found_by"] = how
        notes: List[str] = []
        st["step"] = "reading the product catalogue"
        save()
        platform, products = await fetch_products(root, notes, product_progress, pages=found, cache=cache)
        st["platform"], st["products_found"] = platform, len(products)
        if notes:
            st["catalogue_note"] = "; ".join(notes)[:300]
            log.info("site import %s (%s): %s", tenant_id, root, st["catalogue_note"])
        if products and not dry_run:
            st["step"] = f"keeping the photos of {len(products)} products"
            save()
            await keep_images(tenant_id, products, st)
            st["step"] = f"saving {len(products)} products"
            save()
            await _save_products(tenant_id, products, st)
        if products:
            await store_text(catalogue_text(business or urlparse(root).netloc, root, products),
                             f"Website — product catalogue ({urlparse(root).netloc})", "catalogue")

        urls = (await sitemap_urls(root, platform, pages=found))[:max_pages]
        st["pages_found"] = len(urls)
        st["step"] = f"reading {len(urls)} pages"
        save()
        sem = asyncio.Semaphore(_CONCURRENCY)
        read: List[Tuple[str, str, str]] = []      # (url, title, text) for the profile drafts
        raws: Dict[str, str] = {}                  # url → html, for the exact facts and the PDFs

        async def one(u: str) -> None:
            async with sem:
                try:
                    raw = await _page(u, cache)
                except Exception as exc:
                    st["errors"].append(f"{u}: {_why(exc)}")
                    return
                html = raw.decode("utf-8", errors="replace")
                title, _ = page_text(raw)
                text = site_facts.main_text(html)
                raws[u] = html
                st["pages_read"] += 1
                if len(text) < _MIN_TEXT:
                    st["skipped_pages"] += 1
                    return
                read.append((u, title, text))
                if not await store_text(f"Source: {u}\n\n# {title or u}\n\n{text}",
                                        f"Website — {title or urlparse(u).path}"[:180], u):
                    st["errors"].append(f"{u}: not stored")

        await asyncio.gather(*(one(u) for u in urls))
        order = {u: i for i, u in enumerate(urls)}
        read.sort(key=lambda r: order.get(r[0], 0))

        home = raws.get(root + "/") or next(iter(raws.values()), "")
        home_text = site_facts.main_text(home)
        if site_facts.builds_in_browser(home, home_text):
            st["note"] = (f"This website builds its pages in the browser, so Vula could only read "
                          f"{len(read)} page{'s' if len(read) != 1 else ''} of it.")
        # The footer, once: where the address, phone and hours usually are.
        footer = site_facts.footer_text(home)
        if len(footer) >= 40:
            await store_text(f"Source: {root}/\n\n# Contact details (website footer)\n\n{footer}",
                             f"Website — contact details ({urlparse(root).netloc})", "footer")

        facts = site_facts.business_facts([(u, raws[u]) for u in urls if u in raws])
        pdfs: List[Tuple[str, str]] = []
        for u in urls:
            for link in site_facts.pdf_links(raws.get(u, ""), u, root):
                if link[0] not in [x[0] for x in pdfs]:
                    pdfs.append(link)
        pdfs = pdfs[:MAX_PDFS]

        if not dry_run:
            # The business-profile questions, drafted from these pages for the owner to confirm
            # (profile_from_site.py), then the exact facts — which win over a model's reading.
            st["step"] = "drafting your business profile from the website"
            save()
            try:
                from vula.commerce.profile_from_site import draft_and_save
                st["profile_drafts"] = await draft_and_save(tenant_id, read)
            except Exception as exc:
                log.info("profile drafts skipped for %s: %s", tenant_id, exc)
            try:
                from vula.commerce import business_profile
                st["profile_facts"] = business_profile.save_drafts(tenant_id, site_facts.facts_as_drafts(facts))
            except Exception as exc:
                log.info("profile facts skipped for %s: %s", tenant_id, exc)
            if pdfs:
                st["step"] = f"reading {len(pdfs)} PDF{'s' if len(pdfs) != 1 else ''} from the website"
                save()
                await _read_pdfs(tenant_id, pdfs, st)

        st["facts"] = {k: v["value"] for k, v in facts.items()}
        st["coverage"] = _coverage(products, read, facts, st, how, pdfs)
        st["step"] = "done"
        clean = True
        if not dry_run:
            st["pages_removed"] = await _remove_stale(tenant_id, pipeline, before, st["doc_ids"])
            from vula.commerce.meta_catalog import sync_if_connected
            await sync_if_connected(tenant_id)
    except Exception as exc:
        log.warning("site import failed for %s (%s): %s", tenant_id, root, exc)
        st["step"], st["error"] = "stopped", str(exc)[:200]
    finally:
        st["running"] = False
        st["finished_at"] = _now()
        n_errors = len(st["errors"])
        st["errors"] = st["errors"][:20]
        if not clean and before:
            st["doc_ids"] = list(dict.fromkeys(before + st["doc_ids"]))   # keep what may still be there
        save()
        log.info("site import %s (%s) %s: %s products (%s new, %s updated, %s on request), "
                 "%s/%s pages read, %s stored, %s errors", tenant_id, root, st["step"],
                 st["products_found"], st["products_created"], st["products_updated"],
                 st["products_on_request"], st["pages_read"], st["pages_found"],
                 st["kb_documents"], n_errors)
    return dict(st)


async def inspect_site(url: str, max_pages: int = MAX_PAGES) -> Dict[str, Any]:
    """Everything import_site would find on this site, and nothing saved anywhere — for
    measuring the import on a real site (tools/site_check.py)."""
    return await import_site("inspect", url, max_pages=max_pages, dry_run=True)

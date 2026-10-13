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
# Most useful first when the page cap bites: the pages people ask about.
_PRIORITY = [re.compile(p, re.I) for p in (
    r"/pages/", r"/policies/", r"faq|about|contact|deliver|shipping|return|refund|care|warrant|install",
    r"/blogs?/", r"/collections?/")]

_STATUS: Dict[str, Dict[str, Any]] = {}
_JOB = "site_import"


def _db():
    from vula.commerce import service
    return service._client()


def status(tenant_id: str) -> Dict[str, Any]:
    """The last or ongoing run — from vula_background_jobs (migration 206), so both uvicorn
    workers answer the same; this worker's memory when that table can't be read."""
    try:
        rows = (_db().table("vula_background_jobs").select("running,status,updated_at")
                .eq("tenant_id", tenant_id).eq("kind", _JOB).limit(1).execute().data or [])
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
    return dict(_STATUS.get(tenant_id) or {"running": False})


def _age_s(ts: Any) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                ).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def _save(tenant_id: str, st: Dict[str, Any]) -> None:
    """Write this run's progress where the other worker can see it. Fail-open."""
    _STATUS[tenant_id] = st
    try:
        _db().table("vula_background_jobs").upsert({
            "tenant_id": tenant_id, "kind": _JOB, "running": bool(st.get("running")),
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
            gallery = [i.get("url") if isinstance(i, dict) else i
                       for i in (raw if isinstance(raw, list) else [raw] if raw else [])]
            gallery = [("https:" + i if i.startswith("//") else i) for i in gallery if isinstance(i, str) and i]
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


async def _sitemap_locs(root: str) -> List[str]:
    """Every same-host URL in sitemap.xml (one level of child sitemaps), in sitemap order."""
    host = urlparse(root).netloc
    found: List[str] = []
    seen = set()
    queue = [f"{root}/sitemap.xml"]
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


def _slug_of(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


async def _products_from_pages(root: str, notes: List[str], skip_slugs: frozenset = frozenset(),
                               on_progress: Optional[Callable[[int, int], None]] = None
                               ) -> Tuple[str, List[Dict[str, Any]]]:
    """No readable feed (or one that stopped part-way): each product page from the sitemap not
    already in `skip_slugs` — Shopify's own per-product JSON first ("/products/<handle>.json",
    the feed's shape), else the page's JSON-LD Product."""
    urls = [u for u in await _sitemap_locs(root)
            if re.search(r"/products?/[^/]+/?$", urlparse(u).path)
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
        try:
            data = json.loads(await _get(base + ".json"))
            if isinstance(data, dict) and isinstance(data.get("product"), dict):
                got[u] = ("shopify", _from_shopify(data["product"], root))
                return
        except Exception:
            pass
        try:
            p = _from_jsonld((await _get(u)).decode("utf-8", errors="replace"), u)
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
                         on_progress: Optional[Callable[[int, int], None]] = None
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
                root, notes, frozenset(p["slug"] for p in out), on_progress)
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
    return await _products_from_pages(root, notes, on_progress=on_progress)


async def _save_products(tenant_id: str, products: List[Dict[str, Any]], st: Dict[str, Any]) -> None:
    from vula.api.commerce import admin_import_products
    from vula.commerce import service
    rows = [{"name": p["name"], "slug": p["slug"] or None, "price_cents": p["price_cents"],
             "category": _category_key(p["category_label"] or "general"),
             "description": p["description"][:4000], "image_url": p["image_url"] or "",
             "images": p.get("images") or [],
             "in_stock": "yes" if p["in_stock"] else "no", "status": "active"}
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


async def sitemap_urls(root: str, platform: str = "") -> List[str]:
    """Every page URL on this host from sitemap.xml (one level of child sitemaps), plus the
    shop's policy pages. Product pages are left out when the catalogue feed already has them."""
    found = await _sitemap_locs(root)
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
        return next((i for i, rx in enumerate(_PRIORITY) if rx.search(path)), len(_PRIORITY))
    return sorted(dict.fromkeys(found), key=rank)


def page_text(raw: bytes) -> Tuple[str, str]:
    from vula.commerce.reference_url import _extract_title_and_text
    title, text = _extract_title_and_text(raw.decode("utf-8", errors="replace"))
    text = re.sub(r"\n\s*\n+", "\n\n", text or "").strip()
    return _html.unescape(title or "").strip(), text


def _doc_id(tenant_id: str, key: str) -> str:
    return "site_" + hashlib.md5(f"{tenant_id}:{key}".encode()).hexdigest()[:16]


# ── The run ───────────────────────────────────────────────────────────────────

async def import_site(tenant_id: str, url: str, business: str = "", max_pages: int = MAX_PAGES
                      ) -> Dict[str, Any]:
    root = normalise_root(url)
    st: Dict[str, Any] = {
        "running": True, "url": root, "step": "reading the product catalogue", "started_at": _now(),
        "platform": "", "products_found": 0, "products_created": 0, "products_updated": 0,
        "products_on_request": 0, "variants": 0, "pages_found": 0, "pages_read": 0,
        "kb_documents": 0, "skipped_pages": 0, "errors": []}
    _save(tenant_id, st)

    def product_progress(done: int, total: int) -> None:
        st["step"] = f"reading product pages ({done} of {total})"
        if done % 20 == 0 or done == total:
            _save(tenant_id, st)
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        notes: List[str] = []
        platform, products = await fetch_products(root, notes, product_progress)
        st["platform"], st["products_found"] = platform, len(products)
        if notes:
            st["catalogue_note"] = "; ".join(notes)[:300]
            log.info("site import %s (%s): %s", tenant_id, root, st["catalogue_note"])
        if products:
            st["step"] = f"saving {len(products)} products"
            _save(tenant_id, st)
            await _save_products(tenant_id, products, st)
            res = await pipeline.ingest_text(
                content=catalogue_text(business or urlparse(root).netloc, root, products),
                filename=f"Website — product catalogue ({urlparse(root).netloc})",
                doc_id=_doc_id(tenant_id, "catalogue"), source_type="website")
            st["kb_documents"] += 1 if res.status == "success" else 0

        st["step"] = "finding the website's pages"
        urls = (await sitemap_urls(root, platform))[:max_pages]
        st["pages_found"] = len(urls)
        st["step"] = f"reading {len(urls)} pages"
        _save(tenant_id, st)
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def one(u: str) -> None:
            async with sem:
                try:
                    title, text = page_text(await _get(u))
                except Exception as exc:
                    st["errors"].append(f"{u}: {str(exc)[:80]}")
                    return
                st["pages_read"] += 1
                if len(text) < _MIN_TEXT:
                    st["skipped_pages"] += 1
                    return
                content = f"Source: {u}\n\n# {title or u}\n\n{text}"
                res = await pipeline.ingest_text(
                    content=content, filename=f"Website — {title or urlparse(u).path}"[:180],
                    doc_id=_doc_id(tenant_id, u), source_type="website")
                if res.status == "success":
                    st["kb_documents"] += 1
                else:
                    st["errors"].append(f"{u}: not stored ({res.status})")

        await asyncio.gather(*(one(u) for u in urls))
        st["step"] = "done"
    except Exception as exc:
        log.warning("site import failed for %s (%s): %s", tenant_id, root, exc)
        st["step"], st["error"] = "stopped", str(exc)[:200]
    finally:
        st["running"] = False
        st["finished_at"] = _now()
        n_errors = len(st["errors"])
        st["errors"] = st["errors"][:20]
        _save(tenant_id, st)
        log.info("site import %s (%s) %s: %s products (%s new, %s updated, %s on request), "
                 "%s/%s pages read, %s stored, %s errors", tenant_id, root, st["step"],
                 st["products_found"], st["products_created"], st["products_updated"],
                 st["products_on_request"], st["pages_read"], st["pages_found"],
                 st["kb_documents"], n_errors)
    return dict(st)

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
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger(__name__)

MAX_PAGES = 60
MAX_PRODUCT_PAGES = 10            # × 100 products per feed page
_CONCURRENCY = 4
_MIN_TEXT = 200                    # a page with less real text than this adds nothing

# Pages that never help a customer question: cart, account, search, checkout, login, tags.
_SKIP_PATH = re.compile(r"/(cart|account|checkout|search|login|register|wp-admin|wp-login|my-account|"
                        r"tagged|feed)(/|$|\?)", re.I)
# Shopify serves its policies here whether or not the sitemap lists them.
_SHOPIFY_POLICIES = ("/policies/refund-policy", "/policies/shipping-policy",
                     "/policies/terms-of-service", "/policies/privacy-policy")
# Most useful first when the page cap bites: the pages people ask about.
_PRIORITY = [re.compile(p, re.I) for p in (
    r"/pages/", r"/policies/", r"faq|about|contact|deliver|shipping|return|refund|care|warrant|install",
    r"/blogs?/", r"/collections?/")]

_STATUS: Dict[str, Dict[str, Any]] = {}


def status(tenant_id: str) -> Dict[str, Any]:
    return dict(_STATUS.get(tenant_id) or {"running": False})


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


async def _fetch(url: str) -> bytes:
    from vula.commerce.reference_url import safe_fetch_bytes
    return await safe_fetch_bytes(url)


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
        "in_stock": bool(p.get("is_in_stock", True)), "variants": [],
        "url": p.get("permalink") or root, "tags": [t.get("name") for t in p.get("tags") or [] if t.get("name")],
    }


async def fetch_products(root: str) -> Tuple[str, List[Dict[str, Any]]]:
    """(platform, products) from the shop's public catalogue feed; ("", []) when it has none."""
    out: List[Dict[str, Any]] = []
    for page in range(1, MAX_PRODUCT_PAGES + 1):
        try:
            data = json.loads(await _fetch(f"{root}/products.json?limit=100&page={page}"))
        except Exception:
            break
        items = data.get("products") if isinstance(data, dict) else None
        if not items:
            break
        out += [_from_shopify(p, root) for p in items]
        if len(items) < 100:
            break
    if out:
        return "shopify", out
    for page in range(1, MAX_PRODUCT_PAGES + 1):
        try:
            items = json.loads(await _fetch(f"{root}/wp-json/wc/store/v1/products?per_page=100&page={page}"))
        except Exception:
            break
        if not isinstance(items, list) or not items:
            break
        out += [_from_woo(p, root) for p in items]
        if len(items) < 100:
            break
    return ("woocommerce", out) if out else ("", [])


async def _save_products(tenant_id: str, products: List[Dict[str, Any]], st: Dict[str, Any]) -> None:
    from vula.api.commerce import admin_import_products
    from vula.commerce import service
    rows = [{"name": p["name"], "slug": p["slug"] or None, "price_cents": p["price_cents"],
             "category": _category_key(p["category_label"] or "general"),
             "description": p["description"][:4000], "image_url": p["image_url"] or "",
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
    host = urlparse(root).netloc
    found: List[str] = []
    seen = set()
    queue = [f"{root}/sitemap.xml"]
    for depth in range(2):
        nxt = []
        for sm in queue:
            try:
                xml = (await _fetch(sm)).decode("utf-8", errors="replace")
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
    return (title or "").strip(), text


def _doc_id(tenant_id: str, key: str) -> str:
    return "site_" + hashlib.md5(f"{tenant_id}:{key}".encode()).hexdigest()[:16]


# ── The run ───────────────────────────────────────────────────────────────────

async def import_site(tenant_id: str, url: str, business: str = "", max_pages: int = MAX_PAGES
                      ) -> Dict[str, Any]:
    root = normalise_root(url)
    st = _STATUS[tenant_id] = {
        "running": True, "url": root, "step": "reading the product catalogue", "started_at": _now(),
        "platform": "", "products_found": 0, "products_created": 0, "products_updated": 0,
        "products_on_request": 0, "variants": 0, "pages_found": 0, "pages_read": 0,
        "kb_documents": 0, "skipped_pages": 0, "errors": []}
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        platform, products = await fetch_products(root)
        st["platform"], st["products_found"] = platform, len(products)
        if products:
            st["step"] = f"saving {len(products)} products"
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
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def one(u: str) -> None:
            async with sem:
                try:
                    title, text = page_text(await _fetch(u))
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
        st["errors"] = st["errors"][:20]
    return dict(st)

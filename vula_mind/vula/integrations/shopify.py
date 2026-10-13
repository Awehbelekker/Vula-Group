"""
vula/integrations/shopify.py — a business's own Shopify store, connected to Vula.

2026-10-13 (onboarding SeedleMe, ~290 handmade basins and sinks on Shopify): reading the
public storefront (vula/commerce/site_import.py) gives products only, slowly, and Shopify
rate-limits it. Connected, Vula reads the whole catalogue — drafts, every image, live stock —
through the Admin GraphQL API, and Shopify calls Vula back when a product, a stock level or an
order changes, so the WhatsApp assistant never quotes a price or stock that's out of date.

How the owner connects (Shopify, 2026): Dev Dashboard → create an app → scopes read_products,
read_inventory, read_orders → install it on the store → copy the Client ID and secret into
Vula. Vula trades those for a 24-hour access token (client-credentials grant), caches it and
renews it before it runs out. An older admin "custom app" gives a permanent shpat_ token
instead; that is accepted too, but without its secret Shopify's webhooks can't be verified, so
such a store is synced on demand and every few hours rather than live.

Shopify stays the record for products and stock: Vula upserts by handle (= slug), archives a
deleted product rather than deleting it, and sets stock through service.adjust_stock so every
change is a recorded movement. Credentials are Fernet-encrypted at rest
(vula.email_imap.credentials), never returned by an endpoint, never logged.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import settings

log = logging.getLogger(__name__)

_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
REQUIRED_SCOPES = ("read_products",)
WANTED_SCOPES = ("read_products", "read_inventory", "read_orders")
WEBHOOK_TOPICS = ("PRODUCTS_CREATE", "PRODUCTS_UPDATE", "PRODUCTS_DELETE",
                  "INVENTORY_LEVELS_UPDATE", "ORDERS_CREATE")
_RETRY_WAITS = (2.0, 5.0, 12.0)
_RENEW_BEFORE_S = 300
JOB = "shopify_sync"


class ShopifyError(Exception):
    """Something the owner can act on — the message is shown in the dashboard as-is."""


# ── Storage ───────────────────────────────────────────────────────────────────

def _db():
    from vula.commerce import service
    return service._client()


def _enc(v: Optional[str]) -> Optional[str]:
    from vula.email_imap.credentials import encrypt_secret
    return encrypt_secret(v) if v else None


def _dec(v: Optional[str]) -> str:
    from vula.email_imap.credentials import decrypt_secret
    return decrypt_secret(v or "")


def get_account(tenant_id: str) -> Optional[Dict[str, Any]]:
    rows = (_db().table("vula_shopify_accounts").select("*").eq("tenant_id", tenant_id)
            .limit(1).execute().data or [])
    row = rows[0] if rows else None
    return row if row and row.get("status") != "disconnected" else None


def _update(tenant_id: str, fields: Dict[str, Any]) -> None:
    fields = {**fields, "updated_at": datetime.now(timezone.utc).isoformat()}
    _db().table("vula_shopify_accounts").update(fields).eq("tenant_id", tenant_id).execute()


def public_view(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the dashboard may see: never a token or a secret."""
    if not row:
        return {"connected": False}
    return {"connected": row.get("status") == "connected", "status": row.get("status"),
            "shop_domain": row.get("shop_domain"), "shop_name": row.get("shop_name"),
            "auth_mode": row.get("auth_mode"), "scopes": row.get("scopes") or [],
            "live_updates": bool(row.get("client_secret")) and bool(row.get("webhooks")),
            "last_sync_at": row.get("last_sync_at"), "last_sync": row.get("last_sync"),
            "last_error": row.get("last_error"), "connected_at": row.get("connected_at")}


def normalise_domain(raw: str) -> str:
    """'https://SeedleMe.myshopify.com/admin' / 'seedleme' → 'seedleme.myshopify.com'."""
    d = re.sub(r"^https?://", "", (raw or "").strip().lower()).split("/")[0]
    if d and "." not in d:
        d += ".myshopify.com"
    if not _DOMAIN.match(d):
        raise ShopifyError("Use the store's myshopify.com address — Shopify shows it under "
                           "Settings › Domains, e.g. seedleme.myshopify.com.")
    return d


# ── HTTP ──────────────────────────────────────────────────────────────────────

async def _post(url: str, *, json: Any = None, data: Any = None,
                headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(url, json=json, data=data, headers=headers)


async def exchange_client_credentials(shop: str, client_id: str, client_secret: str) -> Tuple[str, int, List[str]]:
    """Client ID + secret → (access token, seconds it lasts, scopes)."""
    resp = await _post(f"https://{shop}/admin/oauth/access_token",
                       data={"grant_type": "client_credentials", "client_id": client_id,
                             "client_secret": client_secret})
    if resp.status_code in (400, 401, 403, 404):
        raise ShopifyError("Shopify didn't accept that Client ID and secret for this store. Check "
                           "both were copied from the app's Settings in the Dev Dashboard, and that "
                           "the app is installed on this store.")
    resp.raise_for_status()
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise ShopifyError("Shopify returned no access token.")
    scopes = [s.strip() for s in str(body.get("scope") or "").split(",") if s.strip()]
    return token, int(body.get("expires_in") or 86399), scopes


_TOKENS: Dict[str, Tuple[str, float]] = {}       # tenant → (token, monotonic expiry)


async def access_token(acct: Dict[str, Any]) -> str:
    """A working token: the stored shpat_ token, or a client-credentials token renewed
    _RENEW_BEFORE_S before it runs out (shared between workers through the row)."""
    if acct.get("auth_mode") == "token":
        return _dec(acct.get("access_token"))
    tid = acct["tenant_id"]
    hit = _TOKENS.get(tid)
    if hit and hit[1] - _RENEW_BEFORE_S > time.monotonic():
        return hit[0]
    exp = acct.get("token_expires_at")
    if acct.get("access_token") and exp:
        try:
            left = (datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                    - datetime.now(timezone.utc)).total_seconds()
        except ValueError:
            left = 0
        if left > _RENEW_BEFORE_S:
            token = _dec(acct["access_token"])
            _TOKENS[tid] = (token, time.monotonic() + left)
            return token
    token, ttl, _ = await exchange_client_credentials(
        acct["shop_domain"], acct.get("client_id") or "", _dec(acct.get("client_secret")))
    _TOKENS[tid] = (token, time.monotonic() + ttl)
    try:
        _update(tid, {"access_token": _enc(token),
                      "token_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()})
    except Exception as exc:
        log.debug("shopify token not cached for %s: %s", tid, exc)
    return token


async def gql(shop: str, token: str, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One Admin GraphQL call, waiting out Shopify's throttling (429 / THROTTLED)."""
    url = f"https://{shop}/admin/api/{settings.shopify_api_version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    for attempt in range(len(_RETRY_WAITS) + 1):
        resp = await _post(url, json={"query": query, "variables": variables or {}}, headers=headers)
        if resp.status_code in (401, 403):
            raise ShopifyError("Shopify refused Vula's access — the app may have been uninstalled "
                               "or its access changed. Connect the store again.")
        throttled = resp.status_code in (429, 502, 503, 504)
        body: Dict[str, Any] = {}
        if not throttled:
            resp.raise_for_status()
            body = resp.json()
            errors = body.get("errors") or []
            throttled = any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors
                            if isinstance(e, dict))
            if errors and not throttled:
                first = errors[0] if isinstance(errors, list) else errors
                raise ShopifyError(f"Shopify: {first.get('message') if isinstance(first, dict) else first}")
        if not throttled:
            return body.get("data") or {}
        if attempt == len(_RETRY_WAITS):
            raise ShopifyError("Shopify is busy — try again in a minute.")
        await asyncio.sleep(_RETRY_WAITS[attempt])
    return {}


# ── Catalogue ─────────────────────────────────────────────────────────────────

def _product_fragment(with_inventory: bool) -> str:
    inv = " inventoryQuantity" if with_inventory else ""
    return ("fragment P on Product { id handle title descriptionHtml productType status tags "
            "onlineStoreUrl options { name } "
            "media(first: 20) { nodes { ... on MediaImage { image { url } } } } "
            "variants(first: 100) { nodes { id title sku price availableForSale" + inv +
            " selectedOptions { name value } } } }")


_PAGE_QUERY = ("query Catalogue($after: String) { products(first: 50, after: $after) { "
               "pageInfo { hasNextPage endCursor } nodes { ...P } } } ")
_ONE_QUERY = "query One($id: ID!) { product(id: $id) { ...P } } "
_BY_INVENTORY_ITEM = ("query Inv($id: ID!) { inventoryItem(id: $id) { variant { product { ...P } } } } ")

_STATUS = {"ACTIVE": "active", "DRAFT": "draft", "ARCHIVED": "archived", "UNLISTED": "unlisted"}


def map_product(node: Dict[str, Any], root: str) -> Dict[str, Any]:
    """One GraphQL Product → the product dict site_import._save_products saves, plus Shopify's
    id, its status (a draft stays a draft — never offered to customers) and stock."""
    from vula.commerce.site_import import _cents, html_to_text
    variants = []
    for v in ((node.get("variants") or {}).get("nodes") or []):
        values = {o["name"]: o["value"] for o in (v.get("selectedOptions") or [])
                  if o.get("name") and o.get("value") and o.get("value") != "Default Title"}
        variants.append({"option_values": values, "sku": (v.get("sku") or "").strip() or None,
                         "price_cents": _cents(v.get("price")),
                         "in_stock": bool(v.get("availableForSale", True)),
                         "stock": v.get("inventoryQuantity"), "shopify_id": v.get("id"),
                         "title": v.get("title") if v.get("title") != "Default Title" else None})
    images = [((m or {}).get("image") or {}).get("url") for m in ((node.get("media") or {}).get("nodes") or [])]
    images = [u for u in images if u]
    priced = [v["price_cents"] for v in variants if v["price_cents"] > 0]
    handle = (node.get("handle") or "").strip()
    single = variants[0] if len(variants) == 1 else None
    return {
        "name": (node.get("title") or "").strip(), "slug": handle,
        "price_cents": min(priced) if priced else 0,
        "category_label": (node.get("productType") or "").strip() or None,
        "description": html_to_text(node.get("descriptionHtml") or ""),
        "image_url": images[0] if images else None, "images": images,
        "in_stock": any(v["in_stock"] for v in variants) if variants else True,
        "variants": [v for v in variants if v["option_values"]] if len(variants) > 1 else [],
        "url": node.get("onlineStoreUrl") or (f"{root}/products/{handle}" if handle else root),
        "tags": node.get("tags") or [],
        "status": _STATUS.get(str(node.get("status") or "").upper(), "active"),
        "shopify_id": node.get("id"),
        "stock": single.get("stock") if single else None,
    }


async def fetch_catalogue(acct: Dict[str, Any]) -> List[Dict[str, Any]]:
    token = await access_token(acct)
    frag = _product_fragment("read_inventory" in (acct.get("scopes") or []))
    root = f"https://{acct['shop_domain']}"
    out: List[Dict[str, Any]] = []
    after = None
    for _ in range(200):                       # 200 × 50 = 10,000 products, a safety cap
        data = await gql(acct["shop_domain"], token, _PAGE_QUERY + frag, {"after": after})
        page = data.get("products") or {}
        out += [map_product(n, root) for n in page.get("nodes") or []]
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
    return out


async def fetch_one(acct: Dict[str, Any], product_gid: str = "", inventory_item_gid: str = ""
                    ) -> Optional[Dict[str, Any]]:
    token = await access_token(acct)
    frag = _product_fragment("read_inventory" in (acct.get("scopes") or []))
    root = f"https://{acct['shop_domain']}"
    if inventory_item_gid:
        data = await gql(acct["shop_domain"], token, _BY_INVENTORY_ITEM + frag, {"id": inventory_item_gid})
        node = (((data.get("inventoryItem") or {}).get("variant") or {}).get("product"))
    else:
        node = (await gql(acct["shop_domain"], token, _ONE_QUERY + frag, {"id": product_gid})).get("product")
    return map_product(node, root) if node else None


# ── Saving ────────────────────────────────────────────────────────────────────

async def save_products(tenant_id: str, products: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Upsert through the website import's path (by slug, options as variants), then what only
    a connected store knows: Shopify's ids, draft/archived status and stock — stock as a
    recorded movement (reason 'adjust', actor 'shopify') and only when it changed."""
    from vula.commerce import service
    from vula.commerce.site_import import _save_products
    st: Dict[str, Any] = {"products_created": 0, "products_updated": 0, "products_on_request": 0,
                          "variants": 0, "stock_changes": 0, "errors": []}
    await _save_products(tenant_id, products, st)
    by_slug = {p["slug"]: p for p in await service.list_products(
        tenant_id, in_stock_only=False, include_archived=True)}
    for p in products:
        row = by_slug.get(p["slug"])
        if not row:
            continue
        try:
            patch = {}
            if p.get("shopify_id") and row.get("shopify_id") != p["shopify_id"]:
                patch["shopify_id"] = p["shopify_id"]
            if p.get("status") and (row.get("status") or "active") != p["status"]:
                patch["status"] = p["status"]          # a price-on-request draft the import skipped
            if patch:
                await service.update_product(tenant_id, row["id"], patch)
            if p.get("stock") is not None and row.get("stock_quantity") != max(int(p["stock"]), 0):
                # Shopify lets an oversold item go negative; Vula's floor is 0 (compare after it)
                await service.adjust_stock(tenant_id, row["id"], set_to=max(int(p["stock"]), 0),
                                           reason="adjust", actor="shopify", note="Shopify stock")
                st["stock_changes"] += 1
            if p.get("variants"):
                existing = await service.list_variants(tenant_id, row["id"])
                for v in p["variants"]:
                    ev = next((e for e in existing if e.get("option_values") == v["option_values"]
                               or (v.get("sku") and e.get("sku") == v["sku"])), None)
                    if not ev:
                        continue
                    if v.get("shopify_id") and ev.get("shopify_id") != v["shopify_id"]:
                        await service.update_variant(tenant_id, ev["id"], {"shopify_id": v["shopify_id"]})
                    if v.get("stock") is not None and ev.get("stock_quantity") != max(int(v["stock"]), 0):
                        await service.adjust_stock(tenant_id, row["id"], variant_id=ev["id"],
                                                   set_to=max(int(v["stock"]), 0), reason="adjust",
                                                   actor="shopify", note="Shopify stock")
                        st["stock_changes"] += 1
        except Exception as exc:
            st["errors"].append(f"{p['slug']}: {str(exc)[:80]}")
    return st


async def archive_product(tenant_id: str, product_gid: str) -> bool:
    """A product deleted in Shopify is archived in Vula — orders and invoices still name it."""
    from vula.commerce import service
    rows = (_db().table("commerce_products").select("id").eq("tenant_id", tenant_id)
            .eq("shopify_id", product_gid).limit(1).execute().data or [])
    if not rows:
        return False
    await service.update_product(tenant_id, rows[0]["id"], {"status": "archived", "in_stock": False})
    return True


async def sync(tenant_id: str, business: str = "") -> Dict[str, Any]:
    """The whole catalogue, into products and the knowledge base's catalogue document.
    Progress is shared between workers (vula_background_jobs, kind 'shopify_sync')."""
    from vula.commerce import site_import
    st: Dict[str, Any] = {"running": True, "step": "reading the Shopify catalogue",
                          "started_at": datetime.now(timezone.utc).isoformat(), "errors": []}
    site_import._save(tenant_id, st, JOB)
    acct = get_account(tenant_id)
    try:
        if not acct:
            raise ShopifyError("No Shopify store is connected.")
        products = await fetch_catalogue(acct)
        st.update(products_found=len(products), step=f"saving {len(products)} products")
        site_import._save(tenant_id, st, JOB)
        saved = await save_products(tenant_id, products)
        st.update({k: v for k, v in saved.items() if k != "errors"})
        st["errors"] += saved["errors"]
        listed = [p for p in products if p.get("status") == "active"]
        from vula.ingestion.pipeline import VulaIngestionPipeline
        root = f"https://{acct['shop_domain']}"
        res = await VulaIngestionPipeline(tenant_id=tenant_id).ingest_text(
            content=site_import.catalogue_text(business or acct.get("shop_name") or "", root, listed),
            filename=f"Shopify — product catalogue ({acct.get('shop_name') or acct['shop_domain']})",
            doc_id=site_import._doc_id(tenant_id, "catalogue"), source_type="website")
        st["kb_catalogue"] = res.status == "success"
        st["step"] = "done"
        _update(tenant_id, {"last_sync_at": datetime.now(timezone.utc).isoformat(),
                            "last_sync": {k: st.get(k) for k in (
                                "products_found", "products_created", "products_updated",
                                "variants", "stock_changes")},
                            "last_error": None, "status": "connected"})
    except Exception as exc:
        msg = str(exc) if isinstance(exc, ShopifyError) else f"Sync stopped: {str(exc)[:160]}"
        log.warning("shopify sync failed for %s: %s", tenant_id, exc)
        st["step"], st["error"] = "stopped", msg
        if acct:
            try:
                _update(tenant_id, {"last_error": msg})
            except Exception:
                pass
    finally:
        st["running"] = False
        st["finished_at"] = datetime.now(timezone.utc).isoformat()
        st["errors"] = st["errors"][:20]
        site_import._save(tenant_id, st, JOB)
        log.info("shopify sync %s %s: %s products (%s new, %s updated), %s stock changes, %s errors",
                 tenant_id, st["step"], st.get("products_found", 0), st.get("products_created", 0),
                 st.get("products_updated", 0), st.get("stock_changes", 0), len(st["errors"]))
    return dict(st)


# ── Connecting ────────────────────────────────────────────────────────────────

_SHOP_QUERY = ("query { shop { name myshopifyDomain } "
               "currentAppInstallation { accessScopes { handle } } }")


async def connect(tenant_id: str, shop_domain: str, *, client_id: str = "", client_secret: str = "",
                  access_token_value: str = "", connected_by: Optional[str] = None) -> Dict[str, Any]:
    """Check the credentials against the store, store them encrypted, and (with a client
    secret to verify them) ask Shopify to call Vula on product, stock and order changes."""
    shop = normalise_domain(shop_domain)
    client_id, client_secret = client_id.strip(), client_secret.strip()
    access_token_value = access_token_value.strip()
    if client_id and client_secret:
        mode, (token, ttl, _) = "client_credentials", await exchange_client_credentials(shop, client_id, client_secret)
    elif access_token_value:
        mode, token, ttl = "token", access_token_value, 0
    else:
        raise ShopifyError("Paste the app's Client ID and secret (or an Admin API access token).")
    data = await gql(shop, token, _SHOP_QUERY)
    scopes = [s.get("handle") for s in ((data.get("currentAppInstallation") or {}).get("accessScopes") or [])
              if s.get("handle")]
    missing = [s for s in REQUIRED_SCOPES if s not in scopes]
    if missing:
        raise ShopifyError(f"The app needs the {', '.join(missing)} access scope. Add it in the "
                           "app's configuration, release a new version, and connect again.")
    other = (_db().table("vula_shopify_accounts").select("tenant_id").eq("shop_domain", shop)
             .neq("status", "disconnected").execute().data or [])
    if any(r.get("tenant_id") != tenant_id for r in other):
        raise ShopifyError("That Shopify store is already connected to another Vula business.")
    now = datetime.now(timezone.utc)
    row = {"tenant_id": tenant_id, "shop_domain": shop, "auth_mode": mode,
           "client_id": client_id or None, "client_secret": _enc(client_secret),
           "access_token": _enc(token),
           "token_expires_at": (now + timedelta(seconds=ttl)).isoformat() if ttl else None,
           "scopes": scopes, "shop_name": (data.get("shop") or {}).get("name"),
           "status": "connected", "last_error": None, "connected_by": connected_by,
           "connected_at": now.isoformat(), "updated_at": now.isoformat(), "webhooks": []}
    _db().table("vula_shopify_accounts").upsert(row, on_conflict="tenant_id").execute()
    _TOKENS.pop(tenant_id, None)
    if ttl:
        _TOKENS[tenant_id] = (token, time.monotonic() + ttl)
    hooks: List[str] = []
    if mode == "client_credentials":
        hooks = await register_webhooks(shop, token, tenant_id)
        _update(tenant_id, {"webhooks": hooks})
    log.info("shopify connected: tenant=%s shop=%s mode=%s scopes=%s webhooks=%d",
             tenant_id, shop, mode, ",".join(scopes), len(hooks))
    return public_view(get_account(tenant_id))


_HOOK_MUTATION = ("mutation Hook($topic: WebhookSubscriptionTopic!, $sub: WebhookSubscriptionInput!) { "
                  "webhookSubscriptionCreate(topic: $topic, webhookSubscription: $sub) { "
                  "webhookSubscription { id } userErrors { field message } } }")


def webhook_url(tenant_id: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/v1/shopify/webhook/{tenant_id}"


async def register_webhooks(shop: str, token: str, tenant_id: str) -> List[str]:
    """Subscribe to the topics Vula acts on. One that already exists counts as subscribed; one
    the app has no scope for (orders without read_orders) is skipped, not fatal."""
    done: List[str] = []
    for topic in WEBHOOK_TOPICS:
        for key in ("uri", "callbackUrl"):        # `uri` since 2025; older versions take callbackUrl
            try:
                data = await gql(shop, token, _HOOK_MUTATION,
                                 {"topic": topic, "sub": {key: webhook_url(tenant_id), "format": "JSON"}})
            except ShopifyError as exc:
                if key == "uri" and "uri" in str(exc).lower():
                    continue
                log.info("shopify webhook %s not registered for %s: %s", topic, tenant_id, exc)
                break
            errs = ((data.get("webhookSubscriptionCreate") or {}).get("userErrors") or [])
            if not errs or any("taken" in (e.get("message") or "").lower() for e in errs):
                done.append(topic)
            else:
                log.info("shopify webhook %s for %s: %s", topic, tenant_id, errs[0].get("message"))
            break
    return done


def disconnect(tenant_id: str) -> None:
    _TOKENS.pop(tenant_id, None)
    _update(tenant_id, {"status": "disconnected", "access_token": None, "client_secret": None,
                        "token_expires_at": None, "webhooks": []})


# ── Webhooks ──────────────────────────────────────────────────────────────────

def verify_webhook(acct: Dict[str, Any], body: bytes, signature: str) -> bool:
    secret = _dec(acct.get("client_secret"))
    if not secret or not signature:
        return False
    digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, signature.strip())


_SEEN: Dict[str, float] = {}


def first_delivery(webhook_id: str) -> bool:
    """Shopify retries a delivery it isn't sure arrived; handling is idempotent anyway (each
    product is re-read fresh), this only spares the repeat work."""
    now = time.monotonic()
    if len(_SEEN) > 5000:
        for k, t in list(_SEEN.items()):
            if now - t > 86400:
                _SEEN.pop(k, None)
    if not webhook_id:
        return True
    if webhook_id in _SEEN:
        return False
    _SEEN[webhook_id] = now
    return True


def _gid(kind: str, payload: Dict[str, Any]) -> str:
    gid = payload.get("admin_graphql_api_id")
    if gid:
        return str(gid)
    return f"gid://shopify/{kind}/{payload.get('id')}" if payload.get("id") else ""


async def handle_webhook(tenant_id: str, topic: str, payload: Dict[str, Any]) -> str:
    """products/*, inventory_levels/update → re-read that product from Shopify and save it.
    products/delete → archive. orders/create → recorded for the order-messaging step."""
    acct = get_account(tenant_id)
    if not acct:
        return "not connected"
    topic = (topic or "").lower()
    if topic == "products/delete":
        return "archived" if await archive_product(tenant_id, _gid("Product", payload)) else "unknown product"
    if topic in ("products/create", "products/update"):
        p = await fetch_one(acct, product_gid=_gid("Product", payload))
    elif topic == "inventory_levels/update":
        item = payload.get("inventory_item_id")
        p = await fetch_one(acct, inventory_item_gid=f"gid://shopify/InventoryItem/{item}") if item else None
    elif topic == "orders/create":
        log.info("shopify order for %s: %s", tenant_id, payload.get("name") or payload.get("id"))
        return "order noted"
    else:
        return "ignored"
    if not p:
        return "not found"
    await save_products(tenant_id, [p])
    return "saved"

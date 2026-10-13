"""
vula/integrations/woocommerce.py — a business's own WooCommerce store, connected to Vula.

2026-10-13 (Ian: "where are we with WooCommerce sync — this one will come up too"): the
website import (vula/commerce/site_import.py) reads a WooCommerce shop's public Store API once
— published products only, no exact stock, nothing when they change. Connected with REST API
keys, Vula reads the whole catalogue (drafts, variations, every image, stock) and, with
Read/Write keys, registers WooCommerce webhooks so a product, stock or order change reaches
Vula straight away. Read-only keys still work: Vula then re-reads the store every 6 hours.

How the owner connects: WooCommerce › Settings › Advanced › REST API › Add key — description
"Vula", permissions Read/Write (or Read) — then paste the store address, consumer key and
consumer secret into Vula. Same shape as Shopify (vula/integrations/shopify.py), whose saving
path this reuses: upsert by slug, the store's ids on products and variations, draft stays
draft, a deleted product is archived, stock changes are recorded movements.

Every request goes through Vula's guard for addresses a client types in (public HTTPS only,
resolved address checked, no redirects followed). Keys and the webhook secret are
Fernet-encrypted at rest, never returned by an endpoint, never logged.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from config import settings

log = logging.getLogger(__name__)

API = "/wp-json/wc/v3"
WEBHOOK_TOPICS = ("product.created", "product.updated", "product.deleted", "order.created")
JOB = "woocommerce_sync"
_RETRY_WAITS = (2.0, 5.0, 12.0)
_STATUS = {"publish": "active", "draft": "draft", "pending": "draft", "private": "draft", "trash": "archived"}


class WooError(Exception):
    """Something the owner can act on — shown in the dashboard as-is."""


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
    rows = (_db().table("vula_woocommerce_accounts").select("*").eq("tenant_id", tenant_id)
            .limit(1).execute().data or [])
    row = rows[0] if rows else None
    return row if row and row.get("status") != "disconnected" else None


def _update(tenant_id: str, fields: Dict[str, Any]) -> None:
    fields = {**fields, "updated_at": datetime.now(timezone.utc).isoformat()}
    _db().table("vula_woocommerce_accounts").update(fields).eq("tenant_id", tenant_id).execute()


def public_view(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the dashboard may see: never a key or a secret."""
    if not row:
        return {"connected": False}
    return {"connected": row.get("status") == "connected", "status": row.get("status"),
            "store_url": row.get("store_url"), "store_name": row.get("store_name"),
            "permissions": row.get("permissions"),
            "live_updates": bool(row.get("webhooks")),
            "last_sync_at": row.get("last_sync_at"), "last_sync": row.get("last_sync"),
            "last_error": row.get("last_error"), "connected_at": row.get("connected_at")}


def normalise_url(raw: str) -> str:
    """'shop.example.co.za/shop/' → 'https://shop.example.co.za'. HTTPS only — WooCommerce
    sends the keys as Basic auth, which must never travel unencrypted."""
    raw = (raw or "").strip()
    if not raw:
        raise WooError("Paste your shop's web address, e.g. https://www.yourshop.co.za")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    u = urlparse(raw)
    if u.scheme.lower() != "https":
        raise WooError("The shop must use https:// — WooCommerce keys can't be sent over plain http.")
    host = (u.hostname or "").lower()
    if not host or "." not in host:
        raise WooError("That doesn't look like a web address.")
    path = re.sub(r"/(shop|wp-admin|wp-json).*$", "", (u.path or "").rstrip("/"), flags=re.I)
    return f"https://{host}{(':' + str(u.port)) if u.port else ''}{path}"


# ── HTTP (through the guard for typed-in addresses) ───────────────────────────

async def _request(method: str, url: str, key: str, secret: str,
                   json: Any = None, params: Optional[Dict[str, Any]] = None) -> httpx.Response:
    from vula.commerce.reference_url import UnsafeUrlError, _resolve_and_validate
    host = urlparse(url).hostname or ""
    try:
        await _resolve_and_validate(host)
    except UnsafeUrlError as exc:
        raise WooError(f"Vula can't reach that address ({exc}).") from exc
    auth = base64.b64encode(f"{key}:{secret}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "User-Agent": "Vula/1.0 (+woocommerce)"}
    for wait in (*_RETRY_WAITS, None):
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            resp = await client.request(method, url, headers=headers, json=json, params=params)
        if resp.status_code in (429, 502, 503, 504) and wait is not None:
            await asyncio.sleep(wait)
            continue
        return resp
    return resp


def _error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        msg = body.get("message") if isinstance(body, dict) else ""
    except Exception:
        msg = ""
    if resp.status_code in (401, 403):
        return ("WooCommerce refused the keys — check the consumer key and secret, and that the "
                "key's user is a shop manager or administrator.")
    if resp.status_code in (301, 302, 307, 308):
        return (f"The shop redirects to {resp.headers.get('location', 'another address')} — "
                "paste that exact address instead.")
    if resp.status_code == 404:
        return ("No WooCommerce API at that address — check it's the shop's main address and that "
                "WordPress permalinks aren't set to 'Plain'.")
    return msg or f"WooCommerce answered {resp.status_code}."


async def _get(acct: Dict[str, Any], path: str, params: Optional[Dict[str, Any]] = None
               ) -> Tuple[Any, httpx.Response]:
    resp = await _request("GET", f"{acct['store_url']}{API}{path}", _dec(acct.get("consumer_key")),
                          _dec(acct.get("consumer_secret")), params=params)
    if resp.status_code >= 300:
        raise WooError(_error(resp))
    return resp.json(), resp


# ── Reading the catalogue ─────────────────────────────────────────────────────

def _price(v: Any) -> int:
    from vula.commerce.site_import import _cents
    return _cents(v)


def map_product(p: Dict[str, Any], variations: List[Dict[str, Any]], root: str) -> Dict[str, Any]:
    """One REST product (and its variations) → the product dict site_import._save_products
    saves, plus WooCommerce's id, status (a draft stays a draft) and stock."""
    from vula.commerce.site_import import html_to_text
    images = [i.get("src") for i in (p.get("images") or []) if i.get("src")]
    variants = []
    for v in variations:
        values = {a.get("name"): a.get("option") for a in (v.get("attributes") or [])
                  if a.get("name") and a.get("option")}
        stock = v.get("stock_quantity") if v.get("manage_stock") else None
        variants.append({"option_values": values, "sku": (v.get("sku") or "").strip() or None,
                         "price_cents": _price(v.get("price")),
                         "in_stock": v.get("stock_status") in ("instock", "onbackorder"),
                         "stock": stock, "woo_id": str(v.get("id")) if v.get("id") else None,
                         "image": (v.get("image") or {}).get("src")})
        if variants[-1]["image"] and variants[-1]["image"] not in images:
            images.append(variants[-1]["image"])
    priced = [v["price_cents"] for v in variants if v["price_cents"] > 0] or \
        ([_price(p.get("price"))] if _price(p.get("price")) > 0 else [])
    slug = (p.get("slug") or "").strip()
    simple = not variations
    return {
        "name": html_to_text(p.get("name") or "").strip(), "slug": slug,
        "price_cents": min(priced) if priced else 0,
        "category_label": next((c.get("name") for c in (p.get("categories") or []) if c.get("name")), None),
        "description": html_to_text(p.get("description") or p.get("short_description") or ""),
        "image_url": images[0] if images else None, "images": images[:20],
        "in_stock": (p.get("stock_status") in ("instock", "onbackorder")) if simple
        else any(v["in_stock"] for v in variants),
        "variants": [v for v in variants if v["option_values"]],
        "url": p.get("permalink") or (f"{root}/product/{slug}" if slug else root),
        "tags": [t.get("name") for t in (p.get("tags") or []) if t.get("name")],
        "status": _STATUS.get(str(p.get("status") or "publish").lower(), "active"),
        "woo_id": str(p.get("id")) if p.get("id") else None,
        "stock": p.get("stock_quantity") if simple and p.get("manage_stock") else None,
    }


async def _variations(acct: Dict[str, Any], product: Dict[str, Any]) -> List[Dict[str, Any]]:
    if product.get("type") != "variable" or not product.get("variations"):
        return []
    out: List[Dict[str, Any]] = []
    for page in range(1, 11):
        rows, resp = await _get(acct, f"/products/{product['id']}/variations",
                                {"per_page": 100, "page": page})
        out += rows or []
        if page >= int(resp.headers.get("x-wp-totalpages") or 1):
            break
    return out


async def fetch_catalogue(acct: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in range(1, 201):                 # 200 × 100 = 20,000 products, a safety cap
        rows, resp = await _get(acct, "/products", {"per_page": 100, "page": page, "status": "any"})
        for p in rows or []:
            out.append(map_product(p, await _variations(acct, p), acct["store_url"]))
        if page >= int(resp.headers.get("x-wp-totalpages") or 1):
            break
    return out


async def fetch_one(acct: Dict[str, Any], product_id: str) -> Optional[Dict[str, Any]]:
    try:
        p, _ = await _get(acct, f"/products/{product_id}")
    except WooError:
        return None
    if p.get("parent_id"):                     # a variation: read its product
        p, _ = await _get(acct, f"/products/{p['parent_id']}")
    return map_product(p, await _variations(acct, p), acct["store_url"])


# ── Saving / syncing ──────────────────────────────────────────────────────────

async def save_products(tenant_id: str, products: List[Dict[str, Any]]) -> Dict[str, Any]:
    from vula.integrations import shopify
    return await shopify.save_products(tenant_id, products, id_field="woo_id", actor="woocommerce",
                                       note="WooCommerce stock")


async def sync(tenant_id: str, business: str = "") -> Dict[str, Any]:
    """The whole catalogue, into products and the knowledge base's catalogue document.
    Progress is shared between workers (vula_background_jobs, kind 'woocommerce_sync')."""
    from vula.commerce import site_import
    st: Dict[str, Any] = {"running": True, "step": "reading the WooCommerce catalogue",
                          "started_at": datetime.now(timezone.utc).isoformat(), "errors": []}
    site_import._save(tenant_id, st, JOB)
    acct = get_account(tenant_id)
    try:
        if not acct:
            raise WooError("No WooCommerce store is connected.")
        products = await fetch_catalogue(acct)
        st.update(products_found=len(products), step=f"saving {len(products)} products")
        site_import._save(tenant_id, st, JOB)
        saved = await save_products(tenant_id, products)
        st.update({k: v for k, v in saved.items() if k != "errors"})
        st["errors"] += saved["errors"]
        listed = [p for p in products if p.get("status") == "active"]
        from vula.ingestion.pipeline import VulaIngestionPipeline
        res = await VulaIngestionPipeline(tenant_id=tenant_id).ingest_text(
            content=site_import.catalogue_text(business or acct.get("store_name") or "", acct["store_url"], listed),
            filename=f"WooCommerce — product catalogue ({acct.get('store_name') or acct['store_url']})",
            doc_id=site_import._doc_id(tenant_id, "catalogue"), source_type="website")
        st["kb_catalogue"] = res.status == "success"
        st["step"] = "done"
        from vula.commerce.meta_catalog import sync_if_connected
        await sync_if_connected(tenant_id)
        _update(tenant_id, {"last_sync_at": datetime.now(timezone.utc).isoformat(),
                            "last_sync": {k: st.get(k) for k in (
                                "products_found", "products_created", "products_updated",
                                "variants", "stock_changes")},
                            "last_error": None, "status": "connected"})
    except Exception as exc:
        msg = str(exc) if isinstance(exc, WooError) else f"Sync stopped: {str(exc)[:160]}"
        log.warning("woocommerce sync failed for %s: %s", tenant_id, exc)
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
        log.info("woocommerce sync %s %s: %s products (%s new, %s updated), %s stock changes, %s errors",
                 tenant_id, st["step"], st.get("products_found", 0), st.get("products_created", 0),
                 st.get("products_updated", 0), st.get("stock_changes", 0), len(st["errors"]))
    return dict(st)


# ── Connecting ────────────────────────────────────────────────────────────────

def webhook_url(tenant_id: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/v1/woocommerce/webhook/{tenant_id}"


async def _store_name(store_url: str) -> str:
    try:
        from vula.commerce.reference_url import safe_fetch_bytes
        import json
        return (json.loads(await safe_fetch_bytes(f"{store_url}/wp-json/", max_bytes=2_000_000)) or {}).get("name") or ""
    except Exception:
        return ""


async def connect(tenant_id: str, store_url: str, consumer_key: str, consumer_secret: str,
                  connected_by: Optional[str] = None) -> Dict[str, Any]:
    """Check the keys against the store, store them encrypted, and (with Read/Write keys) ask
    WooCommerce to call Vula on product, stock and order changes."""
    url = normalise_url(store_url)
    key, secret = (consumer_key or "").strip(), (consumer_secret or "").strip()
    if not (key.startswith("ck_") and secret.startswith("cs_")):
        raise WooError("Paste the consumer key (starts ck_) and consumer secret (starts cs_) from "
                       "WooCommerce › Settings › Advanced › REST API.")
    probe = {"store_url": url, "consumer_key": _enc(key), "consumer_secret": _enc(secret)}
    await _get(probe, "/products", {"per_page": 1})
    other = (_db().table("vula_woocommerce_accounts").select("tenant_id").eq("store_url", url)
             .neq("status", "disconnected").execute().data or [])
    if any(r.get("tenant_id") != tenant_id for r in other):
        raise WooError("That WooCommerce store is already connected to another Vula business.")
    hook_secret = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    row = {"tenant_id": tenant_id, "store_url": url, "consumer_key": _enc(key),
           "consumer_secret": _enc(secret), "webhook_secret": _enc(hook_secret),
           "store_name": await _store_name(url) or urlparse(url).hostname, "permissions": "read",
           "status": "connected", "last_error": None, "connected_by": connected_by,
           "connected_at": now, "updated_at": now, "webhooks": []}
    _db().table("vula_woocommerce_accounts").upsert(row, on_conflict="tenant_id").execute()
    hooks = await register_webhooks(get_account(tenant_id) or row, tenant_id, hook_secret)
    _update(tenant_id, {"webhooks": hooks, "permissions": "read_write" if hooks else "read"})
    log.info("woocommerce connected: tenant=%s store=%s webhooks=%d", tenant_id, url, len(hooks))
    return public_view(get_account(tenant_id))


async def register_webhooks(acct: Dict[str, Any], tenant_id: str, hook_secret: str) -> List[str]:
    """Read/Write keys can create webhooks; Read keys get a 401 here, which is fine — the store
    is then read every 6 hours instead."""
    done: List[str] = []
    for topic in WEBHOOK_TOPICS:
        resp = await _request("POST", f"{acct['store_url']}{API}/webhooks", _dec(acct.get("consumer_key")),
                              _dec(acct.get("consumer_secret")),
                              json={"name": f"Vula — {topic}", "topic": topic, "status": "active",
                                    "delivery_url": webhook_url(tenant_id), "secret": hook_secret})
        if resp.status_code in (200, 201):
            done.append(topic)
        elif resp.status_code in (401, 403):
            break
        else:
            log.info("woocommerce webhook %s for %s: %s", topic, tenant_id, _error(resp))
    return done


async def disconnect(tenant_id: str) -> None:
    """Forget the keys; the webhooks Vula made are deleted when the keys still allow it."""
    acct = get_account(tenant_id)
    if acct and acct.get("webhooks"):
        try:
            rows, _ = await _get(acct, "/webhooks", {"per_page": 100})
            for h in rows or []:
                if (h.get("delivery_url") or "") == webhook_url(tenant_id):
                    await _request("DELETE", f"{acct['store_url']}{API}/webhooks/{h['id']}",
                                   _dec(acct.get("consumer_key")), _dec(acct.get("consumer_secret")),
                                   params={"force": "true"})
        except Exception as exc:
            log.info("woocommerce webhooks not removed for %s: %s", tenant_id, exc)
    _update(tenant_id, {"status": "disconnected", "consumer_key": None, "consumer_secret": None,
                        "webhook_secret": None, "webhooks": []})


# ── Webhooks ──────────────────────────────────────────────────────────────────

def verify_webhook(acct: Dict[str, Any], body: bytes, signature: str) -> bool:
    """X-WC-Webhook-Signature = base64(HMAC-SHA256(secret, raw body))."""
    secret = _dec(acct.get("webhook_secret"))
    if not secret or not signature:
        return False
    digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, signature.strip())


async def handle_webhook(tenant_id: str, topic: str, payload: Dict[str, Any]) -> str:
    """product.created/updated → re-read that product (a variation's product) and save it;
    product.deleted → archive; order.created → noted for order messaging."""
    acct = get_account(tenant_id)
    if not acct:
        return "not connected"
    topic = (topic or "").lower()
    pid = str(payload.get("parent_id") or payload.get("id") or "")
    if topic == "product.deleted":
        from vula.integrations import shopify
        return "archived" if pid and await shopify.archive_product(tenant_id, pid, id_field="woo_id") else "unknown product"
    if topic in ("product.created", "product.updated"):
        p = await fetch_one(acct, pid) if pid and pid != "0" else None
        if not p:
            return "not found"
        await save_products(tenant_id, [p])
        return "saved"
    if topic == "order.created":
        log.info("woocommerce order for %s: %s", tenant_id, payload.get("number") or payload.get("id"))
        return "order noted"
    return "ignored"

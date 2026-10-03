"""
vula/storage_links.py — tenant files live in PRIVATE Storage buckets; links are signed on the way out.

2026-10-03 (Ian): the 'documents', 'evidence' and 'signatures' buckets were public, so every
filed bank statement, invoice, ID copy and signature could be opened by anyone holding (or
finding in a forwarded message or a log) its link. POPIA wants personal information protected
against unauthorised access, so those buckets are private now (migration 192) and:

  * the database keeps the stable form ".../storage/v1/object/public/<bucket>/<path>" it always
    stored — no row rewrite, it's just an identifier now;
  * anything handed to a person or to Meta goes through `signed()` → a link that expires;
  * anything the server itself reads goes through `fetch()` / `fetch_sync()` → the Storage API
    with the service key (a plain GET on the stored link would now 401).

'product-images' stays public: storefront pictures and logos are meant to be seen.
Every helper fails open to the original link — a signing hiccup must never lose a document.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Iterable, Optional, Tuple

log = logging.getLogger(__name__)

PRIVATE_BUCKETS = frozenset({"documents", "evidence", "signatures"})
FILE_URL_KEYS = ("file_url", "receipt_url", "photo_url", "signature_url")
DASHBOARD_TTL = 3600          # an hour: pages refetch long before that
SHARE_TTL = 7 * 24 * 3600     # a link pasted into WhatsApp should still open next week

_OBJ = re.compile(r"/storage/v1/object/(?:public|sign|authenticated)/([^/?#]+)/([^?#]+)")
_cache: Dict[Tuple[str, str, int], Tuple[float, str]] = {}


def _client():
    from vula.models.field_ops import _client as c
    return c()


def parse(url: Optional[str]) -> Optional[Tuple[str, str]]:
    """(bucket, path) for one of OUR private-bucket links, else None."""
    if not url or not isinstance(url, str):
        return None
    m = _OBJ.search(url)
    if not m or m.group(1) not in PRIVATE_BUCKETS:
        return None
    try:
        from config import settings
        host = (settings.supabase_url or "").rstrip("/")
        if host and not url.startswith(host):
            return None
    except Exception:
        pass
    from urllib.parse import unquote
    return m.group(1), unquote(m.group(2))


def canonical(url: Optional[str]) -> Optional[str]:
    """The stable stored form of a link — a signed link pasted back (e.g. the dashboard saving
    the settings it was shown) is turned back into the identifier, so an expiring token is
    never what the database keeps."""
    p = parse(url)
    if not p:
        return url
    from config import settings
    return f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{p[0]}/{p[1]}"


def signed(url: Optional[str], ttl: int = DASHBOARD_TTL) -> Optional[str]:
    """A link a person (or Meta) can open for `ttl` seconds. Non-private links pass through."""
    p = parse(url)
    if not p:
        return url
    key = (p[0], p[1], ttl)
    hit = _cache.get(key)
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]
    try:
        out = _client().storage.from_(p[0]).create_signed_url(p[1], ttl)
        if isinstance(out, dict):     # older storage3 returned a dict
            out = out.get("signedURL") or out.get("signedUrl") or ""
        if out:
            _cache[key] = (now + ttl / 2, out)   # reuse for half its life
            if len(_cache) > 5000:
                _cache.clear()
            return out
    except Exception as exc:
        log.warning("storage sign failed for %s/%s: %s", p[0], p[1], exc)
    return url


def sign_row(row: Any, ttl: int = DASHBOARD_TTL, keys: Iterable[str] = FILE_URL_KEYS) -> Any:
    """Sign the file links in one API row (dict) or a list of rows, in place; returns it.
    A list is signed in one Storage call per bucket (the Documents page shows up to 200)."""
    if isinstance(row, list):
        keys = tuple(keys)
        _presign([r.get(k) for r in row if isinstance(r, dict) for k in keys], ttl)
        for r in row:
            sign_row(r, ttl, keys)
        return row
    if isinstance(row, dict):
        for k in keys:
            if row.get(k):
                row[k] = signed(row[k], ttl)
    return row


def _presign(urls: Iterable[Optional[str]], ttl: int) -> None:
    """Warm the cache for many links with one batch call per bucket. Best-effort: anything it
    misses is signed one by one by signed()."""
    now = time.time()
    by_bucket: Dict[str, set] = {}
    for u in urls:
        p = parse(u)
        if p and not ((hit := _cache.get((p[0], p[1], ttl))) and hit[0] > now):
            by_bucket.setdefault(p[0], set()).add(p[1])
    for bucket, paths in by_bucket.items():
        try:
            items = _client().storage.from_(bucket).create_signed_urls(sorted(paths), ttl) or []
        except Exception as exc:
            log.warning("storage batch sign failed for %s (%d): %s", bucket, len(paths), exc)
            continue
        for it in items:
            url = (it or {}).get("signedURL") or (it or {}).get("signedUrl")
            if url and not (it or {}).get("error") and it.get("path"):
                _cache[(bucket, it["path"], ttl)] = (now + ttl / 2, url)


def fetch_sync(url: str, timeout: float = 60.0) -> bytes:
    """The file's bytes, for the server's own use. Private links go through the Storage API."""
    p = parse(url)
    if p:
        return _client().storage.from_(p[0]).download(p[1])
    import httpx
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


async def fetch(url: str, timeout: float = 60.0) -> bytes:
    p = parse(url)
    if p:
        import asyncio
        return await asyncio.to_thread(lambda: _client().storage.from_(p[0]).download(p[1]))
    import httpx
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

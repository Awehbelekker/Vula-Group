"""
vula/commerce/reference_url.py — safe fetching + feature/structure analysis of a tenant-supplied
"here's a site I like" URL, for the page-builder's reference-URL feature.

New trust boundary: unlike every other external fetch in this codebase (web_scraper.py's KB
ingestion is admin-curated URLs, not public tenant input; page_copy.py's design-reference image
fetch reads from the tenant's OWN pre-authenticated Supabase Storage bucket), this fetches
arbitrary URLs a TENANT types in. web_scraper.py's WebFetcher has zero SSRF protections
(confirmed: no private/loopback/link-local IP blocking, follow_redirects=True with no
re-validation of the redirect target, no response-size cap on its httpx path, and a crawl4ai
fallback that can itself invoke a full browser) — not safe to reuse as-is. This module is a
narrower, hardened, httpx-only fetcher instead: scheme allowlist, DNS-resolved IP validation
before connecting (blocks private/loopback/link-local/reserved ranges, including the
169.254.169.254 cloud-metadata endpoint), manual redirect handling with the SAME validation
re-applied to each hop, a hard response-size cap, and no browser/crawl4ai fallback at all.

Analysis is deliberately TEXT/STRUCTURE only, not visual — no headless browser exists anywhere
in this deployment (confirmed: not in requirements.txt, zero screenshot-capture code anywhere).
The vision pipeline elsewhere in page_copy.py (mood classification from an uploaded reference
IMAGE) is separate and already built — if URL screenshotting is ever added later, it slots in as
a "fetch a screenshot, then reuse that existing vision pipeline" step, not a rebuild.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Any, Dict, List
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

MAX_URLS = 3
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2MB
MAX_REDIRECTS = 3
_TIMEOUT = 10.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; VulaAI/1.0; reference-analysis-bot)"}

# The feature vocabulary the analysis LLM is constrained to — same "constrained classification
# over open generation" discipline as page_copy.py's MOOD_PRESETS. Features Vula can actually
# build (map to a real block/backend) vs. ones flagged as not-yet-supported are both real,
# recognized values here — the distinction is made by the caller (see FEATURE_BLOCK_MAP in
# page_copy.py), not by silently dropping unsupported ones.
KNOWN_FEATURES = {
    "booking", "faq", "pricing", "shop_grid", "testimonials", "gallery", "contact_form",
    "blog", "login", "live_chat", "newsletter_signup",
}


class UnsafeUrlError(Exception):
    """Raised when a URL fails scheme/hostname/IP-range validation — never a bare httpx
    exception, so callers can distinguish "this URL is unsafe" from "this URL is unreachable"."""


def _is_safe_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


async def _resolve_and_validate(hostname: str) -> None:
    """Raises UnsafeUrlError if the hostname resolves to any private/loopback/link-local/
    reserved/multicast address — blocks both literal internal URLs and DNS records that point at
    internal services or the cloud-metadata endpoint, which is the actual bypass risk (checking
    only the URL string, not what it resolves to, is not enough)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
    except Exception as exc:
        raise UnsafeUrlError(f"Could not resolve {hostname}: {exc}")
    if not infos:
        raise UnsafeUrlError(f"{hostname} did not resolve to any address")
    for info in infos:
        ip_str = info[4][0]
        if not _is_safe_ip(ip_str):
            raise UnsafeUrlError(f"{hostname} resolves to a non-public address ({ip_str})")


def _validate_scheme_and_hostname(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"Unsupported scheme: {parsed.scheme or '(none)'}")
    if not parsed.hostname:
        raise UnsafeUrlError("No hostname in URL")
    return parsed.hostname


async def safe_fetch_html(url: str) -> str:
    """Fetch a single URL's HTML, safely. Raises UnsafeUrlError or an httpx exception on
    failure — callers should catch both and skip that URL rather than let one bad URL block the
    others (see analyze_reference_urls)."""
    hops = 0
    current = url
    while True:
        hostname = _validate_scheme_and_hostname(current)
        await _resolve_and_validate(hostname)

        async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=False) as client:
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise UnsafeUrlError("Redirect with no Location header")
                    hops += 1
                    if hops > MAX_REDIRECTS:
                        raise UnsafeUrlError("Too many redirects")
                    current = str(httpx.URL(current).join(location))
                    continue

                resp.raise_for_status()
                chunks: List[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise UnsafeUrlError("Response too large")
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", errors="replace")


def _extract_title_and_text(html: str):
    """Reuses web_scraper.py's WebFetcher's pure HTML->text parsing (no network I/O, so safe to
    reuse directly) rather than reimplementing it — only the unsafe fetch methods on that class
    are avoided. Returns (title, text)."""
    from vula.skills.web_scraper import WebFetcher
    fetcher = WebFetcher()
    return fetcher._extract_title(html), fetcher._html_to_text(html)


_ANALYSIS_PROMPT = """You are analyzing website(s) a small-business owner says they like, to
identify which real features/functions are present. Below are text extracts from those pages.

Return ONLY a JSON object (no prose, no markdown fences):
{{
  "features_found": ["..."],
  "notes": "<one short sentence summarizing the overall style/purpose, no colors or fonts>"
}}

"features_found" must ONLY contain values from this exact list — do not invent new ones, and only
include a feature if you see real evidence of it (e.g. "Book Now" / a calendar / time slots for
booking; a list of questions with answers for faq; tiered plans with prices for pricing; a grid
of products for shop_grid; customer quotes for testimonials; a photo grid for gallery; a contact
form for contact_form; articles/posts for blog; a login/account area for login; a chat widget for
live_chat; an email signup for newsletter_signup):
{feature_list}

Extracts:
{extracts}"""


async def analyze_reference_urls(urls: List[str]) -> Dict[str, Any]:
    """Fetch up to MAX_URLS tenant-supplied URLs (best-effort — one failing/unsafe URL never
    blocks the others) and identify which known features are present. Returns
    {"features_found": [...], "notes": str, "fetched": [urls actually used]} or
    {"error": str} only if EVERY url failed. Never raises."""
    urls = [u for u in (urls or []) if isinstance(u, str) and u.strip()][:MAX_URLS]
    if not urls:
        return {"error": "No URLs given."}

    extracts = []
    fetched = []
    for url in urls:
        try:
            html = await safe_fetch_html(url)
            title, text = _extract_title_and_text(html)
            extracts.append(f"[{url}] {title}\n{text[:3000]}")
            fetched.append(url)
        except UnsafeUrlError as exc:
            log.info("reference_url: skipped unsafe URL %s: %s", url, exc)
        except Exception as exc:
            log.info("reference_url: could not fetch %s: %s", url, exc)

    if not extracts:
        return {"error": "Couldn't fetch any of those URLs — check they're correct and publicly reachable."}

    import litellm
    from core.llm_router import resolve_generation_route

    litellm.drop_params = True
    try:
        model, api_key, api_base = await resolve_generation_route(task_type="page_copy")
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": _ANALYSIS_PROMPT.format(
                feature_list=sorted(KNOWN_FEATURES), extracts="\n\n---\n\n".join(extracts))}],
            temperature=0.2, max_tokens=400, api_key=api_key, api_base=api_base,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("reference_url: analysis failed: %s", exc)
        return {"error": "Could not analyze those pages right now — please try again."}

    from vula.commerce.page_copy import _parse_llm_json
    data = _parse_llm_json(raw)
    found = data.get("features_found")
    features = sorted({f for f in (found if isinstance(found, list) else []) if f in KNOWN_FEATURES})
    notes = data.get("notes")
    notes = notes.strip()[:200] if isinstance(notes, str) else ""

    return {"features_found": features, "notes": notes, "fetched": fetched}

"""
vula/commerce/shared_link.py — work out what a link someone pasted actually points at.

Real Gerflor transcript, 2026-09-02:

    rep:  Please remind me to contact Danielle in two weeks. <maps.app.goo.gl link>
    Vula: ...the link you shared appears to be a Google Maps link, but I didn't use it for
          anything since you didn't ask me to.
    rep:  What the company details
    Vula: The company details are as follows:
          - Distribution centre in Cape Town and Johannesburg
          - Phone: +27 (0) 87 184 3128
          - Email: sales@sportsflooringwarehouse.co.za

Those are Sports Flooring Warehouse's details, pulled from the knowledge base. The rep was
asking about the company at the LINK — a different company entirely — and got a confident,
completely wrong answer. Confirmed by the rep: "not the same company".

Two things were wrong: nothing ever resolved a pasted link, and when asked about "the company"
the assistant answered from unrelated KB rather than admitting it hadn't looked.

A Google Maps short link (maps.app.goo.gl/...) redirects to a URL that carries the place name
in its path, so resolving the redirect chain alone usually identifies the business without
needing to parse the page at all.

Fetching is delegated to reference_url.safe_fetch_html, which is already SSRF-hardened
(scheme/hostname validation, private-IP rejection, redirect cap, response size cap) — a link
pasted by a user is exactly the untrusted input that guard exists for.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import unquote

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"']+")

_MAPS_HOSTS = ("maps.app.goo.gl", "goo.gl/maps", "google.com/maps", "maps.google.")

# The place name sits in the /place/<Name>/ segment of a resolved Google Maps URL.
_MAPS_PLACE_RE = re.compile(r"/place/([^/@?]+)")


def find_urls(text: str) -> list[str]:
    """Every http(s) URL in a message, in the order they appear."""
    return URL_RE.findall(text or "")


def is_maps_link(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in _MAPS_HOSTS)


def _place_name_from_url(url: str) -> Optional[str]:
    m = _MAPS_PLACE_RE.search(url or "")
    if not m:
        return None
    name = unquote(m.group(1)).replace("+", " ").strip()
    return name or None


async def resolve_shared_link(url: str) -> dict:
    """Identify what a pasted link points at.

    Returns {"url", "final_url", "name", "kind"} — `name` is None when it genuinely couldn't be
    worked out, which the caller must report honestly rather than substituting something else.
    """
    out = {"url": url, "final_url": None, "name": None,
           "kind": "map" if is_maps_link(url) else "web"}
    try:
        from vula.commerce.reference_url import safe_fetch_html, _validate_scheme_and_hostname
        _validate_scheme_and_hostname(url)
    except Exception as exc:
        log.info("shared link rejected before fetch (%s): %s", url[:60], exc)
        return out

    try:
        import httpx
        from vula.commerce.reference_url import _HEADERS, _TIMEOUT, _resolve_and_validate

        # Follow the redirect chain ourselves so the RESOLVED url is available — for a maps
        # short link that is where the place name lives.
        current, hops = url, 0
        async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT,
                                     follow_redirects=False) as client:
            while hops < 6:
                host = _validate_scheme_and_hostname(current)
                await _resolve_and_validate(host)
                resp = await client.get(current)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    current = str(httpx.URL(current).join(loc))
                    hops += 1
                    continue
                break
        out["final_url"] = current
        out["name"] = _place_name_from_url(current)
        if not out["name"] and out["kind"] == "web":
            html = await safe_fetch_html(current)
            from vula.commerce.reference_url import _extract_title_and_text
            title, _ = _extract_title_and_text(html)
            out["name"] = (title or "").strip() or None
    except Exception as exc:
        log.info("couldn't resolve shared link %s: %s", url[:60], exc)
    return out


def describe(resolved: dict) -> str:
    """A line for the assistant's context — deliberately says so when nothing was learned,
    so the model can't quietly answer about some other business instead."""
    name, url = resolved.get("name"), resolved.get("url")
    if name:
        return (f"The person shared a link ({url}) which points to: {name}. "
                f"If they ask about 'the company' or 'this place', they mean THIS one — not any "
                f"business in the knowledge base. You do not have its phone number or email "
                f"unless a tool returns them; say so rather than offering another company's.")
    return (f"The person shared a link ({url}) that couldn't be opened. If they ask about it, "
            f"say plainly that you can't open the link and ask them for the company name — "
            f"never answer with a different business's details from the knowledge base.")

"""
vula/commerce/reviews.py — what customers said about their orders, and the business's Google
review link (23 Nov 2026).

Ratings are collected on WhatsApp after delivery (vula/commerce/order_updates.py asks;
vula/api/whatsapp.py::_record_rating keeps them in commerce_reviews). Until now nothing showed
them to the business. `summary` reads every rating (page by page), and `thanks_text` is the
reply to a rating: a happy customer (4 or 5 stars) is also sent the business's own Google
review link, when it has set one (vula_tenant_config.google_review_url, migration 296).

The link is checked in code before it is kept: https on a Google host only, so the business's
WhatsApp number never sends customers anywhere else.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Where Google's own "write a review" links live (Business Profile › Ask for reviews gives a
# g.page/r/… link; maps.app.goo.gl is the share link; search.google.com/local/writereview?placeid=…).
GOOGLE_HOSTS = frozenset({
    "g.page", "goo.gl", "maps.app.goo.gl", "google.com", "www.google.com", "maps.google.com",
    "search.google.com", "business.google.com", "google.co.za", "www.google.co.za",
})
MAX_URL = 500
HAPPY = 4          # this many stars or more gets the Google link


def _client():
    from vula.commerce import service
    return service._client()


def clean_review_url(url: Optional[str]) -> Optional[str]:
    """The link as it will be sent, '' to clear it, or None when it isn't a Google link."""
    u = (url or "").strip()
    if not u:
        return ""
    if len(u) > MAX_URL or any(c.isspace() for c in u):
        return None
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    p = urlparse(u)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or host not in GOOGLE_HOSTS or p.username or p.password or p.port:
        return None
    return u


def google_link(tenant_id: str) -> str:
    try:
        rows = (_client().table("vula_tenant_config").select("google_review_url")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    except Exception as exc:        # migration 296 not applied yet: no link, never an error
        log.debug("google review link read skipped: %s", exc)
        return ""
    url = (rows[0].get("google_review_url") if rows else "") or ""
    return clean_review_url(url) or ""


def set_google_link(tenant_id: str, url: Optional[str]) -> str:
    """Save the link (read back). Raises ValueError for a link that isn't Google's."""
    clean = clean_review_url(url)
    if clean is None:
        raise ValueError("That isn't a Google review link. In your Google Business Profile, "
                         "choose 'Ask for reviews' and copy the link it gives you.")
    db = _client()
    db.table("vula_tenant_config").update({"google_review_url": clean or None}).eq("tenant_id", tenant_id).execute()
    if google_link(tenant_id) != clean:
        raise RuntimeError("The link didn't save. Try again.")
    return clean


def thanks_text(rating: int, google_url: str = "") -> str:
    """The reply to a customer's rating."""
    if rating == 5:
        text = "Thank you so much for the 5 stars! 🌟 See you next order."
    else:
        text = f"Thanks for the feedback ({rating}/5)."
        if rating <= 3:
            text += " We'd love to make it right — the owner will follow up with you."
    if rating >= HAPPY and google_url:
        text += f"\n\nIf you have a minute, a Google review helps us a lot:\n{google_url}"
    return text


def summary(tenant_id: str, limit: int = 100) -> Dict[str, Any]:
    """Every rating counted (average and how many of each star), the latest `limit` listed with
    the order's number."""
    from vula.db_pages import all_rows
    rows: List[Dict[str, Any]] = all_rows(
        lambda: (_client().table("commerce_reviews")
                 .select("id,order_id,product_id,customer_name,customer_phone,rating,comment,source,created_at")
                 .eq("tenant_id", tenant_id).order("created_at", desc=True)))
    rated = [r for r in rows if isinstance(r.get("rating"), int) and 1 <= r["rating"] <= 5]
    stars = Counter(r["rating"] for r in rated)
    recent = rated[:max(1, min(int(limit), 500))]
    ids = sorted({r["order_id"] for r in recent if r.get("order_id")})
    numbers: Dict[str, str] = {}
    if ids:
        try:
            for o in (_client().table("commerce_orders").select("id,display_id")
                      .eq("tenant_id", tenant_id).in_("id", ids).execute().data or []):
                numbers[o["id"]] = o.get("display_id") or ""
        except Exception as exc:
            log.debug("review order numbers skipped: %s", exc)
    for r in recent:
        r["order_number"] = numbers.get(r.get("order_id") or "", "")
    count = len(rated)
    return {
        "reviews": recent,
        "count": count,
        # one decimal, worked out here, never by a model
        "average": round(sum(r["rating"] for r in rated) / count, 1) if count else None,
        "stars": {str(n): stars.get(n, 0) for n in range(5, 0, -1)},
        "low": sum(stars.get(n, 0) for n in (1, 2, 3)),
        "google_review_url": google_link(tenant_id),
    }

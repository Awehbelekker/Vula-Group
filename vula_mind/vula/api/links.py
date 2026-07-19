"""
vula/api/links.py — public short-link redirect for broadcast click tracking.

Mounted at the app root (no /v1 prefix, no auth) — this is hit directly by a customer's phone
browser after tapping a link in a WhatsApp broadcast, so it has to be unauthenticated and fast.
The short code is looked up against the specific recipient row it was generated for
(commerce_broadcast_recipients, migration 064), so a click is attributed to who clicked, not just
"someone did" — see vula/api/commerce.py::admin_send_broadcast for where codes are generated.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

log = logging.getLogger(__name__)
router = APIRouter(tags=["links"])

# Self-contained fallback — settings.vula_base_url is an unconfigured placeholder domain
# (not a live page), so an unknown/expired code gets a plain message instead of chaining
# into another broken redirect.
_FALLBACK_HTML = HTMLResponse(
    "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
    "<p>This link has expired or is no longer valid.</p></body></html>", status_code=404)


def _client():
    from vula.commerce import service
    return service._client()


@router.get("/l/{code}")
async def follow_link(code: str):
    """Log the click (first-click timestamp + running count) and redirect to the real URL.
    Falls back to the Vula homepage if the code is unknown/expired rather than a dead 404 —
    a customer tapping a link should never hit a broken page."""
    fallback = _FALLBACK_HTML

    try:
        db = _client()
        row = (db.table("commerce_broadcast_recipients")
               .select("id,broadcast_id,target_url,click_count,clicked_at")
               .eq("short_code", code).limit(1).execute().data or [None])[0]
        if not row or not row.get("target_url"):
            return fallback

        was_first_click = not row.get("clicked_at")
        patch = {"click_count": (row.get("click_count") or 0) + 1}
        if was_first_click:
            patch["clicked_at"] = "now()"
        db.table("commerce_broadcast_recipients").update(patch).eq("id", row["id"]).execute()

        # Roll up into the broadcast log (same pattern as delivered/read counts) — only count
        # each recipient's FIRST click, so re-clicking the same link doesn't inflate reach.
        if was_first_click and row.get("broadcast_id"):
            try:
                cur = (db.table("commerce_broadcast_logs").select("clicked_count")
                       .eq("id", row["broadcast_id"]).limit(1).execute().data or [{}])[0]
                db.table("commerce_broadcast_logs").update(
                    {"clicked_count": (cur.get("clicked_count") or 0) + 1}
                ).eq("id", row["broadcast_id"]).execute()
            except Exception:
                pass

        return RedirectResponse(row["target_url"], status_code=302)
    except Exception as exc:
        log.warning("link redirect failed for code=%s: %s", code, exc)
        return fallback

"""
vula/api/project_track.py — the public project page a client opens from a share link.

GET /track/{token} (no login — the token is the key; vula/commerce/project_tracker.py). An
unknown, revoked or expired link shows a plain "not valid" page; a live one shows the project in
the view the business chose for it, from the books as they are now.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)
router = APIRouter(tags=["project-track"])

_GONE = ("<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
         "<h3 style='font-family:system-ui;margin:40px auto;max-width:440px'>This project link isn't valid "
         "any more. Please ask for a new one.</h3>")


@router.get("/track/{token}", response_class=HTMLResponse)
async def project_page(token: str):
    from vula.api.commerce import public_brand
    from vula.commerce import project_tracker as pt
    share = await asyncio.to_thread(pt.share_for_token, token)
    if not share:
        return HTMLResponse(_GONE, status_code=404)
    pos = await asyncio.to_thread(pt.position, share["tenant_id"], share["project"])
    brand = await public_brand(share["tenant_id"])
    await asyncio.to_thread(pt.note_view, share)
    return HTMLResponse(pt.render(brand, pt.client_view(pos, share["view"])),
                        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

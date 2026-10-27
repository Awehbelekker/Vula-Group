"""
vula/api/project_track.py — the public project page a client or QS opens from a share link.

GET /track/{token} (no login — the token is the key; vula/commerce/project_tracker.py). An
unknown, revoked or expired link shows a plain "not valid" page; a live one shows the project in
the view the business chose for it, from the books as they are now. Its downloads are PDFs only
— the page itself, and the priced bill when the project has one priced online — never the
editable Excel (2026-10-27, Ian: "don't want people to reuse the template").
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

log = logging.getLogger(__name__)
router = APIRouter(tags=["project-track"])

_GONE = ("<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
         "<h3 style='font-family:system-ui;margin:40px auto;max-width:440px'>This project link isn't valid "
         "any more. Please ask for a new one.</h3>")
_NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


async def _open(token: str):
    """(share, brand, client view, priced-bill lines) for a live link, else None."""
    from vula.api.commerce import public_brand
    from vula.commerce import boq_online, project_tracker as pt
    share = await asyncio.to_thread(pt.share_for_token, token)
    if not share:
        return None
    pos = await asyncio.to_thread(pt.position, share["tenant_id"], share["project"])
    brand = await public_brand(share["tenant_id"])
    bill = await asyncio.to_thread(boq_online.load, share["tenant_id"], share["project"])
    return share, brand, pt.client_view(pos, share["view"]), bill.get("lines") or []


def _pdf(data: bytes, name: str) -> Response:
    return Response(content=data, media_type="application/pdf",
                    headers={**_NO_STORE, "Content-Disposition": f'attachment; filename="{name}.pdf"'})


def _name(view: dict) -> str:
    return "".join(ch for ch in str(view.get("project") or "Project") if ch.isalnum() or ch in " -_").strip().replace(" ", "_")


@router.get("/track/{token}", response_class=HTMLResponse)
async def project_page(token: str):
    from vula.commerce import project_tracker as pt
    got = await _open(token)
    if not got:
        return HTMLResponse(_GONE, status_code=404)
    share, brand, view, lines = got
    links = {"Download this page (PDF)": f"/track/{token}/pdf"}
    if lines:
        links["Priced bill (PDF)"] = f"/track/{token}/bill.pdf"
    await asyncio.to_thread(pt.note_view, share)
    return HTMLResponse(pt.render(brand, view, links), headers=_NO_STORE)


@router.get("/track/{token}/pdf")
async def project_page_pdf(token: str):
    from vula.commerce import project_tracker as pt
    got = await _open(token)
    if not got:
        return HTMLResponse(_GONE, status_code=404)
    _share, brand, view, _lines = got
    return _pdf(await asyncio.to_thread(pt.pdf, brand, view), f"{_name(view)}_account")


@router.get("/track/{token}/bill.pdf")
async def project_bill_pdf(token: str):
    from vula.commerce import boq_online
    got = await _open(token)
    if not got or not got[3]:
        return HTMLResponse(_GONE, status_code=404)
    share, brand, view, _lines = got
    data = await asyncio.to_thread(boq_online.pdf, share["tenant_id"], share["project"], brand)
    return _pdf(data, f"{_name(view)}_priced_bill")

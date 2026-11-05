"""vula/api/xero.py — "Move from Xero": connect, preview at a cut-over date, bring it in once (checked
to the cent against Xero), then Vula disconnects. Undo takes a move back out.

Tenant-guarded, under /v1/commerce/{tenant}/admin/xero (Money access; changes by the owner or a
manager):
    GET  /v1/commerce/{tenant}/admin/xero                 connection (no tokens), recent runs
    POST /v1/commerce/{tenant}/admin/xero/connect         {by} → {url} to sign in at Xero
    POST /v1/commerce/{tenant}/admin/xero/disconnect      revoke at Xero, forget the tokens
    POST /v1/commerce/{tenant}/admin/xero/preview         {cutover, by} → a run, read in the background
    GET  /v1/commerce/{tenant}/admin/xero/runs/{run_id}   the run, its preview and its result
    POST /v1/commerce/{tenant}/admin/xero/runs/{run_id}/commit   bring it in (one-off), then disconnect
    POST /v1/commerce/{tenant}/admin/xero/runs/{run_id}/undo     take a move back out
Public (Xero sends the owner back here; the state is signed, see vula/xero/credentials.py):
    GET  /v1/xero/oauth/callback
"""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["xero"])             # mounted at /v1/commerce
public_router = APIRouter(tags=["xero"])      # mounted at /v1/xero


async def _owner_or_manager(request: Request, tenant_id: str) -> None:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can move the books.")


@router.get("/{tenant_id}/admin/xero")
async def xero_view(tenant_id: str) -> dict:
    from vula import connections
    from vula.migrate import runs
    from vula.xero import credentials
    return {"connection": await asyncio.to_thread(credentials.connection, tenant_id),
            "one_tap": connections.one_tap("xero"), "runs": await asyncio.to_thread(runs.recent, tenant_id)}


class ByIn(BaseModel):
    by: Optional[str] = None


@router.post("/{tenant_id}/admin/xero/connect")
async def xero_connect(tenant_id: str, body: ByIn, request: Request) -> dict:
    from vula import connections
    from vula.xero import credentials
    await _owner_or_manager(request, tenant_id)
    if not connections.one_tap("xero"):
        return await connections.not_available("xero")
    return {"available": True, "url": credentials.authorize_url(tenant_id, body.by or "")}


@router.post("/{tenant_id}/admin/xero/disconnect")
async def xero_disconnect(tenant_id: str, request: Request) -> dict:
    from vula.xero import credentials
    await _owner_or_manager(request, tenant_id)
    await credentials.disconnect(tenant_id)
    return {"connection": await asyncio.to_thread(credentials.connection, tenant_id)}


class PreviewIn(BaseModel):
    cutover: str
    by: Optional[str] = None


@router.post("/{tenant_id}/admin/xero/preview")
async def xero_preview(tenant_id: str, body: PreviewIn, request: Request) -> dict:
    from vula.migrate import runs
    from vula.xero import credentials
    await _owner_or_manager(request, tenant_id)
    if (credentials.connection(tenant_id) or {}).get("status") != "connected":
        raise HTTPException(status_code=409, detail="Connect Xero first.")
    try:
        return {"run": runs.start_preview(tenant_id, runs.parse_cutover(body.cutover), body.by or "")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/xero/runs/{run_id}/commit")
async def xero_commit(tenant_id: str, run_id: str, body: ByIn, request: Request) -> dict:
    """Bring it in (one-off): everything at the cut-over, checked to the cent against Xero, then
    Vula disconnects from Xero (vula/migrate/commit.py). Runs in the background; poll the run."""
    from vula.migrate import commit
    from vula.xero import credentials
    await _owner_or_manager(request, tenant_id)
    if (credentials.connection(tenant_id) or {}).get("status") != "connected":
        raise HTTPException(status_code=409, detail="Connect Xero first.")
    try:
        return {"run": commit.start_commit(tenant_id, run_id, body.by or "")}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/xero/runs/{run_id}/undo")
async def xero_undo(tenant_id: str, run_id: str, body: ByIn, request: Request) -> dict:
    """Take a move back out: its documents cancelled (kept on record) and the opening balances
    reversed — refused once a payment has been recorded on a moved document."""
    from vula.migrate import commit
    await _owner_or_manager(request, tenant_id)
    try:
        return {"undone": await asyncio.to_thread(commit.undo, tenant_id, run_id, body.by or "")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{tenant_id}/admin/xero/runs/{run_id}")
async def xero_run(tenant_id: str, run_id: str) -> dict:
    from vula.migrate import runs
    run = await asyncio.to_thread(runs.get, tenant_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="That run wasn't found.")
    return {"run": run}


def _page(message: str, ok: bool = True) -> HTMLResponse:
    colour = "#2C5545" if ok else "#b91c1c"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Xero</title></head>
        <body style="font-family:system-ui;text-align:center;padding:48px;color:{colour}">
        <h2>{html.escape(message)}</h2><p>You can close this window.</p>
        <script>try{{window.opener&&window.opener.postMessage('xero-connected','*');}}catch(e){{}}
        setTimeout(function(){{window.close();}}, 1500);</script></body></html>""")


@public_router.get("/oauth/callback")
async def xero_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    from vula.xero import credentials
    who = credentials.verify_state(state)
    if not who:
        return _page("This Xero sign-in link has expired or isn't valid. Press Connect Xero again.", ok=False)
    if error or not code:
        return _page("Xero wasn't connected.", ok=False)
    try:
        tok = await credentials.exchange_code(code)
        saved = await asyncio.to_thread(credentials.store_connection, who["tenant_id"], tok, who["by"])
    except Exception as exc:  # noqa: BLE001 — the owner sees a plain message; the cause is logged
        log.warning("xero connect for %s failed: %s", who["tenant_id"], exc)
        return _page("Xero couldn't be connected. Please try again.", ok=False)
    return _page(f"Xero connected — {saved.get('org_name') or 'your organisation'} ✅")

"""
vula/api/onboarding_api.py — Master: make a new business and set it up over WhatsApp (5 Nov 2026).

    POST /v1/master/clients                     make it, put the owner on its team, send the welcome
    GET  /v1/master/clients/onboarding          every business being set up this way, and how far
    POST /v1/master/clients/{tenant_id}/nudge   send the welcome again

Master only (verified JWT). The work is in vula/onboard_wa.py.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from vula.api.master_auth import require_master

master_router = APIRouter(tags=["onboarding"])


class ClientIn(BaseModel):
    tenant_id: str
    display_name: Optional[str] = None
    business_type: Optional[str] = "other"
    owner_name: str
    owner_whatsapp: str
    owner_email: Optional[str] = None
    send_welcome: bool = True
    website: Optional[str] = None      # brand + products + pages + profile drafts (website_setup.py)


@master_router.post("")
@master_router.post("/")
async def create_client(body: ClientIn, identity: dict = Depends(require_master)) -> dict:
    from vula import onboard_wa
    try:
        out = await onboard_wa.create_client(
            identity, tenant_id=body.tenant_id.strip().lower(), display_name=body.display_name or body.tenant_id,
            business_type=body.business_type or "other", owner_name=body.owner_name,
            owner_whatsapp=body.owner_whatsapp, owner_email=body.owner_email,
            send_welcome_now=body.send_welcome, website=body.website)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if out.get("error") and not out.get("tenant"):
        raise HTTPException(status_code=400, detail=out["error"])
    try:
        from vula.api.master import audit
        audit(identity, "client_onboarding_started", body.tenant_id, welcome=out.get("welcome"))
    except Exception:
        pass
    return out


@master_router.get("/onboarding")
async def onboarding(identity: dict = Depends(require_master)) -> dict:
    from vula import onboard_wa
    from vula.integrations import platform_support as ps
    try:
        items = await asyncio.to_thread(onboard_wa.progress)
    except Exception:
        raise HTTPException(status_code=503, detail="Couldn't read onboarding — has migration 249 been applied?")
    return {"items": items, "vula_number_set": bool(ps.platform_creds()),
            "vula_number": ps.platform_number() or None, "total_steps": len(onboard_wa.STEPS)}


@master_router.post("/{tenant_id}/nudge")
async def nudge(tenant_id: str, identity: dict = Depends(require_master)) -> dict:
    from vula import onboard_wa
    out = await onboard_wa.nudge(tenant_id)
    if out.get("error"):
        raise HTTPException(status_code=400, detail=out["error"])
    try:
        from vula.api.master import audit
        audit(identity, "client_onboarding_nudged", tenant_id, welcome=out.get("welcome"))
    except Exception:
        pass
    return out

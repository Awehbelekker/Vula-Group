"""
vula/api/owner_briefs.py — a business's own switches for the owner briefs (vula/owner_briefs.py).

    GET /v1/team/{tenant}/briefs                  on/off for the morning brief and weekly advisor
    PUT /v1/team/{tenant}/briefs                  {"morning": bool|null, "weekly": bool|null}
    GET /v1/team/{tenant}/briefs/preview?kind=    today's text, exactly as it would be sent

Writes to /v1/team/{tenant}/… are owner-and-managers only (server.py _SECTION_RULES), and the
PUT checks it itself too (require_tenant_admin). The preview shows money figures, so it needs
the owner or a manager as well. It only reads, and sends nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from vula import owner_briefs
from vula.api import merchant_audit
from vula.api.tenant_auth import require_tenant_admin

log = logging.getLogger(__name__)
router = APIRouter(tags=["owner-briefs"])


class BriefsIn(BaseModel):
    morning: Optional[bool] = None
    weekly: Optional[bool] = None
    reset: bool = False          # true → both follow the platform default again


@router.get("/{tenant}/briefs")
async def get_briefs(tenant: str) -> dict:
    return {"settings": owner_briefs.settings_for(tenant)}


@router.put("/{tenant}/briefs")
async def put_briefs(tenant: str, body: BriefsIn, identity: dict = Depends(require_tenant_admin)) -> dict:
    patch = ({k: None for k in owner_briefs.KINDS} if body.reset
             else {k: v for k, v in body.model_dump(exclude={"reset"}).items() if v is not None})
    try:
        saved = owner_briefs.save(tenant, patch)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't save ({exc}). Run migration 257?")
    merchant_audit.audit(tenant, identity, "owner_brief_settings", changed=patch)
    return {"settings": saved}


@router.get("/{tenant}/briefs/preview")
async def preview_brief(tenant: str, kind: str = Query("morning"),
                        identity: dict = Depends(require_tenant_admin)) -> dict:
    # The brief carries money owed and paid, so only the owner or a manager may read it — the
    # section rule above covers writes only.
    if kind not in owner_briefs.KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(owner_briefs.KINDS)}")
    try:
        if kind == "morning":
            from vula import owner_digest as mod
            now = datetime.now(mod.SAST)
            text = mod.render(mod.build(tenant, now.date(), now))
        else:
            from vula import owner_advisor as mod
            text = mod.render(mod.build(tenant, datetime.now(mod.SAST).date()))
    except Exception as exc:
        log.warning("brief preview failed for %s: %s", tenant, exc)
        raise HTTPException(status_code=500, detail="Couldn't build the preview.")
    return {"kind": kind, "text": text, "empty": text is None}

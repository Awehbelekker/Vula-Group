"""
vula/api/app_errors.py — the dashboard reports its crashes; Master reads them (vula/app_errors.py).

POST /v1/app-errors                       public: a crash can happen before sign-in (the login
                                          screen); limited per address. The business is recorded
                                          only when the signed-in session belongs to it.
GET  /v1/master/app-errors                master: what broke, where, how often
POST /v1/master/app-errors/{id}/resolve   master: fixed (it reopens if it happens again)
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from vula import app_errors
from vula.api.master_auth import require_master

router = APIRouter(tags=["app-errors"])
master_router = APIRouter(tags=["app-errors"], dependencies=[Depends(require_master)])

PER_IP_PER_HOUR = 60
_SEEN: Dict[str, list] = {}


class Report(BaseModel):
    message: str = Field(max_length=2000)
    stack: Optional[str] = Field(default="", max_length=8000)
    build: Optional[str] = Field(default="", max_length=64)
    page: Optional[str] = Field(default="", max_length=120)
    tenant_id: Optional[str] = Field(default=None, max_length=80)


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")) or "?"


def _allow(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _SEEN.get(ip, []) if now - t < 3600]
    if len(hits) >= PER_IP_PER_HOUR:
        _SEEN[ip] = hits
        return False
    hits.append(now)
    if len(_SEEN) > 5000:
        _SEEN.clear()
    _SEEN[ip] = hits
    return True


@router.post("")
async def report(body: Report, request: Request, authorization: str = Header(default="")) -> Dict[str, Any]:
    if not _allow(_ip(request)):
        return {"ok": False}                        # quietly: a looping page mustn't get louder
    tenant = None
    if body.tenant_id and authorization:
        from vula.api.tenant_auth import is_tenant_member
        if await is_tenant_member(authorization, body.tenant_id):
            tenant = body.tenant_id
    app_errors.record(message=body.message, stack=body.stack or "", build=body.build or "",
                      page=body.page or "", ua=request.headers.get("user-agent", ""), tenant_id=tenant)
    return {"ok": True}


@master_router.get("")
async def list_errors(days: int = 14, resolved: bool = False) -> Dict[str, Any]:
    rows = app_errors.recent(days=days, include_resolved=resolved)
    return {"errors": rows, "total": sum(int(r.get("count") or 0) for r in rows)}


@master_router.post("/{error_id}/resolve")
async def resolve_error(error_id: str) -> Dict[str, Any]:
    if not app_errors.resolve(error_id):
        raise HTTPException(status_code=404, detail="That error isn't there.")
    return {"ok": True}

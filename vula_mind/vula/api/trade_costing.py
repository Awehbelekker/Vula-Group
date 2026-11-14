"""vula/api/trade_costing.py — build-ups for the dashboard (trade costing, phase 2, 14 Nov 2026).

Mounted under /v1/commerce, so the paths are /v1/commerce/{tenant_id}/admin/… and pass the same
membership guard as the rest of the tenant admin API. Switched on per business with
`trade_costing` (vula/features.py).

  GET   /{tenant_id}/admin/trades                      the 23 trades and what each must consider
  GET   /{tenant_id}/admin/buildups                    this business's build-ups (not retired)
  GET   /{tenant_id}/admin/buildups/find?item=&spec=   the one Vula would use for an item
  POST  /{tenant_id}/admin/buildups/research           research one from cited sources (proposed)
  POST  /{tenant_id}/admin/buildups/{id}/confirm       owner or manager: confirm, optionally changed
  POST  /{tenant_id}/admin/buildups/{id}/retire        owner or manager: stop using it
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["trade-costing"])


def _on(tenant_id: str) -> None:
    from vula import features
    if not features.on(tenant_id, "trade_costing"):
        raise HTTPException(status_code=403, detail="Costing by trade isn't switched on for this business.")


async def _caller(tenant_id: str, request: Request) -> tuple:
    """(person key, full access) — full access is an owner or manager."""
    from vula.api.tenant_auth import member_scope, scope_allows
    from vula.identity import person_for_login
    auth = request.headers.get("authorization", "")
    scope = await member_scope(auth, tenant_id)
    person = await person_for_login(auth, tenant_id)
    return (person.key if person is not None else ""), (scope is not None and scope_allows(scope, frozenset()))


def _err(exc: Exception) -> HTTPException:
    msg = str(exc)
    return HTTPException(status_code=403 if "owner or manager" in msg else 400, detail=msg)


@router.get("/{tenant_id}/admin/trades")
async def trades(tenant_id: str) -> dict:
    from vula.commerce import trade_checklists
    return {"trades": [{"trade": t, "components": trade_checklists.checklist_for(t)}
                       for t in trade_checklists.trades()]}


@router.get("/{tenant_id}/admin/buildups")
async def list_buildups(tenant_id: str, request: Request) -> dict:
    _on(tenant_id)
    from vula.commerce import buildups
    _person, lead = await _caller(tenant_id, request)
    rows = await asyncio.to_thread(buildups.list_for, tenant_id)
    return {"buildups": [buildups.public(b) for b in rows], "can_confirm": lead}


@router.get("/{tenant_id}/admin/buildups/find")
async def find_buildup(tenant_id: str, item: str, spec: Optional[str] = None) -> dict:
    _on(tenant_id)
    from vula.commerce import buildups
    try:
        spec_d = json.loads(spec) if spec else {}
    except ValueError:
        raise HTTPException(status_code=400, detail="spec must be JSON, e.g. {\"thickness\": \"220 mm\"}")
    b = await asyncio.to_thread(buildups.find, tenant_id, item, spec_d)
    return {"buildup": buildups.public(b) if b else None}


class ResearchIn(BaseModel):
    item: str
    spec: Dict[str, str] = {}
    unit: str = "m2"


@router.post("/{tenant_id}/admin/buildups/research")
async def research_buildup(tenant_id: str, body: ResearchIn, request: Request) -> dict:
    _on(tenant_id)
    from vula.commerce import buildups
    person, _lead = await _caller(tenant_id, request)
    existing = await asyncio.to_thread(buildups.find, tenant_id, body.item, body.spec)
    if existing and existing["origin"] in ("own", "shared"):
        return {"buildup": buildups.public(existing), "researched": False}
    try:
        b = await buildups.research(tenant_id, body.item, body.spec, unit=body.unit or "m2", created_by=person)
    except buildups.BuildupError as exc:
        raise _err(exc)
    return {"buildup": buildups.public(b), "researched": True}


class LineChange(BaseModel):
    component: str
    qty_per_unit: Optional[float] = None
    unit: Optional[str] = None
    waste_pct: Optional[float] = None
    label: Optional[str] = None
    kind: Optional[str] = None
    remove: bool = False


class ConfirmIn(BaseModel):
    changes: List[LineChange] = []


@router.post("/{tenant_id}/admin/buildups/{buildup_id}/confirm")
async def confirm_buildup(tenant_id: str, buildup_id: str, body: ConfirmIn, request: Request) -> dict:
    _on(tenant_id)
    from vula.commerce import buildups
    person, lead = await _caller(tenant_id, request)
    changes: List[Dict[str, Any]] = [c.model_dump(exclude_none=True) for c in body.changes]
    try:
        b = await asyncio.to_thread(buildups.confirm, tenant_id, buildup_id, person=person,
                                    full_access=lead, changes=changes)
    except buildups.BuildupError as exc:
        raise _err(exc)
    return {"buildup": buildups.public(b), "message": "Confirmed — Vula will use this for every estimate of this item."}


@router.post("/{tenant_id}/admin/buildups/{buildup_id}/retire")
async def retire_buildup(tenant_id: str, buildup_id: str, request: Request) -> dict:
    _on(tenant_id)
    from vula.commerce import buildups
    _person, lead = await _caller(tenant_id, request)
    try:
        ok = await asyncio.to_thread(buildups.retire, tenant_id, buildup_id, full_access=lead)
    except buildups.BuildupError as exc:
        raise _err(exc)
    return {"retired": ok}

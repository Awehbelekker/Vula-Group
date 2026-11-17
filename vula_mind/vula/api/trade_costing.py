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
  POST  /{tenant_id}/admin/estimates                   cost work from its build-ups (phase 3)
  GET   /{tenant_id}/admin/estimates[/{id}.xlsx]       kept estimates, and one as Excel
  GET   /{tenant_id}/admin/estimates/{id}/actuals      the estimate beside what was paid (phase 4)
  POST  /{tenant_id}/admin/estimates/{id}/learn        owner or manager: the work is finished — learn
  GET   /{tenant_id}/admin/buildup-suggestions         suggested build-up changes waiting for a decision
  POST  /{tenant_id}/admin/buildup-suggestions/{id}/accept|dismiss   owner or manager
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


# ── estimates (phase 3) ──────────────────────────────────────────────────────

class EstimateItem(BaseModel):
    item: str
    spec: Dict[str, str] = {}
    quantity: Optional[float] = None
    quantity_expr: Optional[str] = None
    unit: str = "m2"


class EstimateIn(BaseModel):
    items: List[EstimateItem]
    exclusions: List[str] = []
    title: str = ""
    research: bool = True
    project: Optional[str] = None


@router.post("/{tenant_id}/admin/estimates")
async def make_estimate(tenant_id: str, body: EstimateIn, request: Request) -> dict:
    """Cost the work from each item's build-up (vula/commerce/estimate.py) and keep it."""
    _on(tenant_id)
    from vula.commerce import estimate
    person, _lead = await _caller(tenant_id, request)
    try:
        est = await estimate.estimate(tenant_id, [i.model_dump(exclude_none=True) for i in body.items],
                                      exclusions=body.exclusions, research=body.research,
                                      created_by=person, title=body.title, project=body.project or None)
    except estimate.EstimateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"estimate": est, "text": estimate.summary(est)}


@router.get("/{tenant_id}/admin/estimates")
async def list_estimates(tenant_id: str, limit: int = 20) -> dict:
    _on(tenant_id)
    from vula.commerce import service
    rows = await asyncio.to_thread(
        lambda: service._client().table("vula_estimates").select("id,title,total_cents,created_at,project")
        .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(max(1, min(limit, 100))).execute().data or [])
    return {"estimates": rows}


@router.get("/{tenant_id}/admin/estimates/{estimate_id}.xlsx")
async def estimate_xlsx(tenant_id: str, estimate_id: str):
    """A kept estimate as its Excel breakdown, re-read against the estimate before it is sent."""
    _on(tenant_id)
    from fastapi.responses import Response
    from vula.api.tenants import display_name
    from vula.commerce import estimate_sheet, service, xlsx_check
    rows = await asyncio.to_thread(
        lambda: service._client().table("vula_estimates").select("*").eq("id", estimate_id)
        .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="Estimate not found.")
    r = rows[0]
    t = r.get("totals") or {}
    est = {"title": r["title"], "items": r.get("items") or [], "total_cents": int(r.get("total_cents") or 0),
           "exclusions": t.get("exclusions") or [], "hours_per_day": 8}
    data, want = await asyncio.to_thread(estimate_sheet.workbook, display_name(tenant_id), est)
    if xlsx_check.mismatches(data, want):
        raise HTTPException(status_code=500, detail="The spreadsheet didn't add up, so it wasn't sent.")
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="Estimate_{str(r.get("created_at") or "")[:10] or estimate_id[:8]}.xlsx"'})


# ── what the job actually cost (phase 4) ─────────────────────────────────────

def _actuals_err(exc: Exception) -> HTTPException:
    msg = str(exc)
    status = 403 if "owner or manager" in msg else 404 if msg.startswith("I can't find") else 400
    return HTTPException(status_code=status, detail=msg)


@router.get("/{tenant_id}/admin/estimates/{estimate_id}/actuals")
async def estimate_actuals(tenant_id: str, estimate_id: str) -> dict:
    """The estimate beside what was paid on its project. A running job is progress only."""
    _on(tenant_id)
    from vula.commerce import actuals
    est = await asyncio.to_thread(actuals.get_estimate, tenant_id, estimate_id)
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found.")
    try:
        cmp = await asyncio.to_thread(actuals.compare, tenant_id, est)
    except actuals.ActualsError as exc:
        raise _actuals_err(exc)
    finished = await asyncio.to_thread(actuals.is_finished, tenant_id, cmp["project"])
    props = actuals.proposals(cmp)
    suggestions = await asyncio.to_thread(actuals.list_suggestions, tenant_id, status=None, estimate_id=estimate_id)
    return {"comparison": actuals.public(cmp), "finished": finished,
            "proposals": [actuals._plain(p) for p in props], "suggestions": suggestions,
            "text": actuals.summary(cmp, props, finished=finished)}


@router.post("/{tenant_id}/admin/estimates/{estimate_id}/learn")
async def estimate_learn(tenant_id: str, estimate_id: str, request: Request) -> dict:
    _on(tenant_id)
    from vula.commerce import actuals
    person, lead = await _caller(tenant_id, request)
    try:
        res = await actuals.learn(tenant_id, estimate_id, person=person, full_access=lead, notify=False)
    except actuals.ActualsError as exc:
        raise _actuals_err(exc)
    return res


@router.get("/{tenant_id}/admin/buildup-suggestions")
async def buildup_suggestions(tenant_id: str) -> dict:
    _on(tenant_id)
    from vula.commerce import actuals
    return {"suggestions": await asyncio.to_thread(actuals.list_suggestions, tenant_id)}


@router.post("/{tenant_id}/admin/buildup-suggestions/{suggestion_id}/{decision}")
async def decide_suggestion(tenant_id: str, suggestion_id: str, decision: str, request: Request) -> dict:
    _on(tenant_id)
    from vula.commerce import actuals, buildups
    if decision not in ("accept", "dismiss"):
        raise HTTPException(status_code=404, detail="Not found.")
    person, lead = await _caller(tenant_id, request)
    try:
        if decision == "accept":
            b = await asyncio.to_thread(actuals.accept, tenant_id, suggestion_id, person=person, full_access=lead)
            return {"buildup": buildups.public(b),
                    "message": "Changed — Vula will use the new build-up for every estimate of this item."}
        ok = await asyncio.to_thread(actuals.dismiss, tenant_id, suggestion_id, person=person, full_access=lead)
        return {"dismissed": ok}
    except actuals.ActualsError as exc:
        raise _actuals_err(exc)

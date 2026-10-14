"""
vula/api/manufacturing.py — the dashboard's Costing tab (Manufacturing Core phases 2–3).

Same flows as WhatsApp (vula/commerce/manufacturing.py), so the owner can set up materials,
recipes and batches in either place. Every route is under /v1/commerce/{tenant}/admin/… and so
behind the tenant guard (server.py _TENANT_GUARD_RES). Changes use confirm=true (the dashboard's
own confirm step) and are read back; arithmetic is vula/commerce/costing.py's.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from vula.commerce import costing
from vula.commerce import manufacturing as mfg
from vula.commerce.costing import CostingError

log = logging.getLogger(__name__)
router = APIRouter()


def _actor(request: Request) -> str:
    try:
        from vula.api.commerce import _actor as commerce_actor
        return commerce_actor(request)
    except Exception:
        return "dashboard"


def _done(res: Dict[str, Any]) -> Dict[str, Any]:
    if res.get("error"):
        raise HTTPException(status_code=400, detail=res["error"])
    if res.get("status") == "need_info":
        raise HTTPException(status_code=422, detail=res.get("message"))
    return {**res, "message": res.get("reply_verbatim") or res.get("message")}


def _jsonable(v: Any) -> Any:
    from decimal import Decimal
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


@router.get("/{tenant_id}/admin/mfg/overview")
async def mfg_overview(tenant_id: str):
    """Materials, recipes (with cost per unit and margin), settings and recent batches."""
    from vula.commerce import service
    try:
        mats = costing.list_materials(tenant_id)
    except Exception as exc:
        log.debug("costing unavailable for %s (run migration 211?): %s", tenant_id, exc)
        return {"ready": False, "materials": [], "recipes": [], "runs": [], "settings": {}}
    boms = (costing._db().table("commerce_boms").select("id,product_id,material_id,version")
            .eq("tenant_id", tenant_id).eq("status", "active").execute().data or [])
    products = {str(p["id"]): p for p in await service.list_products(tenant_id, in_stock_only=False)}
    recipes = []
    for b in boms:
        p = products.get(str(b.get("product_id")))
        if p:
            recipes.append(costing.cost_sheet(tenant_id, p))
    runs = (costing._db().table("commerce_production_runs").select("*").eq("tenant_id", tenant_id)
            .order("created_at", desc=True).limit(20).execute().data or [])
    for r in runs:
        r["product"] = (products.get(str(r.get("product_id"))) or {}).get("name")
    return _jsonable({"ready": True, "materials": mats, "recipes": recipes, "runs": runs,
                      "settings": costing.settings_for(tenant_id), "units": list(costing.BASE_UNITS),
                      "products": [{"id": p["id"], "name": p["name"]} for p in products.values()
                                   if not p.get("archived")]})


@router.put("/{tenant_id}/admin/mfg/settings")
async def mfg_settings(tenant_id: str, body: dict, request: Request):
    b = body or {}
    return _done(mfg.update_settings(tenant_id, labour_rate_cents=b.get("labour_rate_cents"),
                                     overhead_rule=b.get("overhead_rule"),
                                     overhead_rate_cents=b.get("overhead_rate_cents"),
                                     actor=_actor(request), confirm=True))


@router.post("/{tenant_id}/admin/mfg/materials")
async def mfg_add_material(tenant_id: str, body: dict, request: Request):
    b = body or {}
    try:
        return _done(mfg.add_material(tenant_id, b.get("name") or "", b.get("unit") or "each",
                                      pack_size=b.get("pack_size"), pack_unit=b.get("pack_unit"),
                                      opening_qty=b.get("opening_qty"),
                                      opening_total_cents=b.get("opening_total_cents"),
                                      supplier=b.get("supplier"), actor=_actor(request), confirm=True))
    except CostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/mfg/materials/receive")
async def mfg_receive(tenant_id: str, body: dict, request: Request):
    b = body or {}
    try:
        return _done(mfg.receive(tenant_id, b.get("material") or "", b.get("qty"), b.get("unit") or "",
                                 b.get("total_cents"), actor=_actor(request), ref_type="dashboard",
                                 ref_id=b.get("reference"), confirm=True))
    except CostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{tenant_id}/admin/mfg/materials/{material_id}/movements")
async def mfg_material_movements(tenant_id: str, material_id: str):
    rows = (costing._db().table("commerce_material_movements").select("*").eq("tenant_id", tenant_id)
            .eq("material_id", material_id).order("created_at", desc=True).limit(50).execute().data or [])
    return {"movements": rows}


@router.post("/{tenant_id}/admin/mfg/recipes")
async def mfg_save_recipe(tenant_id: str, body: dict, request: Request):
    """{product_id, makes, labour_hours, ingredients: [{material, qty, unit, waste_pct}]}"""
    b = body or {}
    try:
        res = await mfg.set_recipe(tenant_id, "", b.get("ingredients") or [], makes=b.get("makes") or 1,
                                   labour_hours=b.get("labour_hours") or 0, product_id=b.get("product_id"),
                                   actor=_actor(request), confirm=True)
    except CostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _done(res)


@router.get("/{tenant_id}/admin/mfg/recipes/{product_id}")
async def mfg_recipe(tenant_id: str, product_id: str):
    found = await mfg.find_product(tenant_id, product_id=product_id)
    if not found.get("product"):
        raise HTTPException(status_code=404, detail="Product not found")
    bom = costing.active_bom(tenant_id, product_id=product_id)
    mats = costing.materials_by_id(tenant_id)
    sheet = costing.cost_sheet(tenant_id, found["product"])
    if bom:
        bom["lines"] = [{**ln, "material": (mats.get(str(ln["material_id"])) or {}).get("name")} for ln in bom["lines"]]
    return _jsonable({"recipe": bom, "sheet": sheet})


@router.post("/{tenant_id}/admin/mfg/batches/preview")
async def mfg_batch_preview(tenant_id: str, body: dict):
    b = body or {}
    try:
        return _done(await mfg.log_batch(tenant_id, "", good=b.get("good"), rejects=b.get("rejects") or 0,
                                         labour_hours=b.get("labour_hours"), product_id=b.get("product_id"),
                                         confirm=False))
    except CostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/mfg/batches")
async def mfg_log_batch(tenant_id: str, body: dict, request: Request):
    b = body or {}
    try:
        return _done(await mfg.log_batch(tenant_id, "", good=b.get("good"), rejects=b.get("rejects") or 0,
                                         labour_hours=b.get("labour_hours"), product_id=b.get("product_id"),
                                         actor=_actor(request), note=b.get("note"), confirm=True))
    except CostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{tenant_id}/admin/mfg/batches/{run_id}/reverse")
async def mfg_reverse_batch(tenant_id: str, run_id: str, request: Request):
    res = costing.reverse_run(tenant_id, run_id, actor=_actor(request))
    back = costing.get_run(tenant_id, run_id) or {}
    if res.get("error") or back.get("status") != "reversed":
        raise HTTPException(status_code=400, detail="That batch couldn't be reversed "
                                                    f"({res.get('error') or back.get('status')}).")
    return {"reversed": run_id, "message": "Batch reversed: materials are back in stock and the units are off."}


# ── Starting from history: prices from past invoices, labour/running costs from the bank ───

def _material(tenant_id: str, material_id: str) -> Dict[str, Any]:
    m = costing.materials_by_id(tenant_id).get(str(material_id))
    if not m:
        raise HTTPException(status_code=404, detail="Material not found")
    return m


@router.get("/{tenant_id}/admin/mfg/materials/{material_id}/suggest")
async def mfg_price_suggest(tenant_id: str, material_id: str):
    """What past supplier invoices say this material costs — for the owner to pick from."""
    from vula.commerce import costing_history as hist
    m = _material(tenant_id, material_id)
    sug = hist.price_suggestions(tenant_id, m["name"], material=m)
    return _jsonable({"material": m["name"], "suggestions": [
        {**s, "line": hist.suggestion_line(s), "per_unit": hist.show_unit_cost(s["unit_cost"], s["base_unit"])}
        for s in sug]})


@router.post("/{tenant_id}/admin/mfg/materials/{material_id}/price")
async def mfg_price_set(tenant_id: str, material_id: str, body: dict, request: Request):
    """{pick} — the n-th suggestion (1 = best) — or {total_cents, qty, unit}: what the owner pays."""
    b = body or {}
    m = _material(tenant_id, material_id)
    try:
        return _done(mfg.price_material(tenant_id, m["name"], pick=b.get("pick"), total_cents=b.get("total_cents"),
                                        qty=b.get("qty"), unit=b.get("unit"), actor=_actor(request), confirm=True))
    except CostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{tenant_id}/admin/mfg/settings/suggest")
async def mfg_settings_suggest(tenant_id: str, hours_per_week: float | None = None,
                               batches_per_month: float | None = None, units_per_month: float | None = None):
    """Labour and running costs a month from the bank (lines not on a project, last 90 days), and
    what they come to per hour / batch / unit for the counts the owner gives."""
    from vula.commerce import costing_history as hist
    b = hist.bank_suggestions(tenant_id)
    hpm = hist.hours_per_month(hours_per_week) if hours_per_week else None
    return _jsonable({
        **b, "overhead_by_account": [{"label": k, "monthly_cents": v} for k, v in b["overhead_by_account"]],
        "hours_per_month": hpm,
        "labour_rate_cents": hist.per(b["labour_monthly_cents"], hpm),
        "overhead_per_hour_cents": hist.per(b["overhead_monthly_cents"], hpm),
        "overhead_per_batch_cents": hist.per(b["overhead_monthly_cents"], batches_per_month),
        "overhead_per_unit_cents": hist.per(b["overhead_monthly_cents"], units_per_month),
    })

"""
vula/commerce/stock_counts.py — stock-takes counted with the phone camera (migration 183).

A count is a session: staff scan items into it (stock_count_scan RPC — atomic, and a repeated
scan_id from an offline phone's retry is ignored), the owner or a manager reviews counted
against what the system holds, then applies it. Apply posts one 'count' movement per line
through service.adjust_stock (migration 182). Nothing touches stock before apply.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from vula.commerce import service

log = logging.getLogger(__name__)


class CountError(ValueError):
    """A count that can't take this action (not found / not open / already applied)."""


def _db():
    return service._client()


async def create_count(tenant_id: str, note: Optional[str] = None, actor: Optional[str] = None) -> dict:
    row = {"tenant_id": tenant_id, "status": "open", "note": (note or "").strip()[:200] or None,
           "started_by": actor}
    res = _db().table("commerce_stock_counts").insert(row).execute()
    return (res.data or [row])[0]


async def list_counts(tenant_id: str, limit: int = 20) -> List[dict]:
    return (_db().table("commerce_stock_counts").select("*").eq("tenant_id", tenant_id)
            .order("created_at", desc=True).limit(limit).execute().data or [])


def _get(tenant_id: str, count_id: str) -> dict:
    rows = (_db().table("commerce_stock_counts").select("*").eq("tenant_id", tenant_id)
            .eq("id", count_id).limit(1).execute().data or [])
    if not rows:
        raise CountError("count not found")
    return rows[0]


async def record_scans(tenant_id: str, count_id: str, scans: List[dict],
                       actor: Optional[str] = None) -> List[dict]:
    """Each scan: {scan_id, product_id, variant_id?, add? | set?}. Returns [{scan_id, counted}]
    — counted None when the count is closed or the product isn't this tenant's."""
    out = []
    for s in scans[:500]:
        pid = s.get("product_id")
        if not pid:
            out.append({"scan_id": s.get("scan_id"), "counted": None})
            continue
        add = s.get("add")
        setv = s.get("set")
        res = _db().rpc("stock_count_scan", {
            "p_tenant_id": tenant_id, "p_count_id": count_id, "p_product_id": pid,
            "p_variant_id": s.get("variant_id") or None,
            "p_add": int(add) if add is not None else None,
            "p_set": int(setv) if setv is not None else None,
            "p_scan_id": str(s["scan_id"])[:80] if s.get("scan_id") else None,
            "p_actor": actor,
        }).execute()
        data = res.data
        if isinstance(data, list):
            data = data[0] if data else None
        out.append({"scan_id": s.get("scan_id"), "counted": int(data) if data is not None else None})
    return out


def _variance(counted: int, expected: Optional[int], cost_cents: Optional[int]) -> dict:
    diff = counted - (expected or 0)
    return {"variance": diff,
            "variance_cents": diff * int(cost_cents) if cost_cents is not None else None}


async def review(tenant_id: str, count_id: str) -> dict:
    """The count with every line: counted vs what the system holds now, the difference, and its
    value at cost where a cost is known. Also how many tracked products weren't counted."""
    count = _get(tenant_id, count_id)
    lines = (_db().table("commerce_stock_count_lines").select("*").eq("tenant_id", tenant_id)
             .eq("count_id", count_id).execute().data or [])
    products = {p["id"]: p for p in await service.list_products(
        tenant_id, in_stock_only=False, include_archived=True)}
    variant_ids = [ln["variant_id"] for ln in lines if ln.get("variant_id")]
    variants = {}
    if variant_ids:
        variants = {v["id"]: v for v in (_db().table("commerce_product_variants").select("*")
                                         .eq("tenant_id", tenant_id).in_("id", variant_ids)
                                         .execute().data or [])}
    out, total_cents, unknown_cost = [], 0, 0
    for ln in lines:
        p = products.get(ln["product_id"]) or {}
        v = variants.get(ln.get("variant_id")) if ln.get("variant_id") else None
        src = v or p
        expected = ln.get("expected") if count["status"] == "applied" else src.get("stock_quantity")
        cost = src.get("cost_cents") if src.get("cost_cents") is not None else p.get("cost_cents")
        var = _variance(int(ln["counted"]), expected, cost)
        if var["variance"] and var["variance_cents"] is None:
            unknown_cost += 1
        total_cents += var["variance_cents"] or 0
        label = p.get("name") or "Unknown product"
        if v:
            opts = v.get("option_values") or {}
            label += " — " + (" / ".join(str(x) for x in opts.values()) if isinstance(opts, dict) else str(opts))
        out.append({"product_id": ln["product_id"], "variant_id": ln.get("variant_id"), "name": label,
                    "counted": int(ln["counted"]), "expected": expected, "counted_by": ln.get("counted_by"),
                    "cost_cents": cost, **var})
    out.sort(key=lambda r: (-abs(r["variance"]), r["name"]))
    counted_ids = {ln["product_id"] for ln in lines}
    uncounted = [{"product_id": p["id"], "name": p.get("name"), "expected": p.get("stock_quantity")}
                 for p in products.values()
                 if p.get("stock_quantity") is not None and not p.get("archived") and p["id"] not in counted_ids]
    return {"count": count, "lines": out, "variance_cents": total_cents,
            "lines_without_cost": unknown_cost, "uncounted": uncounted}


async def apply(tenant_id: str, count_id: str, actor: Optional[str] = None) -> dict:
    """Set every counted item's stock to its count, once. The status flips open → applied
    first (conditionally), so two owners pressing Apply together can't both post movements."""
    _get(tenant_id, count_id)
    flipped = (_db().table("commerce_stock_counts")
               .update({"status": "applied", "applied_by": actor, "applied_at": service._now()})
               .eq("tenant_id", tenant_id).eq("id", count_id).eq("status", "open").execute().data or [])
    if not flipped:
        raise CountError("This count has already been applied or cancelled.")
    lines = (_db().table("commerce_stock_count_lines").select("*").eq("tenant_id", tenant_id)
             .eq("count_id", count_id).execute().data or [])
    applied, failed = 0, []
    for ln in lines:
        try:
            before = await _current_qty(tenant_id, ln["product_id"], ln.get("variant_id"))
            after = await service.adjust_stock(
                tenant_id, ln["product_id"], variant_id=ln.get("variant_id"), set_to=int(ln["counted"]),
                reason="count", ref_type="stock_count", ref_id=count_id, actor=actor)
            if after is None:
                failed.append(ln["product_id"])
                continue
            _db().table("commerce_stock_count_lines").update({"expected": before}) \
                .eq("id", ln["id"]).execute()
            applied += 1
        except Exception as exc:
            log.warning("stock count %s line %s failed: %s", count_id, ln.get("product_id"), exc)
            failed.append(ln["product_id"])
    return {"applied": applied, "failed": failed}


async def _current_qty(tenant_id: str, product_id: str, variant_id: Optional[str]) -> Optional[int]:
    table = "commerce_product_variants" if variant_id else "commerce_products"
    rows = (_db().table(table).select("stock_quantity").eq("tenant_id", tenant_id)
            .eq("id", variant_id or product_id).limit(1).execute().data or [])
    return rows[0].get("stock_quantity") if rows else None


async def cancel(tenant_id: str, count_id: str) -> None:
    _get(tenant_id, count_id)
    done = (_db().table("commerce_stock_counts").update({"status": "cancelled"})
            .eq("tenant_id", tenant_id).eq("id", count_id).eq("status", "open").execute().data or [])
    if not done:
        raise CountError("Only an open count can be cancelled.")

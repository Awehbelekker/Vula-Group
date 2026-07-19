"""
vula/commerce/automations.py — a lean trigger → action rules engine (P3).

Deliberately narrow v1: two triggers (an order reaches a chosen status / a product hits its
reorder threshold — reusing the P3.3 fields) and two actions (WhatsApp the customer / WhatsApp
the team helper). Evaluated by a poller against existing tables rather than hooked into every
order/stock write path — much lower risk, same pattern as the campaign/subscription pollers.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

TRIGGER_TYPES = {"order_status", "low_stock"}
ACTION_TYPES = {"whatsapp_customer", "whatsapp_team"}


def _client():
    from vula.commerce import service as cs
    return cs._client()


def list_automations(tenant_id: str) -> list[dict]:
    try:
        return (_client().table("commerce_automations").select("*")
                .eq("tenant_id", tenant_id).order("created_at", desc=True).execute().data or [])
    except Exception as exc:
        log.debug("automations list skipped (run migration 079?): %s", exc)
        return []


def create_automation(tenant_id: str, body: dict) -> dict:
    trigger_type = body.get("trigger_type")
    action_type = body.get("action_type")
    if trigger_type not in TRIGGER_TYPES:
        raise ValueError(f"trigger_type must be one of {sorted(TRIGGER_TYPES)}")
    if action_type not in ACTION_TYPES:
        raise ValueError(f"action_type must be one of {sorted(ACTION_TYPES)}")
    if action_type == "whatsapp_customer" and trigger_type != "order_status":
        raise ValueError("whatsapp_customer only applies to an order_status trigger (no customer on a low_stock event)")
    row = {
        "tenant_id": tenant_id, "name": body.get("name") or f"{trigger_type} → {action_type}",
        "trigger_type": trigger_type, "trigger_config": body.get("trigger_config") or {},
        "action_type": action_type, "action_config": body.get("action_config") or {},
        "enabled": bool(body.get("enabled", True)),
    }
    try:
        res = _client().table("commerce_automations").insert(row).execute()
    except Exception as exc:
        log.warning("automation create failed (run migration 079?): %s", exc)
        return {"error": "Automations aren't set up yet on this server (run migration 079)."}
    return res.data[0] if res.data else {"error": "insert failed"}


def update_automation(tenant_id: str, automation_id: str, patch: dict) -> dict:
    allowed = {"name", "trigger_config", "action_config", "enabled"}
    upd = {k: v for k, v in patch.items() if k in allowed}
    if not upd:
        return {}
    res = (_client().table("commerce_automations").update(upd)
           .eq("tenant_id", tenant_id).eq("id", automation_id).execute())
    return res.data[0] if res.data else {}


def delete_automation(tenant_id: str, automation_id: str) -> None:
    _client().table("commerce_automations").delete() \
        .eq("tenant_id", tenant_id).eq("id", automation_id).execute()


def _already_fired(automation_id: str, entity_key: str) -> bool:
    try:
        rows = (_client().table("commerce_automation_log").select("id")
                .eq("automation_id", automation_id).eq("entity_key", entity_key).limit(1).execute().data or [])
        return bool(rows)
    except Exception:
        return False


def _mark_fired(automation_id: str, entity_key: str) -> None:
    try:
        _client().table("commerce_automation_log").insert(
            {"automation_id": automation_id, "entity_key": entity_key}).execute()
    except Exception as exc:
        log.debug("automation log write skipped (dup or migration 079?): %s", exc)


def _fill(template: str, ctx: dict) -> str:
    def sub(m):
        return str(ctx.get(m.group(1), m.group(0)))
    return re.sub(r"\{\{(\w+)\}\}", sub, template or "")


async def _run_action(tenant_id: str, automation: dict, ctx: dict) -> bool:
    from vula.api.whatsapp import _send_reply
    action_type = automation["action_type"]
    message = _fill((automation.get("action_config") or {}).get("message") or "", ctx)
    if not message:
        return False
    if action_type == "whatsapp_customer":
        phone = ctx.get("customer_phone")
        if not phone:
            return False
        return await _send_reply(phone, message, tenant_id=tenant_id)
    if action_type == "whatsapp_team":
        from vula.escalation import _pick_helper
        helper = _pick_helper(tenant_id)
        if not helper or not helper.get("whatsapp"):
            return False
        return await _send_reply(helper["whatsapp"], message, tenant_id=tenant_id)
    return False


async def _check_order_status(tenant_id: str, automation: dict) -> int:
    to_status = (automation.get("trigger_config") or {}).get("to_status")
    if not to_status:
        return 0
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    try:
        orders = (_client().table("commerce_orders").select("*")
                  .eq("tenant_id", tenant_id).eq("status", to_status)
                  .gte("updated_at", since).limit(100).execute().data or [])
    except Exception as exc:
        log.debug("order_status automation check skipped: %s", exc)
        return 0
    fired = 0
    for o in orders:
        key = o["id"]
        if _already_fired(automation["id"], key):
            continue
        ctx = {
            "order_id": o.get("display_id") or o.get("id"), "customer_name": o.get("customer_name") or "there",
            "customer_phone": o.get("customer_phone"), "status": to_status,
        }
        if await _run_action(tenant_id, automation, ctx):
            fired += 1
        _mark_fired(automation["id"], key)
    return fired


async def _check_low_stock(tenant_id: str, automation: dict) -> int:
    from vula.commerce import service
    try:
        products = await service.list_products(tenant_id, in_stock_only=False, include_archived=False)
    except Exception as exc:
        log.debug("low_stock automation check skipped: %s", exc)
        return 0

    # Variant-aware (migration 087): a product with variants can't be judged by its own
    # reorder_threshold (Size L might be out while Size M has 50 units) — check each variant's
    # own threshold instead, and only fall back to the product-level fields when it has none.
    low = []
    for p in products:
        variants = await service.list_variants(tenant_id, p["id"], include_archived=False)
        if variants:
            for v in variants:
                if v.get("reorder_threshold") is None:
                    continue
                if (v.get("stock_quantity") or 0) <= v["reorder_threshold"]:
                    opts = v.get("option_values") or {}
                    suffix = ", ".join(f"{k}: {val}" for k, val in opts.items())
                    low.append({
                        "key": v["id"], "name": f"{p.get('name')} ({suffix})" if suffix else p.get("name"),
                        "stock": v.get("stock_quantity") or 0, "threshold": v["reorder_threshold"],
                    })
        elif (p.get("reorder_threshold") is not None
              and (p.get("stock_quantity") or 0) <= p["reorder_threshold"]):
            low.append({"key": p["id"], "name": p.get("name"),
                       "stock": p.get("stock_quantity") or 0, "threshold": p["reorder_threshold"]})

    from datetime import date
    fired = 0
    for item in low:
        key = f"{item['key']}:{date.today().isoformat()}"   # once per product/variant per day
        if _already_fired(automation["id"], key):
            continue
        ctx = {"product_name": item["name"], "stock": item["stock"], "threshold": item["threshold"]}
        if await _run_action(tenant_id, automation, ctx):
            fired += 1
        _mark_fired(automation["id"], key)
    return fired


async def process_due_automations() -> int:
    """Evaluate every enabled automation across all tenants. Called by the scheduler loop."""
    try:
        rows = (_client().table("commerce_automations").select("*")
                .eq("enabled", True).limit(500).execute().data or [])
    except Exception as exc:
        log.debug("automations poll skipped (run migration 079?): %s", exc)
        return 0
    total = 0
    for automation in rows:
        tenant_id = automation["tenant_id"]
        try:
            if automation["trigger_type"] == "order_status":
                n = await _check_order_status(tenant_id, automation)
            elif automation["trigger_type"] == "low_stock":
                n = await _check_low_stock(tenant_id, automation)
            else:
                n = 0
            if n:
                total += n
                from vula.commerce import service
                _client().table("commerce_automations").update({
                    "last_fired_at": service._now(), "fire_count": (automation.get("fire_count") or 0) + n,
                }).eq("id", automation["id"]).execute()
        except Exception as exc:
            log.warning("automation %s failed: %s", automation.get("id"), exc)
    return total

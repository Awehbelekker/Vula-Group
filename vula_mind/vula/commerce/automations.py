"""
vula/commerce/automations.py — a lean trigger → action rules engine (P3).

Deliberately narrow v1: two triggers (an order reaches a chosen status / a product hits its
reorder threshold — reusing the P3.3 fields) and two actions (WhatsApp the customer / WhatsApp
the team helper). Evaluated by a poller against existing tables rather than hooked into every
order/stock write path — much lower risk, same pattern as the campaign/subscription pollers.

2026-08: two additions, both from the Warmwind-roadmap "teaching mode" step —
  1. Approval gate (migration 137, commerce_automation_firings): a match used to fire its
     WhatsApp action immediately — the one place in this platform where a trigger acted on a
     customer with no human in the loop. A match now STAGES a pending firing instead; the owner
     approves/rejects from the dashboard. Same propose-confirm posture as everything else this
     platform automates.
  2. parse_rule_from_text(): conversational rule authoring — an owner describes a rule in plain
     language ("when an order is dispatched, message the customer") and an LLM call maps it onto
     this SAME small, already-validated trigger/action vocabulary (constrained classification,
     not open generation) — never a new trigger/action type the engine doesn't already support.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

TRIGGER_TYPES = {"order_status", "low_stock"}
ACTION_TYPES = {"whatsapp_customer", "whatsapp_team"}
ORDER_STATUSES = {"paid", "confirmed", "packing", "dispatched", "delivered", "cancelled"}


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
        "created_from": body.get("created_from") or "dashboard",
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
    """Actually send the WhatsApp message. Only ever called from approve_firing() now — a
    trigger match no longer reaches this directly, it stages a pending firing instead (see
    _stage_firing below)."""
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


def _stage_firing(tenant_id: str, automation: dict, ctx: dict) -> bool:
    """A trigger matched — stage it as a pending firing instead of sending anything. The owner
    reviews and approves/rejects from the dashboard (list_pending_firings/approve_firing/
    reject_firing below). Returns False (no message ready to stage) if the template renders
    empty, same guard _run_action always had."""
    message = _fill((automation.get("action_config") or {}).get("message") or "", ctx)
    if not message:
        return False
    try:
        _client().table("commerce_automation_firings").insert({
            "tenant_id": tenant_id, "automation_id": automation["id"], "trigger_context": ctx,
            "action_type": automation["action_type"],
            "action_config": automation.get("action_config") or {}, "message": message,
        }).execute()
        return True
    except Exception as exc:
        log.warning("could not stage automation firing for %s (run migration 137?): %s",
                    automation.get("id"), exc)
        return False


def list_pending_firings(tenant_id: str) -> list[dict]:
    try:
        return (_client().table("commerce_automation_firings").select("*")
                .eq("tenant_id", tenant_id).eq("status", "pending")
                .order("created_at", desc=True).limit(200).execute().data or [])
    except Exception as exc:
        log.debug("pending firings list skipped (run migration 137?): %s", exc)
        return []


async def approve_firing(tenant_id: str, firing_id: str) -> dict:
    """Approve a staged firing: actually send its message, then mark it approved. Idempotent —
    approving an already-decided firing is a no-op, not a double-send."""
    from vula.commerce import service
    rows = (_client().table("commerce_automation_firings").select("*")
            .eq("tenant_id", tenant_id).eq("id", firing_id).limit(1).execute().data or [])
    if not rows:
        return {"error": "firing not found"}
    firing = rows[0]
    if firing["status"] != "pending":
        return firing  # already decided — no-op
    automation = {"action_type": firing["action_type"], "action_config": firing["action_config"]}
    sent = await _run_action(tenant_id, automation, firing.get("trigger_context") or {})
    upd = {"status": "approved" if sent else "rejected", "decided_at": service._now()}
    res = (_client().table("commerce_automation_firings").update(upd)
           .eq("tenant_id", tenant_id).eq("id", firing_id).execute())
    return res.data[0] if res.data else {**firing, **upd}


def reject_firing(tenant_id: str, firing_id: str) -> dict:
    from vula.commerce import service
    res = (_client().table("commerce_automation_firings")
           .update({"status": "rejected", "decided_at": service._now()})
           .eq("tenant_id", tenant_id).eq("id", firing_id).eq("status", "pending").execute())
    return res.data[0] if res.data else {}


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
        if _stage_firing(tenant_id, automation, ctx):
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
        if _stage_firing(tenant_id, automation, ctx):
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


# ── Conversational rule authoring ("teaching mode") ──────────────────────────────

_RULE_PARSE_PROMPT = (
    "A South African small-business owner just described an automation rule in their own "
    "words, over WhatsApp or chat. Map it onto EXACTLY this vocabulary — never invent a "
    "trigger, action, or status outside these lists:\n\n"
    "trigger_type: one of \"order_status\" (fires when an order reaches a chosen status) or "
    "\"low_stock\" (fires when a product's stock drops to/below its reorder threshold, "
    "configured separately in Products — this trigger takes no other config).\n"
    f"If trigger_type is order_status, trigger_config.to_status must be one of: "
    f"{sorted(ORDER_STATUSES)}.\n\n"
    "action_type: one of \"whatsapp_customer\" (message the customer on the order — only valid "
    "with trigger_type=order_status) or \"whatsapp_team\" (message the team helper).\n"
    "action_config.message: the message to send, written in the owner's own words if they gave "
    "one, otherwise write a short, sensible default. You may use {{order_id}}, "
    "{{customer_name}}, {{status}} placeholders for order_status, or {{product_name}}, "
    "{{stock}}, {{threshold}} for low_stock.\n\n"
    "If the request doesn't clearly map onto this vocabulary (e.g. it asks for a trigger/action "
    "this engine doesn't support), return {\"error\": \"<short plain-English reason>\"} instead.\n\n"
    "Return STRICT JSON only, no other text, in exactly this shape:\n"
    '{"name": "short label", "trigger_type": "...", "trigger_config": {...}, '
    '"action_type": "...", "action_config": {"message": "..."}}\n\n'
    "The owner's request:\n"
)


def _clean_json_response(raw: str) -> dict:
    import json
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _validate_parsed_rule(obj: dict) -> dict:
    """Whitelist-validate the LLM's proposed rule against the same fixed vocabulary
    create_automation already enforces — the LLM's output is never trusted as-is. Returns a
    clean body ready for create_automation(), or {"error": ...}."""
    if obj.get("error"):
        return {"error": str(obj["error"])[:200]}
    trigger_type = obj.get("trigger_type")
    action_type = obj.get("action_type")
    if trigger_type not in TRIGGER_TYPES:
        return {"error": f"I can only automate on: {sorted(TRIGGER_TYPES)}."}
    if action_type not in ACTION_TYPES:
        return {"error": f"I can only take these actions: {sorted(ACTION_TYPES)}."}
    if action_type == "whatsapp_customer" and trigger_type != "order_status":
        return {"error": "Messaging the customer only works with the order-status trigger."}

    trigger_config = {}
    if trigger_type == "order_status":
        to_status = (obj.get("trigger_config") or {}).get("to_status")
        if to_status not in ORDER_STATUSES:
            return {"error": f"Order status must be one of: {sorted(ORDER_STATUSES)}."}
        trigger_config = {"to_status": to_status}

    message = str((obj.get("action_config") or {}).get("message") or "").strip()
    if not message:
        return {"error": "I need a message to send for this rule."}

    return {
        "name": str(obj.get("name") or f"{trigger_type} → {action_type}")[:100],
        "trigger_type": trigger_type, "trigger_config": trigger_config,
        "action_type": action_type, "action_config": {"message": message[:500]},
    }


async def parse_rule_from_text(tenant_id: str, text: str) -> dict:
    """Turn a plain-language rule description into a created automation, via an LLM call
    constrained to this module's own TRIGGER_TYPES/ACTION_TYPES/ORDER_STATUSES vocabulary —
    the same whitelist create_automation() enforces, applied a second time here since the LLM's
    raw output is never trusted. Never auto-executes anything: the created rule inherits the
    same approval-gated firing path every automation goes through (see _stage_firing above).
    Returns the created automation row, or {"error": ...}."""
    text = (text or "").strip()
    if not text:
        return {"error": "Describe the rule you'd like, e.g. \"when an order is dispatched, "
                          "message the customer to say it's on its way\"."}

    import litellm
    from core.llm_router import resolve_generation_route
    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="automation_rule")
    try:
        resp = await litellm.acompletion(
            model=model, messages=[{"role": "user", "content": _RULE_PARSE_PROMPT + text}],
            temperature=0.2, max_tokens=400, api_key=api_key, api_base=api_base,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("automation rule parse failed: %s", exc)
        return {"error": "Could not understand that rule right now — please try again."}

    parsed = _clean_json_response(raw)
    if not parsed:
        return {"error": "Could not understand that rule — try describing it more simply."}
    validated = _validate_parsed_rule(parsed)
    if validated.get("error"):
        return validated
    validated["created_from"] = "conversation"
    return create_automation(tenant_id, validated)

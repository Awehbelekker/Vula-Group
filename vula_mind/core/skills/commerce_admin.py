"""
core/skills/commerce_admin.py — Owner/staff admin assistant skill.

The tenant-facing counterpart to commerce_assistant. Where commerce_assistant
helps *customers* shop, this lets the *owner* (e.g. Staci @ Off the Hook) run
the business conversationally over WhatsApp:

    "what were today's sales?"          → sales_summary
    "show me orders to dispatch"        → recent_orders
    "mark OTH-00042 dispatched"         → update_order_status
    "how's stock looking?"              → stock_status
    "set hake fillets to 20kg"          → update_stock
    "what invoices are outstanding?"    → outstanding_invoices
    "log R450 packaging from Boxshop"   → add_expense
    "who would the weekly special reach?" → preview_broadcast

Multi-tenant by design: every tool is scoped by inp.tenant_id, so the same
skill serves every tenant. Owner detection (which phone numbers may use it)
lives in the WhatsApp router, not here.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from config import settings
from core.llm_router import resolve_generation_route, escalate_to_cloud
from core.reasoning_telemetry import emit as _emit, log_tool_call as _log_tool_call
from core.skills.base import BaseSkill, SkillInput, SkillOutput
from vula.commerce import service

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 3

_PAID_STATUSES = {"paid", "confirmed", "packing", "dispatched", "delivered"}
_VALID_ORDER_STATUS = {"confirmed", "packing", "dispatched", "delivered", "cancelled", "refunded"}


def _readback_gate(tid: str, tool: str, ok: bool, expected: Dict[str, Any],
                   observed: Dict[str, Any]) -> None:
    """Record the deterministic 'verified done' outcome of a state-mutating admin tool: after
    the write, the caller re-read the row and checked the intended change actually persisted.
    POPIA: expected/observed carry ids, statuses and counts only — never customer contact data.
    `anchored` rides the verified-reasoning report's existing mapping, so a mismatch counts as
    a caught wrong claim of success."""
    try:
        from uuid import uuid4
        _emit(system="verified-reasoning", run_id=uuid4().hex[:8], task=tool,
              outcome="confirmed" if ok else "mismatch", escalated=not ok,
              verifier="gate.readback", tenant_id=tid,
              extra={"expected": expected, "observed": observed, "anchored": ok})
    except Exception:
        pass

TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "sales_summary",
        "description": "Revenue and order count for a period (today, week, or month).",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "enum": ["today", "week", "month"], "description": "Defaults to today."}}},
    }},
    {"type": "function", "function": {
        "name": "recent_orders",
        "description": "List recent orders, optionally filtered by status (e.g. paid, packing, dispatched).",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string"}, "limit": {"type": "integer"}}},
    }},
    {"type": "function", "function": {
        "name": "update_order_status",
        "description": "Update an order's fulfilment status by its display id (e.g. OTH-00042).",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string"},
            "status": {"type": "string", "enum": sorted(_VALID_ORDER_STATUS)}},
            "required": ["order_id", "status"]},
    }},
    {"type": "function", "function": {
        "name": "stock_status",
        "description": "Show product stock. Set low_only=true to show just low/out-of-stock items.",
        "parameters": {"type": "object", "properties": {"low_only": {"type": "boolean"}}},
    }},
    {"type": "function", "function": {
        "name": "update_stock",
        "description": "Set a product's stock quantity by name or slug.",
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string"}, "quantity": {"type": "integer"}},
            "required": ["product", "quantity"]},
    }},
    {"type": "function", "function": {
        "name": "outstanding_invoices",
        "description": "List unpaid/overdue invoices and the total amount owed.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "add_expense",
        "description": "Record a business expense in Rands.",
        "parameters": {"type": "object", "properties": {
            "amount_rands": {"type": "number"},
            "category": {"type": "string", "description": "stock, delivery, packaging, marketing, equipment, staff, rent, utilities, or other"},
            "description": {"type": "string"},
            "supplier": {"type": "string"}},
            "required": ["amount_rands", "description"]},
    }},
    {"type": "function", "function": {
        "name": "preview_broadcast",
        "description": "Preview how many customers a broadcast would reach (does NOT send).",
        "parameters": {"type": "object", "properties": {
            "audience": {"type": "string", "enum": ["all", "active_30d", "high_value"]}}},
    }},
    {"type": "function", "function": {
        "name": "finance_insights",
        "description": "Plain-language money read: revenue, expenses, profit/margin, VAT collected, who owes you. Answers 'am I profitable', 'what's my VAT'.",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "Look-back window, default 30."}}},
    }},
]

# ── Module-gated tools (added to the base set only for tenants with that module) ──
INVOICE_TOOLS = [
    {"type": "function", "function": {
        "name": "create_invoice",
        "description": "Create an invoice or quote for a customer. Give the customer name and one or more line items.",
        "parameters": {"type": "object", "properties": {
            "doc_type": {"type": "string", "enum": ["invoice", "quote"]},
            "customer_name": {"type": "string"},
            "customer_phone": {"type": "string"},
            "line_items": {"type": "array", "items": {"type": "object", "properties": {
                "description": {"type": "string"}, "quantity": {"type": "number"},
                "unit": {"type": "string"}, "unit_price_rands": {"type": "number"}}}},
            "discount_pct": {"type": "number"}, "deposit_rands": {"type": "number"}},
            "required": ["customer_name", "line_items"]}}},
    {"type": "function", "function": {
        "name": "send_invoice",
        "description": "Send an existing invoice to the customer on WhatsApp, by its number (e.g. OTH-INV-00001).",
        "parameters": {"type": "object", "properties": {"invoice_number": {"type": "string"}},
                       "required": ["invoice_number"]}}},
]
PRODUCT_TOOLS = [
    {"type": "function", "function": {
        "name": "create_product",
        "description": "Add a new product/service to the catalogue with a price in Rands.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "price_rands": {"type": "number"},
            "description": {"type": "string"}, "category": {"type": "string"}},
            "required": ["name", "price_rands"]}}},
    {"type": "function", "function": {
        "name": "update_product",
        "description": "Change a product's price, name, or daily-catch flag (find it by name).",
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string"}, "price_rands": {"type": "number"},
            "new_name": {"type": "string"}, "is_daily_catch": {"type": "boolean"}},
            "required": ["product"]}}},
]
BOOKING_TOOLS = [
    {"type": "function", "function": {
        "name": "booking_availability",
        "description": "Show free appointment slots for a date (YYYY-MM-DD).",
        "parameters": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}}},
    {"type": "function", "function": {
        "name": "list_bookings",
        "description": "List upcoming appointments, optionally by status.",
        "parameters": {"type": "object", "properties": {"status": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "create_booking",
        "description": "Book an appointment: start time (YYYY-MM-DDTHH:MM), customer name/phone, optional service.",
        "parameters": {"type": "object", "properties": {
            "start": {"type": "string"}, "customer_name": {"type": "string"},
            "customer_phone": {"type": "string"}, "service": {"type": "string"}},
            "required": ["start", "customer_name"]}}},
    {"type": "function", "function": {
        "name": "cancel_booking",
        "description": "Cancel an appointment by its id.",
        "parameters": {"type": "object", "properties": {"booking_id": {"type": "string"}}, "required": ["booking_id"]}}},
]
MARKETING_TOOLS = [
    {"type": "function", "function": {
        "name": "generate_marketing",
        "description": "Write marketing copy: today's specials post, a product description, promo, or broadcast text.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["specials", "product", "promo", "broadcast"]},
            "topic": {"type": "string"}, "tone": {"type": "string"}}}}},
]
SUBSCRIPTION_TOOLS = [
    {"type": "function", "function": {
        "name": "create_subscription",
        "description": "Set up a recurring/standing order for a customer (weekly/biweekly/monthly).",
        "parameters": {"type": "object", "properties": {
            "customer_name": {"type": "string"}, "customer_phone": {"type": "string"},
            "cadence": {"type": "string", "enum": ["weekly", "biweekly", "monthly"]},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "product": {"type": "string"}, "quantity": {"type": "number"}}}}},
            "required": ["customer_phone", "cadence", "items"]}}},
    {"type": "function", "function": {
        "name": "list_subscriptions",
        "description": "List active recurring orders.",
        "parameters": {"type": "object", "properties": {"status": {"type": "string"}}}}},
]
CRM_TOOLS = [
    {"type": "function", "function": {
        "name": "customer_lookup",
        "description": "Look up a customer by phone or name: lifetime value, orders, last seen, preferred language.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]
BROADCAST_TOOLS = [
    {"type": "function", "function": {
        "name": "send_broadcast",
        "description": ("Actually SEND a broadcast to an audience using an approved template. Destructive — only "
                        "call with confirm=true AFTER the owner has explicitly confirmed. Without confirm, it previews."),
        "parameters": {"type": "object", "properties": {
            "template_name": {"type": "string"}, "audience": {"type": "string", "enum": ["all", "active_30d", "high_value"]},
            "confirm": {"type": "boolean"}}, "required": ["template_name"]}}},
]

_GATED_GROUPS = [
    ("invoices", INVOICE_TOOLS), ("products", PRODUCT_TOOLS), ("bookings", BOOKING_TOOLS),
    ("orders", SUBSCRIPTION_TOOLS), ("crm", CRM_TOOLS), ("broadcasts", BROADCAST_TOOLS),
]
# Always available (universally useful, not tied to a business type): marketing copy.
_ALL_TOOL_SPECS = (TOOL_SPECS + INVOICE_TOOLS + PRODUCT_TOOLS + BOOKING_TOOLS
                   + MARKETING_TOOLS + SUBSCRIPTION_TOOLS + CRM_TOOLS + BROADCAST_TOOLS)


def _tools_for(tenant_id: str) -> List[Dict[str, Any]]:
    """Base tools + the gated groups this tenant's modules unlock (finance_insights is always on)."""
    try:
        from vula.api.tenants import enabled_modules
        mods = set(enabled_modules(tenant_id) or [])
    except Exception:
        mods = set()
    tools = list(TOOL_SPECS) + MARKETING_TOOLS   # marketing is always on
    show_all = not mods                       # no config yet → show everything
    for mod, group in _GATED_GROUPS:
        if show_all or mod in mods:
            tools += group
    return tools


class CommerceAdminSkill(BaseSkill):
    name = "commerce_admin"
    description = (
        "Owner/staff admin assistant — run the shop over WhatsApp: sales, orders, "
        "stock, invoices, expenses, and broadcast previews."
    )

    async def run(self, inp: SkillInput) -> SkillOutput:
        ctx = {"tenant_id": inp.tenant_id}
        tools = _tools_for(inp.tenant_id)
        system_msg = self._system_prompt(inp.tenant_id)
        try:
            answer = await self._agent_loop(system_msg, inp.conversation_history, inp.question, ctx, tools)
            if not answer:
                raise RuntimeError("empty answer from admin agent loop")
            return SkillOutput(answer=answer, skill_name=self.name, confidence=0.8)
        except Exception as exc:
            logger.warning("commerce_admin loop failed (%s)", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

    def _system_prompt(self, tenant_id: str) -> str:
        return (
            "You are the AI business assistant for the OWNER of a South African business "
            "(you are talking to the owner/staff, not a customer). Help them run the business with "
            "the tools available to you — which may include sales, orders, stock, invoices/quotes, "
            "expenses, products, bookings/appointments, marketing copy, financial insights, recurring "
            "orders, customers, and broadcasts. Only offer what your tools actually support; if you "
            "genuinely lack a tool for something, say so plainly. Use tools to read and change REAL "
            "data — never invent figures. Show money in ZAR (e.g. R1 250.00). Keep replies short and "
            "WhatsApp-friendly with the key numbers, and confirm back what you changed after any "
            "update.\n"
            "IMPORTANT — confirm before acting on anything that spends money, sends messages to "
            "customers, or can't be undone: creating/sending an invoice, sending a broadcast, "
            "cancelling/refunding. Show the details and wait for a clear 'yes' first. For send_broadcast, "
            "only pass confirm=true after the owner has explicitly confirmed."
        )

    # ── Agent loop (mirrors commerce_assistant) ──────────────────────────────
    async def _agent_loop(self, system_msg: str, history: str, question: str, ctx: Dict[str, Any],
                          tools: Optional[List[Dict[str, Any]]] = None) -> str:
        import litellm
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()
        # The admin agent has a rich toolset with nested arguments (invoices, subscriptions). Small
        # local models emit malformed tool args, so prefer the cloud model for reliable structured
        # tool-calling. Low volume (owner-only) → cost is fine. Falls back to local if no cloud key.
        esc = escalate_to_cloud("admin_agent_toolcalling", task_type="commerce_admin")
        if esc:
            model, api_key, api_base = esc
        tools = tools or TOOL_SPECS

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_msg}]
        if history:
            messages.append({"role": "user", "content": f"(Earlier conversation)\n{history}"})
        messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await litellm.acompletion(
                model=model, messages=messages, tools=tools, tool_choice="auto",
                temperature=0.2, max_tokens=900, api_key=api_key, api_base=api_base,
            )
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)

            # Some local models (e.g. Ollama gpt-oss) emit the call as JSON text
            # in content instead of using the tool_calls field. Parse + execute.
            if not tool_calls:
                inline = self._parse_inline_toolcall(msg.content or "")
                if inline:
                    name, args = inline
                    result = await self._dispatch_tool(name, args, ctx)
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content": (
                        f"[tool {name} returned]: {json.dumps(result, default=str)}\n"
                        "Reply to the owner in plain, short WhatsApp language using this data. "
                        "Do not output JSON or tool calls."
                    )})
                    continue
                return (msg.content or "").strip()

            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                               for tc in tool_calls],
            })
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                result = await self._dispatch_tool(tc.function.name, args, ctx)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "name": tc.function.name, "content": json.dumps(result, default=str)})

        # Final pass — force a plain-language answer (no tools available now).
        resp = await litellm.acompletion(
            model=model, messages=messages, temperature=0.2, max_tokens=600,
            api_key=api_key, api_base=api_base,
        )
        answer = (resp.choices[0].message.content or "").strip()

        # Safety net: if a weak model still returned a tool-call JSON, run it
        # once more and summarise so the owner never sees raw JSON.
        inline = self._parse_inline_toolcall(answer)
        if inline:
            name, args = inline
            result = await self._dispatch_tool(name, args, ctx)
            resp = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": "Summarise this data for a shop owner in short, plain WhatsApp language. No JSON."},
                    {"role": "user", "content": json.dumps(result, default=str)},
                ],
                temperature=0.2, max_tokens=400, api_key=api_key, api_base=api_base,
            )
            answer = (resp.choices[0].message.content or "").strip()
        return answer

    _TOOL_NAMES = {t["function"]["name"] for t in _ALL_TOOL_SPECS}

    def _parse_inline_toolcall(self, content: str):
        """Extract a tool call emitted as JSON text by models without native
        tool-calling. Accepts {"function"|"name": ..., "arguments"|"parameters": {}}.
        Returns (name, args) if it maps to a known tool, else None.
        """
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
        name = obj.get("function") or obj.get("name") or obj.get("tool")
        args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if name in self._TOOL_NAMES and isinstance(args, dict):
            return name, args
        return None

    # ── Tool dispatch ─────────────────────────────────────────────────────────
    async def _dispatch_tool(self, name: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
        tid = ctx["tenant_id"]
        _log_tool_call(tid, "admin", name, args)
        try:
            if name == "sales_summary":      return await self._sales_summary(tid, args.get("period", "today"))
            if name == "recent_orders":      return await self._recent_orders(tid, args.get("status"), args.get("limit", 10))
            if name == "update_order_status": return await self._update_order_status(tid, args.get("order_id", ""), args.get("status", ""))
            if name == "stock_status":       return await self._stock_status(tid, bool(args.get("low_only")))
            if name == "update_stock":       return await self._update_stock(tid, args.get("product", ""), args.get("quantity", 0))
            if name == "outstanding_invoices": return await self._outstanding_invoices(tid)
            if name == "add_expense":        return await self._add_expense(tid, args)
            if name == "preview_broadcast":  return await self._preview_broadcast(tid, args.get("audience", "all"))
            if name == "finance_insights":   return await self._finance_insights(tid, int(args.get("days") or 30))
            if name == "create_invoice":     return await self._create_invoice(tid, args)
            if name == "send_invoice":       return await self._send_invoice(tid, args.get("invoice_number", ""))
            if name == "create_product":     return await self._create_product(tid, args)
            if name == "update_product":     return await self._update_product(tid, args)
            if name == "booking_availability": return await self._booking_availability(tid, args.get("date", ""))
            if name == "list_bookings":      return await self._list_bookings(tid, args.get("status"))
            if name == "create_booking":     return await self._create_booking(tid, args)
            if name == "cancel_booking":     return await self._cancel_booking(tid, args.get("booking_id", ""))
            if name == "generate_marketing": return await self._generate_marketing(tid, args)
            if name == "create_subscription": return await self._create_subscription(tid, args)
            if name == "list_subscriptions": return await self._list_subscriptions(tid, args.get("status"))
            if name == "customer_lookup":    return await self._customer_lookup(tid, args.get("query", ""))
            if name == "send_broadcast":     return await self._send_broadcast(tid, args)
        except Exception as exc:
            logger.warning("admin tool %s failed: %s", name, exc)
            return {"error": str(exc)}
        return {"error": f"unknown tool {name}"}

    def _rands(self, c: int) -> str:
        return f"R{(c or 0) / 100:,.2f}"

    async def _sales_summary(self, tid: str, period: str) -> Dict[str, Any]:
        orders = await service.list_orders(tid, limit=500)
        now = datetime.now(timezone.utc)
        days = {"today": 1, "week": 7, "month": 30}.get(period, 1)
        cutoff = now - timedelta(days=days)
        if period == "today":
            cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        revenue = count = 0
        for o in orders:
            if o.get("status") not in _PAID_STATUSES:
                continue
            ca = o.get("created_at")
            try:
                dt = datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
            except Exception:
                continue
            if dt >= cutoff:
                revenue += int(o.get("total_cents") or 0)
                count += 1
        return {"period": period, "revenue": self._rands(revenue), "paid_orders": count}

    async def _recent_orders(self, tid: str, status: Optional[str], limit: int) -> Any:
        orders = await service.list_orders(tid, status=status, limit=min(int(limit or 10), 25))
        return [{"order": o.get("display_id"), "status": o.get("status"),
                 "total": self._rands(o.get("total_cents")), "customer": o.get("customer_name")}
                for o in orders] or {"message": "No orders found."}

    async def _update_order_status(self, tid: str, display_id: str, status: str) -> Dict[str, Any]:
        if status not in _VALID_ORDER_STATUS:
            return {"error": f"status must be one of {sorted(_VALID_ORDER_STATUS)}"}
        orders = await service.list_orders(tid, limit=200)
        match = next((o for o in orders if (o.get("display_id") or "").upper() == display_id.strip().upper()), None)
        if not match:
            return {"error": f"No order {display_id} found."}
        await service.update_order_status(match["id"], status)
        result = {"updated": match["display_id"], "new_status": status}
        if settings.readback_verify_enabled:
            row = await service.get_order(match["id"])
            observed = (row or {}).get("status")
            ok = observed == status
            _readback_gate(tid, "update_order_status", ok,
                           {"order": match["display_id"], "status": status}, {"status": observed})
            if not ok:
                return {"error": f"Update to {status} did not persist for {match['display_id']} — "
                                 f"current status is {observed or 'unknown'}. Not confirmed."}
            result["verified"] = True
        return result

    async def _stock_status(self, tid: str, low_only: bool) -> Any:
        products = await service.list_products(tid, in_stock_only=False)
        rows = []
        for p in products:
            qty = p.get("stock_quantity")
            low = (not p.get("in_stock")) or (qty is not None and qty <= 5)
            if low_only and not low:
                continue
            rows.append({"product": p["name"], "qty": qty, "in_stock": p.get("in_stock")})
        return rows or {"message": "All products well stocked." if low_only else "No products."}

    async def _update_stock(self, tid: str, product_name: str, quantity: int) -> Dict[str, Any]:
        name = (product_name or "").strip()
        prod = None
        if re.match(r"^[a-z0-9-]+$", name):
            prod = await service.get_product_by_slug(tid, name)
        if not prod:
            candidates = await service.list_products(tid, in_stock_only=False)
            prod = next((p for p in candidates if name.lower() in p["name"].lower()), None)
        if not prod:
            return {"error": f"No product matching '{name}'."}
        await service.update_product(tid, prod["id"], {"stock_quantity": int(quantity), "in_stock": int(quantity) > 0})
        result = {"updated": prod["name"], "stock_quantity": int(quantity)}
        if settings.readback_verify_enabled:
            p2 = await service.get_product(tid, prod["id"])
            observed = (p2 or {}).get("stock_quantity")
            ok = p2 is not None and observed is not None and int(observed) == int(quantity)
            _readback_gate(tid, "update_stock", ok,
                           {"product_id": prod["id"], "stock_quantity": int(quantity)},
                           {"stock_quantity": observed})
            if not ok:
                return {"error": f"Stock update for {prod['name']} did not persist — the product "
                                 f"still shows {observed if observed is not None else 'unknown'}. Not confirmed."}
            result["verified"] = True
        return result

    async def _outstanding_invoices(self, tid: str) -> Dict[str, Any]:
        owed = 0
        invoices = []
        for st in ("sent", "overdue", "draft"):
            for inv in await service.list_invoices(tid, status=st, limit=100):
                owed += int(inv.get("total_cents") or 0)
                invoices.append({"invoice": inv.get("invoice_number"), "status": inv.get("status"),
                                 "total": self._rands(inv.get("total_cents")), "customer": inv.get("customer_name")})
        return {"outstanding_total": self._rands(owed), "count": len(invoices), "invoices": invoices[:15]}

    async def _add_expense(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from uuid import uuid4
        cents = int(round(float(args.get("amount_rands", 0)) * 100))
        if cents <= 0:
            return {"error": "amount_rands must be positive"}
        row = {
            "id": str(uuid4()), "tenant_id": tid,
            "date": datetime.now(timezone.utc).date().isoformat(),
            "category": args.get("category") or "other",
            "description": args.get("description") or "Expense (WhatsApp)",
            "amount_cents": cents, "supplier": args.get("supplier"), "source": "whatsapp_admin",
        }
        service._client().table("commerce_expenses").insert(row).execute()
        return {"logged": self._rands(cents), "category": row["category"], "description": row["description"]}

    async def _preview_broadcast(self, tid: str, audience: str) -> Dict[str, Any]:
        from vula.api.commerce import _aggregate_customers, _filter_audience, _norm_phone
        customers = await _aggregate_customers(tid)
        rows = _filter_audience(customers, audience)
        reachable = [c for c in rows if _norm_phone(c.get("phone")).isdigit()]
        return {"audience": audience, "would_reach": len(reachable),
                "note": "Preview only. Send the broadcast from the dashboard Broadcast tab."}

    # ── New capability tools (gated per tenant) ───────────────────────────────
    async def _finance_insights(self, tid: str, days: int) -> Dict[str, Any]:
        from vula.commerce import finances
        data = await finances.insights(tid, days=days)
        summary = await finances.narrate(tid, data)
        return {"summary": summary, "revenue": self._rands(data["revenue_cents"]),
                "expenses": self._rands(data["expenses_cents"]),
                "gross_profit": self._rands(data["gross_profit_cents"]), "margin_pct": data["margin_pct"],
                "vat_collected": self._rands(data["vat"]["collected_cents"]),
                "owed_to_you": self._rands(data["receivables"]["outstanding_cents"])}

    async def _create_invoice(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        raw = args.get("line_items") or []
        line_items = [{
            "description": (it.get("description") or "").strip(),
            "quantity": float(it.get("quantity") or 1),
            "unit": (it.get("unit") or None),
            "unit_price_cents": int(round(float(it.get("unit_price_rands") or 0) * 100)),
        } for it in raw if isinstance(it, dict) and (it.get("description") or "").strip()]
        if not line_items:
            return {"error": "Need at least one line item with a description and unit_price_rands."}
        data = {
            "doc_type": args.get("doc_type", "invoice"),
            "customer_name": args.get("customer_name", "Customer"),
            "customer_phone": args.get("customer_phone"),
            "line_items": line_items,
            "discount_pct": float(args.get("discount_pct") or 0),
            "deposit_cents": int(round(float(args.get("deposit_rands") or 0) * 100)),
            "status": "draft",
        }
        inv = await service.create_invoice(tid, data)
        result = {"created": True, "invoice_number": inv.get("invoice_number"),
                  "total": self._rands(inv.get("total_cents")), "status": inv.get("status"),
                  "note": "Created as a draft. Say 'send it' to WhatsApp it to the customer."}
        if settings.readback_verify_enabled:
            inv2 = await service.get_invoice(tid, inv["id"]) if inv.get("id") else None
            ok = bool(inv2) and bool(inv2.get("invoice_number")) and inv2.get("status") == "draft"
            _readback_gate(tid, "create_invoice", ok,
                           {"status": "draft"},
                           {"found": bool(inv2), "status": (inv2 or {}).get("status")})
            if not ok:
                return {"error": "Invoice creation could not be confirmed — the draft did not "
                                 "appear on re-read. Not confirmed; please check the Invoices tab."}
            result["verified"] = True
        return result

    async def _send_invoice(self, tid: str, invoice_number: str) -> Dict[str, Any]:
        num = (invoice_number or "").strip()
        rows = (service._client().table("commerce_invoices").select("id,invoice_number,customer_phone")
                .eq("tenant_id", tid).eq("invoice_number", num).limit(1).execute().data or [])
        if not rows:
            return {"error": f"No invoice {num} found."}
        if not rows[0].get("customer_phone"):
            return {"error": f"Invoice {num} has no customer phone on file to send to."}
        from vula.api.commerce import admin_send_invoice_whatsapp
        await admin_send_invoice_whatsapp(tid, rows[0]["id"], {})
        return {"sent": True, "invoice_number": num}

    async def _create_product(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        name = (args.get("name") or "").strip()
        price = int(round(float(args.get("price_rands") or 0) * 100))
        if not name or price <= 0:
            return {"error": "Need a product name and a price in Rands."}
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "product"
        data = {"name": name, "slug": slug, "description": args.get("description") or name,
                "price_cents": price, "category": args.get("category") or "other", "in_stock": True}
        p = await service.create_product(tid, data)
        return {"created": p.get("name"), "price": self._rands(p.get("price_cents"))}

    async def _update_product(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        name = (args.get("product") or "").strip().lower()
        prods = await service.list_products(tid, in_stock_only=False)
        p = next((x for x in prods if name and name in (x.get("name") or "").lower()), None)
        if not p:
            return {"error": f"No product matching '{args.get('product')}'."}
        patch: Dict[str, Any] = {}
        if args.get("price_rands") is not None:
            patch["price_cents"] = int(round(float(args["price_rands"]) * 100))
        if args.get("new_name"):
            patch["name"] = args["new_name"]
        if args.get("is_daily_catch") is not None:
            patch["is_daily_catch"] = bool(args["is_daily_catch"])
        if not patch:
            return {"error": "Nothing to change (price_rands / new_name / is_daily_catch)."}
        await service.update_product(tid, p["id"], patch)
        out = {"updated": p["name"]}
        if "price_cents" in patch:
            out["new_price"] = self._rands(patch["price_cents"])
        return out

    async def _booking_availability(self, tid: str, date: str) -> Dict[str, Any]:
        from vula.bookings import service as bk
        try:
            r = await bk.availability(tid, (date or "").strip())
        except ValueError as exc:
            return {"error": str(exc)}
        if r.get("closed"):
            return {"date": r["date"], "message": "Closed that day."}
        return {"date": r["date"], "available_times": [s["label"] for s in r.get("slots", [])][:12]}

    async def _list_bookings(self, tid: str, status: Optional[str]) -> Dict[str, Any]:
        from vula.bookings import service as bk
        rows = await bk.list_bookings(tid, status=status or "confirmed", from_utc=bk._now_utc().isoformat())
        return {"count": len(rows), "bookings": [
            {"id": b["id"], "when": b.get("start_at"), "service": b.get("service_name"),
             "customer": b.get("customer_name")} for b in rows[:15]]}

    async def _create_booking(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from vula.bookings import service as bk
        svc_id, svc_name = None, (args.get("service") or "").strip()
        if svc_name:
            for s in await bk.list_services(tid):
                if svc_name.lower() in (s.get("name") or "").lower():
                    svc_id, svc_name = s["id"], s["name"]
                    break
        res = await bk.create_booking(tid, {
            "service_id": svc_id, "service_name": svc_name or None,
            "customer_name": args.get("customer_name"), "customer_phone": args.get("customer_phone"),
            "start": args.get("start"), "channel": "dashboard"})
        if res.get("error"):
            return res
        return {"booked": True, "when": res["booking"].get("start_local"), "service": res["booking"].get("service_name")}

    async def _cancel_booking(self, tid: str, booking_id: str) -> Dict[str, Any]:
        from vula.bookings import service as bk
        if not booking_id:
            return {"error": "Need the booking id (use list_bookings to find it)."}
        await bk.set_status(tid, booking_id, "cancelled")
        return {"cancelled": True, "booking_id": booking_id}

    async def _generate_marketing(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from vula.commerce import marketing
        res = await marketing.generate(tid, kind=args.get("kind", "specials"),
                                       topic=args.get("topic", ""), tone=args.get("tone", ""), variants=1)
        if res.get("error"):
            return res
        return {"copy": (res.get("variants") or [""])[0]}

    async def _create_subscription(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from vula.commerce import subscriptions as subs
        prods = await service.list_products(tid, in_stock_only=False)
        items = []
        for it in (args.get("items") or []):
            if not isinstance(it, dict):
                continue
            want = (it.get("product") or "").strip().lower()
            p = next((x for x in prods if want and want in (x.get("name") or "").lower()), None)
            if not p:
                return {"error": f"No product matching '{it.get('product')}'."}
            items.append({"product_id": p["id"], "product_name": p["name"],
                          "quantity": float(it.get("quantity") or 1), "unit_price_cents": p.get("price_cents") or 0})
        if not items:
            return {"error": "Need at least one product for the standing order."}
        res = await subs.create(tid, {"customer_name": args.get("customer_name"),
                                      "customer_phone": args.get("customer_phone"),
                                      "cadence": args.get("cadence", "weekly"), "items": items, "channel": "dashboard"})
        if res.get("error"):
            return res
        return {"created": True, "cadence": res["subscription"].get("cadence"), "next_run": res["subscription"].get("next_run")}

    async def _list_subscriptions(self, tid: str, status: Optional[str]) -> Dict[str, Any]:
        from vula.commerce import subscriptions as subs
        rows = await subs.list_subs(tid, status=status or "active")
        return {"count": len(rows), "subscriptions": [
            {"customer": s.get("customer_name") or s.get("customer_phone"), "cadence": s.get("cadence"),
             "next_run": s.get("next_run")} for s in rows[:15]]}

    async def _customer_lookup(self, tid: str, query: str) -> Dict[str, Any]:
        from vula.api.commerce import _aggregate_customers, _norm_phone
        q = (query or "").strip().lower()
        if not q:
            return {"error": "Give a name or phone to look up."}
        customers = await _aggregate_customers(tid)
        qd = _norm_phone(q)
        match = None
        for c in customers.values():
            if (qd and qd in _norm_phone(c.get("phone"))) or (q in (c.get("name") or "").lower()):
                match = c
                break
        if not match:
            return {"message": f"No customer found for '{query}'."}
        return {"name": match.get("name"), "phone": match.get("phone"),
                "orders": match.get("orders", 0), "lifetime_value": self._rands(match.get("total_spent_cents")),
                "last_seen": match.get("last_order_at") or match.get("last_seen_at")}

    async def _send_broadcast(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        audience = args.get("audience", "all")
        template = (args.get("template_name") or "").strip()
        if not template:
            return {"error": "Need the approved template_name to send."}
        if not args.get("confirm"):
            prev = await self._preview_broadcast(tid, audience)
            return {"preview": True, "would_reach": prev.get("would_reach"),
                    "message": "This will message customers. Confirm to send (call again with confirm=true)."}
        from vula.api.commerce import admin_send_broadcast
        res = await admin_send_broadcast(tid, {"template_name": template, "audience_filter": audience, "dry_run": False})
        result = {"sent": True, "result": res}
        if settings.readback_verify_enabled:
            row = None
            try:
                bid = res.get("broadcast_id") if isinstance(res, dict) else None
                if bid:
                    rows = (service._client().table("commerce_broadcast_logs")
                            .select("id,status,recipient_count,sent_count,failed_count")
                            .eq("id", bid).limit(1).execute().data or [])
                    row = rows[0] if rows else None
            except Exception as exc:
                logger.debug("broadcast read-back failed: %s", exc)
            ok = bool(row) and row.get("status") == "sent" and int(row.get("sent_count") or 0) >= 1
            _readback_gate(tid, "send_broadcast", ok,
                           {"recipient_count": (res or {}).get("recipient_count")},
                           {"status": (row or {}).get("status"),
                            "sent_count": (row or {}).get("sent_count"),
                            "failed_count": (row or {}).get("failed_count")})
            if not ok:
                return {"error": "Broadcast did not confirm as sent — the delivery log shows "
                                 f"status={(row or {}).get('status') or 'missing'}, "
                                 f"sent={(row or {}).get('sent_count') or 0}. Not confirmed."}
            result["verified"] = True
        return result

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

from core.llm_router import resolve_generation_route
from core.skills.base import BaseSkill, SkillInput, SkillOutput
from vula.commerce import service

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 3

_PAID_STATUSES = {"paid", "confirmed", "packing", "dispatched", "delivered"}
_VALID_ORDER_STATUS = {"confirmed", "packing", "dispatched", "delivered", "cancelled", "refunded"}

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
]


class CommerceAdminSkill(BaseSkill):
    name = "commerce_admin"
    description = (
        "Owner/staff admin assistant — run the shop over WhatsApp: sales, orders, "
        "stock, invoices, expenses, and broadcast previews."
    )

    async def run(self, inp: SkillInput) -> SkillOutput:
        ctx = {"tenant_id": inp.tenant_id}
        system_msg = self._system_prompt(inp.tenant_id)
        try:
            answer = await self._agent_loop(system_msg, inp.conversation_history, inp.question, ctx)
            if not answer:
                raise RuntimeError("empty answer from admin agent loop")
            return SkillOutput(answer=answer, skill_name=self.name, confidence=0.8)
        except Exception as exc:
            logger.warning("commerce_admin loop failed (%s)", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

    def _system_prompt(self, tenant_id: str) -> str:
        return (
            "You are the AI business assistant for the OWNER of a South African food "
            "business (you are talking to the owner/staff, not a customer). Help them run "
            "the shop: check sales, manage orders, stock, invoices and expenses, and preview "
            "broadcasts. Use the tools to read and update real data — never invent figures. "
            "Show money in ZAR (e.g. R1 250.00). Keep replies short and WhatsApp-friendly with "
            "the key numbers. Confirm back what you changed after any update. For broadcasts, "
            "only PREVIEW the audience and tell them to send it from the dashboard."
        )

    # ── Agent loop (mirrors commerce_assistant) ──────────────────────────────
    async def _agent_loop(self, system_msg: str, history: str, question: str, ctx: Dict[str, Any]) -> str:
        import litellm
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_msg}]
        if history:
            messages.append({"role": "user", "content": f"(Earlier conversation)\n{history}"})
        messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await litellm.acompletion(
                model=model, messages=messages, tools=TOOL_SPECS, tool_choice="auto",
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

    _TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}

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
        try:
            if name == "sales_summary":      return await self._sales_summary(tid, args.get("period", "today"))
            if name == "recent_orders":      return await self._recent_orders(tid, args.get("status"), args.get("limit", 10))
            if name == "update_order_status": return await self._update_order_status(tid, args.get("order_id", ""), args.get("status", ""))
            if name == "stock_status":       return await self._stock_status(tid, bool(args.get("low_only")))
            if name == "update_stock":       return await self._update_stock(tid, args.get("product", ""), args.get("quantity", 0))
            if name == "outstanding_invoices": return await self._outstanding_invoices(tid)
            if name == "add_expense":        return await self._add_expense(tid, args)
            if name == "preview_broadcast":  return await self._preview_broadcast(tid, args.get("audience", "all"))
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
        return {"updated": match["display_id"], "new_status": status}

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
        return {"updated": prod["name"], "stock_quantity": int(quantity)}

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

"""
core/skills/commerce_assistant.py — Conversational shopping assistant skill.

Ports the Awake-SA whatsapp-bot tool-calling agent into Vula's BaseSkill.
A tool-calling agent loop over the Vula Commerce service (products, cart,
orders), grounded with the tenant knowledge base (RAG) and multi-turn
conversation history. Falls back to a single grounded reply when the
local/cloud model does not support tool-calling.

Tenant isolation: every service call is scoped by inp.tenant_id. The cart
session is keyed on the customer's phone (metadata['session_id']).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from core.llm_router import resolve_generation_route
from core.skills.base import BaseSkill, SkillInput, SkillOutput
from vula.commerce import service

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 4

# OpenAI-style function specs — used by litellm for both Ollama and OpenRouter.
TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_products",
            "description": "List in-stock products, optionally filtered by category or a free-text search term.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Optional category filter."},
                    "search": {"type": "string", "description": "Optional free-text search over name/description."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_cart",
            "description": "Add a product to the customer's cart by name or slug.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "Product name or slug."},
                    "quantity": {"type": "integer", "description": "Quantity, default 1."},
                },
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_cart",
            "description": "Show the current cart contents, subtotal, delivery and total.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_checkout",
            "description": "Begin checkout — returns the secure checkout link for the customer's cart.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_order",
            "description": "Look up the status of an existing order by its display id (e.g. OTH-00042).",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "Order display id."}},
                "required": ["order_id"],
            },
        },
    },
]


class CommerceAssistantSkill(BaseSkill):
    name = "commerce_assistant"
    description = (
        "Conversational shopping assistant — browse products, build a cart, "
        "checkout, and track orders over WhatsApp or web chat"
    )

    # ── Entry point ──────────────────────────────────────────────────────────
    async def run(self, inp: SkillInput) -> SkillOutput:
        ctx = {
            "tenant_id": inp.tenant_id,
            "session_id": (
                inp.metadata.get("session_id")
                or inp.metadata.get("customer_phone")
                or f"{inp.tenant_id}:web"
            ),
            "customer_phone": inp.metadata.get("customer_phone"),
        }
        kb_context, sources = await self._retrieve_kb(inp)
        system_msg = self._system_prompt(inp.tenant_id, kb_context)

        try:
            answer = await self._agent_loop(system_msg, inp.conversation_history, inp.question, ctx)
            if not answer:
                raise RuntimeError("empty answer from agent loop")
            return SkillOutput(
                answer=answer,
                skill_name=self.name,
                confidence=0.8 if kb_context else 0.7,
                sources=sources,
            )
        except Exception as exc:
            logger.warning("commerce_assistant tool loop failed (%s) — falling back", exc)
            return await self._fallback(inp, ctx, kb_context, sources)

    # ── Prompt + grounding ───────────────────────────────────────────────────
    def _system_prompt(self, tenant_id: str, kb_context: str) -> str:
        kb_block = f"\n\nBusiness knowledge (use this to answer accurately):\n{kb_context}" if kb_context else ""
        return (
            "You are a friendly, concise WhatsApp shopping assistant for a South African "
            "business. Help the customer find products, build their cart, check out, and "
            "track orders. Use the provided tools to look up real product, cart and order "
            "data — never invent prices or stock. Always show money in ZAR (e.g. R185.00) "
            "and use South African conventions for dates and phone numbers. Keep replies "
            "short and WhatsApp-friendly. When the customer is ready to pay, call "
            "start_checkout and share the link." + kb_block
        )

    async def _retrieve_kb(self, inp: SkillInput) -> Tuple[str, List[Dict[str, Any]]]:
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline

            pipeline = VulaIngestionPipeline(tenant_id=inp.tenant_id)
            chunks = await pipeline.query(inp.question, top_k=4)
        except Exception as exc:
            logger.debug("commerce_assistant KB retrieval skipped: %s", exc)
            return "", []
        if not chunks:
            return "", []
        kb_context = "\n\n".join(
            f"[{c.get('filename', 'doc')}]: {c.get('text', '')[:400]}" for c in chunks
        )
        sources = [
            {"type": "kb", "filename": c.get("filename", "?"), "score": round(c.get("score", 0.0), 3)}
            for c in chunks
        ]
        return kb_context, sources

    # ── Agent loop ───────────────────────────────────────────────────────────
    async def _agent_loop(
        self, system_msg: str, history: str, question: str, ctx: Dict[str, Any]
    ) -> str:
        import litellm

        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_msg}]
        if history:
            messages.append({"role": "user", "content": f"(Earlier conversation)\n{history}"})
        messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=TOOL_SPECS,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=900,
                api_key=api_key,
                api_base=api_base,
            )
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                return (msg.content or "").strip()

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                result = await self._dispatch_tool(tc.function.name, args, ctx)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "content": json.dumps(result, default=str),
                    }
                )

        # Tool budget exhausted — ask the model to summarise without more tools.
        resp = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=600,
            api_key=api_key,
            api_base=api_base,
        )
        return (resp.choices[0].message.content or "").strip()

    # ── Tool dispatch + executors ────────────────────────────────────────────
    async def _dispatch_tool(self, name: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
        tid, sid, phone = ctx["tenant_id"], ctx["session_id"], ctx["customer_phone"]
        if name == "list_products":
            return await self._exec_list_products(tid, args)
        if name == "add_to_cart":
            return await self._exec_add_to_cart(tid, sid, phone, args)
        if name == "view_cart":
            return await self._exec_view_cart(tid, sid, phone)
        if name == "start_checkout":
            return self._exec_start_checkout(tid)
        if name == "track_order":
            return await self._exec_track_order(tid, args)
        return {"error": f"unknown tool {name}"}

    async def _exec_list_products(self, tenant_id: str, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        products = await service.list_products(
            tenant_id, category=args.get("category"), in_stock_only=True
        )
        search = (args.get("search") or "").lower().strip()
        if search:
            products = [
                p for p in products
                if search in p["name"].lower() or search in (p.get("description") or "").lower()
            ]
        return [
            {
                "slug": p["slug"],
                "name": p["name"],
                "price": f"R{p['price_cents'] / 100:.2f}",
                "category": p.get("category"),
            }
            for p in products[:20]
        ]

    async def _exec_add_to_cart(
        self, tenant_id: str, session_id: str, phone: Optional[str], args: Dict[str, Any]
    ) -> Dict[str, Any]:
        name = (args.get("product") or "").strip()
        try:
            qty = max(1, int(args.get("quantity", 1) or 1))
        except (TypeError, ValueError):
            qty = 1
        product = None
        if re.match(r"^[a-z0-9-]+$", name):
            product = await service.get_product_by_slug(tenant_id, name)
        if not product:
            candidates = await service.list_products(tenant_id, in_stock_only=True)
            product = next((p for p in candidates if name.lower() in p["name"].lower()), None)
        if not product:
            return {"error": f"No in-stock product matching '{name}'."}
        cart = await service.get_or_create_cart(tenant_id, session_id, phone)
        await service.add_to_cart(cart["id"], product["id"], qty)
        return {
            "added": product["name"],
            "quantity": qty,
            "unit_price": f"R{product['price_cents'] / 100:.2f}",
        }

    async def _exec_view_cart(
        self, tenant_id: str, session_id: str, phone: Optional[str]
    ) -> Dict[str, Any]:
        cart = await service.get_or_create_cart(tenant_id, session_id, phone)
        items = cart.get("commerce_cart_items", []) or []
        lines, subtotal = [], 0
        for it in items:
            prod = it.get("commerce_products") or {}
            line_total = it["quantity"] * it["unit_price_cents"]
            subtotal += line_total
            lines.append(
                {"name": prod.get("name"), "quantity": it["quantity"], "line_total": f"R{line_total / 100:.2f}"}
            )
        delivery = cart.get("delivery_cents", 8000)
        return {
            "items": lines,
            "subtotal": f"R{subtotal / 100:.2f}",
            "delivery": f"R{delivery / 100:.2f}",
            "total": f"R{(subtotal + delivery) / 100:.2f}",
        }

    def _exec_start_checkout(self, tenant_id: str) -> Dict[str, Any]:
        base = (settings.store_urls.get(tenant_id, "") or "").rstrip("/")
        if base:
            return {"checkout_url": f"{base}/cart"}
        return {"message": "Reply with your delivery address and we'll send a payment link directly."}

    async def _exec_track_order(self, tenant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
        display_id = (args.get("order_id") or "").strip().upper()
        orders = await service.list_orders(tenant_id, limit=50)
        match = next((o for o in orders if (o.get("display_id") or "").upper() == display_id), None)
        if not match:
            return {"error": f"No order {display_id} found."}
        return {
            "order_id": match["display_id"],
            "status": match["status"],
            "total": f"R{match['total_cents'] / 100:.2f}",
        }

    # ── Fallback (no tool-calling support) ───────────────────────────────────
    async def _fallback(
        self, inp: SkillInput, ctx: Dict[str, Any], kb_context: str, sources: List[Dict[str, Any]]
    ) -> SkillOutput:
        import litellm

        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()

        try:
            products = await service.list_products(inp.tenant_id, in_stock_only=True)
            catalog = "\n".join(
                f"- {p['name']} ({p['slug']}): R{p['price_cents'] / 100:.2f}" for p in products[:30]
            )
        except Exception:
            catalog = ""

        system_msg = self._system_prompt(inp.tenant_id, kb_context)
        history_block = f"\nConversation so far:\n{inp.conversation_history}\n" if inp.conversation_history else ""
        catalog_block = f"\nCurrent product list:\n{catalog}\n" if catalog else ""
        user_msg = f"{catalog_block}{history_block}\nCustomer: {inp.question}\n\nReply:"

        try:
            resp = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
                max_tokens=700,
                api_key=api_key,
                api_base=api_base,
            )
            raw = resp.choices[0].message.content or ""
            answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            return SkillOutput(
                answer=answer,
                skill_name=self.name,
                confidence=0.55 if kb_context else 0.45,
                sources=sources,
            )
        except Exception as exc:
            logger.error("commerce_assistant fallback failed: %s", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

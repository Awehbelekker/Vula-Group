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

MAX_TOOL_ITERATIONS = 6

# Known tool names — so we only treat text as a tool call when it actually names one of ours.
_TOOL_NAMES = {"list_products", "add_to_cart", "view_cart", "start_checkout", "track_order",
               "get_daily_catch", "suggest_recipe", "create_quote", "place_order", "review_order",
               "remove_from_cart", "change_order"}


def _parse_text_toolcall(text: str):
    """Some models emit a tool call as text instead of a structured tool_calls object — either as
    valid JSON ({"function":"get_daily_catch","arguments":{}}), malformed (no outer braces, e.g.
    "function": "suggest_recipe", "arguments": {"dish":"hake"}), or a bare function name glued
    straight onto its args with no framing at all (e.g. remove_from_cart{"product": "..."} — seen
    leaking to a real customer). Extract (name, args) from any of these so we never leak it.
    Returns None if the text isn't a recognised tool call."""
    if not text or '"' not in text:
        return None
    s = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.IGNORECASE).strip()
    # 0) Bare "tool_name{...}" or "tool_name: {...}" with no key framing — check this first since
    # the JSON-object strategy below would otherwise parse the args as a nameless dict and miss it.
    m0 = re.match(r"^([a-zA-Z_]+)\s*:?\s*(\{.*\})\s*$", s, re.DOTALL)
    if m0 and m0.group(1) in _TOOL_NAMES:
        try:
            args = json.loads(m0.group(2))
            if isinstance(args, dict):
                return m0.group(1), args
        except Exception:
            pass
    # 1) Well-formed JSON object anywhere in the text.
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        try:
            d = json.loads(s[i:j + 1])
            if isinstance(d, dict):
                name = d.get("function") or d.get("name") or d.get("tool")
                if isinstance(name, dict):
                    name = name.get("name")
                args = d.get("arguments") or d.get("parameters") or d.get("args") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if isinstance(name, str) and name in _TOOL_NAMES and isinstance(args, dict):
                    return name, args
        except Exception:
            pass
    # 2) Malformed — regex out the function name + arguments object.
    m = re.search(r'"(?:function|name|tool)"\s*:\s*"([a-zA-Z_]+)"', text)
    if m and m.group(1) in _TOOL_NAMES:
        args: Dict[str, Any] = {}
        am = re.search(r'"(?:arguments|parameters|args)"\s*:\s*(\{[^{}]*\})', text)
        if am:
            try:
                args = json.loads(am.group(1))
            except Exception:
                args = {}
        return m.group(1), args
    return None

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
    {
        "type": "function",
        "function": {
            "name": "get_daily_catch",
            "description": (
                "Return today's fresh catch highlights and any specials. Call this when a "
                "customer asks what's fresh, what's good today, what the special is, or "
                "when greeting and you want to proactively inspire them."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_recipe",
            "description": (
                "Suggest a South African recipe when the customer asks what to cook, "
                "mentions a fish or ingredient, or wants meal ideas. Returns a short "
                "recipe and lists which ingredients Off the Hook has in stock so they "
                "can be added to the cart."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dish": {
                        "type": "string",
                        "description": "The dish, ingredient, or meal type the customer mentioned (e.g. 'yellowtail', 'fish braai', 'snoek pâté', 'something quick for dinner').",
                    },
                    "serves": {
                        "type": "integer",
                        "description": "Number of people to serve, default 4.",
                    },
                },
                "required": ["dish"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_quote",
            "description": (
                "Generate a formal price quote for the customer. Use the items the customer "
                "asks to be quoted, or leave items empty to quote everything currently in their "
                "cart. Returns a quote number and total — quotes do not take payment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Optional explicit items to quote. Omit to quote the current cart.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "product": {"type": "string", "description": "Product name or slug."},
                                "quantity": {"type": "integer", "description": "Quantity, default 1."},
                            },
                            "required": ["product"],
                        },
                    },
                    "customer_name": {"type": "string", "description": "Customer name for the quote, if known."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_order",
            "description": (
                "Place the customer's cart as a confirmed order and return an itemised order "
                "confirmation (their quotation). Call this once you have the delivery address AND "
                "the customer has chosen how to pay. payment_method must be one of: 'online' (pay "
                "by card now), 'cod' (pay on delivery), or 'eft' (bank transfer). Always send the "
                "returned confirmation back to the customer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "payment_method": {
                        "type": "string",
                        "enum": ["online", "cod", "eft"],
                        "description": "How the customer chose to pay: online card, cod (pay on delivery), or eft (bank transfer).",
                    },
                    "delivery_address": {"type": "string", "description": "Where to deliver the order."},
                    "customer_name": {"type": "string", "description": "Customer's name."},
                    "delivery_slot": {
                        "type": "string",
                        "enum": ["morning", "afternoon", "express"],
                        "description": "Preferred delivery slot, default morning.",
                    },
                    "delivery_notes": {"type": "string", "description": "Any special delivery instructions."},
                },
                "required": ["payment_method", "delivery_address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_order",
            "description": (
                "Show the customer an itemised order summary (total, delivery, payment) to confirm "
                "BEFORE it's placed. ALWAYS call this before place_order. Returns a preview to send "
                "them; then wait for them to reply CONFIRM. Same fields as place_order."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "payment_method": {"type": "string", "enum": ["online", "cod", "eft"]},
                    "delivery_address": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "delivery_slot": {"type": "string", "enum": ["morning", "afternoon", "express"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_cart",
            "description": "Remove a product from the cart by name (used when the customer wants to change their order before confirming).",
            "parameters": {
                "type": "object",
                "properties": {"product": {"type": "string", "description": "Product name to remove."}},
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "change_order",
            "description": (
                "The customer wants to change an order they've ALREADY placed. Flags their most recent "
                "order to the shop team to update. Use only after place_order; for changes before "
                "confirming, edit the cart instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {"change": {"type": "string", "description": "What they want changed."}},
                "required": ["change"],
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
            "You are a friendly, knowledgeable WhatsApp assistant for a South African fresh "
            "fish and chicken delivery business. Your goals: help customers find great products, "
            "inspire them with recipes, build their cart, and check out.\n\n"
            "Guidelines:\n"
            "- Use real tools for products, cart and orders — never invent prices or stock.\n"
            "- Show money in ZAR (e.g. R185.00). Keep replies short and WhatsApp-friendly.\n"
            "- On first contact or when asked what's good/fresh/special, call get_daily_catch "
            "to show today's highlights before anything else.\n"
            "- When a customer mentions a dish, ingredient, or asks what to cook, call "
            "suggest_recipe — it returns a recipe AND shows which ingredients are in stock.\n"
            "- After suggesting a recipe, offer to add the available ingredients to their cart.\n"
            "- Be proactive: if a customer buys yellowtail, suggest a recipe for it unprompted.\n"
            "- To ORDER over WhatsApp: confirm the cart, get the delivery address, then ask how "
            "they'd like to pay — *online card*, *pay on delivery*, or *EFT / bank transfer*.\n"
            "- Then call *review_order* and send the customer the summary it returns. WAIT for them "
            "to reply *CONFIRM*. Do NOT call place_order until they confirm.\n"
            "- If they want to change something before confirming, use add_to_cart / remove_from_cart, "
            "then call review_order again to show the updated summary.\n"
            "- Once they reply CONFIRM, call *place_order* with the payment_method and send the "
            "returned confirmation (order number + payment instructions) — that is their receipt.\n"
            "- If a customer wants to change an order they ALREADY placed, call change_order.\n"
            "- Only use start_checkout if the customer specifically wants to pay on the website.\n"
            "- For a price quote without ordering, call create_quote and share the number and total."
            + kb_block
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
        from uuid import uuid4
        from core.llm_router import escalate_to_cloud, looks_unreliable

        litellm.drop_params = True
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_msg}]
        if history:
            messages.append({"role": "user", "content": f"(Earlier conversation)\n{history}"})
        messages.append({"role": "user", "content": question})

        run_id = str(uuid4())
        model, api_key, api_base = await resolve_generation_route(
            task_type="commerce_chat", messages=messages, run_id=run_id)

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
                answer = (msg.content or "").strip()
                # Some models (esp. local ones) emit the tool call as raw JSON TEXT instead of a
                # structured tool_calls object. Never send that JSON to the customer — detect it,
                # run the tool, feed the result back, and loop so the model writes a real reply.
                parsed = _parse_text_toolcall(answer)
                if parsed:
                    # A text tool-call means the (local) model isn't doing structured tool-calling.
                    # Escalate this turn to the cloud model — which does — and retry cleanly.
                    if model.startswith("ollama/"):
                        esc = escalate_to_cloud("local_toolcall_text", run_id=run_id, task_type="commerce_chat")
                        if esc:
                            model, api_key, api_base = esc
                            continue
                    # No cloud available → run the parsed tool ourselves and loop for a reply.
                    tname, targs = parsed
                    result = await self._dispatch_tool(tname, targs, ctx)
                    messages.append({"role": "assistant", "content": answer})
                    messages.append({"role": "user", "content":
                        f"(system: the {tname} tool returned: {json.dumps(result, default=str)[:1500]}. "
                        f"Now reply to the customer in plain, friendly language — never output JSON.)"})
                    continue
                # Requirement (b): a weak local final answer escalates to cloud (tool turns stay local).
                if model.startswith("ollama/") and looks_unreliable(answer):
                    esc = escalate_to_cloud("local_unreliable", run_id=run_id, task_type="commerce_chat")
                    if esc:
                        model, api_key, api_base = esc
                        resp = await litellm.acompletion(
                            model=model, messages=messages, temperature=0.3,
                            max_tokens=900, api_key=api_key, api_base=api_base)
                        answer = (resp.choices[0].message.content or "").strip()
                return answer

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
        if name == "get_daily_catch":
            return await self._exec_get_daily_catch(tid)
        if name == "suggest_recipe":
            return await self._exec_suggest_recipe(tid, args)
        if name == "create_quote":
            return await self._exec_create_quote(tid, sid, phone, args)
        if name == "review_order":
            return await self._exec_review_order(tid, sid, phone, args)
        if name == "remove_from_cart":
            return await self._exec_remove_from_cart(tid, sid, phone, args)
        if name == "change_order":
            return await self._exec_change_order(tid, phone, args)
        if name == "place_order":
            return await self._exec_place_order(tid, sid, phone, args)
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
        await service.add_to_cart(tenant_id, cart["id"], product["id"], qty)
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

    async def _exec_get_daily_catch(self, tenant_id: str) -> Dict[str, Any]:
        """Return products flagged as today's catch + any fresh fish in stock."""
        try:
            all_products = await service.list_products(tenant_id, in_stock_only=True)
        except Exception as exc:
            return {"error": f"Could not load products: {exc}"}

        specials = [p for p in all_products if p.get("is_daily_catch")]
        fresh_fish = [p for p in all_products if p.get("category") == "fresh_fish"]

        highlights = specials or fresh_fish[:5]
        if not highlights:
            return {"message": "No specials today — check back tomorrow or browse our full range."}

        header = "🎣 Today's catch highlights:" if specials else "🐟 Fresh in stock today:"
        names = ", ".join(p["name"] for p in highlights[:3])
        return {
            "daily_catch": [
                {
                    "name": p["name"],
                    "slug": p["slug"],
                    "price": f"R{p['price_cents'] / 100:.2f}/kg" if p.get("sold_by") == "kg" else f"R{p['price_cents'] / 100:.2f}",
                    "description": (p.get("description") or "")[:120],
                    "is_highlight": p.get("is_daily_catch", False),
                }
                for p in highlights[:6]
            ],
            "message": f"{header} {names}.",
        }

    async def _exec_suggest_recipe(self, tenant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a South African recipe and match ingredients to in-stock products."""
        import litellm

        dish = (args.get("dish") or "fish").strip()
        serves = max(1, int(args.get("serves") or 4))

        # Fetch catalog so we know what's actually available
        try:
            products = await service.list_products(tenant_id, in_stock_only=True)
            catalog_names = [p["name"] for p in products]
            catalog_str = ", ".join(catalog_names[:40])
        except Exception:
            products, catalog_names, catalog_str = [], [], ""

        # Ground in the tenant's OWN recipe knowledge base first, so recommendations adapt a real
        # OTH recipe rather than being invented fresh each time.
        kb_recipe = ""
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            chunks = await VulaIngestionPipeline(tenant_id=tenant_id).query(f"{dish} recipe", top_k=2)
            kb_recipe = "\n\n".join((c.get("text") or "")[:600] for c in (chunks or []) if c.get("text"))
        except Exception:
            kb_recipe = ""
        grounding = (f"\nOur own recipe to base this on (adapt it, keep it true to ours):\n{kb_recipe}\n"
                     if kb_recipe.strip() else "")

        # Live web inspiration (B): fresh chef-style ideas. Used as INSPIRATION only — the model
        # writes an Off the Hook-voiced recipe, never copies wording (copyright-safe). Best-effort.
        web_ref = ""
        try:
            from core.skills.web_search import _ddg_search
            hits = await _ddg_search(f"{dish} recipe", limit=4)
            web_ref = "\n".join(f"- {h.get('title', '')} ({h.get('url', '')})"
                                for h in (hits or [])[:4] if h.get("title"))
        except Exception:
            web_ref = ""
        inspiration = (f"\nFresh ideas from the web for INSPIRATION ONLY — adapt into Off the Hook's own "
                       f"voice, do NOT copy any wording; you may nod to the dish or chef style:\n{web_ref}\n"
                       if web_ref.strip() else "")

        prompt = (
            f"You are a South African recipe assistant for a fresh fish and chicken delivery business.\n"
            f"A customer wants to cook: {dish} (serves {serves}).\n"
            f"{grounding}{inspiration}\n"
            f"These ingredients are currently in stock and available to order:\n{catalog_str}\n\n"
            f"Write a SHORT, practical South African recipe (max 180 words):\n"
            f"- Recipe name\n"
            f"- Ingredients list (highlight which ones are available to order)\n"
            f"- Quick method (4–6 steps)\n"
            f"- A 'From Off the Hook' section listing ONLY the in-stock items needed, "
            f"with their exact names from the catalog so they can be added to the cart.\n\n"
            f"Keep it warm, South African, and appetising. "
            f"If the dish doesn't need any fish/chicken, suggest a protein that works well."
        )

        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()
        try:
            resp = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500,
                api_key=api_key,
                api_base=api_base,
            )
            recipe_text = re.sub(
                r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.DOTALL
            ).strip()
        except Exception as exc:
            logger.warning("Recipe generation failed: %s", exc)
            return {"error": "Could not generate recipe right now — try asking again."}

        # Extract which in-stock products the recipe mentions
        recipe_lower = recipe_text.lower()
        matched = [p for p in products if p["name"].lower() in recipe_lower]
        # Fuzzy: also catch partial matches (e.g. "hake" matches "Hake Fillets")
        for p in products:
            first_word = p["name"].split()[0].lower()
            if len(first_word) > 3 and first_word in recipe_lower and p not in matched:
                matched.append(p)

        available = [
            {"name": p["name"], "slug": p["slug"], "price": f"R{p['price_cents'] / 100:.2f}"}
            for p in matched[:6]
        ]

        return {
            "recipe": recipe_text,
            "available_to_order": available,
            "tip": (
                "I can add any of these to your cart — just say which ones you want!"
                if available else
                "Let me know what else I can help you with."
            ),
        }

    async def _resolve_product(self, tenant_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Find an in-stock product by slug or fuzzy name match."""
        name = (name or "").strip()
        if not name:
            return None
        if re.match(r"^[a-z0-9-]+$", name):
            product = await service.get_product_by_slug(tenant_id, name)
            if product:
                return product
        candidates = await service.list_products(tenant_id, in_stock_only=True)
        return next((p for p in candidates if name.lower() in p["name"].lower()), None)

    async def _exec_create_quote(
        self, tenant_id: str, session_id: str, phone: Optional[str], args: Dict[str, Any]
    ) -> Dict[str, Any]:
        line_items: List[Dict[str, Any]] = []
        items_arg = args.get("items") or []
        if items_arg:
            for it in items_arg:
                product = await self._resolve_product(tenant_id, it.get("product", ""))
                if not product:
                    continue
                try:
                    qty = max(1, int(it.get("quantity", 1) or 1))
                except (TypeError, ValueError):
                    qty = 1
                line_items.append(
                    {
                        "description": product["name"],
                        "quantity": qty,
                        "unit_price_cents": product["price_cents"],
                        "product_id": product["id"],
                    }
                )
        else:
            cart = await service.get_or_create_cart(tenant_id, session_id, phone)
            for it in cart.get("commerce_cart_items", []) or []:
                prod = it.get("commerce_products") or {}
                line_items.append(
                    {
                        "description": prod.get("name", "Item"),
                        "quantity": it["quantity"],
                        "unit_price_cents": it["unit_price_cents"],
                        "product_id": it.get("product_id"),
                    }
                )

        if not line_items:
            return {"error": "Nothing to quote — add items to the cart or list the products to quote."}

        quote = await service.create_invoice(
            tenant_id,
            {
                "doc_type": "quote",
                "customer_name": (args.get("customer_name") or "Customer").strip(),
                "customer_phone": phone,
                "line_items": line_items,
            },
        )
        return {
            "quote_number": quote["invoice_number"],
            "items": len(line_items),
            "total": f"R{quote['total_cents'] / 100:.2f}",
        }

    async def _exec_review_order(
        self, tenant_id: str, session_id: str, phone: Optional[str], args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Preview the order (itemised total + delivery + payment) WITHOUT placing it, so the
        customer can confirm or change first. place_order runs only after they reply CONFIRM."""
        from vula.commerce import order_workflow as ow
        cfg = ow.get_order_settings(tenant_id)
        enabled = [str(m).lower() for m in (cfg.get("payment_methods") or ["online", "cod", "eft"])]
        method = (args.get("payment_method") or "").lower().strip()

        cart = await service.get_or_create_cart(tenant_id, session_id, phone)
        items = cart.get("commerce_cart_items", []) or []
        if not items:
            return {"error": "The cart is empty — add items before reviewing."}
        subtotal = sum(i["quantity"] * i["unit_price_cents"] for i in items)
        delivery = cart.get("delivery_cents", 8000)
        total = subtotal + delivery
        item_lines = "\n".join(
            f"• {it['quantity']}× {(it.get('commerce_products') or {}).get('name', 'Item')} — "
            f"R{it['quantity'] * it['unit_price_cents'] / 100:.2f}" for it in items)
        addr = (args.get("delivery_address") or "").strip()
        pay = ow.PAYMENT_LABELS.get(method) if method in enabled else None
        preview = (
            f"🧾 *Please check your order:*\n{item_lines}\n"
            f"Subtotal: R{subtotal / 100:.2f}\nDelivery: R{delivery / 100:.2f}\n*Total: R{total / 100:.2f}*"
            + (f"\nDeliver to: {addr}" if addr else "")
            + (f"\nPayment: {pay}" if pay else "")
            + "\n\nReply *CONFIRM* to place it, or tell me what to change.")
        return {
            "preview": preview,
            "still_needed": [k for k, v in (("delivery address", addr), ("payment method", pay)) if not v],
            "instruction_to_assistant": ("Send 'preview' to the customer verbatim. Do NOT call "
                                         "place_order until they reply CONFIRM. Ask for anything in "
                                         "'still_needed' first."),
        }

    async def _exec_remove_from_cart(
        self, tenant_id: str, session_id: str, phone: Optional[str], args: Dict[str, Any]
    ) -> Dict[str, Any]:
        name = (args.get("product") or "").strip()
        cart = await service.get_or_create_cart(tenant_id, session_id, phone)
        items = cart.get("commerce_cart_items", []) or []
        target = next((it for it in items
                       if name.lower() in (it.get("commerce_products") or {}).get("name", "").lower()), None)
        if not target:
            return {"error": f"'{name}' isn't in the cart."}
        await service.remove_from_cart(cart["id"], target["id"])
        return {"removed": (target.get("commerce_products") or {}).get("name", name)}

    async def _exec_change_order(
        self, tenant_id: str, phone: Optional[str], args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Customer wants to change an order they've already placed → flag their most recent live
        order to the shop team (line-item edits on a placed order are handled by a person)."""
        note = (args.get("change") or "").strip()
        digits = "".join(c for c in (phone or "") if c.isdigit())
        orders = await service.list_orders(tenant_id, limit=30)
        live = [o for o in orders
                if "".join(c for c in (o.get("customer_phone") or "") if c.isdigit()).endswith(digits[-9:] or "x")
                and o.get("status") not in ("delivered", "cancelled", "refunded")]
        if not live:
            return {"error": "I couldn't find a recent order to change — could be already delivered."}
        order = live[0]
        try:
            from vula.commerce import order_workflow as ow
            await ow.dispatch_order(tenant_id, order["id"],
                                    f"✏️ CHANGE REQUEST on {order['display_id']}: {note}",
                                    order.get("customer_name") or "")
        except Exception as exc:
            logger.warning("change_order notify failed: %s", exc)
        return {"order_number": order["display_id"],
                "message": f"Got it — I've asked the team to update order {order['display_id']}. "
                           f"They'll confirm the change with you shortly."}

    async def _exec_place_order(
        self, tenant_id: str, session_id: str, phone: Optional[str], args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Turn the current cart into a confirmed order with a chosen payment method, and
        return an itemised confirmation (the customer's quotation). Handles online card
        (pay-link if a gateway is connected), pay-on-delivery, and EFT/bank transfer."""
        from vula.commerce import order_workflow as ow

        cfg = ow.get_order_settings(tenant_id)
        enabled = [str(m).lower() for m in (cfg.get("payment_methods") or ["online", "cod", "eft"])]
        method = (args.get("payment_method") or "").lower().strip()
        if method not in ("online", "cod", "eft"):
            return {"error": "Ask the customer how they'd like to pay — online card, pay on delivery, or EFT — then call place_order."}
        if method not in enabled:
            offered = ", ".join(ow.PAYMENT_LABELS.get(m, m) for m in enabled)
            return {"error": f"That payment method isn't offered here. Available: {offered}."}

        address = (args.get("delivery_address") or "").strip()
        if not address:
            return {"error": "Need a delivery address before placing the order — ask the customer for it."}
        name = (args.get("customer_name") or "Customer").strip()
        slot = (args.get("delivery_slot") or "morning").strip().lower()
        if slot not in ("morning", "afternoon", "express"):
            slot = "morning"

        cart = await service.get_or_create_cart(tenant_id, session_id, phone)
        items = cart.get("commerce_cart_items", []) or []
        if not items:
            return {"error": "The cart is empty — add items before placing the order."}

        try:
            order = await service.create_order(tenant_id, cart, {
                "customer_phone": phone or "",
                "customer_name": name,
                "delivery_address": address,
                "delivery_slot": slot,
                "delivery_notes": args.get("delivery_notes"),
                "channel": "whatsapp",
                "payment_method": method,
            })
        except Exception as exc:
            logger.warning("place_order create_order failed: %s", exc)
            return {"error": "Something went wrong placing the order — please try again in a moment."}

        lines = []
        for it in items:
            prod = it.get("commerce_products") or {}
            qty, unit = it["quantity"], it["unit_price_cents"]
            lines.append({"name": prod.get("name", "Item"), "quantity": qty,
                          "line_total": f"R{qty * unit / 100:.2f}"})

        total = order["total_cents"]
        pay_line = ow.payment_instructions(cfg, method)
        pay_link = None
        if method == "online":
            pl = await self._online_pay_link(tenant_id, order)
            if pl:
                pay_line, pay_link = f"💳 Pay securely here: {pl}", pl
            else:
                base = (settings.store_urls.get(tenant_id, "") or "").rstrip("/")
                if base:
                    pay_line = f"💳 Complete your payment here: {base}/cart"

        await self._notify_shop_new_order(tenant_id, order, lines, method, name)

        item_lines = "\n".join(f"• {l['quantity']}× {l['name']} — {l['line_total']}" for l in lines)
        confirmation = (
            f"✅ *Order {order['display_id']} confirmed*\n{item_lines}\n"
            f"Subtotal: R{order['subtotal_cents'] / 100:.2f}\n"
            f"Delivery: R{order['delivery_cents'] / 100:.2f}\n"
            f"*Total: R{total / 100:.2f}*\n\n"
            f"Deliver to: {address} ({slot})\n\n{pay_line}"
        )
        return {
            "order_number": order["display_id"],
            "items": lines,
            "subtotal": f"R{order['subtotal_cents'] / 100:.2f}",
            "delivery": f"R{order['delivery_cents'] / 100:.2f}",
            "total": f"R{total / 100:.2f}",
            "payment_method": ow.PAYMENT_LABELS.get(method, method),
            "payment_link": pay_link,
            "confirmation": confirmation,
            "instruction_to_assistant": "Send the 'confirmation' text to the customer verbatim as their order confirmation.",
        }

    async def _online_pay_link(self, tenant_id: str, order: Dict[str, Any]) -> Optional[str]:
        """A hosted card pay-link via the tenant's connected gateway, or None if none is set up."""
        try:
            from vula.payments import create_pay_link, default_provider_row
            base = (settings.store_urls.get(tenant_id, "") or "").rstrip("/") or "https://vula-group-production.up.railway.app"
            api = "https://vula-group-production.up.railway.app"
            prov = (default_provider_row(tenant_id) or {}).get("provider", "default")
            link = await create_pay_link(
                tenant_id,
                amount_cents=order["total_cents"],
                reference=order["display_id"],
                description=f"Order {order['display_id']}",
                success_url=f"{base}/order/{order['display_id']}",
                cancel_url=f"{base}/cart",
                notify_url=f"{api}/api/payments/webhook/{tenant_id}/{prov}",
                customer={"name": order.get("customer_name"), "phone": order.get("customer_phone")},
            )
            return link.url if link else None
        except Exception as exc:
            logger.warning("online pay-link failed for %s: %s", tenant_id, exc)
            return None

    async def _notify_shop_new_order(
        self, tenant_id: str, order: Dict[str, Any], lines: List[Dict[str, Any]], method: str, name: str
    ) -> None:
        """Tell the shop about a new order. COD/EFT orders never hit the payment webhook,
        so this is the only way the owner hears about them."""
        try:
            from vula.commerce import order_workflow as ow
            item_str = "\n".join(f"  • {l['quantity']}× {l['name']} — {l['line_total']}" for l in lines)
            summary = (
                f"🆕 New WhatsApp order {order['display_id']}\n"
                f"Customer: {name} ({order.get('customer_phone')})\n"
                f"Deliver to: {order.get('delivery_address')} ({order.get('delivery_slot')})\n"
                f"{item_str}\n"
                f"Total: R{order['total_cents'] / 100:.2f}\n"
                f"Payment: {ow.PAYMENT_LABELS.get(method, method)}"
            )
            await ow.dispatch_order(tenant_id, order["id"], summary, name)
        except Exception as exc:
            logger.warning("new-order notify failed: %s", exc)

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

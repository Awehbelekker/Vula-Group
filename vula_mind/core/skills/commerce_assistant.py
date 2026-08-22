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
from core.llm_router import resolve_generation_route, looks_degenerate, DEGENERATE_OUTPUT_FALLBACK
from core.prompt_safety import fence
from core.skills.base import BaseSkill, SkillInput, SkillOutput, behaviour_preamble
from vula.commerce import service

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 6


def _tenant_has_bookings(tenant_id: str) -> bool:
    """True if this tenant has the bookings module enabled (gates the appointment tools)."""
    try:
        from vula.api.tenants import enabled_modules
        return "bookings" in (enabled_modules(tenant_id) or [])
    except Exception:
        return False


# health/services are the two business types that ship with "bookings" enabled by default
# (vula/api/tenants.py BUSINESS_TYPES) — the two tenant shapes this skill's hardcoded "fresh
# fish and chicken delivery" storefront prompt/tool list has never fit. A booking-focused
# tenant gets a bookings-first prompt and a trimmed tool list (booking tools + ask_team only)
# instead of 15 product/cart/order tools it has no products or orders to use.
_BOOKING_FOCUSED_TYPES = {"health", "services"}


def _tenant_business_type(tenant_id: str) -> str:
    try:
        from vula.api.tenants import get_config
        return (get_config(tenant_id).get("business_type") or "").lower()
    except Exception:
        return ""


def _is_booking_focused(tenant_id: str) -> bool:
    return _tenant_has_bookings(tenant_id) and _tenant_business_type(tenant_id) in _BOOKING_FOCUSED_TYPES

# Known tool names — so we only treat text as a tool call when it actually names one of ours.
# Populated from TOOL_SPECS + BOOKING_TOOL_SPECS after they're defined (end of module) — was a
# hand-maintained set that silently drifted: cancel_order was added to TOOL_SPECS but not here,
# so its text tool-call leaked raw JSON to a real customer (confirmed live 2026-07-16).
_TOOL_NAMES: set = set()


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


def _as_json_dict(text: str) -> Optional[dict]:
    """The whole reply parsed as a JSON object (fences stripped), or None. Any reply that
    parses here is machine output, never customer-facing prose."""
    if not text or not text.strip().startswith("{"):
        return None
    s = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.IGNORECASE).strip()
    try:
        d = json.loads(s)
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _extract_message_leak(text: str) -> Optional[str]:
    """Some models, instead of writing prose, echo a tool RESULT's shape back as the final
    answer verbatim (e.g. {"message": "Today's catch highlights: ..."}) — distinct from the
    tool-CALL leak _parse_text_toolcall catches. Unwrap the human-readable field so the
    customer gets the message text instead of raw JSON. Returns None if `text` isn't this
    specific leak shape."""
    d = _as_json_dict(text)
    if not d:
        return None
    msg = d.get("message") or d.get("reply") or d.get("text")
    return msg.strip() if isinstance(msg, str) and msg.strip() else None


# A code-level backstop for place_order: the system prompt tells the model "only call this
# after the customer replies CONFIRM", but that's a prompt instruction, not an enforced rule —
# a local model placed a real order after a customer said "No, I just want 1kg" (a correction,
# not a confirmation). This requires the CURRENT turn's raw text to contain an explicit
# affirmative before place_order is allowed to run at all.
#
# Covers English, Afrikaans, isiZulu, isiXhosa and Sesotho — the assistant's system prompt
# promises to reply in whichever of these the customer uses, so the confirm-gate must recognise
# "yes" in all of them too, or it silently blocks real confirmations from those customers instead
# of just from English/Afrikaans ones. Short words (ee, ja) are still \b-bounded to whole tokens
# to avoid matching inside unrelated text — a missed match just asks the customer to confirm
# again (safe), so this leans inclusive rather than risking a false negative.
_CONFIRM_RE = re.compile(
    r"\b(confirm(ed)?|yes|yep|yea|yeah|correct|sounds good|go ahead|place it|place the order|"
    r"bevestig|ja|reg so|plaas dit|"                       # Afrikaans
    r"yebo|kulungile|ngiyavuma|"                            # isiZulu: yes / it's fine / I agree
    r"ewe|kulungile|ndiyavuma|"                             # isiXhosa: yes / it's fine / I agree
    r"ee|ho lokile|kea dumela)\b",                          # Sesotho: yes / it's fine / I agree
    re.IGNORECASE)


def _is_kg(product) -> bool:
    return str((product or {}).get("sold_by") or "").lower() == "kg"


def _norm_qty(product, raw) -> float:
    """Parse a requested quantity: decimals allowed for kg (e.g. 1.5), whole packs otherwise."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 1.0
    if _is_kg(product):
        return round(v, 3) if v > 0 else 1.0
    return float(max(1, int(v)))


def _fmt_qty(product, qty) -> str:
    """Human label for a quantity given how the product is sold (e.g. '1.5kg' or '2')."""
    if _is_kg(product):
        return f"{float(qty):.3f}".rstrip("0").rstrip(".") + "kg"
    return str(int(float(qty)))


def _line_cents(qty, unit_price_cents) -> int:
    """Line total in integer cents, decimal-safe for kg quantities."""
    return int(round(float(qty) * float(unit_price_cents or 0)))


def _match_variant(variants: List[Dict[str, Any]], option_names: List[str], text: str):
    """Resolve a customer's free-text option choice (e.g. 'large', 'size L red') to a single
    variant. Returns (variant, unresolved_option_names): variant is set only on an exact single
    match; otherwise unresolved_option_names lists which option(s) still need an answer, scoped
    to whatever the text has already narrowed down (so a second turn only needs to ask about the
    remaining option, not restart from scratch)."""
    active = [v for v in variants if not v.get("archived")]
    text = (text or "").lower()
    tokens = [t.strip() for t in re.split(r"[,/]|\s+and\s+", text) if t.strip()]

    def mentioned(value: Optional[str]) -> bool:
        v = (value or "").lower()
        if not v:
            return False
        return any(v in tok or tok in v for tok in tokens)

    candidates = active
    if tokens:
        # Score each variant by how many of ITS OWN option values are mentioned (not just any),
        # so "large red" prefers the variant matching both axes over one matching only one.
        scored = [(v, sum(1 for name in option_names if mentioned((v.get("option_values") or {}).get(name))))
                  for v in active]
        best = max((score for _, score in scored), default=0)
        if best > 0:
            candidates = [v for v, score in scored if score == best]

    if len(candidates) == 1:
        return candidates[0], []

    unresolved = [name for name in option_names
                  if len({(v.get("option_values") or {}).get(name) for v in candidates} - {None}) > 1]
    return None, (unresolved or option_names)


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
            "description": "Add a product to the customer's cart by name or slug. Some products come in "
                           "options (e.g. Size, Colour) — if the result asks you to choose, ask the customer "
                           "a short natural question and call this again with their answer in `variant`. "
                           "Never guess an option or add a plain product to the cart when it has options "
                           "and none were given.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "Product name or slug."},
                    "quantity": {"type": "number", "description": "Amount to add. For products priced per kg, this is the number of kilograms (decimals allowed, e.g. 1.5). For packs, the number of packs. Default 1."},
                    "variant": {"type": "string", "description": "The customer's chosen option(s) for a product that has variants, in their own words (e.g. 'large', 'red', 'size L red'). Omit for products with no options."},
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
            "description": (
                "Check an order's delivery/preparation STATUS by its order number (e.g. OTH-00042). "
                "Does NOT know about invoices/receipts — for those, or anything the customer wants "
                "sent/resent as a document, use resend_invoice instead."
            ),
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
            "name": "resend_invoice",
            "description": (
                "Re-send a customer's invoice/receipt PDF via WhatsApp. Call this when they ask "
                "to resend, reprint, or get a copy of their invoice. Accepts either the invoice's "
                "own number (e.g. OTH-INV-00002) or the order number (e.g. OTH-00001) — customers "
                "don't reliably know which one they have, so try whatever they give you. If they "
                "don't give a number at all, omit it and their most recent invoice is sent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_number": {"type": "string", "description": "Invoice or order number, if the customer gave one."}
                },
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
                    "discount_code": {"type": "string", "description": "A promo/discount code the customer gave, if any."},
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
                    "discount_code": {"type": "string", "description": "A promo/discount code the customer gave, if any."},
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
    {
        "type": "function",
        "function": {
            "name": "ask_team",
            "description": (
                "Ask the shop's human team a question you CANNOT answer from your facts or other "
                "tools — e.g. whether we deliver to an area not in your delivery facts, special/"
                "custom requests, opening hours you don't know. The team is pinged on WhatsApp and "
                "their answer goes to the customer directly. ALWAYS use this instead of guessing "
                "or inventing business facts."
            ),
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string",
                                            "description": "The customer's question, as asked."}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": (
                "The customer wants to cancel an order they've ALREADY placed (placed = after "
                "place_order ran, they have an order number). Flags it to the shop team to confirm "
                "and process the cancellation (a paid order may need a refund, which is a human "
                "step). NEVER tell the customer their order IS cancelled — only that the team has "
                "been asked to cancel it and will confirm."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_subscription",
            "description": (
                "Set up a recurring/standing order from the customer's CURRENT cart (e.g. 'send me "
                "this every Friday', 'the same order weekly'). Add the items to the cart first, then "
                "call this. An order is auto-created each cycle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cadence": {"type": "string", "enum": ["weekly", "biweekly", "monthly"],
                                "description": "How often to repeat."},
                },
                "required": ["cadence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_my_subscriptions",
            "description": "List the customer's own standing/recurring orders (active or paused).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_subscription",
            "description": "Cancel the customer's standing/recurring order.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_subscription",
            "description": "Pause the customer's standing/recurring order (no orders will be "
                           "auto-created until it's resumed — tell them to ask to start it "
                           "again when they want it back).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reorder_last",
            "description": (
                "Repeat the customer's most recent order (e.g. 'same as last time', 'reorder', "
                "'the usual'). Adds those items to their current cart — call review_order next "
                "so they can confirm before it's placed."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# Appointment tools — only exposed to tenants with the `bookings` module enabled.
BOOKING_TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_availability",
            "description": (
                "Show free appointment slots for a given date. Call this when a customer wants to "
                "book and has named (or you've agreed) a day. Returns open times they can pick from."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "The date as YYYY-MM-DD (local SA date)."},
                    "service": {"type": "string", "description": "Service name they want, if mentioned."},
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book an appointment once the customer has chosen a specific date AND time. Confirm "
                "the time back to them first. Returns the confirmed booking or an error if the slot is taken."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "Chosen slot as YYYY-MM-DDTHH:MM (local time)."},
                    "service": {"type": "string", "description": "Service name they're booking."},
                    "customer_name": {"type": "string", "description": "Customer's name."},
                },
                "required": ["start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel the customer's upcoming appointment (their most recent confirmed booking).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Derived from the specs so the leak guard can never drift out of sync with the real tool list
# again (see _TOOL_NAMES comment at the top of the module).
_TOOL_NAMES.update(s["function"]["name"] for s in TOOL_SPECS + BOOKING_TOOL_SPECS)

# Booking-focused tenants (see _is_booking_focused) get this instead of the full storefront
# TOOL_SPECS — ask_team (the one universal, non-storefront tool) plus the booking tools.
_ASK_TEAM_ONLY = [t for t in TOOL_SPECS if t["function"]["name"] == "ask_team"]


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
            "bookings": _tenant_has_bookings(inp.tenant_id),
            "booking_focused": _is_booking_focused(inp.tenant_id),
            "current_message": inp.question,
        }
        kb_context, sources = await self._retrieve_kb(inp)
        system_msg = self._system_prompt(inp.tenant_id, kb_context, inp.metadata.get("preferred_language"),
                                         booking_focused=ctx["booking_focused"])

        try:
            answer = await self._agent_loop(system_msg, inp.conversation_history, inp.question, ctx)
            if not answer:
                raise RuntimeError("empty answer from agent loop")
            if looks_degenerate(answer):
                answer = DEGENERATE_OUTPUT_FALLBACK
            return SkillOutput(
                answer=answer,
                skill_name=self.name,
                confidence=0.8 if kb_context else 0.7,
                sources=sources,
                media_url=ctx.get("media_url"),
            )
        except Exception as exc:
            logger.warning("commerce_assistant tool loop failed (%s) — falling back", exc)
            return await self._fallback(inp, ctx, kb_context, sources)

    # ── Prompt + grounding ───────────────────────────────────────────────────
    def _system_prompt(self, tenant_id: str, kb_context: str, preferred_language: str = None,
                       booking_focused: bool = False) -> str:
        # 2026-08-08: this skill never called the platform's shared behaviour_preamble() at
        # all (confirmed while auditing every tool-calling skill for the same gap fixed in
        # commerce_admin.py earlier the same day) — it hand-rolled just an UNTRUSTED_CONTENT_RULE
        # inclusion. Now gets the full shared policy (ethics/honesty/reasoning/conversation/
        # untrusted-content + the agentic tool-calling rules) like every other skill.
        from core.prompt_safety import fence
        kb_block = fence("BUSINESS_KNOWLEDGE", kb_context)
        lang_block = ""
        try:
            from core.lang import language_name
            name = language_name(preferred_language)
            if name and name != "English":
                lang_block = (
                    f"\n\nThis customer usually speaks {name}. Greet and reply in {name} by default, "
                    f"unless they clearly switch to another language in this message — then follow them."
                )
        except Exception:
            pass

        persona_block = ""
        try:
            from vula.api.tenants import get_config
            persona = (get_config(tenant_id) or {}).get("persona_prompt")
            if persona:
                persona_block = f"\n\nHow you should sound: {persona}"
        except Exception:
            pass

        # health/services tenants (see _is_booking_focused) get a bookings-first prompt instead
        # of the storefront one below — they have no products, cart, or orders, so the fish-shop
        # cart/checkout guidance was never applicable and only confused the model into offering
        # tools it didn't have.
        if booking_focused:
            return (
                "You are a friendly, professional WhatsApp assistant for a South African "
                "business that takes bookings. Your goal: help customers check availability, "
                "book, and cancel appointments, and answer questions about the business.\n\n"
                "Guidelines:\n"
                "- Reply in the SAME language the customer writes in. South Africans message in "
                "English, Afrikaans, isiZulu, isiXhosa, Sesotho and more — mirror their language "
                "naturally and warmly. If they mix languages, follow their lead; if unsure, use "
                "English.\n"
                "- To book: find out what they need and when, call list_availability to show "
                "REAL open times, confirm the exact time back to them, then call "
                "book_appointment. Never promise or invent a time you haven't confirmed via "
                "list_availability.\n"
                "- To cancel an appointment, call cancel_appointment.\n"
                "- If you can't answer a question about the business itself (services offered, "
                "pricing, location, custom requests), call *ask_team* with the customer's "
                "question — a real person is asked on WhatsApp and the customer gets the answer. "
                "Never invent business facts, and never answer yes/no when you don't know.\n"
                "- Show money in ZAR (e.g. R185.00). Keep replies short and WhatsApp-friendly."
                + lang_block
                + persona_block
                + "\n\n" + behaviour_preamble(agentic=True)
                + kb_block
            )

        # Delivery coverage + fee rules as HARD FACTS — without these the model has invented
        # coverage ("Ja, ons lewer na Timbuktu", live 2026-07-16) instead of escalating.
        delivery_block = ""
        try:
            from vula.commerce.order_workflow import get_order_settings
            cfg = get_order_settings(tenant_id)
            areas = cfg.get("delivery_areas") or []
            lines = []
            if areas:
                lines.append("We ONLY deliver to these areas: " + ", ".join(str(a) for a in areas) + ". "
                             "If a customer asks about delivery ANYWHERE else, do NOT say yes or no — "
                             "call ask_team with their question.")
            else:
                lines.append("You do NOT know this business's delivery area — if asked whether we "
                             "deliver somewhere, do NOT guess, and do NOT answer yes OR no: call "
                             "ask_team with their question.")
            if cfg.get("delivery_fee_cents") is not None:
                lines.append(f"Standard delivery fee: R{cfg['delivery_fee_cents'] / 100:.2f}.")
            if cfg.get("free_delivery_over_cents"):
                lines.append(f"Delivery is FREE for orders of R{cfg['free_delivery_over_cents'] / 100:.2f} or more.")
            if cfg.get("min_order_cents"):
                lines.append(f"Minimum order: R{cfg['min_order_cents'] / 100:.2f} — politely decline "
                             "smaller orders and suggest adding something.")
            if cfg.get("delivery_radius_km") and cfg.get("origin_lat") is not None:
                lines.append(f"We deliver within {cfg['delivery_radius_km']:g} km of "
                             f"{cfg.get('origin_label') or 'the shop'}. If a customer shares their "
                             "location PIN, you'll be told the exact distance — trust that verdict. "
                             "They can also just share a pin to check instantly.")
            delivery_block = "\n\nDELIVERY FACTS (authoritative — never contradict or invent beyond these):\n- " + "\n- ".join(lines)
        except Exception:
            pass
        booking_block = ""
        if _tenant_has_bookings(tenant_id):
            booking_block = (
                "\n- This business takes APPOINTMENTS. If a customer wants to book, agree a date, call "
                "list_availability to show open times, confirm the exact time back to them, then call "
                "book_appointment. To cancel, call cancel_appointment. Never promise a time you haven't "
                "confirmed via list_availability."
            )
        return (
            "You are a friendly, knowledgeable WhatsApp assistant for a South African fresh "
            "fish and chicken delivery business. Your goals: help customers find great products, "
            "inspire them with recipes, build their cart, and check out.\n\n"
            "Guidelines:\n"
            "- Reply in the SAME language the customer writes in. South Africans message in English, "
            "Afrikaans, isiZulu, isiXhosa, Sesotho and more — mirror their language naturally and "
            "warmly. If they mix languages, follow their lead; if unsure, use English.\n"
            "- Use real tools for products, cart and orders — never invent prices or stock.\n"
            "- If you can't answer a question about the BUSINESS itself (delivery coverage, "
            "opening hours, custom/special requests), call *ask_team* with the customer's "
            "question — a real person is asked on WhatsApp and the customer gets the answer. "
            "Never invent business facts, and never answer yes/no when you don't know.\n"
            "- Show money in ZAR (e.g. R185.00). Keep replies short and WhatsApp-friendly.\n"
            "- CART DISCIPLINE (important): only add a product to the cart when the customer has "
            "clearly named THAT specific item AND a quantity. NEVER add several products at once, and "
            "NEVER add items they didn't ask for. If they only greet, ask what's available, or are "
            "vague, call list_products or get_daily_catch and let them choose — add nothing yet.\n"
            "- Voice notes and accents can make numbers unclear. If a quantity is large or surprising "
            "(say 10 or more), repeat it back and ask them to confirm before adding — e.g. 'Just to "
            "check — did you want 10 hake, or 1?'. When unsure of a quantity, ask; don't guess.\n"
            "- Some products are sold by the *kilogram* (their price shows as R…/kg). For those, ask "
            "the customer HOW MANY KG they'd like — halves are fine (0.5, 1, 1.5, 2) — and pass that "
            "as the quantity to add_to_cart. For packs, quantity is simply the number of packs.\n"
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
            "- If a customer wants the same order repeated regularly ('every Friday', 'weekly'), "
            "add the items to their cart, then call create_subscription with the cadence.\n"
            "- If a customer wants to change an order they ALREADY placed, call change_order.\n"
            "- If a customer wants to CANCEL an order they already placed, call cancel_order — "
            "never say an order is cancelled without calling it, and never claim it's fully done; "
            "the team confirms the actual cancellation (a paid order may need a refund).\n"
            "- Only use start_checkout if the customer specifically wants to pay on the website.\n"
            "- For a price quote without ordering, call create_quote and share the number and total.\n"
            "- track_order is ONLY for checking an order's delivery/preparation STATUS by its order "
            "number (e.g. OTH-00042) — it does not know about invoices and will never find one. If "
            "a customer wants a document — their invoice, receipt, a copy, wants it 'resent' — call "
            "resend_invoice instead, even if the number they gave you looks like an order number; it "
            "tries both. Never call track_order with something that looks like an invoice number "
            "(e.g. contains 'INV'), and never invent/guess a number that wasn't actually given to you.\n"
            "- Only call a tool when the customer's message actually needs one. A reply like 'thanks', "
            "an unrelated follow-up ('reply in English', 'ok', a new question) does NOT mean repeat "
            "your last action — respond in plain text, or ask what they'd like, instead."
            + delivery_block
            + lang_block
            + booking_block
            + persona_block
            + "\n\n" + behaviour_preamble(agentic=True)
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
            {"type": "kb", "filename": c.get("filename", "?"), "score": round(c.get("score", 0.0), 3),
             "text": c.get("text", "")[:400]}
            for c in chunks
        ]
        return kb_context, sources

    # ── Agent loop ───────────────────────────────────────────────────────────
    async def _agent_loop(
        self, system_msg: str, history: str, question: str, ctx: Dict[str, Any]
    ) -> str:
        import litellm
        from uuid import uuid4
        from config import settings
        from core.llm_router import escalate_to_cloud, looks_unreliable, compute_confidence

        litellm.drop_params = True
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_msg}]
        if history:
            messages.append({"role": "user", "content": f"(Earlier conversation)\n{history}"})
        messages.append({"role": "user", "content": question})

        run_id = str(uuid4())
        model, api_key, api_base = await resolve_generation_route(
            task_type="commerce_chat", messages=messages, run_id=run_id)

        if ctx.get("booking_focused"):
            tools = _ASK_TEAM_ONLY + BOOKING_TOOL_SPECS
        elif ctx.get("bookings"):
            tools = TOOL_SPECS + BOOKING_TOOL_SPECS
        else:
            tools = TOOL_SPECS

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=900,
                api_key=api_key,
                api_base=api_base,
                # See reasoning.py for why this is unconditional — dropped silently
                # wherever unsupported (cloud routes, tool-calling turns on some
                # backends, older Ollama builds) rather than erroring.
                logprobs=True, top_logprobs=1,
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
                        f"(system: the {tname} tool returned:{fence('TOOL_RESULT', json.dumps(result, default=str)[:1500])} "
                        f"Now reply to the customer in plain, friendly language — never output JSON.)"})
                    continue
                leaked_msg = _extract_message_leak(answer)
                if leaked_msg:
                    logger.warning(
                        "commerce_assistant: model echoed raw tool-result JSON as final answer "
                        "(model=%s) — unwrapped 'message' field instead of sending JSON", model)
                    return leaked_msg
                # Catch-ALL for any other machine-JSON shape (a third leak variant,
                # {"type":"function","name":"cancel_order"}, reached a real customer
                # 2026-07-16 because it matched neither guard above): a JSON object is
                # NEVER a valid customer reply. Nudge the model to answer in prose —
                # escalating a local model to cloud first, which reliably complies.
                if _as_json_dict(answer) is not None:
                    logger.warning(
                        "commerce_assistant: suppressed unrecognised raw-JSON final answer "
                        "(model=%s): %.120s", model, answer)
                    if model.startswith("ollama/"):
                        esc = escalate_to_cloud("local_json_leak", run_id=run_id, task_type="commerce_chat")
                        if esc:
                            model, api_key, api_base = esc
                    messages.append({"role": "assistant", "content": answer})
                    messages.append({"role": "user", "content":
                        "(system: that reply was raw JSON, which must never be shown to the "
                        "customer. Answer the customer's question in plain, friendly language — "
                        "if you can't help with it, say so honestly.)"})
                    continue
                # Requirement (b): a weak local final answer escalates to cloud (tool turns stay local).
                # logprob_conf is None when the backend returned no logprobs (2026-08: previously
                # always None since no caller requested them — see compute_confidence docstring).
                if model.startswith("ollama/"):
                    logprob_conf = compute_confidence(resp)
                    if looks_unreliable(answer, confidence=logprob_conf,
                                        confidence_threshold=settings.local_confidence_threshold):
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
                        "content": fence('TOOL_RESULT', json.dumps(result, default=str)),
                    }
                )

        # Tool budget exhausted — ask the model to summarise without more tools.
        # See commerce_admin.py's _agent_loop for why this nudge exists (2026-08-22 real
        # fabricated-success incident) — matters even more here since this skill talks straight
        # to customers: a false "your order is placed" is the worst version of this bug.
        messages.append({"role": "user", "content": (
            "You were not able to complete this within the available attempts. Do NOT claim an "
            "order, booking, or payment succeeded unless a tool result above actually shows "
            "that. Tell the customer plainly what's missing or that you need to check with the "
            "team instead."
        )})
        resp = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=600,
            api_key=api_key,
            api_base=api_base,
        )
        final_answer = (resp.choices[0].message.content or "").strip()
        leaked = _extract_message_leak(final_answer)
        if leaked:
            return leaked
        if _as_json_dict(final_answer) is not None:  # same never-send-JSON rule as the main loop
            return "Sorry — I got a bit tangled up there. Could you ask me that again? 🙏"
        return final_answer

    # ── Tool dispatch + executors ────────────────────────────────────────────
    async def _dispatch_tool(self, name: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
        tid, sid, phone = ctx["tenant_id"], ctx["session_id"], ctx["customer_phone"]
        try:
            from core.reasoning_telemetry import log_tool_call
            log_tool_call(tid, "customer", name, args)
        except Exception:
            pass
        if name == "list_products":
            return await self._exec_list_products(tid, args)
        if name == "add_to_cart":
            return await self._exec_add_to_cart(tid, sid, phone, args, ctx)
        if name == "view_cart":
            return await self._exec_view_cart(tid, sid, phone)
        if name == "start_checkout":
            return self._exec_start_checkout(tid)
        if name == "track_order":
            return await self._exec_track_order(tid, args)
        if name == "resend_invoice":
            return await self._exec_resend_invoice(tid, phone, args)
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
        if name == "cancel_order":
            return await self._exec_cancel_order(tid, phone)
        if name == "ask_team":
            return await self._exec_ask_team(tid, phone, args)
        if name == "place_order":
            return await self._exec_place_order(tid, sid, phone, args, ctx)
        if name == "list_availability":
            return await self._exec_list_availability(tid, args)
        if name == "book_appointment":
            return await self._exec_book_appointment(tid, phone, args)
        if name == "cancel_appointment":
            return await self._exec_cancel_appointment(tid, phone)
        if name == "create_subscription":
            return await self._exec_create_subscription(tid, sid, phone, args)
        if name == "list_my_subscriptions":
            return await self._exec_list_my_subscriptions(tid, phone)
        if name == "cancel_subscription":
            return await self._exec_set_my_subscription_status(tid, phone, "cancelled")
        if name == "pause_subscription":
            return await self._exec_set_my_subscription_status(tid, phone, "paused")
        if name == "reorder_last":
            return await self._exec_reorder_last(tid, sid, phone)
        return {"error": f"unknown tool {name}"}

    async def _exec_create_subscription(self, tenant_id: str, session_id: str,
                                        phone: Optional[str], args: Dict[str, Any]) -> Dict[str, Any]:
        from vula.commerce import subscriptions as subs
        cart = await service.get_or_create_cart(tenant_id, session_id, phone)
        items = (cart or {}).get("commerce_cart_items") or []
        if not items:
            return {"error": "Their cart is empty — add the items they want repeated first."}
        sub_items = [{
            "product_id": i.get("product_id"),
            "product_name": (i.get("commerce_products") or {}).get("name") or "",
            "quantity": i.get("quantity") or 1,
            "unit_price_cents": i.get("unit_price_cents") or 0,
        } for i in items]
        cadence = args.get("cadence") if args.get("cadence") in subs.CADENCES else "weekly"
        res = await subs.create(tenant_id, {
            "customer_phone": phone, "customer_name": (cart or {}).get("customer_name"),
            "items": sub_items, "cadence": cadence, "channel": "whatsapp",
            "delivery_cents": (cart or {}).get("delivery_cents") or 0,
        })
        if res.get("error"):
            return res
        s = res["subscription"]
        return {"created": True, "cadence": cadence, "next_run": s.get("next_run"),
                "message": f"Standing order set — repeats {cadence}, next on {s.get('next_run')}."}

    async def _my_subscriptions(self, tenant_id: str, phone: Optional[str]) -> List[dict]:
        from vula.commerce import subscriptions as subs
        rows = await subs.list_subs(tenant_id, phone=phone or "")
        return [s for s in rows if s.get("status") in ("active", "paused")]

    async def _exec_list_my_subscriptions(self, tenant_id: str, phone: Optional[str]) -> Dict[str, Any]:
        rows = await self._my_subscriptions(tenant_id, phone)
        if not rows:
            return {"message": "No standing orders found for this number."}
        return {"subscriptions": [
            {"cadence": s.get("cadence"), "status": s.get("status"), "next_run": s.get("next_run"),
             "items": [i.get("product_name") for i in (s.get("items") or [])]} for s in rows]}

    async def _exec_set_my_subscription_status(self, tenant_id: str, phone: Optional[str],
                                               status: str) -> Dict[str, Any]:
        from vula.commerce import subscriptions as subs
        rows = await self._my_subscriptions(tenant_id, phone)
        if not rows:
            return {"error": "I couldn't find a standing order for this number to "
                             f"{'cancel' if status == 'cancelled' else 'pause'}."}
        if len(rows) > 1:
            cadences = ", ".join(s.get("cadence", "?") for s in rows)
            return {"error": f"Found more than one standing order ({cadences}) — ask which one "
                             "before I change anything (I can't tell them apart yet)."}
        sub = rows[0]
        await subs.set_status(tenant_id, sub["id"], status)
        verb = "cancelled" if status == "cancelled" else "paused"
        return {"updated": True, "status": status,
                "message": f"Done — your {sub.get('cadence')} standing order is now {verb}."}

    async def _exec_reorder_last(self, tenant_id: str, session_id: str,
                                 phone: Optional[str]) -> Dict[str, Any]:
        try:
            last = await service.reorder_from_last_order(tenant_id, phone or "")
        except ValueError as exc:
            return {"error": str(exc)}
        cart = await service.get_or_create_cart(tenant_id, session_id, phone)
        added, skipped = [], []
        for it in last["items"]:
            product = await service.get_product(tenant_id, it.get("product_id"))
            if not product or not product.get("in_stock"):
                skipped.append(it.get("product_name") or "an item")
                continue
            await service.add_to_cart(tenant_id, cart["id"], it["product_id"], it.get("quantity") or 1,
                                      variant_id=it.get("variant_id"))
            added.append(it.get("product_name") or product.get("name") or "item")
        if not added:
            return {"error": f"Sorry, nothing from order {last['display_id']} is still available to reorder."}
        return {"repeated_order": last["display_id"], "items_added": added, "skipped": skipped,
                "instruction_to_assistant": "Tell the customer what was added (and mention "
                                            "anything skipped as no longer available), then call "
                                            "review_order so they can confirm."}

    # ── Booking tool handlers (only reachable when the tenant has bookings) ────
    async def _exec_list_availability(self, tenant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from vula.bookings import service as bk
        svc_id = None
        want = (args.get("service") or "").strip().lower()
        if want:
            for s in await bk.list_services(tenant_id):
                if want in (s.get("name") or "").lower():
                    svc_id = s["id"]
                    break
        try:
            result = await bk.availability(tenant_id, (args.get("date") or "").strip(), svc_id)
        except ValueError as exc:
            return {"error": str(exc)}
        if result.get("closed"):
            return {"date": result["date"], "message": "We're closed that day — please pick another date."}
        times = [s["label"] for s in result.get("slots", [])]
        return {"date": result["date"], "available_times": times[:12],
                "message": ("No free slots that day." if not times
                            else f"{len(times)} slots available.")}

    async def _exec_book_appointment(self, tenant_id: str, phone: Optional[str],
                                     args: Dict[str, Any]) -> Dict[str, Any]:
        from vula.bookings import service as bk
        svc_id, svc_name = None, (args.get("service") or "").strip()
        if svc_name:
            for s in await bk.list_services(tenant_id):
                if svc_name.lower() in (s.get("name") or "").lower():
                    svc_id, svc_name = s["id"], s["name"]
                    break
        res = await bk.create_booking(tenant_id, {
            "service_id": svc_id, "service_name": svc_name or None,
            "customer_name": args.get("customer_name"), "customer_phone": phone,
            "start": args.get("start"), "channel": "whatsapp",
        })
        if res.get("error"):
            return res
        b = res["booking"]
        return {"confirmed": True, "when": b.get("start_local"),
                "service": b.get("service_name"),
                "message": f"Booked for {b.get('start_local')}."}

    async def _exec_cancel_appointment(self, tenant_id: str, phone: Optional[str]) -> Dict[str, Any]:
        from vula.bookings import service as bk
        if not phone:
            return {"error": "I need your phone number on file to find the booking."}
        upcoming = await bk.list_bookings(tenant_id, status="confirmed",
                                          from_utc=bk._now_utc().isoformat(), phone=phone)
        if not upcoming:
            return {"message": "You have no upcoming appointments to cancel."}
        await bk.set_status(tenant_id, upcoming[0]["id"], "cancelled")
        return {"cancelled": True, "message": "Your appointment has been cancelled."}

    async def _exec_list_products(self, tenant_id: str, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        products = await service.list_products(
            tenant_id, category=args.get("category"), in_stock_only=True, statuses={"active"},
            with_variant_price_range=True,
        )
        search = (args.get("search") or "").lower().strip()
        if search:
            products = [
                p for p in products
                if search in p["name"].lower() or search in (p.get("description") or "").lower()
            ]

        def _price(p):
            rng = p.get("variant_price_range")
            if rng:
                return f"From R{rng['min'] / 100:.2f}" if rng["min"] != rng["max"] else f"R{rng['min'] / 100:.2f}"
            return f"R{p['price_cents'] / 100:.2f}"

        return [
            {
                "slug": p["slug"],
                "name": p["name"],
                "price": _price(p),
                "category": p.get("category"),
                **({"options": p["options"]} if p.get("options") else {}),
            }
            for p in products[:20]
        ]

    async def _exec_add_to_cart(
        self, tenant_id: str, session_id: str, phone: Optional[str], args: Dict[str, Any],
        ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        name = (args.get("product") or "").strip()
        product = None
        if re.match(r"^[a-z0-9-]+$", name):
            product = await service.get_product_by_slug(tenant_id, name, statuses={"active", "unlisted"})
        if not product:
            candidates = await service.list_products(tenant_id, in_stock_only=True, statuses={"active"})
            product = next((p for p in candidates if name.lower() in p["name"].lower()), None)
        if not product:
            return {"error": f"No in-stock product matching '{name}'."}

        option_names = product.get("options") or []
        variant = None
        if option_names:
            # get_product_by_slug already attaches non-archived variants; the fuzzy-name match
            # above (via list_products) does not, so fetch them here if still missing.
            variants = product.get("variants")
            if variants is None:
                variants = await service.list_variants(tenant_id, product["id"], include_archived=False)
            if not variants:
                return {"error": f"'{product['name']}' has no available options right now."}
            variant, unresolved = _match_variant(variants, option_names, args.get("variant") or "")
            if not variant:
                choices = {
                    name: sorted({(v.get("option_values") or {}).get(name)
                                 for v in variants if not v.get("archived")} - {None})
                    for name in unresolved
                }
                return {
                    "needs_selection": True,
                    "product": product["name"],
                    "choices": choices,
                    "message": "Ask the customer which " + " and ".join(unresolved) +
                               f" they'd like for {product['name']}, offering: " +
                               "; ".join(f"{k}: {', '.join(v)}" for k, v in choices.items()),
                }
            if not variant.get("in_stock", True):
                return {"error": f"That option of '{product['name']}' is currently out of stock."}

        unit_price_cents = variant.get("price_cents") if variant and variant.get("price_cents") is not None else product["price_cents"]
        # Decimal amount for kg products (e.g. 1.5 kg), whole packs otherwise.
        qty = _norm_qty(product, args.get("quantity", 1))
        cart = await service.get_or_create_cart(tenant_id, session_id, phone)
        await service.add_to_cart(tenant_id, cart["id"], product["id"], qty,
                                  variant_id=variant["id"] if variant else None)
        unit = "/kg" if _is_kg(product) else ""
        variant_label = (", ".join(f"{k}: {v}" for k, v in (variant.get("option_values") or {}).items())
                        if variant else None)
        # 2026-08-14: show the customer a real photo of what they just added — image_url is a
        # real, populated column that was never surfaced anywhere in the ordering conversation
        # before this. WhatsApp send-side already existed (_send_wa_image, used only for the
        # greeting menu header) — this is the first time a product photo appears while actually
        # shopping. Scoped to add-to-cart only (one confirmed item, one image) rather than every
        # browse/list result, to avoid sending a flood of images per message.
        if ctx is not None and product.get("image_url"):
            ctx["media_url"] = product["image_url"]
        return {
            "added": f"{product['name']} ({variant_label})" if variant_label else product["name"],
            "quantity": _fmt_qty(product, qty),
            "unit_price": f"R{unit_price_cents / 100:.2f}{unit}",
            "line_total": f"R{_line_cents(qty, unit_price_cents) / 100:.2f}",
        }

    async def _exec_view_cart(
        self, tenant_id: str, session_id: str, phone: Optional[str]
    ) -> Dict[str, Any]:
        cart = await service.get_or_create_cart(tenant_id, session_id, phone)
        items = cart.get("commerce_cart_items", []) or []
        lines, subtotal = [], 0
        for it in items:
            prod = it.get("commerce_products") or {}
            variant = it.get("commerce_product_variants")
            options = (variant or {}).get("option_values") or {}
            label = ", ".join(f"{k}: {v}" for k, v in options.items())
            line_total = _line_cents(it["quantity"], it["unit_price_cents"])
            subtotal += line_total
            lines.append(
                {"name": f"{prod.get('name')} ({label})" if label else prod.get("name"),
                 "quantity": _fmt_qty(prod, it["quantity"]),
                 "line_total": f"R{line_total / 100:.2f}"}
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
        # Code-level backstop, not just prompt guidance: an invoice number was given here despite
        # the tool description — confirmed live 2026-07-25, twice in a row on a real customer
        # conversation — track_order has no invoice data at all, so guide the NEXT turn instead
        # of a bare "not found" that just repeats the same mistake.
        if "INV" in display_id:
            return {"error": f"{display_id} looks like an invoice number, not an order number — "
                              f"call resend_invoice instead, not track_order."}
        orders = await service.list_orders(tenant_id, limit=50)
        match = next((o for o in orders if (o.get("display_id") or "").upper() == display_id), None)
        if not match:
            return {"error": f"No order {display_id} found."}
        return {
            "order_id": match["display_id"],
            "status": match["status"],
            "total": f"R{match['total_cents'] / 100:.2f}",
        }

    async def _exec_resend_invoice(self, tenant_id: str, phone: Optional[str],
                                   args: Dict[str, Any]) -> Dict[str, Any]:
        """Re-send an already-created invoice PDF via WhatsApp. Looks up by the invoice's OWN
        number OR its order's display id (confirmed live 2026-07-25: a customer gave the real
        invoice number and the assistant said it "wasn't in our system" — the only lookup that
        existed, track_order, only ever matched commerce_orders.display_id, so an invoice number
        could never be found by it). Phone-scoped throughout — only ever finds documents billed
        to the requesting number, never an arbitrary invoice by guessing numbers."""
        digits = "".join(c for c in (phone or "") if c.isdigit())
        number = (args.get("invoice_number") or "").strip().upper()
        db = service._client()
        try:
            rows = (db.table("commerce_invoices").select("*")
                    .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(200)
                    .execute().data or [])
        except Exception as exc:
            return {"error": f"Could not look up invoices: {exc}"}
        mine = [r for r in rows if "".join(c for c in (r.get("customer_phone") or "") if c.isdigit())
               .endswith(digits[-9:] or "x")]
        if not mine:
            return {"error": "I couldn't find any invoices under this number."}

        match = None
        if number:
            match = next((r for r in mine if (r.get("invoice_number") or "").upper() == number), None)
            if not match:
                # They may have given the ORDER number instead — try that too before giving up.
                try:
                    orow = (db.table("commerce_orders").select("id")
                            .eq("tenant_id", tenant_id).eq("display_id", number).limit(1)
                            .execute().data or [])
                    if orow:
                        match = next((r for r in mine if r.get("order_id") == orow[0]["id"]), None)
                except Exception:
                    pass
        else:
            match = mine[0]  # most recent, when no number was given
        if not match:
            return {"error": f"I couldn't find an invoice matching {number!r} under this number."}

        try:
            from vula.commerce.pdf import render_invoice_pdf, merge_branding
            from vula.api.whatsapp import _send_invoice_document
            branding = merge_branding(tenant_id, await service.get_invoice_settings(tenant_id))
            pdf_bytes = render_invoice_pdf(match, branding)
            tenant_name = branding.get("name") or tenant_id.replace("-", " ").title()
            inv_num = match.get("invoice_number", match["id"])
            total = f"R{(int(match.get('total_cents') or 0) / 100):.2f}"
            caption = f"Here's a copy of your invoice {inv_num} for {total} from {tenant_name}."
            if match.get("pay_url") and match.get("status") != "paid":
                caption += f"\n\n💳 Pay now: {match['pay_url']}"
            sent = await _send_invoice_document(phone, pdf_bytes, f"{inv_num}.pdf", caption, tenant_id)
        except Exception as exc:
            return {"error": f"Found the invoice but couldn't send the PDF: {exc}"}
        if not sent:
            return {"error": "Found the invoice but the WhatsApp document send failed."}
        return {"invoice_number": inv_num, "total": total,
                "instruction_to_assistant": "The PDF was just sent as a separate WhatsApp "
                                            "document message — just confirm briefly, don't "
                                            "repeat the invoice details in your text reply."}

    async def _exec_get_daily_catch(self, tenant_id: str) -> Dict[str, Any]:
        """Return products flagged as today's catch + any fresh fish in stock."""
        try:
            all_products = await service.list_products(tenant_id, in_stock_only=True, statuses={"active"})
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
            products = await service.list_products(tenant_id, in_stock_only=True, statuses={"active"})
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
            product = await service.get_product_by_slug(tenant_id, name, statuses={"active", "unlisted"})
            if product:
                return product
        candidates = await service.list_products(tenant_id, in_stock_only=True, statuses={"active"})
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
                qty = _norm_qty(product, it.get("quantity", 1))   # decimals for kg items
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
        subtotal = sum(_line_cents(i["quantity"], i["unit_price_cents"]) for i in items)
        delivery = cart.get("delivery_cents", 8000)

        # Discount preview only — never trust this for the amount actually charged. place_order
        # re-resolves the code authoritatively inside service.create_order (same as the
        # storefront), so a preview mismatch here can never under-charge.
        discount_cents, discount_note, code_given = 0, "", (args.get("discount_code") or "").strip()
        if code_given:
            try:
                resolved = await service.resolve_discount_code(tenant_id, code_given, subtotal, phone or "")
                discount_cents = resolved["discount_cents"]
                if resolved["free_shipping"]:
                    delivery = 0
                discount_note = f"\nDiscount ({resolved['code_row']['code']}): -R{discount_cents / 100:.2f}"
            except service.DiscountError as exc:
                discount_note = f"\n⚠️ {exc}"
        total = max(0, subtotal - discount_cents) + delivery

        item_lines = "\n".join(
            f"• {_fmt_qty(it.get('commerce_products') or {}, it['quantity'])} "
            f"{(it.get('commerce_products') or {}).get('name', 'Item')} — "
            f"R{_line_cents(it['quantity'], it['unit_price_cents']) / 100:.2f}" for it in items)
        addr = (args.get("delivery_address") or "").strip()
        pay = ow.PAYMENT_LABELS.get(method) if method in enabled else None
        preview = (
            f"🧾 *Please check your order:*\n{item_lines}\n"
            f"Subtotal: R{subtotal / 100:.2f}\nDelivery: R{delivery / 100:.2f}" + discount_note
            + f"\n*Total: R{total / 100:.2f}*"
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

    async def _exec_ask_team(self, tenant_id: str, phone: Optional[str],
                             args: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministic escalation: the model CALLS this instead of having to phrase 'I'm not
        sure' in a way a regex can detect — which a local model failed to do live 2026-07-16
        (it confidently deflected a delivery question and escalation never fired). Reuses the
        same escalate-and-learn plumbing as the phrasing-based path in vula/api/whatsapp.py."""
        question = (args.get("question") or "").strip()
        if not question:
            return {"error": "Pass the customer's actual question to ask_team."}
        try:
            from vula import escalation as esc
            learned = esc.find_learned_answer(tenant_id, question)
            if learned:
                return {"answer_from_team": learned,
                        "instruction_to_assistant": "The team has answered this exact question "
                                                    "before — relay this answer to the customer."}
            row = esc.create_escalation(tenant_id, phone or "", question)
            if not row:
                return {"error": "No team helper is configured for this business — tell the "
                                 "customer you'll find out and someone will get back to them."}
            from vula.api.whatsapp import _send_reply
            await _send_reply(
                row["helper_phone"],
                f"❓ A customer asked:\n\n\"{question}\"\n\nReply to this message with the "
                f"answer — I'll send it to them and remember it.",
                tenant_id=tenant_id)
            return {"escalated": True,
                    "instruction_to_assistant": "Tell the customer (in their language) that "
                    "you're checking with the team and will get back to them shortly. Do NOT "
                    "invent an answer in the meantime."}
        except Exception as exc:
            return {"error": f"could not reach the team right now: {exc}"}

    async def _exec_cancel_order(self, tenant_id: str, phone: Optional[str]) -> Dict[str, Any]:
        """Customer wants to cancel order(s) they've already placed. Flags every live order to
        the shop team — never marks it cancelled directly, since a paid order may need a real
        refund (a human step), and this codebase had no cancel path at all before (the assistant
        was previously just improvising a reply with no actual effect)."""
        digits = "".join(c for c in (phone or "") if c.isdigit())
        orders = await service.list_orders(tenant_id, limit=30)
        live = [o for o in orders
                if "".join(c for c in (o.get("customer_phone") or "") if c.isdigit()).endswith(digits[-9:] or "x")
                and o.get("status") not in ("delivered", "cancelled", "refunded")]
        if not live:
            return {"error": "I couldn't find a live order to cancel — could be already delivered."}
        try:
            from vula.commerce import order_workflow as ow
            for order in live:
                await ow.dispatch_order(tenant_id, order["id"],
                                        f"❌ CANCEL REQUEST on {order['display_id']}",
                                        order.get("customer_name") or "")
        except Exception as exc:
            logger.warning("cancel_order notify failed: %s", exc)
        numbers = ", ".join(o["display_id"] for o in live)
        return {"order_numbers": numbers,
                "message": f"Got it — I've asked the team to cancel {numbers}. They'll confirm "
                           f"with you shortly (a refund may be needed if it was already paid)."}

    async def _exec_place_order(
        self, tenant_id: str, session_id: str, phone: Optional[str], args: Dict[str, Any],
        ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Turn the current cart into a confirmed order with a chosen payment method, and
        return an itemised confirmation (the customer's quotation). Handles online card
        (pay-link if a gateway is connected), pay-on-delivery, and EFT/bank transfer."""
        from vula.commerce import order_workflow as ow

        current_msg = ((ctx or {}).get("current_message") or "").strip()
        if not _CONFIRM_RE.search(current_msg):
            return {"error": "The customer has NOT explicitly confirmed in their latest message — "
                              "do not place the order. Call review_order to show them the itemised "
                              "total first, then wait for them to reply CONFIRM (or a clear yes) "
                              "before calling place_order again."}

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
        # Prefer the verified on-file contact name over whatever the model supplied — a local
        # model has invented generic placeholders ("Customer's", "Klient") instead of using the
        # real name already captured during onboarding.
        contact_name = ""
        try:
            from vula.commerce import onboarding as _onb
            contact = _onb.get_contact(tenant_id, phone or "")
            contact_name = (contact or {}).get("name") or ""
        except Exception:
            pass
        name = (contact_name or args.get("customer_name") or "Customer").strip()
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
                # Resolved authoritatively inside create_order (never trusts a client-side
                # preview amount) — an invalid/expired code is silently dropped there, same as
                # the storefront, rather than failing the whole checkout.
                "discount_code": (args.get("discount_code") or "").strip() or None,
            })
        except Exception as exc:
            logger.warning("place_order create_order failed: %s", exc)
            return {"error": "Something went wrong placing the order — please try again in a moment."}

        lines = []
        for it in items:
            prod = it.get("commerce_products") or {}
            qty, unit = it["quantity"], it["unit_price_cents"]
            lines.append({"name": prod.get("name", "Item"), "quantity": _fmt_qty(prod, qty),
                          "line_total": f"R{_line_cents(qty, unit) / 100:.2f}"})

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

        try:
            from vula.commerce.service import send_order_invoice
            await send_order_invoice(tenant_id, order["id"])
        except Exception as exc:
            logger.debug("auto invoice send skipped: %s", exc)

        item_lines = "\n".join(f"• {l['quantity']} × {l['name']} — {l['line_total']}" for l in lines)
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
                notify_url=f"{api}/v1/payments/webhook/{tenant_id}/{prov}",
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
            item_str = "\n".join(f"  • {l['quantity']} × {l['name']} — {l['line_total']}" for l in lines)
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

        catalog = ""
        if not ctx.get("booking_focused"):
            try:
                products = await service.list_products(inp.tenant_id, in_stock_only=True, statuses={"active"})
                catalog = "\n".join(
                    f"- {p['name']} ({p['slug']}): R{p['price_cents'] / 100:.2f}" for p in products[:30]
                )
            except Exception:
                catalog = ""

        system_msg = self._system_prompt(inp.tenant_id, kb_context, booking_focused=ctx.get("booking_focused", False))
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

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
from core.llm_router import (
    resolve_generation_route, escalate_to_cloud, looks_degenerate, DEGENERATE_OUTPUT_FALLBACK,
)
from core.prompt_safety import fence
from core.reasoning_telemetry import emit as _emit, log_tool_call as _log_tool_call
from core.skills.base import (
    BaseSkill, SkillInput, SkillOutput, behaviour_preamble, need_info_message, tool_source,
    looks_like_tenant_data_question,
)
from vula.commerce import service

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 3

# 2026-08-08: the four guardrails that used to live here as a local `_GUARDRAILS` constant
# (added after a real-transcript review found an off-topic non-answer, a leaked internal tool
# name, and a hallucinated "exported to Xero" claim) now live centrally as `AGENTIC_RULES` in
# core/skills/base.py, alongside the platform's other shared behaviour policy — every other
# tool-calling skill was found to have the exact same gaps, and commerce_admin itself was found
# to be missing the FOUNDATIONAL shared policy (ethics/honesty/reasoning/untrusted-content
# rules) entirely, not just these four. See `behaviour_preamble(agentic=True)` below.

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
        "description": "Set a product's stock quantity by name or slug. Without confirm=true, "
                       "returns a preview (current vs. new quantity) instead of applying it — "
                       "only pass confirm=true after the owner has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string"}, "quantity": {"type": "integer"},
            "confirm": {"type": "boolean"}},
            "required": ["product", "quantity"]},
    }},
    {"type": "function", "function": {
        "name": "outstanding_invoices",
        "description": "List unpaid/overdue invoices and the total amount owed.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "find_document",
        "description": "Search filed documents (invoices, quotes, proof-of-payment, BOQs, "
                       "receipts, etc.) by what they're about — a supplier/customer name, an "
                       "invoice number, an amount, what it was for. Use this ONLY when the owner "
                       "refers to a specific document/invoice/payment they've already sent "
                       "('that invoice', 'the proof of payment I sent you', 'this payment on "
                       "the bank statement') and you need to identify exactly which one before "
                       "acting or answering. Do NOT use this for a request to CREATE something "
                       "new (e.g. 'make an invoice for X') — go straight to create_invoice for "
                       "that, there's no existing document to look up yet. If no result matches "
                       "well, say so and ask the owner for the invoice/document number, or to "
                       "resend it — do not fall back to a different, unrelated tool (e.g. "
                       "logging an expense, checking bookings) just because nothing matched; "
                       "either proceed with what was actually asked or ask a plain question.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Free text: supplier/customer name, "
                      "invoice number, amount, or what the document was for."},
            "category": {"type": "string", "description": "Optional filter, e.g. 'Invoice', "
                        "'Proof of Payment', 'Quote / Estimate', 'Bill of Quantities (BOQ)'."}},
            "required": ["query"]},
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
    {"type": "function", "function": {
        "name": "reimbursement_balance",
        "description": "What the business owes a specific person for money they paid out of pocket "
                       "(e.g. stock/materials bought on a personal card or cash) that hasn't been "
                       "reimbursed yet. Answers 'what do we owe X', 'has Y been paid back'.",
        "parameters": {"type": "object", "properties": {
            "payee": {"type": "string", "description": "The person's name or phone number."}},
            "required": ["payee"]},
    }},
    {"type": "function", "function": {
        "name": "learn_my_voice",
        "description": "Analyze the owner's own real WhatsApp replies, meeting notes, and sent "
                       "emails and suggest how Vula should sound to match their tone. Answers "
                       "'learn my tone', 'sound more like me'. Show the suggestion to the owner "
                       "and ask if they want it before calling apply_voice_persona — never apply "
                       "it without them explicitly saying yes.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "apply_voice_persona",
        "description": "Adopt a suggested voice/tone as how Vula sounds going forward. Only call "
                       "after the owner has explicitly confirmed they want to use it.",
        "parameters": {"type": "object", "properties": {
            "persona_prompt": {"type": "string", "description": "The exact suggested tone text "
                               "to adopt, as returned by learn_my_voice."}},
            "required": ["persona_prompt"]},
    }},
    {"type": "function", "function": {
        "name": "create_manual_order",
        "description": "Log an order taken by phone or in person on the customer's behalf — "
                       "uses the same cart/stock/pricing path as the storefront and WhatsApp "
                       "ordering, so it behaves identically. Without confirm=true, returns a "
                       "preview instead of creating it — only pass confirm=true after the owner "
                       "has explicitly said to go ahead. If mark_paid is true, say so plainly in "
                       "the preview since that also records a real payment.",
        "parameters": {"type": "object", "properties": {
            "customer_name": {"type": "string"}, "customer_phone": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "product": {"type": "string"}, "quantity": {"type": "number"}}}},
            "delivery_address": {"type": "string"},
            "payment_method": {"type": "string", "enum": ["cod", "eft", "online"]},
            "mark_paid": {"type": "boolean"}, "confirm": {"type": "boolean"}},
            "required": ["customer_name", "customer_phone", "items"]},
    }},
]

# ── Module-gated tools (added to the base set only for tenants with that module) ──
# create_automation_rule is gated behind the "automations" module (same module the dashboard's
# Automations tab checks) rather than being always-on — its default-food/retail scoping matches
# the trigger vocabulary itself (order status, product stock), and a tenant without the module
# has no dashboard tab to review/approve the pending firings this tool would create.
AUTOMATION_TOOLS = [
    {"type": "function", "function": {
        "name": "create_automation_rule",
        "description": "Teach Vula a standing rule from a plain-language description — e.g. "
                       "\"when an order is dispatched, message the customer to say it's on its "
                       "way\" or \"tell the team when stock runs low\". Vula can only automate "
                       "on: an order reaching a chosen status, or a product's stock dropping to "
                       "its reorder threshold — and can only message the customer or the team, "
                       "never anything else. IMPORTANT: this creates a real standing rule — its "
                       "matches still always wait for the owner's approval before anything "
                       "actually sends (reviewed under Automations), but only pass confirm=true "
                       "after the owner has clearly said to go ahead with creating the rule "
                       "itself. Without confirm=true, describe back what rule would be created "
                       "and ask them to confirm.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "The rule in the owner's own words."},
            "confirm": {"type": "boolean"}},
            "required": ["description"]},
    }},
]
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
                "unit": {"type": "string"}, "unit_price_rands": {"type": "number"},
                "section": {"type": "string", "description": "Optional BoQ trade section "
                            "to group this line under, e.g. 'Demolition', 'Structure' — "
                            "only set this if the owner explicitly organised the invoice "
                            "into sections; leave unset otherwise."}}}},
            "discount_pct": {"type": "number"}, "deposit_rands": {"type": "number"}},
            "required": ["customer_name", "line_items"]}}},
    {"type": "function", "function": {
        "name": "send_invoice",
        "description": "Send an existing invoice to the customer on WhatsApp, by its number (e.g. OTH-INV-00001).",
        "parameters": {"type": "object", "properties": {"invoice_number": {"type": "string"}},
                       "required": ["invoice_number"]}}},
    {"type": "function", "function": {
        "name": "record_payment",
        "description": "Record a payment received against an existing invoice, by its number "
                       "(e.g. OTH-INV-00001). Supports partial payments — status becomes "
                       "'part_paid' until the running total reaches the invoice total, then "
                       "flips to 'paid' automatically. Without confirm=true, returns a preview "
                       "instead of recording it — only pass confirm=true after the owner has "
                       "explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "invoice_number": {"type": "string"},
            "amount_rands": {"type": "number"},
            "payment_method": {"type": "string", "description": "e.g. eft, cash, card, snapscan"},
            "note": {"type": "string"}, "confirm": {"type": "boolean"}},
            "required": ["invoice_number", "amount_rands"]}}},
    {"type": "function", "function": {
        "name": "list_quotes",
        "description": "List quotes, optionally filtered by status (draft, sent, accepted, declined, expired). "
                       "To create a quote, use create_invoice with doc_type='quote'.",
        "parameters": {"type": "object", "properties": {"status": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "convert_quote_to_invoice",
        "description": "Convert an accepted quote into a real invoice, by the quote's number "
                       "(e.g. OTH-QUO-00003). The quote must already be marked accepted "
                       "(update_quote_status) first.",
        "parameters": {"type": "object", "properties": {"quote_number": {"type": "string"}},
                       "required": ["quote_number"]}}},
    {"type": "function", "function": {
        "name": "update_quote_status",
        "description": "Change a quote's status by its number, e.g. mark it accepted after "
                       "the customer agrees, or declined/expired.",
        "parameters": {"type": "object", "properties": {
            "quote_number": {"type": "string"},
            "status": {"type": "string", "enum": ["sent", "accepted", "declined", "expired"]}},
            "required": ["quote_number", "status"]}}},
]
PURCHASE_ORDER_TOOLS = [
    {"type": "function", "function": {
        "name": "list_suppliers",
        "description": "List known suppliers.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "upsert_supplier",
        "description": "Add or update a supplier (matched by name). Without confirm=true, "
                       "returns a preview instead of saving — only pass confirm=true after the "
                       "owner has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "contact_email": {"type": "string"},
            "contact_phone": {"type": "string"}, "payment_terms": {"type": "string"},
            "confirm": {"type": "boolean"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "delete_supplier",
        "description": "Remove a supplier by name. Without confirm=true, returns a preview "
                       "instead of deleting — only pass confirm=true after the owner has "
                       "explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"},
                       "confirm": {"type": "boolean"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "reorder_suggestions",
        "description": "Products at or below their reorder threshold, grouped by supplier — "
                       "what should I order right now.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "create_purchase_order",
        "description": "Create a draft purchase order for a supplier with line items. Without "
                       "confirm=true, returns a preview instead of creating it — only pass "
                       "confirm=true after the owner has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "supplier_name": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "quantity": {"type": "number"},
                "unit_cost_rands": {"type": "number"}}}},
            "notes": {"type": "string"}, "confirm": {"type": "boolean"}},
            "required": ["supplier_name", "items"]}}},
    {"type": "function", "function": {
        "name": "list_purchase_orders",
        "description": "List purchase orders, optionally filtered by status (draft, sent, received, cancelled).",
        "parameters": {"type": "object", "properties": {"status": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "update_po_status",
        "description": "Change a purchase order's status by its short reference (first 8 "
                       "characters of its id, as shown by list_purchase_orders). Marking it "
                       "'received' bumps real stock quantities for every line item. Without "
                       "confirm=true, returns a preview instead of applying it — only pass "
                       "confirm=true after the owner has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "po_ref": {"type": "string"},
            "status": {"type": "string", "enum": ["draft", "sent", "received", "cancelled"]},
            "confirm": {"type": "boolean"}},
            "required": ["po_ref", "status"]}}},
    {"type": "function", "function": {
        "name": "send_purchase_order",
        "description": "Actually dispatch a draft purchase order to its supplier by email "
                       "and/or WhatsApp, by its short reference — commits real spend. Without "
                       "confirm=true, returns a preview instead of sending — only pass "
                       "confirm=true after the owner has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "po_ref": {"type": "string"},
            "channel": {"type": "string", "enum": ["email", "whatsapp", "both"]},
            "confirm": {"type": "boolean"}},
            "required": ["po_ref"]}}},
]
DISCOUNT_TOOLS = [
    {"type": "function", "function": {
        "name": "list_discount_codes",
        "description": "List discount/promo codes.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "create_discount_code",
        "description": "Create a redeemable discount code for customers. Without confirm=true, "
                       "returns a preview instead of creating it — only pass confirm=true "
                       "after the owner has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "e.g. WEEKEND10"},
            "discount_type": {"type": "string", "enum": ["percent", "fixed", "free_shipping"]},
            "value": {"type": "number", "description": "Percentage number (e.g. 10 for 10%) "
                      "for type=percent, or amount in Rands for type=fixed. Ignored for free_shipping."},
            "min_order_rands": {"type": "number"},
            "usage_limit": {"type": "integer"}, "confirm": {"type": "boolean"}},
            "required": ["code", "discount_type"]}}},
    {"type": "function", "function": {
        "name": "update_discount_code",
        "description": "Change an existing discount code's active state or other fields, by "
                       "its code. Without confirm=true, returns a preview instead of applying "
                       "it — only pass confirm=true after the owner has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}, "active": {"type": "boolean"},
            "usage_limit": {"type": "integer"}, "confirm": {"type": "boolean"}},
            "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "delete_discount_code",
        "description": "Delete a discount code by its code. Without confirm=true, returns a "
                       "preview instead of deleting — only pass confirm=true after the owner "
                       "has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"},
                       "confirm": {"type": "boolean"}},
                       "required": ["code"]}}},
]
PRODUCT_TOOLS = [
    {"type": "function", "function": {
        "name": "create_product",
        "description": "Add a new product/service to the catalogue with a price in Rands. "
                       "Without confirm=true, returns a preview instead of creating it — only "
                       "pass confirm=true after the owner has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "price_rands": {"type": "number"},
            "description": {"type": "string"}, "category": {"type": "string"},
            "confirm": {"type": "boolean"}},
            "required": ["name", "price_rands"]}}},
    {"type": "function", "function": {
        "name": "update_product",
        "description": "Change a product's price, name, or daily-catch flag (find it by name). "
                       "Without confirm=true, returns a preview instead of applying it — only "
                       "pass confirm=true after the owner has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string"}, "price_rands": {"type": "number"},
            "new_name": {"type": "string"}, "is_daily_catch": {"type": "boolean"},
            "confirm": {"type": "boolean"}},
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
        "description": "Book an appointment: start time (YYYY-MM-DDTHH:MM), customer name/phone, "
                       "optional service. Without confirm=true, returns a preview instead of "
                       "booking it — only pass confirm=true after the owner has explicitly said "
                       "to go ahead.",
        "parameters": {"type": "object", "properties": {
            "start": {"type": "string"}, "customer_name": {"type": "string"},
            "customer_phone": {"type": "string"}, "service": {"type": "string"},
            "confirm": {"type": "boolean"}},
            "required": ["start", "customer_name"]}}},
    {"type": "function", "function": {
        "name": "cancel_booking",
        "description": "Cancel an appointment by its id. Without confirm=true, returns a "
                       "preview instead of cancelling — only pass confirm=true after the owner "
                       "has explicitly said to go ahead.",
        "parameters": {"type": "object", "properties": {"booking_id": {"type": "string"},
                       "confirm": {"type": "boolean"}}, "required": ["booking_id"]}}},
]
MARKETING_TOOLS = [
    {"type": "function", "function": {
        "name": "generate_marketing",
        "description": "Write marketing copy: today's specials post, a product description, promo, or "
                       "broadcast text. Give a couple of options by default so you can pick the best one.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["specials", "product", "promo", "broadcast"]},
            "topic": {"type": "string"}, "tone": {"type": "string"},
            "variant_count": {"type": "integer", "description": "How many options to generate (1-3, default 3)."}}}}},
]
DRAFT_TOOLS = [
    {"type": "function", "function": {
        "name": "draft_letter",
        "description": (
            "Draft a professional business letter/proposal, put it on the business's branded "
            "letterhead as a PDF, and send it back as a WhatsApp document. Optionally also save "
            "it to the user's connected Google Drive."
        ),
        "parameters": {"type": "object", "properties": {
            "document_type": {"type": "string", "enum": [
                "fee_proposal", "scope_of_works", "appointment_letter", "site_meeting_minutes",
                "tender_invitation", "project_programme", "payment_certificate"],
                "description": "fee_proposal | scope_of_works | appointment_letter | "
                               "site_meeting_minutes | tender_invitation | project_programme | "
                               "payment_certificate"},
            "brief": {"type": "string", "description": "What the letter should say — the fuller, the better."},
            "project_name": {"type": "string"},
            "client_name": {"type": "string"},
            "recipient": {"type": "string", "description": "Who it's addressed to (name/address block)."},
            "save_to_drive": {"type": "boolean", "description": "Also save a copy to Google Drive."},
        }, "required": ["document_type", "brief"]}}},
    {"type": "function", "function": {
        "name": "competitor_check",
        "description": (
            "Research a competitor or market price online — searches the live web and "
            "summarises what's found into price position, notable differentiators, and a "
            "one-line recommendation."
        ),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to research — a competitor name, "
                      "or e.g. 'eFoil pricing South Africa'."},
        }, "required": ["query"]}}},
]
CONTACT_TOOLS = [
    {"type": "function", "function": {
        "name": "create_contact",
        "description": (
            "Save a new contact (e.g. from a scanned business card or a client you just met) "
            "to your contact book."
        ),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "phone": {"type": "string"},
            "company": {"type": "string"}, "email": {"type": "string"},
            "title": {"type": "string"}, "notes": {"type": "string"}},
            "required": ["name"]}}},
]
MEETING_TOOLS = [
    {"type": "function", "function": {
        "name": "log_meeting",
        "description": (
            "Log a client/site meeting from a description (usually a voice note transcript): "
            "pulls out attendees, a summary, and any action items, files it against the contact, "
            "creates a real reminder for each action item, and sends back a PDF meeting-summary "
            "document automatically — no separate step needed. If the notes mention a follow-up "
            "date, ask the owner whether to book it before calling create_booking."
        ),
        "parameters": {"type": "object", "properties": {
            "notes": {"type": "string", "description": "The meeting description/transcript, as given."},
            "contact_name_or_phone": {"type": "string", "description": "Who the meeting was with, if known."}},
            "required": ["notes"]}}},
    {"type": "function", "function": {
        "name": "draft_followup_email",
        "description": (
            "Draft a thank-you / follow-up email from a logged meeting (attendees, what was "
            "discussed, next steps). Saved as a Gmail DRAFT for you to review and send yourself "
            "— it is never sent automatically."
        ),
        "parameters": {"type": "object", "properties": {
            "to_email": {"type": "string"}, "meeting_notes": {"type": "string"},
            "subject": {"type": "string"}},
            "required": ["to_email", "meeting_notes"]}}},
]
REMINDER_TOOLS = [
    {"type": "function", "function": {
        "name": "create_reminder",
        "description": "Set a reminder/commitment (e.g. 'remind me to follow up with John Friday'). "
                       "An undated reminder (no due date) is fine too.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "What to be reminded about."},
            "due_at": {"type": "string", "description": "ISO datetime, if a specific time was given."},
            "contact_name_or_phone": {"type": "string", "description": "Who this relates to, if known."}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "list_reminders",
        "description": "List reminders — open ones by default, or all including completed.",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["open", "done", "all"]}}}}},
    {"type": "function", "function": {
        "name": "complete_reminder",
        "description": "Mark a reminder done (use list_reminders first to find its id).",
        "parameters": {"type": "object", "properties": {
            "reminder_id": {"type": "string"}}, "required": ["reminder_id"]}}},
]
SUBSCRIPTION_TOOLS = [
    {"type": "function", "function": {
        "name": "create_subscription",
        "description": "Set up a recurring/standing order for a customer (weekly/biweekly/"
                       "monthly). Without confirm=true, returns a preview instead of creating "
                       "it — only pass confirm=true after the owner has explicitly said to go "
                       "ahead.",
        "parameters": {"type": "object", "properties": {
            "customer_name": {"type": "string"}, "customer_phone": {"type": "string"},
            "cadence": {"type": "string", "enum": ["weekly", "biweekly", "monthly"]},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "product": {"type": "string"}, "quantity": {"type": "number"}}}},
            "confirm": {"type": "boolean"}},
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
    {"type": "function", "function": {
        "name": "dynamics_lookup",
        "description": "Search the connected Dynamics 365 CRM for an account (company), contact (person), "
                       "or opportunity by name. Only works once Dynamics 365 is connected for this account.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Name to search for."},
            "kind": {"type": "string", "enum": ["account", "contact", "opportunity"],
                     "description": "What to search — company, person, or open opportunity."}},
            "required": ["query", "kind"]}}},
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
# 2026-08-14: page-building, previously dashboard-only (vula/commerce/page_copy.py, reachable
# only via the page-builder UI's own buttons). Same two-layer confirm=true gate as
# send_broadcast, with one deliberate addition: confirm=true here only ever SAVES A DRAFT, never
# publishes — a storefront page is customer-facing and this is a text-only channel that can't
# show the owner the actual rendered result, so publishing stays a separate, dashboard-side step
# (Save draft/Publish already exist there) — same "proposal only" trust boundary page_copy.py
# was built around from the start. Scoped to editing an EXISTING page only; creating a brand-new
# page from a template is a more visual, dashboard-side decision, not attempted here.
PAGE_TOOLS = [
    {"type": "function", "function": {
        "name": "list_storefront_pages",
        "description": "List this business's storefront pages (draft and published) with their status.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "draft_storefront_page",
        "description": ("Edit an EXISTING storefront page's copy per a specific instruction (e.g. "
                        "'mention we now deliver on weekends'). Without confirm, previews what would "
                        "change. Only call with confirm=true AFTER the owner has explicitly confirmed "
                        "— even then this only SAVES A DRAFT, it never publishes; the owner still "
                        "reviews and publishes from the dashboard."),
        "parameters": {"type": "object", "properties": {
            "slug": {"type": "string", "description": "Page slug, e.g. 'home' for the homepage."},
            "instruction": {"type": "string", "description": "What to change, in plain language."},
            "confirm": {"type": "boolean"}}, "required": ["slug", "instruction"]}}},
    {"type": "function", "function": {
        "name": "add_storefront_section",
        "description": ("Add a new section to an existing storefront page — a real booking calendar "
                        "(linked to actual availability), an FAQ section, or a pricing table. Without "
                        "confirm, previews. Only call with confirm=true AFTER the owner has explicitly "
                        "confirmed — even then this only SAVES A DRAFT, it never publishes."),
        "parameters": {"type": "object", "properties": {
            "slug": {"type": "string"},
            "feature": {"type": "string", "enum": ["booking", "faq", "pricing"]},
            "confirm": {"type": "boolean"}}, "required": ["slug", "feature"]}}},
]

_GATED_GROUPS = [
    ("invoices", INVOICE_TOOLS), ("products", PRODUCT_TOOLS), ("bookings", BOOKING_TOOLS),
    ("orders", SUBSCRIPTION_TOOLS), ("crm", CRM_TOOLS), ("broadcasts", BROADCAST_TOOLS),
    ("pages", PAGE_TOOLS), ("purchase_orders", PURCHASE_ORDER_TOOLS), ("discounts", DISCOUNT_TOOLS),
    ("automations", AUTOMATION_TOOLS),
]
# Always available (universally useful, not tied to a business type): marketing copy, letter
# drafting, and — since a sales rep's own contact book/meeting log is just as relevant to a
# shop owner fielding a client relationship — contacts and meeting logging too.
_ALL_TOOL_SPECS = (TOOL_SPECS + INVOICE_TOOLS + PRODUCT_TOOLS + BOOKING_TOOLS
                   + MARKETING_TOOLS + DRAFT_TOOLS + SUBSCRIPTION_TOOLS + CRM_TOOLS
                   + BROADCAST_TOOLS + CONTACT_TOOLS + MEETING_TOOLS + REMINDER_TOOLS
                   + PAGE_TOOLS + PURCHASE_ORDER_TOOLS + DISCOUNT_TOOLS + AUTOMATION_TOOLS)

# A sales rep sharing the tenant's WhatsApp number with the owner/other reps gets a personal-
# scope toolset — their own contacts, meetings, proposals, and bookings — not shop-wide levers
# (stock, invoices, broadcasts, products) an individual rep has no business touching.
# 2026-08-24: finance_insights (shop-wide revenue/margin/VAT) used to be included here despite
# directly contradicting this comment's own stated scope — confirmed a real gap, not an
# intentional commission-visibility exception, and removed.
_REP_TOOL_SPECS = (TOOL_SPECS[:0] + MARKETING_TOOLS + DRAFT_TOOLS + BOOKING_TOOLS
                   + CRM_TOOLS + CONTACT_TOOLS + MEETING_TOOLS + REMINDER_TOOLS)


# 2026-08-16: keyword pre-filter for _tools_for's gated groups — added alongside the purchase-
# order/quote/discount/payment tools above, which pushed the flat per-call tool count past 45
# with zero mitigation (an audit found NO intent-based subsetting existed anywhere: every
# message exposed every enabled group's full tool list regardless of topic). Cheap and
# deliberately conservative: only NARROWS the set when a group confidently matches; any message
# that matches nothing (including short follow-ups like "confirm" or "yes") falls back to the
# unfiltered behavior from before this existed, so multi-turn confirm flows are unaffected.
_KEYWORD_GROUPS: Dict[str, set] = {
    "invoices": {"invoice", "quote", "quotation", "bill", "payment", "paid", "owe", "owing",
                 "outstanding", "convert"},
    "products": {"product", "price", "catalogue", "catalog", "stock", "item"},
    "bookings": {"booking", "appointment", "slot", "schedule", "calendar"},
    "orders": {"subscription", "standing order", "recurring"},
    "crm": {"customer", "dynamics", "crm"},
    "broadcasts": {"broadcast", "blast", "campaign"},
    "pages": {"page", "website", "storefront", "section"},
    "purchase_orders": {"purchase order", "supplier", "reorder", "restock", "po "},
    "discounts": {"discount", "promo", "coupon", "voucher"},
    "automations": {"automation", "automate", "rule", "trigger", "teach vula"},
}


def _match_groups(message: str) -> Optional[set]:
    """Which gated tool-groups this message plausibly relates to, by keyword hit. Returns None
    (meaning: don't filter, show everything) if nothing matched confidently."""
    text = (message or "").lower()
    hits = {mod for mod, keywords in _KEYWORD_GROUPS.items() if any(kw in text for kw in keywords)}
    return hits or None


# "AI employee" framing pass (2026-08): the owner/staff prompt introduces Vula by a role label
# rather than just "the assistant" — plain dict, not tenant-overridable yet, easy to extend or
# make configurable later if that proves worth it. Priority order matters: a tenant with both
# "invoices" and "broadcasts" enabled gets "bookkeeper" (the earlier entry), since that's the
# more central day-to-day framing for most small businesses on this platform.
_ROLE_LABELS: List[tuple] = [
    ("invoices", "AI bookkeeper"),
    ("purchase_orders", "AI procurement assistant"),
    ("broadcasts", "AI marketing assistant"),
    ("discounts", "AI marketing assistant"),
    ("bookings", "AI scheduling assistant"),
    ("crm", "AI sales assistant"),
    ("pages", "AI web assistant"),
    ("products", "AI inventory assistant"),
]
_DEFAULT_ROLE_LABEL = "AI business assistant"


def _role_label(tenant_id: str) -> str:
    """Which "AI employee" role to introduce Vula as, based on the tenant's enabled modules —
    e.g. a tenant with invoicing on hears "I'm your AI bookkeeper" rather than a generic
    "assistant". Falls back to the generic label if nothing enabled matches, or on any error."""
    try:
        from vula.api.tenants import enabled_modules
        mods = set(enabled_modules(tenant_id) or [])
    except Exception:
        return _DEFAULT_ROLE_LABEL
    for mod, label in _ROLE_LABELS:
        if mod in mods:
            return label
    return _DEFAULT_ROLE_LABEL


def _tools_for(tenant_id: str, role: Optional[str] = None, message: str = "") -> List[Dict[str, Any]]:
    """Base tools + the gated groups this tenant's modules unlock (finance_insights is always on).
    role="sales_rep" gets the narrower personal-scope set (see _REP_TOOL_SPECS) regardless of
    which modules the tenant has enabled — a rep never gets shop-wide stock/invoice/broadcast
    tools just because the tenant (e.g. their employer) has those modules on. `message`
    (optional) narrows the gated groups further by keyword match — see _match_groups."""
    if role == "sales_rep":
        return list(_REP_TOOL_SPECS)
    try:
        from vula.api.tenants import enabled_modules
        mods = set(enabled_modules(tenant_id) or [])
    except Exception:
        mods = set()
    tools = list(TOOL_SPECS) + MARKETING_TOOLS + DRAFT_TOOLS + CONTACT_TOOLS + MEETING_TOOLS  # always on
    show_all = not mods                       # no config yet → show everything
    matched = _match_groups(message) if message else None
    for mod, group in _GATED_GROUPS:
        if not (show_all or mod in mods):
            continue
        if matched is None or mod in matched:
            tools += group
    return tools


class CommerceAdminSkill(BaseSkill):
    name = "commerce_admin"
    description = (
        "Owner/staff admin assistant — run the shop over WhatsApp: sales, orders, "
        "stock, invoices, expenses, and broadcast previews."
    )
    # 2026-08 accuracy audit: the highest-stakes skill in the platform (real invoice/stock/
    # broadcast mutations) had zero adversarial verification — VRL's checker-framed second
    # pass (core/verification.py) was wired into every skill's call path but never turned on
    # here. Low volume (owner-only), so the added cost of one extra LLM call per request is
    # smallest where it matters most. Never blocks a mutating tool call itself (those are
    # already guarded by the confirm=true gate + post-write readback) — this catches
    # inaccurate READ-ONLY replies (sales summaries, stock status) that had no check at all.
    verification_policy = "adversarial"

    async def run(self, inp: SkillInput) -> SkillOutput:
        caller_role = inp.metadata.get("caller_role")
        ctx = {"tenant_id": inp.tenant_id, "phone": inp.metadata.get("customer_phone"),
               "caller_name": inp.metadata.get("caller_name"), "caller_role": caller_role}
        tools = _tools_for(inp.tenant_id, role=caller_role, message=inp.question)
        system_msg = self._system_prompt(inp.tenant_id, role=caller_role, name=ctx["caller_name"],
                                         lang=inp.metadata.get("preferred_language", ""))
        collected_sources: List[Dict[str, Any]] = []
        try:
            answer = await self._agent_loop(system_msg, inp.conversation_history, inp.question, ctx,
                                            tools, sources=collected_sources)
            if not answer:
                raise RuntimeError("empty answer from admin agent loop")
            # 2026-08-22: a real WhatsApp reply was ~1000 literal '!' characters, sent straight
            # to the owner — nothing caught it. See core.llm_router.looks_degenerate.
            if looks_degenerate(answer):
                answer = DEGENERATE_OUTPUT_FALLBACK
            return SkillOutput(answer=answer, skill_name=self.name, confidence=0.8,
                               sources=collected_sources)
        except Exception as exc:
            logger.warning("commerce_admin loop failed (%s)", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

    def _system_prompt(self, tenant_id: str, role: Optional[str] = None, name: Optional[str] = None,
                       lang: str = "") -> str:
        persona_block = ""
        try:
            from vula.api.tenants import get_config
            persona = (get_config(tenant_id) or {}).get("persona_prompt")
            if persona:
                persona_block = f"\n\nHow you should sound: {persona}"
        except Exception:
            pass

        if role == "sales_rep":
            who = f"{name} — a sales rep/agent" if name else "a sales rep/agent"
            return (
                f"You are {who}'s personal AI sales assistant, reachable on WhatsApp like any "
                "other colleague they'd message. You are talking to THEM, "
                "not a customer — help them run their day: capturing contacts (e.g. from a scanned "
                "business card), logging what happened in a client meeting from a voice note, "
                "drafting a proposal/letter onto branded letterhead, drafting (never sending) a "
                "follow-up email, checking availability, and booking a follow-up meeting. Only offer "
                "what your tools actually support; if you genuinely lack a tool for something, say so "
                "plainly. Use tools to read and change REAL data — never invent facts about a contact "
                "or meeting. Keep replies short and WhatsApp-friendly.\n"
                "IMPORTANT — confirm before anything that can't be undone or reaches someone else: "
                "sending a proposal document, drafting an email (drafts are safe/reversible so this is "
                "lower-stakes, but still confirm the recipient), or booking a meeting. Show the details "
                "and wait for a clear 'yes' first.\n\n"
                + behaviour_preamble(agentic=True, preferred_language=lang) + persona_block
            )
        role_label = _role_label(tenant_id)
        return (
            f"You are the owner's {role_label}, reachable on WhatsApp like any other colleague "
            "they'd message — for a South African business "
            "(you are talking to the owner/staff, not a customer). Help them run the business with "
            "the tools available to you — which may include sales, orders, stock, invoices/quotes, "
            "expenses, products, bookings/appointments, marketing copy, financial insights, recurring "
            "orders, customers, contacts, meeting logs, broadcasts, and their storefront website "
            "pages. Only offer what your tools actually support; if you genuinely lack a tool for "
            "something, say so plainly. Use tools to read and change REAL data — never invent "
            "figures. Show money in ZAR (e.g. R1 250.00). Keep replies short and WhatsApp-friendly "
            "with the key numbers, and confirm back what you changed after any update.\n"
            "IMPORTANT — if the owner refers to a specific document/invoice/payment they've "
            "already sent or that's on a bank statement ('that invoice', 'the proof of payment "
            "I sent you', 'this payment'), use find_document to identify exactly which one "
            "BEFORE acting or answering. This does NOT apply to a request to CREATE something "
            "new ('make an invoice for X') — there's nothing to look up yet, go straight to the "
            "tool that does that. If find_document doesn't turn up a clear match, say so and ask "
            "for the invoice/document number or for it to be resent — never fall back to a "
            "different, unrelated tool (bookings, a meeting log, a finance summary, logging an "
            "expense) just because it's the closest-sounding one; either proceed with what was "
            "actually asked or ask a plain question.\n"
            "IMPORTANT — confirm before acting on anything that spends money, sends messages to "
            "customers, or can't be undone: creating/sending an invoice, sending a broadcast, "
            "cancelling/refunding, or adopting a new voice/tone. Show the details and wait for a "
            "clear 'yes' first. For send_broadcast, only pass confirm=true after the owner has "
            "explicitly confirmed. For apply_voice_persona, only call it after showing the "
            "learn_my_voice suggestion and getting a clear yes. For draft_storefront_page/"
            "add_storefront_section, show what would change and only pass confirm=true after a "
            "clear yes — note that even confirmed, these only SAVE A DRAFT and never publish live "
            "(the owner still reviews and publishes from the dashboard), so they're lower-risk than "
            "a broadcast or invoice but still need a real go-ahead since a saved draft overwrites "
            "whatever draft existed before.\n\n"
            # 2026-08-24: this branch (the common owner/staff case) never passed `lang` through
            # despite computing it — only the sales_rep branch above did. A non-English-speaking
            # owner got the generic "mirror their language" fallback instead of the explicit
            # per-language block, letting the loop drift to English mid-multi-turn.
            + behaviour_preamble(agentic=True, preferred_language=lang) + persona_block
        )

    # ── Agent loop (mirrors commerce_assistant) ──────────────────────────────
    async def _agent_loop(self, system_msg: str, history: str, question: str, ctx: Dict[str, Any],
                          tools: Optional[List[Dict[str, Any]]] = None,
                          sources: Optional[List[Dict[str, Any]]] = None) -> str:
        # `sources` (2026-08-24): when the caller passes a list, every dispatched tool's result
        # is appended to it as a tool_source() entry — lets run() populate SkillOutput.sources so
        # the adversarial verifier (verification_policy="adversarial") can actually ground-check
        # the final answer against what the tools returned, instead of running blind. Optional
        # and defaults to None so every existing direct-call test keeps working unchanged.
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
                # Unconditional — dropped silently wherever unsupported (cloud routes, older
                # Ollama builds) rather than erroring. See reasoning.py for the original wiring.
                # Matters here specifically for the no-cloud-key fallback case, since this
                # loop otherwise force-escalates to cloud above.
                logprobs=True, top_logprobs=1,
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
                    if sources is not None:
                        sources.append(tool_source(name, result))
                    need_info = need_info_message(result)
                    if need_info:
                        return need_info
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content": (
                        f"[tool {name} returned]:{fence('TOOL_RESULT', json.dumps(result, default=str))}\n"
                        "Reply to the owner in plain, short WhatsApp language using this data. "
                        "Do not output JSON or tool calls."
                    )})
                    continue
                answer = (msg.content or "").strip()
                # 2026-08-24: considered porting reasoning.py's blanket "no tool + tenant-data-
                # shaped question -> decline" guard here too, and deliberately did NOT. Unlike
                # reasoning.py (zero tools — a match is unconditionally unanswerable) or
                # finance_admin.py (a narrow, fixed 4-tool surface where "not found" is a clean
                # per-turn signal), commerce_admin.py has ~40+ tools covering almost every
                # tenant-data concept — "no tool was dispatched" here very often just means the
                # model correctly followed AGENTIC_RULES' own "ask a clarifying question instead
                # of guessing" instruction (e.g. user: "what's the status of my order" -> model:
                # "Which order do you mean?"). A blanket keyword-based guard would silently
                # replace that legitimate clarifying question with a generic canned decline —
                # confirmed by testing: looks_like_tenant_data_question("Which order do you
                # mean?"." is True, so the guard would fire on the model's OWN good clarifying
                # reply just as readily as on a fabrication. Phase 1's verifier-grounding fix
                # (SkillOutput.sources now populated from real tool calls) already gives the
                # adversarial checker a genuine signal here: a money/status claim with zero
                # backing tool_source this turn has nothing to ground against, so a fabrication
                # still gets caught — just as a soft caveat, not a hard pre-LLM block, which is
                # the right trade-off given this skill's much broader tool surface.
                # 2026-08 accuracy audit: zero adoption of the logprob-confidence escalation
                # wired into reasoning.py/commerce_assistant.py the same day. Only fires in
                # the no-cloud-key fallback case (model.startswith("ollama/")) — when the
                # force-escalation above succeeded, model is already a cloud model here.
                if model.startswith("ollama/"):
                    from config import settings
                    from core.llm_router import looks_unreliable, compute_confidence
                    logprob_conf = compute_confidence(resp)
                    if looks_unreliable(answer, confidence=logprob_conf,
                                        confidence_threshold=settings.local_confidence_threshold):
                        esc2 = escalate_to_cloud("local_unreliable", task_type="commerce_admin")
                        if esc2:
                            model, api_key, api_base = esc2
                            resp = await litellm.acompletion(
                                model=model, messages=messages, temperature=0.2,
                                max_tokens=900, api_key=api_key, api_base=api_base,
                                logprobs=True, top_logprobs=1)
                            answer = (resp.choices[0].message.content or "").strip()
                return answer

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
                if sources is not None:
                    sources.append(tool_source(tc.function.name, result))
                need_info = need_info_message(result)
                if need_info:
                    return need_info
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "name": tc.function.name,
                                 "content": fence('TOOL_RESULT', json.dumps(result, default=str))})

        # Final pass — force a plain-language answer (no tools available now).
        # 2026-08-22: a real transcript showed this exact call fabricate a full "I've created an
        # invoice" narrative — plausible line items, a real-looking invoice number — after 3
        # exhausted attempts that had ALL genuinely failed (a malformed line item kept tripping
        # create_invoice's price gate). Nothing in this pass told it that running out of
        # attempts isn't the same as succeeding. The _need_info_message short-circuits above
        # remove the single most common way this happens, but this is the backstop for any
        # other way the budget runs out.
        messages.append({"role": "user", "content": (
            "You were not able to complete this within the available attempts. Do NOT claim "
            "anything was created, sent, updated, or otherwise succeeded unless a tool result "
            "above actually shows that. Tell the owner plainly what's missing or what went "
            "wrong instead."
        )})
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
            if sources is not None:
                sources.append(tool_source(name, result))
            resp = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": "Summarise this data for a shop owner in short, plain WhatsApp language. No JSON."},
                    {"role": "user", "content": fence('TOOL_RESULT', json.dumps(result, default=str))},
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
            if name == "update_stock":       return await self._update_stock(tid, args.get("product", ""), args.get("quantity", 0), bool(args.get("confirm")))
            if name == "outstanding_invoices": return await self._outstanding_invoices(tid)
            if name == "find_document":      return await self._find_document(tid, args)
            if name == "add_expense":        return await self._add_expense(tid, args)
            if name == "preview_broadcast":  return await self._preview_broadcast(tid, args.get("audience", "all"))
            if name == "finance_insights":   return await self._finance_insights(tid, int(args.get("days") or 30))
            if name == "reimbursement_balance": return await self._reimbursement_balance(tid, args.get("payee", ""))
            if name == "learn_my_voice":     return await self._learn_my_voice(tid)
            if name == "apply_voice_persona": return await self._apply_voice_persona(tid, args.get("persona_prompt", ""))
            if name == "create_invoice":     return await self._create_invoice(tid, args)
            if name == "send_invoice":       return await self._send_invoice(tid, args.get("invoice_number", ""))
            if name == "record_payment":     return await self._record_payment(tid, args)
            if name == "list_quotes":        return await self._list_quotes(tid, args.get("status"))
            if name == "convert_quote_to_invoice": return await self._convert_quote_to_invoice(tid, args.get("quote_number", ""))
            if name == "update_quote_status": return await self._update_quote_status(tid, args.get("quote_number", ""), args.get("status", ""))
            if name == "list_suppliers":     return await self._list_suppliers(tid)
            if name == "upsert_supplier":    return await self._upsert_supplier(tid, args)
            if name == "delete_supplier":    return await self._delete_supplier(tid, args.get("name", ""), bool(args.get("confirm")))
            if name == "reorder_suggestions": return await self._reorder_suggestions(tid)
            if name == "create_purchase_order": return await self._create_purchase_order(tid, args)
            if name == "list_purchase_orders": return await self._list_purchase_orders(tid, args.get("status"))
            if name == "update_po_status":   return await self._update_po_status(tid, args.get("po_ref", ""), args.get("status", ""), bool(args.get("confirm")))
            if name == "send_purchase_order": return await self._send_purchase_order(tid, args.get("po_ref", ""), args.get("channel", "email"), bool(args.get("confirm")))
            if name == "create_manual_order": return await self._create_manual_order(tid, args)
            if name == "create_automation_rule": return await self._create_automation_rule(tid, args.get("description", ""), bool(args.get("confirm")))
            if name == "list_discount_codes": return await self._list_discount_codes(tid)
            if name == "create_discount_code": return await self._create_discount_code(tid, args)
            if name == "update_discount_code": return await self._update_discount_code(tid, args)
            if name == "delete_discount_code": return await self._delete_discount_code(tid, args.get("code", ""), bool(args.get("confirm")))
            if name == "create_product":     return await self._create_product(tid, args)
            if name == "update_product":     return await self._update_product(tid, args)
            if name == "booking_availability": return await self._booking_availability(tid, args.get("date", ""))
            if name == "list_bookings":      return await self._list_bookings(tid, args.get("status"))
            if name == "create_booking":     return await self._create_booking(tid, args)
            if name == "cancel_booking":     return await self._cancel_booking(tid, args.get("booking_id", ""), bool(args.get("confirm")))
            if name == "generate_marketing": return await self._generate_marketing(tid, args)
            if name == "create_subscription": return await self._create_subscription(tid, args)
            if name == "list_subscriptions": return await self._list_subscriptions(tid, args.get("status"))
            if name == "customer_lookup":    return await self._customer_lookup(tid, args.get("query", ""))
            if name == "dynamics_lookup":    return await self._dynamics_lookup(tid, args.get("query", ""), args.get("kind", "contact"))
            if name == "send_broadcast":     return await self._send_broadcast(tid, args)
            if name == "list_storefront_pages": return await self._list_storefront_pages(tid)
            if name == "draft_storefront_page": return await self._draft_storefront_page(tid, args.get("slug", ""), args.get("instruction", ""), bool(args.get("confirm")))
            if name == "add_storefront_section": return await self._add_storefront_section(tid, args.get("slug", ""), args.get("feature", ""), bool(args.get("confirm")))
            if name == "draft_letter":
                from core.skills.draft_admin import draft_letter
                return await draft_letter(args, tid, ctx.get("phone") or "")
            if name == "create_contact":     return await self._create_contact(tid, args, ctx)
            if name == "log_meeting":        return await self._log_meeting(tid, args, ctx)
            if name == "draft_followup_email": return await self._draft_followup_email(tid, args, ctx)
            if name == "competitor_check":   return await self._competitor_check(tid, args, ctx)
            if name == "create_reminder":    return await self._create_reminder(tid, args, ctx)
            if name == "list_reminders":     return await self._list_reminders(tid, args, ctx)
            if name == "complete_reminder":  return await self._complete_reminder(tid, args, ctx)
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

    async def _update_stock(self, tid: str, product_name: str, quantity: int, confirm: bool = False) -> Dict[str, Any]:
        name = (product_name or "").strip()
        prod = None
        if re.match(r"^[a-z0-9-]+$", name):
            prod = await service.get_product_by_slug(tid, name)
        if not prod:
            candidates = await service.list_products(tid, in_stock_only=False)
            prod = next((p for p in candidates if name.lower() in p["name"].lower()), None)
        if not prod:
            return {"error": f"No product matching '{name}'."}
        if not confirm:
            return {"preview": True, "product": prod["name"],
                    "current_stock": prod.get("stock_quantity"), "new_stock": int(quantity),
                    "message": "Confirm to apply (call again with confirm=true)."}
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
        # 2026-08-22: was summing ALL directions — a real transcript (off-the-hook) showed this
        # silently mixing inbound supplier bills (direction="inbound", money the tenant OWES,
        # see commerce/service.py's commit_inbound_document) into what an owner reads as "money
        # owed to me," inflating a real R37,938.69/25-invoice figure into a nonsense
        # R109,743.11/79-invoice one as supplier bills kept arriving by email. direction="outbound"
        # restricts this to the tenant's own issued invoices/quotes, matching what "outstanding
        # invoices" actually means to a business owner. "draft" dropped too — an invoice that was
        # never sent isn't outstanding to anyone yet.
        owed = 0
        invoices = []
        for st in ("sent", "overdue"):
            for inv in await service.list_invoices(tid, status=st, direction="outbound", limit=100):
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

    async def _reimbursement_balance(self, tid: str, payee: str) -> Dict[str, Any]:
        """What's still owed to `payee` for money they paid out of pocket — sums
        commerce_expenses where reimbursable=true and reimbursed_at is unset, matched by
        paid_by (phone) or paid_by_name (case-insensitive substring, to tolerate spelling
        drift like 'NELETU' vs 'NELETHU' on different documents for the same person)."""
        payee = (payee or "").strip()
        if not payee:
            return {"error": "Need a name or phone number to check."}
        digits = "".join(c for c in payee if c.isdigit())
        q = (service._client().table("commerce_expenses").select(
                "id,date,description,amount_cents,supplier,project,paid_by,paid_by_name")
             .eq("tenant_id", tid).eq("reimbursable", True).is_("reimbursed_at", "null"))
        if digits and len(digits) >= 7:
            rows = q.eq("paid_by", digits).execute().data or []
        else:
            rows = q.ilike("paid_by_name", f"%{payee}%").execute().data or []
        if not rows:
            return {"payee": payee, "owed": self._rands(0), "items": [],
                    "note": "No outstanding reimbursable expenses found for this person."}
        total = sum(int(r.get("amount_cents") or 0) for r in rows)
        items = [{"date": r.get("date"), "description": r.get("description"),
                  "amount": self._rands(r.get("amount_cents")), "project": r.get("project")}
                 for r in rows]
        return {"payee": payee, "owed": self._rands(total), "item_count": len(items), "items": items}

    async def _learn_my_voice(self, tid: str) -> Dict[str, Any]:
        """WhatsApp entry point for vula/commerce/voice_profile.py's tone analysis (migration
        119/120) — previously only reachable via a dashboard endpoint (vula/api/commerce.py's
        admin_analyze_persona) that nobody had triggered for any real tenant. Returns the
        LLM-described suggestion (or the existing "not enough data yet" message) as-is."""
        from vula.commerce import voice_profile
        result = await voice_profile.analyze_voice(tid)
        if "error" in result:
            return result
        return {"suggested_persona": result["suggested"], "sample_count": result["sample_count"],
                "note": "Show this to the owner and ask if they'd like Vula to sound like this — "
                       "only call apply_voice_persona if they say yes."}

    async def _apply_voice_persona(self, tid: str, persona_prompt: str) -> Dict[str, Any]:
        """Same accept semantics as vula/api/commerce.py's admin_set_persona: set persona_prompt,
        clear the pending suggestion either way so it doesn't linger stale."""
        persona_prompt = (persona_prompt or "").strip()
        if not persona_prompt:
            return {"error": "No persona text given — call learn_my_voice first."}
        service._client().table("vula_tenant_config").update({
            "persona_prompt": persona_prompt,
            "persona_prompt_suggested": None,
            "persona_prompt_suggested_at": None,
        }).eq("tenant_id", tid).execute()
        try:
            from vula.api import tenants as _tenants
            _tenants.invalidate(tid)
        except Exception:
            pass
        return {"applied": True, "persona_prompt": persona_prompt}

    async def _create_invoice(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        raw = args.get("line_items") or []
        # Price-completeness gate (2026-08-08) — a missing unit_price_rands used to silently
        # coerce to 0 below, creating a real R0.00 draft invoice instead of asking for the price
        # first (confirmed live: OFF-INV-00016/17/18/00065 are junk artifacts of exactly this).
        # An explicit 0 (a genuinely free item) is still allowed through — only an ABSENT price
        # gates. Mirrors _fee_proposal_gaps' need_info shape in core/skills/draft_admin.py.
        price_gaps = [
            (it.get("description") or "").strip() for it in raw
            if isinstance(it, dict) and (it.get("description") or "").strip()
            and it.get("unit_price_rands") is None
        ]
        if price_gaps:
            return {
                "status": "need_info",
                "missing": [f"the price for {d}" for d in price_gaps],
                "message": "Before I create this, I need the price for: " + "; ".join(price_gaps) + ".",
            }
        line_items = [{
            "description": (it.get("description") or "").strip(),
            "quantity": float(it.get("quantity") or 1),
            "unit": (it.get("unit") or None),
            "unit_price_cents": int(round(float(it.get("unit_price_rands") or 0) * 100)),
            "section": (it.get("section") or "").strip() or None,
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

    async def _find_invoice_by_number(self, tid: str, number: str) -> Optional[Dict[str, Any]]:
        """Shared lookup for record_payment/convert_quote_to_invoice/update_quote_status —
        owners refer to invoices/quotes by their display number, never the internal id."""
        num = (number or "").strip()
        if not num:
            return None
        rows = (service._client().table("commerce_invoices").select("*")
                .eq("tenant_id", tid).eq("invoice_number", num).limit(1).execute().data or [])
        return rows[0] if rows else None

    async def _record_payment(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        num = (args.get("invoice_number") or "").strip()
        inv = await self._find_invoice_by_number(tid, num)
        if not inv:
            return {"error": f"No invoice {num} found."}
        cents = int(round(float(args.get("amount_rands") or 0) * 100))
        if cents <= 0:
            return {"error": "amount_rands must be positive."}
        if not args.get("confirm"):
            return {"preview": True, "invoice_number": num, "amount": self._rands(cents),
                    "message": "Confirm to record this payment (call again with confirm=true)."}
        try:
            updated = await service.record_invoice_payment(
                tid, inv["id"], cents, args.get("payment_method"), args.get("note"))
        except ValueError as exc:
            return {"error": str(exc)}
        result = {"invoice_number": num, "recorded": self._rands(cents),
                  "new_status": updated.get("status"),
                  "balance_due": self._rands(updated.get("balance_due_cents"))}
        if settings.readback_verify_enabled:
            payments = await service.list_invoice_payments(tid, inv["id"])
            total_paid = sum(int(p.get("amount_cents") or 0) for p in payments)
            expected_min = int(inv.get("total_paid_cents") or 0) + cents
            ok = total_paid >= expected_min
            _readback_gate(tid, "record_payment", ok,
                           {"invoice_id": inv["id"], "amount_cents": cents},
                           {"total_paid_cents": total_paid})
            if not ok:
                return {"error": f"Payment for {num} did not persist — re-read shows "
                                 f"R{total_paid / 100:.2f} total paid. Not confirmed."}
            result["verified"] = True
        return result

    async def _list_quotes(self, tid: str, status: Optional[str]) -> Dict[str, Any]:
        quotes = await service.list_invoices(tid, doc_type="quote", status=status, limit=20)
        if not quotes:
            return {"message": "No quotes found."}
        return {"count": len(quotes), "quotes": [
            {"quote": q.get("invoice_number"), "status": q.get("status"),
             "total": self._rands(q.get("total_cents")), "customer": q.get("customer_name")}
            for q in quotes]}

    async def _convert_quote_to_invoice(self, tid: str, quote_number: str) -> Dict[str, Any]:
        quote = await self._find_invoice_by_number(tid, quote_number)
        if not quote:
            return {"error": f"No quote {quote_number} found."}
        try:
            inv = await service.convert_quote_to_invoice(tid, quote["id"])
        except ValueError as exc:
            return {"error": str(exc)}
        result = {"converted": True, "quote_number": quote_number,
                  "invoice_number": inv.get("invoice_number"), "total": self._rands(inv.get("total_cents"))}
        if settings.readback_verify_enabled:
            inv2 = await service.get_invoice(tid, inv["id"]) if inv.get("id") else None
            ok = bool(inv2) and inv2.get("doc_type") == "invoice" and inv2.get("source_quote_id") == quote["id"]
            _readback_gate(tid, "convert_quote_to_invoice", ok,
                           {"quote_id": quote["id"]}, {"found": bool(inv2)})
            if not ok:
                return {"error": "Conversion could not be confirmed — the new invoice did not "
                                 "appear on re-read. Not confirmed; please check the Invoices tab."}
            result["verified"] = True
        return result

    async def _update_quote_status(self, tid: str, quote_number: str, status: str) -> Dict[str, Any]:
        valid = {"sent", "accepted", "declined", "expired"}
        if status not in valid:
            return {"error": f"status must be one of {sorted(valid)}"}
        quote = await self._find_invoice_by_number(tid, quote_number)
        if not quote:
            return {"error": f"No quote {quote_number} found."}
        await service.update_invoice_status(tid, quote["id"], status)
        return {"updated": quote_number, "new_status": status}

    async def _list_suppliers(self, tid: str) -> Dict[str, Any]:
        suppliers = await service.list_suppliers(tid)
        if not suppliers:
            return {"message": "No suppliers on file yet."}
        return {"suppliers": [{"name": s.get("name"), "email": s.get("contact_email"),
                               "phone": s.get("contact_phone")} for s in suppliers]}

    async def _upsert_supplier(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        name = (args.get("name") or "").strip()
        if not name:
            return {"error": "Need a supplier name."}
        data = {"name": name}
        if args.get("contact_email"):
            data["contact_email"] = args["contact_email"]
        if args.get("contact_phone"):
            data["contact_phone"] = args["contact_phone"]
        if args.get("payment_terms"):
            data["payment_terms"] = args["payment_terms"]
        if not args.get("confirm"):
            return {"preview": True, "supplier": name, "contact_email": data.get("contact_email"),
                    "contact_phone": data.get("contact_phone"), "payment_terms": data.get("payment_terms"),
                    "message": "Confirm to save this supplier (call again with confirm=true)."}
        s = await service.upsert_supplier(tid, data)
        return {"saved": s.get("name")}

    async def _delete_supplier(self, tid: str, name: str, confirm: bool = False) -> Dict[str, Any]:
        name = (name or "").strip().lower()
        suppliers = await service.list_suppliers(tid)
        s = next((x for x in suppliers if name and name in (x.get("name") or "").lower()), None)
        if not s:
            return {"error": f"No supplier matching '{name}'."}
        if not confirm:
            return {"preview": True, "supplier": s.get("name"),
                    "message": "Confirm to delete this supplier (call again with confirm=true)."}
        await service.delete_supplier(tid, s["id"])
        return {"deleted": s.get("name")}

    async def _reorder_suggestions(self, tid: str) -> Dict[str, Any]:
        from vula.commerce import purchase_orders
        data = await purchase_orders.get_reorder_suggestions(tid)
        if not data.get("count"):
            return {"message": "Nothing is low on stock right now."}
        return data

    async def _resolve_po(self, tid: str, po_ref: str) -> Optional[Dict[str, Any]]:
        """Owners refer to a PO by the short id-prefix shown in list_purchase_orders/
        render_po_email (there's no dedicated display-id column for purchase orders)."""
        ref = (po_ref or "").strip().lower()
        if not ref:
            return None
        rows = (service._client().table("commerce_purchase_orders").select("*")
                .eq("tenant_id", tid).order("created_at", desc=True).limit(100).execute().data or [])
        return next((r for r in rows if str(r.get("id") or "").lower().startswith(ref)), None)

    async def _create_purchase_order(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        supplier_name = (args.get("supplier_name") or "").strip()
        if not supplier_name:
            return {"error": "Need a supplier name."}
        suppliers = await service.list_suppliers(tid)
        supplier = next((s for s in suppliers
                         if supplier_name.lower() in (s.get("name") or "").lower()), None)
        if not supplier:
            return {"error": f"No supplier matching '{supplier_name}' — add them first with upsert_supplier."}
        items = [{"name": (it.get("name") or "").strip(),
                  "quantity": int(it.get("quantity") or 0),
                  "unit_cost_cents": int(round(float(it.get("unit_cost_rands") or 0) * 100))}
                 for it in (args.get("items") or []) if isinstance(it, dict) and it.get("name")]
        if not items:
            return {"error": "Need at least one item with a name and quantity."}
        if not args.get("confirm"):
            total = sum(it["quantity"] * it["unit_cost_cents"] for it in items)
            return {"preview": True, "supplier": supplier["name"], "items": items,
                    "total": self._rands(total),
                    "message": "Confirm to create this draft PO (call again with confirm=true)."}
        from vula.api.commerce import admin_create_purchase_order
        po = await admin_create_purchase_order(tid, {
            "supplier_id": supplier["id"], "supplier_name": supplier["name"],
            "items": items, "notes": args.get("notes")})
        result = {"created": True, "supplier": supplier["name"],
                  "po_ref": str(po.get("id") or "")[:8], "total": self._rands(po.get("total_cents"))}
        if settings.readback_verify_enabled:
            found = await self._resolve_po(tid, result["po_ref"])
            ok = bool(found) and found.get("status") == "draft"
            _readback_gate(tid, "create_purchase_order", ok,
                           {"supplier_id": supplier["id"]}, {"found": bool(found)})
            if not ok:
                return {"error": "Purchase order creation could not be confirmed on re-read. "
                                 "Not confirmed; please check the dashboard."}
            result["verified"] = True
        return result

    async def _list_purchase_orders(self, tid: str, status: Optional[str]) -> Dict[str, Any]:
        from vula.api.commerce import admin_list_purchase_orders
        res = await admin_list_purchase_orders(tid, status)
        pos = res.get("purchase_orders") or []
        if not pos:
            return {"message": "No purchase orders found."}
        return {"purchase_orders": [
            {"po_ref": str(p.get("id") or "")[:8], "supplier": p.get("supplier_name"),
             "status": p.get("status"), "total": self._rands(p.get("total_cents"))}
            for p in pos[:15]]}

    async def _update_po_status(self, tid: str, po_ref: str, status: str, confirm: bool = False) -> Dict[str, Any]:
        valid = {"draft", "sent", "received", "cancelled"}
        if status not in valid:
            return {"error": f"status must be one of {sorted(valid)}"}
        po = await self._resolve_po(tid, po_ref)
        if not po:
            return {"error": f"No purchase order matching '{po_ref}' — use list_purchase_orders to find its reference."}
        if not confirm:
            return {"preview": True, "po_ref": po_ref, "current_status": po.get("status"),
                    "new_status": status,
                    "message": "Confirm to apply (call again with confirm=true)."
                               + (" Marking 'received' will increase real stock." if status == "received" else "")}
        from vula.api.commerce import admin_update_po_status
        await admin_update_po_status(tid, po["id"], {"status": status})
        result = {"updated": str(po["id"])[:8], "new_status": status}
        if settings.readback_verify_enabled:
            found = await self._resolve_po(tid, po_ref)
            observed = (found or {}).get("status")
            ok = observed == status
            _readback_gate(tid, "update_po_status", ok,
                           {"po_id": po["id"], "status": status}, {"status": observed})
            if not ok:
                return {"error": f"Status update did not persist — purchase order still shows "
                                 f"{observed or 'unknown'}. Not confirmed."}
            result["verified"] = True
        return result

    async def _send_purchase_order(self, tid: str, po_ref: str, channel: str, confirm: bool = False) -> Dict[str, Any]:
        po = await self._resolve_po(tid, po_ref)
        if not po:
            return {"error": f"No purchase order matching '{po_ref}' — use list_purchase_orders to find its reference."}
        if not confirm:
            return {"preview": True, "po_ref": po_ref, "supplier": po.get("supplier_name"),
                    "total": self._rands(po.get("total_cents")), "via": channel or "email",
                    "message": "This commits real spend with the supplier. Confirm to send "
                               "(call again with confirm=true)."}
        from vula.commerce import purchase_orders
        res = await purchase_orders.send_purchase_order(tid, po["id"], channel or "email")
        if res.get("error"):
            return res
        return {"sent": True, "po_ref": po_ref, "via": res.get("sent_via"), "warnings": res.get("warnings")}

    async def _create_manual_order(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        name = (args.get("customer_name") or "").strip()
        phone = (args.get("customer_phone") or "").strip()
        if not name or not phone:
            return {"error": "Need the customer's name and phone."}
        raw_items = args.get("items") or []
        if not raw_items:
            return {"error": "Need at least one item."}
        products = await service.list_products(tid, in_stock_only=False)
        resolved = []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            want = (it.get("product") or "").strip().lower()
            p = next((x for x in products if want and want in (x.get("name") or "").lower()), None)
            if not p:
                return {"error": f"No product matching '{it.get('product')}'."}
            resolved.append((p, float(it.get("quantity") or 1)))
        if not args.get("confirm"):
            preview_total = sum(int(p.get("price_cents") or 0) * qty for p, qty in resolved)
            msg = "Confirm to create this order (call again with confirm=true)."
            if args.get("mark_paid"):
                msg += " This will also record it as already paid."
            return {"preview": True, "customer": name,
                    "items": [{"product": p.get("name"), "quantity": qty} for p, qty in resolved],
                    "total": self._rands(int(preview_total)), "message": msg}
        from uuid import uuid4
        session_id = f"manual-{uuid4().hex[:12]}"
        cart = await service.get_or_create_cart(tid, session_id, customer_phone=phone)
        try:
            for p, qty in resolved:
                await service.add_to_cart(tid, cart["id"], p["id"], qty)
        except Exception as exc:
            return {"error": f"Couldn't add items to the order: {exc}"}
        cart = await service.get_or_create_cart(tid, session_id)  # re-read with items joined
        try:
            order = await service.create_order(tid, cart, {
                "customer_phone": phone, "customer_name": name,
                "delivery_address": args.get("delivery_address") or "Collection / to arrange",
                "delivery_slot": "morning", "channel": "manual",
                "payment_method": args.get("payment_method") or "cod",
            })
        except service.OutOfStockError as exc:
            return {"error": str(exc)}
        if args.get("mark_paid"):
            await service.update_order_status(order["id"], "paid")
        result = {"created": True, "order": order.get("display_id"), "total": self._rands(order.get("total_cents"))}
        if settings.readback_verify_enabled:
            found = await service.get_order(order["id"]) if order.get("id") else None
            ok = bool(found) and bool(found.get("display_id"))
            _readback_gate(tid, "create_manual_order", ok,
                           {"customer_phone": phone}, {"found": bool(found)})
            if not ok:
                return {"error": "Order creation could not be confirmed on re-read. Not confirmed; "
                                 "please check the Orders tab."}
            result["verified"] = True
        return result

    async def _create_automation_rule(self, tid: str, description: str, confirm: bool) -> Dict[str, Any]:
        """"Teaching mode": create a standing automation from a plain-language description.
        The rule itself only ever gets created after confirm=true — but even once created, its
        matches still always wait for a separate owner approval before anything sends (see
        vula/commerce/automations.py::_stage_firing), so this confirm only gates creating the
        rule, not any future action it might propose."""
        from vula.commerce import automations
        description = (description or "").strip()
        if not description:
            return {"error": "Describe the rule you'd like, e.g. \"when an order is dispatched, "
                              "message the customer to say it's on its way\"."}
        if not confirm:
            return {"preview": True, "description": description,
                    "message": "Confirm to create this rule (call again with confirm=true). "
                               "Its matches will still always wait for your approval before "
                               "anything actually sends."}
        result = await automations.parse_rule_from_text(tid, description)
        if result.get("error"):
            return result
        return {"created": True, "automation": result.get("name"),
                "trigger": result.get("trigger_type"), "action": result.get("action_type"),
                "message": "Rule created. Matches will show up under Automations for your "
                           "approval before anything sends."}

    async def _list_discount_codes(self, tid: str) -> Dict[str, Any]:
        codes = await service.list_discount_codes(tid)
        if not codes:
            return {"message": "No discount codes yet."}
        return {"codes": [{"code": c.get("code"), "type": c.get("type"), "value": c.get("value"),
                           "active": c.get("active"), "usage_count": c.get("usage_count")}
                          for c in codes]}

    async def _create_discount_code(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        code = (args.get("code") or "").strip()
        dtype = args.get("discount_type")
        if not code or dtype not in ("percent", "fixed", "free_shipping"):
            return {"error": "Need a code and discount_type (percent, fixed, or free_shipping)."}
        data: Dict[str, Any] = {"code": code, "type": dtype, "active": True}
        if dtype == "percent":
            data["value"] = float(args.get("value") or 0)
        elif dtype == "fixed":
            data["value"] = int(round(float(args.get("value") or 0) * 100))
        if args.get("min_order_rands") is not None:
            data["min_order_cents"] = int(round(float(args["min_order_rands"]) * 100))
        if args.get("usage_limit") is not None:
            data["usage_limit"] = int(args["usage_limit"])
        if not args.get("confirm"):
            return {"preview": True, "code": code, "discount_type": dtype, "value": data.get("value"),
                    "message": "Confirm to create this code (call again with confirm=true)."}
        try:
            c = await service.create_discount_code(tid, data)
        except Exception as exc:
            if "idx_discount_codes_tenant_code" in str(exc) or "23505" in str(exc):
                return {"error": f"Code '{code}' already exists."}
            return {"error": str(exc)}
        result = {"created": True, "code": c.get("code"), "type": c.get("type")}
        if settings.readback_verify_enabled:
            codes = await service.list_discount_codes(tid)
            found = next((x for x in codes if x.get("code") == c.get("code")), None)
            ok = bool(found)
            _readback_gate(tid, "create_discount_code", ok, {"code": c.get("code")}, {"found": ok})
            if not ok:
                return {"error": "Discount code creation could not be confirmed on re-read. Not confirmed."}
            result["verified"] = True
        return result

    async def _update_discount_code(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        code = (args.get("code") or "").strip().upper()
        if not code:
            return {"error": "Need the code to update."}
        codes = await service.list_discount_codes(tid)
        row = next((x for x in codes if (x.get("code") or "").upper() == code), None)
        if not row:
            return {"error": f"No discount code '{code}' found."}
        patch: Dict[str, Any] = {}
        if args.get("active") is not None:
            patch["active"] = bool(args["active"])
        if args.get("usage_limit") is not None:
            patch["usage_limit"] = int(args["usage_limit"])
        if not patch:
            return {"error": "Nothing to change (active / usage_limit)."}
        if not args.get("confirm"):
            return {"preview": True, "code": code, "changes": patch,
                    "message": "Confirm to apply (call again with confirm=true)."}
        await service.update_discount_code(tid, row["id"], patch)
        return {"updated": code, **patch}

    async def _delete_discount_code(self, tid: str, code: str, confirm: bool = False) -> Dict[str, Any]:
        code = (code or "").strip().upper()
        codes = await service.list_discount_codes(tid)
        row = next((x for x in codes if (x.get("code") or "").upper() == code), None)
        if not row:
            return {"error": f"No discount code '{code}' found."}
        if not confirm:
            return {"preview": True, "code": code,
                    "message": "Confirm to delete this code (call again with confirm=true)."}
        await service.delete_discount_code(tid, row["id"])
        return {"deleted": code}

    async def _create_product(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        name = (args.get("name") or "").strip()
        price = int(round(float(args.get("price_rands") or 0) * 100))
        if not name or price <= 0:
            return {"error": "Need a product name and a price in Rands."}
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "product"
        data = {"name": name, "slug": slug, "description": args.get("description") or name,
                "price_cents": price, "category": args.get("category") or "other", "in_stock": True}
        if not args.get("confirm"):
            return {"preview": True, "product": name, "price": self._rands(price),
                    "category": data["category"],
                    "message": "Confirm to add this product (call again with confirm=true)."}
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
        # 2026-08-24: previously the only price-mutating tool in this file with neither a
        # confirm gate nor readback-verify — a misheard/misparsed product match could silently
        # reprice the wrong item with nothing to catch it.
        if not args.get("confirm"):
            preview = {"preview": True, "product": p["name"],
                       "message": "Confirm to apply this change (call again with confirm=true)."}
            if "price_cents" in patch:
                preview["current_price"] = self._rands(p.get("price_cents"))
                preview["new_price"] = self._rands(patch["price_cents"])
            if "name" in patch:
                preview["new_name"] = patch["name"]
            if "is_daily_catch" in patch:
                preview["is_daily_catch"] = patch["is_daily_catch"]
            return preview
        await service.update_product(tid, p["id"], patch)
        out = {"updated": p["name"]}
        if "price_cents" in patch:
            out["new_price"] = self._rands(patch["price_cents"])
        if settings.readback_verify_enabled:
            p2 = await service.get_product(tid, p["id"])
            ok = p2 is not None and all(p2.get(k) == v for k, v in patch.items())
            _readback_gate(tid, "update_product", ok, {"product_id": p["id"], **patch},
                           {k: (p2 or {}).get(k) for k in patch})
            if not ok:
                return {"error": f"Update to {p['name']} could not be confirmed on re-read. "
                                 "Not confirmed; please check the Products tab."}
            out["verified"] = True
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
        if not args.get("confirm"):
            return {"preview": True, "customer": args.get("customer_name"), "start": args.get("start"),
                    "service": svc_name or None,
                    "message": "Confirm to book this appointment (call again with confirm=true)."}
        res = await bk.create_booking(tid, {
            "service_id": svc_id, "service_name": svc_name or None,
            "customer_name": args.get("customer_name"), "customer_phone": args.get("customer_phone"),
            "start": args.get("start"), "channel": "dashboard"})
        if res.get("error"):
            return res
        return {"booked": True, "when": res["booking"].get("start_local"), "service": res["booking"].get("service_name")}

    async def _cancel_booking(self, tid: str, booking_id: str, confirm: bool = False) -> Dict[str, Any]:
        from vula.bookings import service as bk
        if not booking_id:
            return {"error": "Need the booking id (use list_bookings to find it)."}
        if not confirm:
            return {"preview": True, "booking_id": booking_id,
                    "message": "Confirm to cancel this booking (call again with confirm=true)."}
        await bk.set_status(tid, booking_id, "cancelled")
        return {"cancelled": True, "booking_id": booking_id}

    async def _generate_marketing(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from vula.commerce import marketing
        count = max(1, min(int(args.get("variant_count") or 3), 3))
        res = await marketing.generate(tid, kind=args.get("kind", "specials"),
                                       topic=args.get("topic", ""), tone=args.get("tone", ""),
                                       variants=count)
        if res.get("error"):
            return res
        variants = res.get("variants") or []
        if not variants:
            return {"error": "No copy came back — try again."}
        if len(variants) == 1:
            return {"copy": variants[0]}
        return {"options": variants, "note": f"{len(variants)} options — reply with which one to use, "
                "or ask for changes."}

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
        if not args.get("confirm"):
            return {"preview": True, "customer": args.get("customer_name"),
                    "cadence": args.get("cadence", "weekly"),
                    "items": [{"product": it["product_name"], "quantity": it["quantity"]} for it in items],
                    "message": "Confirm to set up this standing order (call again with confirm=true)."}
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

    async def _find_document(self, tid: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Search vula_filed_documents (invoices/quotes/proof-of-payment/BOQs/receipts) by free
        text. 2026-08-21: added after a real transcript showed the admin loop guessing among
        unrelated tools (bookings, log_meeting, finance_insights) three times in a row rather
        than looking up the specific document the owner referenced — this gives the model
        something correct to reach for instead. Mirrors vula/api/documents.py's list_filed
        filter shape (filename/summary ilike) rather than inventing a new search path."""
        query = (args.get("query") or "").strip()
        if not query:
            return {"error": "Give a few words about the document — supplier/customer name, "
                              "invoice number, amount, or what it was for."}
        category = (args.get("category") or "").strip()
        # Commas/parens are PostgREST or_() filter syntax — strip them so free text (which may
        # come straight from a WhatsApp message) can't alter the query's filter structure.
        safe_query = re.sub(r"[,()]", " ", query).strip()[:100]
        try:
            q = (service._client().table("vula_filed_documents")
                 .select("id,filename,category,summary,fields,status,created_at,customer_phone")
                 .eq("tenant_id", tid).order("created_at", desc=True))
            if category:
                q = q.eq("category", category)
            if safe_query:
                q = q.or_(f"filename.ilike.%{safe_query}%,summary.ilike.%{safe_query}%")
            rows = q.limit(5).execute().data or []
        except Exception as exc:
            logger.warning("find_document query failed: %s", exc)
            return {"error": "Couldn't search documents right now."}
        if not rows:
            return {"message": f"No filed document matches '{query}'. Ask the owner for the "
                                "invoice/document number, or to resend it — don't guess."}
        results = []
        for r in rows:
            fields = r.get("fields") or {}
            results.append({
                "id": r.get("id"), "filename": r.get("filename"), "category": r.get("category"),
                "summary": (r.get("summary") or "")[:200],
                "amount": fields.get("amount") or fields.get("total") or fields.get("amount_rands"),
                "party": fields.get("supplier") or fields.get("payee_name") or fields.get("customer"),
                "filed_at": r.get("created_at"),
            })
        return {"matches": results}

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

    async def _dynamics_lookup(self, tid: str, query: str, kind: str) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {"error": "Give a name to search for."}
        try:
            from vula.dynamics365 import client as d365
            from vula.dynamics365.client import Dynamics365NotConnected
            if kind == "account":
                results = await d365.search_accounts(tid, q)
            elif kind == "opportunity":
                results = await d365.list_opportunities(tid, q)
            else:
                results = await d365.search_contacts(tid, q)
        except Dynamics365NotConnected:
            return {"error": "Dynamics 365 isn't connected for this account yet — connect it from the dashboard first."}
        except Exception as exc:
            return {"error": f"Dynamics 365 lookup failed: {exc}"}
        if not results:
            return {"message": f"No {kind} found for '{query}'."}
        return {"kind": kind, "results": results}

    async def _create_contact(self, tid: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        from uuid import uuid4
        name = (args.get("name") or "").strip()
        if not name:
            return {"error": "Need at least a name."}
        phone = re.sub(r"\D", "", args.get("phone") or "")
        if phone.startswith("0"):
            phone = "27" + phone[1:]
        # commerce_contacts' unique key is (tenant_id, phone) — a card with no printed number
        # still needs one to save under; a placeholder keeps the save working, editable later.
        no_phone = not phone
        if no_phone:
            phone = f"nophone-{uuid4().hex[:10]}"
        row = {
            "tenant_id": tid, "phone": phone, "name": name,
            "email": args.get("email") or None, "company": args.get("company") or None,
            "title": args.get("title") or None, "notes": args.get("notes") or None,
            "source": "whatsapp_admin", "created_by": ctx.get("phone") or None,
        }
        try:
            service._client().table("commerce_contacts").upsert(row, on_conflict="tenant_id,phone").execute()
        except Exception as exc:
            # company/title/created_by are from migration 110 — degrade gracefully if it hasn't
            # run yet in this environment rather than failing the save outright.
            if not any(k in str(exc) for k in ("company", "title", "created_by")):
                raise
            for k in ("company", "title", "created_by"):
                row.pop(k, None)
            service._client().table("commerce_contacts").upsert(row, on_conflict="tenant_id,phone").execute()
        return {"saved": name, "phone": None if no_phone else phone, "company": args.get("company")}

    async def _log_meeting(self, tid: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        notes = (args.get("notes") or "").strip()
        if not notes:
            return {"error": "Need the meeting notes/transcript."}

        # Resolve which contact this was with, if named — links the log to their record.
        customer_phone, contact_name = None, None
        who = (args.get("contact_name_or_phone") or "").strip()
        if who:
            digits = re.sub(r"\D", "", who)
            try:
                q = service._client().table("commerce_contacts").select("phone,name").eq("tenant_id", tid)
                rows = (q.eq("phone", digits).execute().data if digits
                        else q.ilike("name", f"%{who}%").limit(1).execute().data) or []
                if rows:
                    customer_phone = rows[0]["phone"]
                    contact_name = rows[0].get("name")
            except Exception as exc:
                logger.debug("meeting contact lookup skipped: %s", exc)

        # One LLM pass: turn the raw voice-note transcript into attendees/summary/action items
        # (+ next_meeting_hint, 2026-08-17). 2026-08-18: each action item now also carries an
        # optional assignee/due_phrase — read from what the transcript actually says (e.g. "Peter
        # said he'd send the drawings by Friday"), never invented if no name/date is attached, and
        # the model is never asked to COMPUTE an actual date itself (LLMs are unreliable at date
        # arithmetic) — due_phrase is the raw phrase, resolved deterministically below.
        summary, fields, next_meeting_hint = notes[:400], {"raw_notes": notes}, None
        try:
            import litellm
            litellm.drop_params = True
            model, api_key, api_base = await resolve_generation_route()
            resp = await litellm.acompletion(
                model=model, temperature=0.1, max_tokens=500, api_key=api_key, api_base=api_base,
                messages=[
                    {"role": "system", "content": (
                        "Extract structured meeting notes from this text. Return STRICT JSON only: "
                        '{"summary": "1-2 sentences", "attendees": ["..."], "action_items": '
                        '[{"text": "...", "assignee": "..."|null, "due_phrase": "..."|null}], '
                        '"next_meeting_hint": "..."|null}. assignee is who committed to that '
                        "specific item, ONLY if a name is clearly attached to it — never guess who's "
                        "responsible. due_phrase is the RAW date/time phrase mentioned for that item "
                        '(e.g. "by Friday", "next Tuesday") — copy it verbatim, do not compute an '
                        "actual date yourself. next_meeting_hint is a follow-up date/time ONLY if "
                        "explicitly mentioned in the text (e.g. \"let's meet again next Tuesday\"), "
                        "never invented.")},
                    {"role": "user", "content": notes[:3000]},
                ])
            raw = (resp.choices[0].message.content or "").strip().replace("```json", "").replace("```", "").strip()
            i, j = raw.find("{"), raw.rfind("}")
            if i >= 0 and j > i:
                data = json.loads(raw[i:j + 1])
                summary = data.get("summary") or summary
                # Tolerate the model occasionally still returning plain strings.
                norm_items = []
                for it in (data.get("action_items") or []):
                    if isinstance(it, str) and it.strip():
                        norm_items.append({"text": it.strip(), "assignee": None, "due_phrase": None})
                    elif isinstance(it, dict) and (it.get("text") or "").strip():
                        norm_items.append({"text": it["text"].strip(),
                                           "assignee": (it.get("assignee") or "").strip() or None,
                                           "due_phrase": (it.get("due_phrase") or "").strip() or None})
                fields = {"attendees": data.get("attendees") or [],
                          "action_items": norm_items, "raw_notes": notes}
                next_meeting_hint = (data.get("next_meeting_hint") or "").strip() or None
        except Exception as exc:
            logger.debug("meeting extraction failed, filing raw notes: %s", exc)

        from vula.integrations.doc_filing import file_document
        fname = f"meeting-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.txt"
        filed_row = await file_document(
            tid, filename=fname, data=None, content_type="text/plain",
            category="meeting_notes", summary=summary, fields=fields,
            source="whatsapp_admin", filed_by=ctx.get("phone") or "", status="filed",
            customer_phone=customer_phone,
        )
        # file_document() swallows its own DB errors and still returns a row dict on total
        # failure — the ONLY reliable "did this actually save" signal is a real id coming
        # back. Without this check, a DB-side failure (e.g. a pending migration) was reported
        # to the rep as a successful log even though nothing was ever persisted.
        if not filed_row.get("id"):
            return {"error": "Couldn't save the meeting log — please try again or check with support."}

        def _format_action_item(it: Dict[str, Any]) -> str:
            text = it.get("text") or ""
            if it.get("assignee"):
                text = f"{it['assignee']}: {text}"
            if it.get("due_phrase"):
                text = f"{text} ({it['due_phrase']})"
            return text

        def _resolve_due_at(due_phrase: Optional[str]) -> Optional[str]:
            """Deterministic, never LLM-computed — a wrong due date is worse than none. Only
            trusts dateutil's fuzzy parse when the phrase actually contains a real date/weekday/
            relative-day signal (fuzzy=True alone can false-positive on ordinary text)."""
            if not due_phrase:
                return None
            if not re.search(
                r"\b(mon(day)?|tue(sday)?|wed(nesday)?|thu(rsday)?|fri(day)?|sat(urday)?|"
                r"sun(day)?|today|tomorrow|jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|"
                r"jul(y)?|aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?|"
                r"\d{1,2}[/-]\d{1,2}|\d{1,2}(st|nd|rd|th)?)\b",
                due_phrase, re.IGNORECASE,
            ):
                return None
            try:
                from dateutil import parser as _date_parser
                dt = _date_parser.parse(due_phrase, fuzzy=True, default=datetime.now(timezone.utc))
                return dt.isoformat()
            except Exception:
                return None

        # Turn each extracted action item into a real, trackable reminder — previously these only
        # lived inside the filed document's fields jsonb and were forgotten the moment the
        # WhatsApp reply was sent. 2026-08-18: now carries the assignee (folded into the reminder
        # text — free text, not a DB relationship, since it may be a client's name rather than a
        # real team-member account) and a deterministically-resolved due date, when either was
        # actually stated in the transcript.
        action_items = fields.get("action_items") or []
        if action_items:
            try:
                rows = [{"tenant_id": tid, "created_by": ctx.get("phone") or "",
                        "text": _format_action_item(it),
                        "due_at": _resolve_due_at(it.get("due_phrase")),
                        "source": "log_meeting", "linked_contact_phone": customer_phone}
                       for it in action_items if it.get("text")]
                if rows:
                    service._client().table("vula_reminders").insert(rows).execute()
            except Exception as exc:
                logger.warning("Persisting meeting action items as reminders failed: %s", exc)

        formatted_items = [_format_action_item(it) for it in action_items if it.get("text")]
        result: Dict[str, Any] = {"logged": True, "summary": summary, "action_items": formatted_items,
                                  "linked_contact": bool(customer_phone)}

        # 2026-08-17: a meeting log the owner never actually sees isn't much use — render it onto
        # the tenant's real letterhead as a PDF and send it back, reusing draft_letter's existing
        # generation/render/send pipeline exactly as-is (site_meeting_minutes is already a valid
        # document_type there) rather than duplicating any of it here. Best-effort: the log itself
        # (filed + reminders) has already succeeded above regardless of whether this PDF step works.
        try:
            attendees = fields.get("attendees") or []
            brief_parts = [f"Summary: {summary}"]
            if attendees:
                brief_parts.append("Attendees: " + ", ".join(attendees))
            if formatted_items:
                brief_parts.append("Action items:\n" + "\n".join(f"- {a}" for a in formatted_items))
            from core.skills.draft_admin import draft_letter
            pdf_result = await draft_letter({
                "document_type": "site_meeting_minutes",
                "brief": "\n\n".join(brief_parts),
                "client_name": contact_name,
            }, tid, ctx.get("phone") or "")
            result["pdf_sent"] = bool(pdf_result.get("sent_via_whatsapp"))
        except Exception as exc:
            logger.warning("Meeting-summary PDF failed for tenant %s: %s", tid, exc)
            result["pdf_sent"] = False

        if next_meeting_hint:
            result["next_meeting_hint"] = next_meeting_hint
            result["note"] = (f"The notes mention a follow-up: \"{next_meeting_hint}\". Ask the "
                              f"owner if they'd like it booked, and only call create_booking once "
                              f"they've confirmed a specific date/time.")

        return result

    async def _competitor_check(self, tid: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        query = (args.get("query") or "").strip()
        if not query:
            return {"error": "Need something to research — a competitor name or a product/price query."}
        from core.skills.web_search import _ddg_search, _fetch_text
        from core.prompt_safety import UNTRUSTED_CONTENT_RULE

        hits = await _ddg_search(f"{query} price buy South Africa", limit=5)
        if not hits:
            return {"error": f"Couldn't find live web results for '{query}' right now."}

        contexts, sources = [], []
        for h in hits[:3]:
            text = await _fetch_text(h["url"])
            if text:
                contexts.append(f"[{h['url']}] {h['title']}\n{text}")
                sources.append(h["url"])
        if not contexts:
            return {"error": "Found results but couldn't read any of the pages.",
                    "links": [h["url"] for h in hits[:5]]}

        try:
            import litellm
            from core.llm_router import resolve_generation_route
            litellm.drop_params = True
            model, api_key, api_base = await resolve_generation_route()
            system = (
                "You are a competitive-intelligence researcher. Using ONLY the web results "
                "given, produce a short brief (under 300 words) with three sections: "
                "Price position (how this compares on price), Notable differentiators (what "
                "stands out — features, service, reputation), and Recommendation (one line, "
                "actionable). Cite real figures only; never invent a price or claim that isn't "
                "in the results.\n\n" + UNTRUSTED_CONTENT_RULE
            )
            web_block = fence("WEB_RESULTS", "\n\n---\n\n".join(contexts)[:6000])
            resp = await litellm.acompletion(
                model=model, temperature=0.2, max_tokens=900, api_key=api_key, api_base=api_base,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Research query: {query}{web_block}"},
                ])
            summary = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("competitor_check synthesis failed: %s", exc)
            return {"error": f"Found results but couldn't summarise them: {exc}",
                    "links": sources}
        return {"summary": summary or "No clear findings from the search results.", "sources": sources}

    async def _draft_followup_email(self, tid: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        to = (args.get("to_email") or "").strip()
        if not to:
            return {"error": "Need the recipient's email."}
        notes = (args.get("meeting_notes") or "").strip()
        subject = (args.get("subject") or "Great meeting you").strip()
        body = notes
        try:
            import litellm
            litellm.drop_params = True
            model, api_key, api_base = await resolve_generation_route()
            resp = await litellm.acompletion(
                model=model, temperature=0.3, max_tokens=350, api_key=api_key, api_base=api_base,
                messages=[
                    {"role": "system", "content": "Write a short, warm, professional thank-you/"
                     "follow-up email body from these meeting notes. Plain text, no markdown, no "
                     "subject line, no placeholder brackets."},
                    {"role": "user", "content": notes[:2000]},
                ])
            body = (resp.choices[0].message.content or notes).strip() or notes
        except Exception as exc:
            logger.debug("followup email generation failed, using raw notes: %s", exc)
        try:
            from vula.google import service as google_service
            from vula.google.service import GoogleNotConnected
            await google_service.gmail_create_draft(tid, to, subject, body)
        except GoogleNotConnected:
            return {"error": "Google isn't connected for this account yet — connect it from the dashboard first."}
        except Exception as exc:
            return {"error": f"Couldn't create the Gmail draft: {exc}"}
        return {"drafted": True, "to": to, "subject": subject,
                "note": "Saved as a Gmail DRAFT — review and send it yourself, nothing was sent automatically."}

    async def _create_reminder(self, tid: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        text = (args.get("text") or "").strip()
        if not text:
            return {"error": "Need something to remind you about."}
        linked_phone = None
        who = (args.get("contact_name_or_phone") or "").strip()
        if who:
            digits = re.sub(r"\D", "", who)
            try:
                q = service._client().table("commerce_contacts").select("phone,name").eq("tenant_id", tid)
                rows = (q.eq("phone", digits).execute().data if digits
                        else q.ilike("name", f"%{who}%").limit(1).execute().data) or []
                if rows:
                    linked_phone = rows[0]["phone"]
            except Exception as exc:
                logger.debug("reminder contact lookup skipped: %s", exc)
        row = {
            "tenant_id": tid, "created_by": ctx.get("phone") or "", "text": text,
            "due_at": args.get("due_at") or None, "linked_contact_phone": linked_phone,
        }
        try:
            res = service._client().table("vula_reminders").insert(row).execute()
        except Exception as exc:
            return {"error": f"Couldn't save the reminder: {exc}"}
        if not (res.data and res.data[0].get("id")):
            return {"error": "Couldn't save the reminder — please try again."}
        return {"created": True, "text": text, "due_at": row["due_at"]}

    async def _list_reminders(self, tid: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        status = (args.get("status") or "open").strip()
        q = service._client().table("vula_reminders").select("id,text,due_at,status,created_at").eq("tenant_id", tid)
        if status != "all":
            q = q.eq("status", status)
        rows = q.order("due_at", desc=False).limit(20).execute().data or []
        return {"count": len(rows), "reminders": [
            {"id": r["id"], "text": r["text"], "due_at": r.get("due_at"), "status": r["status"]}
            for r in rows]} if rows else {"message": "No reminders found."}

    async def _complete_reminder(self, tid: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        reminder_id = (args.get("reminder_id") or "").strip()
        if not reminder_id:
            return {"error": "Need the reminder id (use list_reminders to find it)."}
        res = (service._client().table("vula_reminders")
               .update({"status": "done", "completed_at": datetime.now(timezone.utc).isoformat()})
               .eq("id", reminder_id).eq("tenant_id", tid).execute())
        if not res.data:
            return {"error": f"No reminder found with id {reminder_id} for this tenant."}
        return {"completed": True, "text": res.data[0].get("text")}

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

    async def _list_storefront_pages(self, tid: str) -> Dict[str, Any]:
        from vula.api.commerce import admin_list_pages
        res = await admin_list_pages(tid)
        pages = res.get("pages") or []
        if not pages:
            return {"message": "No storefront pages yet — create one from the dashboard first."}
        return {"pages": [{"slug": p.get("slug"), "title": p.get("title"), "status": p.get("status")} for p in pages]}

    async def _fetch_page_content(self, tid: str, slug: str):
        """Shared read for both page tools below — reuses the exact same query the dashboard
        editor's own GET route uses, so a WhatsApp/chat edit sees exactly what the editor would."""
        from vula.api.commerce import admin_get_page
        page = await admin_get_page(tid, slug)
        content = (page.get("puck_data") or {}).get("content") or []
        return page, content

    async def _save_page_draft(self, tid: str, slug: str, page: Dict[str, Any], content: List[dict]) -> None:
        """Always saves as status='draft', regardless of the page's current status — publishing
        stays a separate, dashboard-side step (see PAGE_TOOLS' own comment for why)."""
        from vula.api.commerce import upsert_page, PageIn
        body = PageIn(title=page.get("title"),
                      puck_data={**(page.get("puck_data") or {}), "content": content},
                      seo=page.get("seo") or {}, status="draft")
        await upsert_page(tid, slug, body)

    async def _draft_storefront_page(self, tid: str, slug: str, instruction: str, confirm: bool) -> Dict[str, Any]:
        slug = (slug or "").strip()
        instruction = (instruction or "").strip()
        if not slug:
            return {"error": "Need a page slug, e.g. 'home' for the homepage."}
        if not instruction:
            return {"error": "Tell me what to change on the page."}
        page, content = await self._fetch_page_content(tid, slug)
        if not content:
            return {"error": f"Page '{slug}' doesn't exist yet or has no content to edit — "
                             "create it from the dashboard's page builder first."}
        from vula.commerce import page_copy
        result = await page_copy.refine_page_copy(tid, content, instruction)
        if "error" in result:
            return result
        if not confirm:
            changes = []
            for old_b, new_b in zip(content, result["content"]):
                old_p, new_p = old_b.get("props") or {}, new_b.get("props") or {}
                for k, v in new_p.items():
                    if isinstance(v, str) and old_p.get(k) != v:
                        changes.append({"block": new_b.get("type"), "field": k, "was": old_p.get(k), "now": v})
            if not changes:
                return {"preview": True, "slug": slug, "changes": [],
                        "message": "That instruction didn't change anything on this page."}
            return {"preview": True, "slug": slug, "changes": changes,
                    "message": "This will save a draft of your page. Confirm to save (call again with confirm=true)."}
        await self._save_page_draft(tid, slug, page, result["content"])
        return {"saved": True, "slug": slug, "status": "draft",
                "message": "Saved as a draft — review and publish from the dashboard when you're happy with it."}

    async def _add_storefront_section(self, tid: str, slug: str, feature: str, confirm: bool) -> Dict[str, Any]:
        slug = (slug or "").strip()
        from vula.commerce import page_copy
        block_type = page_copy.FEATURE_BLOCK_MAP.get((feature or "").strip().lower())
        if not block_type:
            return {"error": f"'{feature}' isn't a section I can add. Supported: "
                             f"{', '.join(page_copy.FEATURE_BLOCK_MAP)}."}
        if not slug:
            return {"error": "Need a page slug, e.g. 'home' for the homepage."}
        page, content = await self._fetch_page_content(tid, slug)
        new_content = page_copy.add_block(content, block_type)
        if not confirm:
            return {"preview": True, "slug": slug, "adding": block_type,
                    "message": "This will save a draft of your page with the new section. "
                               "Confirm to save (call again with confirm=true)."}
        new_id = new_content[-1]["props"]["id"]
        result = await page_copy.refine_page_copy(
            tid, new_content,
            f"Write real, business-specific content for the new section you just added "
            f"(block id: {new_id}). Leave every other section exactly as it already is.")
        final_content = result.get("content", new_content)
        await self._save_page_draft(tid, slug, page, final_content)
        return {"saved": True, "slug": slug, "added": block_type, "status": "draft",
                "message": "Saved as a draft with the new section — review and publish from the "
                           "dashboard when ready."}

"""
core/skills/employees.py — the business's team of AI employees, behind one WhatsApp number.

31 Oct 2026 (Ian): "a ready-made team every business gets", not a build-your-own-agent tool. The
owner's messages used to reach one admin agent holding up to ~95 tools (commerce_admin), introduced
by one job title (_ROLE_LABELS). Giving a model more tools than the job needs is the main cause of a
wrong first move, so each employee here has a job description and only the tools that job uses.

The front desk (pick) is deterministic — no model. It hands a message to an employee only when one
clearly fits; anything unclear (no hit, a tie, "yes"/"confirm"/"thanks", a bare photo) stays with the
whole team, which is exactly what every message got before this existed. Every employee also has a
hand_over tool: if it was given the wrong job it says so and the turn re-runs with the whole team,
so a wrong hand-over costs one step, never a wrong answer.

What the business can change (vula_employees, migration 236): switch an employee off, and give it
instructions in plain words. Everything is on by default, so nothing changes until an owner edits.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

TABLE = "vula_employees"
MAX_INSTRUCTIONS = 600

# Every employee keeps the business's memory and the general helpers: what the business sells and
# its policies, exact arithmetic, the owner's standing rules and decisions, finding a filed document
# or an email, and reminders. Plus hand_over.
SHARED_TOOLS: FrozenSet[str] = frozenset({
    "lookup_business_info", "calculate", "remember_rule", "list_rules", "forget_rule",
    "record_decision", "decision_log", "withdraw_decision", "find_document", "email_thread_summary",
    "create_reminder", "list_reminders", "complete_reminder", "hand_over",
})

HAND_OVER_TOOL = {"type": "function", "function": {
    "name": "hand_over",
    "description": (
        "Use ONLY when this request is clearly outside your job and none of your tools can do it "
        "(e.g. you're the bookkeeper and they ask to change the website). The whole team then "
        "takes it. Never use it for a question you can answer or a tool you have."
    ),
    "parameters": {"type": "object", "properties": {
        "reason": {"type": "string", "description": "One line: what they asked for."}}}}}


@dataclass(frozen=True)
class Employee:
    id: str
    title: str
    emoji: str
    summary: str          # one line for the owner's "Your team" page
    job: str              # the job description, in the agent's prompt
    tools: FrozenSet[str]
    keywords: Tuple[str, ...] = field(default=())


def _kw(*words: str) -> Tuple[str, ...]:
    return tuple(w.lower() for w in words)


TEAM: Tuple[Employee, ...] = (
    Employee(
        id="bookkeeper", title="AI bookkeeper", emoji="📒",
        summary="Invoices and payments, slips and expenses, the bank, project costs, VAT and tax, reports.",
        job=(
            "You are the business's bookkeeper. You keep the books right: invoices and who has paid, "
            "slips and expenses booked to the right account and project, bank lines matched, what a "
            "project cost against what the client paid, VAT and tax, and the reports. Every figure "
            "comes from a tool or the calculate tool — never from your head. When the owner corrects "
            "something or settles who paid what, note it with record_decision, and give the decision "
            "as the reason when someone asks why a figure is what it is. If a number doesn't add up, "
            "say so plainly instead of smoothing it over."
        ),
        tools=frozenset({
            "outstanding_invoices", "send_payment_reminders", "finance_settings", "confirm_expense",
            "add_expense", "finance_insights", "financial_report", "cash_summary", "bank_payments",
            "move_to_project", "reimbursement_balance", "sales_summary", "recent_orders",
            "update_order_status", "order_payment",
            "create_invoice", "send_invoice", "payment_link", "credit_note", "recurring_invoice",
            "record_payment", "list_quotes", "convert_quote_to_invoice", "update_quote_status",
            "project_profit", "price_advice", "boq_template", "share_project", "client_paid_direct",
            "tax_estimate", "vat_position", "tax_calendar", "set_vat_registration", "set_tax_regime",
            "list_suppliers", "cost_sheet", "create_subscription", "list_subscriptions",
        }),
        keywords=_kw(
            "invoice", "bill", "paid", "payment", "pay ", "owe", "owing", "outstanding", "debtor",
            "expense", "spent", "spend", "receipt", "slip", "bank", "statement", "reconcil", "vat",
            "tax", "sars", "profit", "p&l", "income statement", "balance sheet", "cash", "money",
            "budget", "bookkeep", "reimburse", "petty cash", "credit note", "refund", "final account",
            "paid directly", "wrong project", "filed under", "boq", "bill of quantities", "costs",
            "turnover", "revenue", "account", "eft", "deposit", "overdue", "payment reminder",
        ),
    ),
    Employee(
        id="sales", title="AI sales and customer service assistant", emoji="🤝",
        summary="Quotes, orders, customers and follow-ups, meetings, bookings and subscriptions.",
        job=(
            "You look after sales and customers: quotes, orders and their status, who the customers "
            "are and what they've bought, follow-ups after a meeting, call sheets, bookings and "
            "standing orders. Be quick and friendly with customer details, never invent a price — "
            "prices come from lookup_business_info or a tool — and nothing goes to a customer until "
            "the owner has seen the preview and said yes."
        ),
        tools=frozenset({
            "sales_summary", "recent_orders", "update_order_status", "create_manual_order",
            "order_payment", "stock_status", "outstanding_invoices", "send_payment_reminders",
            "create_invoice", "send_invoice", "payment_link", "list_quotes", "convert_quote_to_invoice",
            "update_quote_status", "recurring_invoice", "price_advice",
            "customer_lookup", "dynamics_lookup", "create_contact",
            "log_meeting", "draft_followup_email", "configure_call_sheet", "view_call_sheet",
            "update_call_sheet", "booking_availability", "list_bookings", "create_booking",
            "cancel_booking", "create_subscription", "list_subscriptions", "find_product_document",
            "list_discount_codes",
        }),
        keywords=_kw(
            "quote", "quotation", "order", "customer", "lead", "prospect", "follow up", "follow-up",
            "followup", "meeting", "call sheet", "visit", "booking", "appointment", "book in",
            "slot", "sold", "sales", "sell", "subscription", "standing order", "crm", "dynamics",
            "price list", "datasheet", "spec sheet", "brochure",
        ),
    ),
    Employee(
        id="office_admin", title="AI office administrator", emoji="🗂️",
        summary="Reminders, letters, emails, contacts, staff notices and polls, meeting notes.",
        job=(
            "You run the office: reminders, letters on the letterhead, finding and summarising "
            "emails, saving contacts, meeting notes, staff notices and polls. Keep things short and "
            "clear, confirm dates and times back, and never send anything to staff or outsiders "
            "without the preview first."
        ),
        tools=frozenset({
            "draft_letter", "create_contact", "log_meeting", "draft_followup_email", "staff_notice",
            "staff_poll", "configure_expense_sheet", "configure_call_sheet", "view_call_sheet",
            "update_call_sheet", "create_automation_rule",
        }),
        keywords=_kw(
            "remind", "letter", "letter of", "email", "e-mail", "mail", "notice", "poll", "staff",
            "birthday", "public holiday", "contact", "minutes", "expense sheet", "the reps",
            "automation", "automate",
        ),
    ),
    Employee(
        id="marketing", title="AI marketing assistant", emoji="📣",
        summary="Promotions and broadcasts, posts in your own voice, discounts, your website pages.",
        job=(
            "You do the marketing: promotions and broadcasts in the business's own voice and layout, "
            "product photos, discount codes, the website pages, and a look at what competitors "
            "charge. Use the business's real products and prices, never made-up ones, and nothing "
            "goes out to customers until the owner has seen the preview and said yes."
        ),
        tools=frozenset({
            "generate_marketing", "preview_broadcast", "send_broadcast", "picture_promotion",
            "recent_promotions", "learn_my_voice", "apply_voice_persona", "set_product_photo",
            "list_discount_codes", "create_discount_code", "update_discount_code",
            "delete_discount_code", "list_storefront_pages", "draft_storefront_page",
            "add_storefront_section", "competitor_check", "sales_summary",
        }),
        keywords=_kw(
            "promo", "promotion", "broadcast", "blast", "campaign", "marketing", "post", "caption",
            "advert", "discount", "coupon", "voucher", "special", "website", "web page", "storefront",
            "instagram", "facebook", "social", "my voice", "tone", "competitor", "flyer",
            "newsletter", "everyone", "all customers", "all our customers", "all my customers",
            "all clients", "all our clients", "mass message", "photo",
        ),
    ),
    Employee(
        id="operations", title="AI operations assistant", emoji="📦",
        summary="Stock, products, suppliers and purchase orders, production and costing, bookings.",
        job=(
            "You run operations: stock and what's running low, products, suppliers and purchase "
            "orders, deliveries received, production batches, recipes and what things cost to make, "
            "and the bookings calendar. Stock and cost figures come from the tools; anything that "
            "orders from a supplier or changes stock waits for the owner's yes on the preview."
        ),
        tools=frozenset({
            "stock_status", "update_stock", "receive_stock", "create_product", "update_product",
            "list_suppliers", "upsert_supplier", "delete_supplier", "reorder_suggestions",
            "create_purchase_order", "list_purchase_orders", "update_po_status",
            "send_purchase_order", "cost_sheet", "add_material", "receive_material",
            "price_material", "set_recipe", "log_batch", "undo_last_batch", "costing_settings",
            "booking_availability", "list_bookings", "create_booking", "cancel_booking",
            "create_automation_rule", "recent_orders", "update_order_status", "set_product_photo",
            "find_product_document",
        }),
        keywords=_kw(
            "stock", "inventory", "running low", "run out", "restock", "reorder", "supplier",
            "purchaseorder", "po ", "recipe", "batch", "production", "material", "ingredient",
            "costing", "cost sheet", "cost to make", "product", "catalogue", "catalog", "dispatch",
            "deliver", "received", "arrived", "wastage", "reject", "roster", "schedule",
        ),
    ),
)

BY_ID: Dict[str, Employee] = {e.id: e for e in TEAM}

# Short replies that continue the last turn ("yes", "confirm 2") must keep every tool the earlier
# step might need, so they never go to one employee.
_CONTINUATION = re.compile(
    r"^\s*(yes|yep|yeah|ja|no|nope|nee|ok|okay|sure|confirm|cancel|thanks|thank you|dankie|"
    r"go ahead|do it|send it|that one|the (first|second|third|last) one|\d{1,2})\b[\s.!]*\S{0,40}$",
    re.I)


def _hits(emp: Employee, text: str) -> int:
    return sum(1 for k in emp.keywords if re.search(r"(?<![a-z])" + re.escape(k), text))


def scores(message: str) -> Dict[str, int]:
    # "purchase order" is operations' word; written as one token so sales' "order" doesn't match it.
    text = " " + re.sub(r"purchase\s+orders?", "purchaseorder", (message or "").lower()) + " "
    return {e.id: _hits(e, text) for e in TEAM}


def pick(message: str, enabled: Optional[set] = None) -> Optional[Employee]:
    """The one employee this message clearly belongs to, or None (the whole team takes it).
    `enabled`: the employee ids the business has on (None = all)."""
    text = (message or "").strip()
    if not text or _CONTINUATION.match(text) or text.startswith("[What's in the photo"):
        return None
    s = {k: v for k, v in scores(text).items() if enabled is None or k in enabled}
    if not s:
        return None
    ranked = sorted(s.items(), key=lambda kv: -kv[1])
    top_id, top = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0
    if top == 0 or top == runner:
        return None
    return BY_ID[top_id]


def narrow(tools: List[Dict], emp: Optional[Employee]) -> List[Dict]:
    """The caller's tools cut down to this employee's job (plus the shared ones and hand_over).
    The input is already gated by modules and the caller's access, so this only ever removes."""
    if emp is None:
        return tools
    keep = emp.tools | SHARED_TOOLS
    out = [t for t in tools if t["function"]["name"] in keep]
    if not any(t["function"]["name"] == "hand_over" for t in out):
        out.append(HAND_OVER_TOOL)
    return out


def without_switched_off(tools: List[Dict], enabled: set) -> List[Dict]:
    """The whole team's tools minus those only a switched-off employee has."""
    off = [e for e in TEAM if e.id not in enabled]
    if not off:
        return tools
    still = set(SHARED_TOOLS).union(*(e.tools for e in TEAM if e.id in enabled))
    only_off = set().union(*(e.tools for e in off)) - still
    return [t for t in tools if t["function"]["name"] not in only_off]


def job_block(emp: Employee, instructions: str = "") -> str:
    """The employee's job description (and the owner's instructions for it) for the agent's prompt."""
    out = f"\n\nYOUR JOB — {emp.title}: {emp.job}"
    if (instructions or "").strip():
        out += ("\nTHE OWNER'S INSTRUCTIONS FOR YOU (follow them in this job): "
                + instructions.strip()[:MAX_INSTRUCTIONS])
    out += ("\nIf the request is clearly another colleague's job and none of your tools fit, call "
            "hand_over rather than saying you can't.")
    return out


# ── the business's settings (migration 236) ─────────────────────────────────

def _client():
    from vula.commerce import service
    return service._client()


def settings_for(tenant_id: str) -> Dict[str, Dict]:
    """{employee_id: {"enabled": bool, "instructions": str}} for every employee; defaults (on, no
    instructions) when nothing is saved or before migration 236."""
    out = {e.id: {"enabled": True, "instructions": ""} for e in TEAM}
    try:
        rows = (_client().table(TABLE).select("employee,enabled,instructions")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("employee settings unreadable: %s", exc)
        return out
    for r in rows:
        if r.get("employee") in out:
            out[r["employee"]] = {"enabled": r.get("enabled") is not False,
                                  "instructions": (r.get("instructions") or "").strip()}
    return out


def save(tenant_id: str, employee: str, *, enabled: Optional[bool] = None,
         instructions: Optional[str] = None, by: Optional[str] = None) -> Dict:
    """Switch an employee on/off or set its instructions. Read back. ValueError on bad input."""
    if employee not in BY_ID:
        raise ValueError("There's no such employee.")
    text = None if instructions is None else re.sub(r"\s+", " ", instructions).strip()
    if text is not None and len(text) > MAX_INSTRUCTIONS:
        raise ValueError(f"Keep the instructions under {MAX_INSTRUCTIONS} characters.")
    current = settings_for(tenant_id)[employee]
    from datetime import datetime, timezone
    row = {"tenant_id": tenant_id, "employee": employee,
           "enabled": current["enabled"] if enabled is None else bool(enabled),
           "instructions": current["instructions"] if text is None else text,
           "updated_by": (by or "").strip() or None,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    db = _client()
    db.table(TABLE).upsert(row, on_conflict="tenant_id,employee").execute()
    back = (db.table(TABLE).select("employee,enabled,instructions").eq("tenant_id", tenant_id)
            .eq("employee", employee).limit(1).execute().data or [])
    if not back or back[0].get("enabled") != row["enabled"] or \
            (back[0].get("instructions") or "") != (row["instructions"] or ""):
        raise RuntimeError("the change was not saved")
    return {"employee": employee, "enabled": back[0]["enabled"],
            "instructions": back[0].get("instructions") or ""}


def team_view(tenant_id: str) -> List[Dict]:
    """The "Your team" page: each employee, what it does, and the business's settings for it."""
    s = settings_for(tenant_id)
    return [{"id": e.id, "title": e.title, "emoji": e.emoji, "summary": e.summary,
             "enabled": s[e.id]["enabled"], "instructions": s[e.id]["instructions"]} for e in TEAM]

"""
vula/team_presets.py — preset teams: the dashboard tabs each kind of job needs.

12 Nov 2026 (Ian): "capabilities, not flooding tenants with messy or busy dashboards and tabs that
confuse day-to-day work." Putting someone in a team gives them that team's tabs, and only those
(the dashboard also hides any tab the business doesn't use). The owner can change any tab
afterwards. A team never switches notifications on: each person chooses their own (Team › People),
so `suggested_notify` is only shown as a hint.

Teams also name groups: "what did the sales team do?" (vula/team_activity.py), Team › Activity,
and staff notices to one team (vula/staff_notices.py).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

TEAMS: Dict[str, Dict[str, Any]] = {
    "sales": {"label": "Sales", "summary": "Customers, quotes, orders, follow-ups and their own chats.",
              "access": ["inbox", "customers", "contacts", "followups", "orders", "bookings", "rep-chats"],
              "suggested_notify": ["new_order", "chat_assigned", "help_request"]},
    "admin": {"label": "Admin / office", "summary": "Chats, customers, bookings, follow-ups and documents.",
              "access": ["inbox", "customers", "contacts", "followups", "bookings", "documents"],
              "suggested_notify": ["help_request", "followup_digest"]},
    "bookkeeping": {"label": "Bookkeeping", "summary": "Invoices, expenses, bank, books and payments.",
                    "access": ["invoices", "expenses", "bank", "books", "payments", "finances", "documents"],
                    "suggested_notify": ["payment_received", "new_invoice"]},
    "operations": {"label": "Operations / stock", "summary": "Orders, products, stock, suppliers and delivery.",
                   "access": ["orders", "products", "stock", "suppliers", "delivery", "costing"],
                   "suggested_notify": ["new_order", "low_stock", "procurement_done"]},
    "marketing": {"label": "Marketing", "summary": "Broadcasts, campaigns, the website and customers.",
                  "access": ["broadcast", "marketing", "email-campaigns", "pages", "customers", "wa-templates"],
                  "suggested_notify": []},
    "field": {"label": "Field / site", "summary": "Projects, site work, documents and bookings.",
              "access": ["projects", "fieldops", "documents", "bookings", "labour"],
              "suggested_notify": ["which_project"]},
}
_REP_PREFIX = "rep-"


def presets() -> List[Dict[str, Any]]:
    return [{"id": k, **v} for k, v in TEAMS.items()]


def find(words: Optional[str]) -> Optional[str]:
    """'sales', 'Sales team', 'the bookkeeper', 'stock' → a team id, or None."""
    w = re.sub(r"[^a-z ]", " ", (words or "").lower())
    w = re.sub(r"\b(?:the|team|dept|department)\b", " ", w).strip()
    if not w:
        return None
    if w in TEAMS:
        return w
    aliases = {"sales": ("sale", "sales", "rep", "reps", "selling"),
               "admin": ("admin", "office", "reception", "front desk"),
               "bookkeeping": ("bookkeeper", "bookkeeping", "accounts", "finance", "books"),
               "operations": ("operations", "ops", "stock", "warehouse", "dispatch", "delivery"),
               "marketing": ("marketing", "social", "promotions"),
               "field": ("field", "site", "sites", "installers", "installer", "technician", "technicians")}
    for team, words_ in aliases.items():
        if any(re.search(rf"\b{re.escape(a)}\b", w) for a in words_):
            return team
    return None


def access_for(team: str, role: Optional[str], current: Optional[List[str]] = None) -> List[str]:
    """The tabs a person in this team gets. A sales rep keeps their own "My Work" tabs too."""
    preset = list(TEAMS[team]["access"])
    keep = [a for a in (current or []) if a.startswith(_REP_PREFIX)] if (role or "") == "sales_rep" else []
    return list(dict.fromkeys(keep + preset))

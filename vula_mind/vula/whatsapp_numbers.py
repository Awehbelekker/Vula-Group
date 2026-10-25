"""
vula/whatsapp_numbers.py — a business's WhatsApp numbers, each with a purpose (25 Oct 2026, Ian:
"giving them a choice to use the number for clients or internal sales, admin, stores, booking…
and what does it look like if the client wants to add numbers").

A business can have several numbers (rows of vula_whatsapp_accounts, one per phone_number_id).
Each has a purpose the owner picks, which decides how Vula answers on it:

  shop       customers: products, orders, payments          → route_mode "commerce"
  bookings   customers: bookings first (the bookings module) → route_mode "commerce"
  assistant  answers from the business's documents           → route_mode "knowledge"
  team       the business's own people only; anyone else is pointed to the main number
                                                             → route_mode "team"

One number is the business's main number (is_primary): messages Vula starts itself (order
updates, booking reminders, login codes, broadcasts) go from it. A reply always goes from the
number the person wrote to: receive_message (vula/api/whatsapp.py) sets INBOUND for the message
being handled, and whatsapp._get_tenant_wa_creds reads it — so the many places that send by
tenant need no change.

Rows from before migration 225 have no purpose / is_primary: their route_mode stands, and a
business's only row counts as its main number.
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict, List, Optional

PURPOSES: Dict[str, str] = {"shop": "commerce", "bookings": "commerce",
                            "assistant": "knowledge", "team": "team"}
PURPOSE_LABELS: Dict[str, str] = {
    "shop": "Customers: shop and orders",
    "bookings": "Customers: bookings",
    "assistant": "Assistant: answers from your documents",
    "team": "Team only",
}

# The number the message being handled came in on: {"pnid", "purpose", "from"}. Set per inbound
# message; copied into every background task started while handling it.
INBOUND: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "vula_wa_inbound_number", default=None)


def purpose_of(row: Dict[str, Any]) -> str:
    """The row's purpose, or the one its route_mode implies (rows from before migration 225)."""
    p = (row.get("purpose") or "").strip()
    if p in PURPOSES:
        return p
    return {"knowledge": "assistant", "team": "team"}.get(row.get("route_mode") or "", "shop")


def connected(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if r.get("status") == "connected"]


def main_row(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The business's main number among its connected rows: the one marked main, else a customer
    number (not team-only), else the oldest."""
    live = connected(rows)
    if not live:
        return None
    marked = [r for r in live if r.get("is_primary")]
    if marked:
        return marked[0]
    customer = [r for r in live if purpose_of(r) != "team"]
    return sorted(customer or live, key=lambda r: str(r.get("created_at") or r.get("connected_at") or ""))[0]


def _digits(v: Any) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def pick(rows: List[Dict[str, Any]], phone_number_id: Optional[str] = None,
         to: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The number to send from: the one asked for, else the one the current message came in on,
    when it is this business's and connected; else the main number.

    On a team-only number only the person who wrote is answered from it: anything sent to
    someone else while handling their message (a customer's order update, a quote a rep asked
    for) goes from the main number, the one customers know."""
    pnid = phone_number_id
    inbound = INBOUND.get() or {}
    if not pnid and inbound.get("pnid"):
        if inbound.get("purpose") != "team" or (to and _digits(to) == _digits(inbound.get("from"))):
            pnid = inbound["pnid"]
    if pnid:
        hit = next((r for r in connected(rows) if r.get("phone_number_id") == pnid), None)
        if hit:
            return hit
    return main_row(rows)


def inbound_purpose() -> Optional[str]:
    return (INBOUND.get() or {}).get("purpose")


def rows_for(tenant_id: str, client=None) -> List[Dict[str, Any]]:
    """Every number row of a business ("*" so it reads the same before and after migration 225)."""
    if client is None:
        from vula.commerce import service
        client = service._client()
    return (client.table("vula_whatsapp_accounts").select("*")
            .eq("tenant_id", tenant_id).execute().data or [])


def main_number(tenant_id: str, client=None) -> Optional[Dict[str, Any]]:
    """The business's main connected number row, or None."""
    try:
        return main_row(rows_for(tenant_id, client))
    except Exception:
        return None


def public(row: Dict[str, Any], main_pnid: Optional[str]) -> Dict[str, Any]:
    """A number as the dashboard shows it (never the token)."""
    p = purpose_of(row)
    return {"phone_number_id": row.get("phone_number_id"), "phone_number": row.get("phone_number") or "",
            "verified_name": row.get("verified_name") or "", "status": row.get("status"),
            "webhook_registered": bool(row.get("webhook_registered")), "waba_id": row.get("waba_id"),
            "coexistence": bool(row.get("coexistence")), "purpose": p, "purpose_label": PURPOSE_LABELS[p],
            "label": row.get("label") or "", "is_primary": bool(main_pnid and row.get("phone_number_id") == main_pnid),
            "on_vula_account": not row.get("access_token"), "connected_at": row.get("connected_at")}

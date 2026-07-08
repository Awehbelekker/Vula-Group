"""
vula/commerce/order_workflow.py — per-tenant order workflow.

A tenant can require owner approval before an order is fulfilled, and choose where the
fulfilment ticket is dispatched (WhatsApp / email / both). Used by the order-paid path
(yoco.py) and the approvals engine's _on_approved hook.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "require_approval": False, "dispatch_channel": "whatsapp",
    "fulfillment_email": None, "fulfillment_whatsapp": None,
    "payment_methods": ["online", "cod", "eft"], "eft_details": None,
}
_FIELDS = ("require_approval", "dispatch_channel", "fulfillment_email", "fulfillment_whatsapp",
           "payment_methods", "eft_details")

# Customer-facing labels for the payment methods.
PAYMENT_LABELS = {"online": "Card / online payment", "cod": "Pay on delivery", "eft": "EFT / bank transfer"}


def payment_instructions(cfg: dict, method: str) -> str:
    """The line(s) to show a customer for their chosen payment method."""
    method = (method or "").lower()
    if method == "cod":
        return "💵 *Pay on delivery* — have cash or card ready when your order arrives."
    if method == "eft":
        details = (cfg.get("eft_details") or "").strip()
        if details:
            return ("🏦 *EFT / bank transfer* — please pay into:\n" + details +
                    "\n\nUse your order number as the reference and send proof of payment here.")
        return ("🏦 *EFT / bank transfer* — we'll send our banking details shortly. "
                "Use your order number as the payment reference.")
    return "💳 *Card / online payment*"


def _client():
    from vula.commerce import service as cs
    return cs._client()


def get_order_settings(tenant_id: str) -> dict:
    try:
        rows = (_client().table("commerce_order_settings").select("*")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        return {**_DEFAULTS, **rows[0]} if rows else dict(_DEFAULTS)
    except Exception as exc:
        logger.debug("order settings read skipped (run migration 043?): %s", exc)
        return dict(_DEFAULTS)


def upsert_order_settings(tenant_id: str, patch: dict) -> dict:
    row = {k: v for k, v in (patch or {}).items() if k in _FIELDS}
    row["tenant_id"] = tenant_id
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    db = _client()
    existing = (db.table("commerce_order_settings").select("tenant_id")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    if existing:
        db.table("commerce_order_settings").update(row).eq("tenant_id", tenant_id).execute()
    else:
        db.table("commerce_order_settings").insert(row).execute()
    return get_order_settings(tenant_id)


async def dispatch_order(tenant_id: str, order_id: str, summary: str, customer_name: str = "") -> None:
    """Send the fulfilment ticket to the tenant's chosen channel(s)."""
    cfg = get_order_settings(tenant_id)
    ch = (cfg.get("dispatch_channel") or "whatsapp").lower()

    if ch in ("whatsapp", "both") and cfg.get("fulfillment_whatsapp"):
        try:
            from vula.api.whatsapp import _send_reply
            await _send_reply(cfg["fulfillment_whatsapp"], f"📦 Order to fulfil:\n\n{summary}", tenant_id=tenant_id)
        except Exception as exc:
            logger.warning("order whatsapp dispatch failed: %s", exc)

    if ch in ("email", "both") and cfg.get("fulfillment_email"):
        try:
            from vula.api.email import _send
            safe = summary.replace("<", "&lt;")
            html = f"<pre style='font-family:ui-monospace,monospace;font-size:14px'>{safe}</pre>"
            subject = f"New order to fulfil{(' — ' + customer_name) if customer_name else ''}"
            await _send(cfg["fulfillment_email"], subject, html, summary)
        except Exception as exc:
            logger.warning("order email dispatch failed: %s", exc)

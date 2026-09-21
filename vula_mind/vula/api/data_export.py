"""
vula/api/data_export.py — POPIA "send me my data" export.

Automated erasure already existed (whatsapp.py's _handle_data_deletion), but a data *access*
request was entirely manual (email hello@vula.co.za). This closes that gap for the common case:
a WhatsApp trigger phrase assembles a bounded PDF — the requester's own chat history plus their
own orders/invoices for this tenant, tenant-scoped and phone-scoped, NEVER the tenant's full
filed-document library or other customers' data — and emails it to the address on file (email,
not a WhatsApp attachment: cleaner for a larger payload, and "we emailed it to the address on
file" doubles as identity confirmation).

Fails closed throughout: any assembly/render/send failure returns a typed error, never a
partial or wrong export, and the caller (whatsapp.py) falls back to the same
"email hello@vula.co.za" language _handle_data_deletion already uses.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_MAX_MESSAGES = 500
_MAX_ROWS = 200


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _matches_phone(row_phone: str, digits: str) -> bool:
    row_digits = _digits(row_phone)
    return bool(row_digits and digits and row_digits.endswith(digits[-9:]))


async def _own_orders(tenant_id: str, phone: str) -> List[Dict[str, Any]]:
    """This phone's own orders for this tenant. Same defensive last-9-digit suffix matching as
    commerce/service.py's get_customer_profile/reorder_from_last_order — stored customer_phone
    formatting isn't perfectly consistent."""
    digits = _digits(phone)
    if not digits:
        return []
    try:
        from vula.commerce.service import _client
        rows = (_client().table("commerce_orders")
                .select("display_id,total_cents,status,created_at,customer_phone,customer_email")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True).limit(2000).execute().data or [])
    except Exception as exc:
        logger.warning("data export: orders lookup failed (tenant=%s): %s", tenant_id, exc)
        return []
    if not isinstance(rows, list):
        return []
    return [o for o in rows if _matches_phone(o.get("customer_phone") or "", digits)][:_MAX_ROWS]


async def _own_invoices(tenant_id: str, phone: str) -> List[Dict[str, Any]]:
    """This phone's own invoices/quotes for this tenant — same phone-suffix matching as orders."""
    digits = _digits(phone)
    if not digits:
        return []
    try:
        from vula.commerce.service import _client
        rows = (_client().table("commerce_invoices")
                .select("invoice_number,doc_type,total_cents,status,created_at,"
                        "customer_phone,customer_email")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True).limit(2000).execute().data or [])
    except Exception as exc:
        logger.warning("data export: invoices lookup failed (tenant=%s): %s", tenant_id, exc)
        return []
    if not isinstance(rows, list):
        return []
    return [i for i in rows if _matches_phone(i.get("customer_phone") or "", digits)][:_MAX_ROWS]


async def assemble_export(tenant_id: str, phone: str) -> Dict[str, Any]:
    """Everything this phone number's own for this tenant: chat history + their own orders/
    invoices. Never the tenant's full filed-document library or other customers' data."""
    from vula.chat.history import get_db
    messages = get_db().get(tenant_id, phone, limit=_MAX_MESSAGES, max_age_hours=None)
    orders = await _own_orders(tenant_id, phone)
    invoices = await _own_invoices(tenant_id, phone)
    email = None
    for row in invoices + orders:
        candidate = (row.get("customer_email") or "").strip()
        if candidate:
            email = candidate
            break
    return {"messages": messages, "orders": orders, "invoices": invoices, "email": email}


def _render_export_markdown(phone: str, data: Dict[str, Any]) -> str:
    messages = data["messages"]
    orders = data["orders"]
    invoices = data["invoices"]
    lines = [f"Data export for **{phone}**", ""]

    lines.append(f"## Conversation history ({len(messages)} messages)")
    if messages:
        for m in messages:
            who = "You" if m.role == "user" else "Vula"
            when = (m.created_at or "")[:16].replace("T", " ")
            lines.append(f"- *{when}* — **{who}**: {m.text}")
    else:
        lines.append("_No conversation history on file._")
    lines.append("")

    lines.append(f"## Orders ({len(orders)})")
    if orders:
        for o in orders:
            total = (o.get("total_cents") or 0) / 100
            lines.append(f"- {o.get('display_id', '')} — R{total:.2f} — "
                         f"{o.get('status', '')} — {(o.get('created_at') or '')[:10]}")
    else:
        lines.append("_No orders on file._")
    lines.append("")

    lines.append(f"## Invoices & quotes ({len(invoices)})")
    if invoices:
        for i in invoices:
            total = (i.get("total_cents") or 0) / 100
            lines.append(f"- {i.get('invoice_number', '')} ({i.get('doc_type', '')}) — "
                         f"R{total:.2f} — {i.get('status', '')} — {(i.get('created_at') or '')[:10]}")
    else:
        lines.append("_No invoices or quotes on file._")

    return "\n".join(lines)


async def send_data_export(tenant_id: str, phone: str) -> Dict[str, Any]:
    """Assemble, render, and email the requester's own data. Returns {"sent": True, "email":
    ...} or {"error": "<reason>"} — never raises; the caller (whatsapp.py) decides the reply
    wording, matching the existing deletion flow's fallback-to-email language on any failure."""
    try:
        data = await assemble_export(tenant_id, phone)
    except Exception as exc:
        logger.error("data export assembly failed (tenant=%s): %s", tenant_id, exc)
        return {"error": "assembly_failed"}

    email = data.get("email")
    if not email:
        return {"error": "no_email_on_file"}

    try:
        from vula.commerce.pdf import merge_branding, render_letter_pdf
        from vula.commerce.service import get_invoice_settings
        settings_row = await get_invoice_settings(tenant_id)
        branding = merge_branding(tenant_id, settings_row)
        tenant_name = branding.get("name") or tenant_id.replace("-", " ").title()
        md = _render_export_markdown(phone, data)
        pdf_bytes = render_letter_pdf(
            tenant_id=tenant_id, body_markdown=md, doc_label="Your Data Export",
            tenant_profile=branding,
        )
    except Exception as exc:
        logger.error("data export PDF render failed (tenant=%s): %s", tenant_id, exc)
        return {"error": "render_failed"}

    try:
        from vula.api.email import send_data_export_email
        ok = await send_data_export_email(email, tenant_name, pdf_bytes)
    except Exception as exc:
        logger.error("data export email send failed (tenant=%s): %s", tenant_id, exc)
        return {"error": "email_failed"}

    if not ok:
        return {"error": "email_not_sent"}
    return {"sent": True, "email": email}

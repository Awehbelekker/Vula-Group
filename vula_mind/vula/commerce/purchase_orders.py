"""
vula/commerce/purchase_orders.py — supplier auto-reorder (migration 123, builds on the
commerce_purchase_orders / reorder_threshold/reorder_qty/default_supplier_id columns from
migration 077, which existed but were never read by anything).

Draft-only, owner confirms before send — same propose→confirm shape already proven as this
platform's safest real-automation pattern (filing rules, voice-profile suggestions). Vula never
commits real spend to a supplier without a human clicking Send.

Email carries the actual itemized PO (vula/email_imap/service.py::send(), works for any
address via the tenant's connected mailbox, no external approval needed). WhatsApp can only
carry a short companion notification — proactive WhatsApp sends require a pre-approved Meta
template (see _send_wa_template's own docstring), so a "supplier_po_notice" template must exist
and be approved before that leg will actually deliver; until then it fails cleanly with a clear
message rather than silently doing nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_reorder_suggestions(tenant_id: str) -> Dict[str, Any]:
    """Products (or, for a variant product, each individual variant) at/below their reorder
    threshold, grouped by default supplier — the single source of truth for "what should I
    order", shared by the dashboard's manual suggestions view and draft_reorder_pos() below.
    Anything without a threshold set is never suggested."""
    from vula.commerce import service

    products = await service.list_products(tenant_id, in_stock_only=False, include_archived=False)
    low: List[dict] = []
    for p in products:
        variants = await service.list_variants(tenant_id, p["id"], include_archived=False)
        if variants:
            for v in variants:
                if v.get("reorder_threshold") is None:
                    continue
                if (v.get("stock_quantity") or 0) <= v["reorder_threshold"]:
                    opts = v.get("option_values") or {}
                    suffix = ", ".join(f"{k}: {val}" for k, val in opts.items())
                    low.append({
                        "product_id": p["id"], "variant_id": v["id"],
                        "name": f"{p['name']} ({suffix})" if suffix else p["name"],
                        "stock_quantity": v.get("stock_quantity") or 0,
                        "reorder_threshold": v["reorder_threshold"], "suggested_qty": v.get("reorder_qty") or 10,
                        "unit_cost_cents": v.get("price_cents") or p.get("price_cents") or 0,
                        "default_supplier_id": v.get("default_supplier_id"),
                    })
        elif (p.get("reorder_threshold") is not None
              and (p.get("stock_quantity") or 0) <= p["reorder_threshold"]):
            low.append({
                "product_id": p["id"], "variant_id": None, "name": p["name"],
                "stock_quantity": p.get("stock_quantity") or 0,
                "reorder_threshold": p["reorder_threshold"], "suggested_qty": p.get("reorder_qty") or 10,
                "unit_cost_cents": p.get("price_cents") or 0,
                "default_supplier_id": p.get("default_supplier_id"),
            })
    suppliers = {s["id"]: s for s in await service.list_suppliers(tenant_id)}
    groups: Dict[str, dict] = {}
    for item in low:
        sid = item.pop("default_supplier_id") or "unassigned"
        g = groups.setdefault(sid, {
            "supplier_id": None if sid == "unassigned" else sid,
            "supplier_name": suppliers.get(sid, {}).get("name") if sid != "unassigned" else "No default supplier",
            "items": [],
        })
        g["items"].append(item)
    return {"groups": list(groups.values()), "count": len(low)}


async def draft_reorder_pos(tenant_id: str) -> List[Dict[str, Any]]:
    """Draft (or extend an existing open draft for the same supplier with) a PO for every
    low-stock product/variant that has a default supplier — items with no default supplier
    can't be auto-ordered and are left for the owner to handle manually (still visible via
    get_reorder_suggestions' "unassigned" group, same as before this feature existed)."""
    db = _client()
    suggestions = await get_reorder_suggestions(tenant_id)
    results: List[Dict[str, Any]] = []

    for group in suggestions["groups"]:
        supplier_id = group["supplier_id"]
        if not supplier_id:  # "unassigned" bucket — no supplier to send a PO to
            continue

        new_lines = [{"product_id": it["product_id"], "variant_id": it.get("variant_id"),
                      "name": it["name"], "quantity": it["suggested_qty"],
                      "unit_cost_cents": it.get("unit_cost_cents") or 0} for it in group["items"]]

        existing = (db.table("commerce_purchase_orders").select("*")
                    .eq("tenant_id", tenant_id).eq("supplier_id", supplier_id).eq("status", "draft")
                    .order("created_at", desc=True).limit(1).execute().data or [])

        if existing:
            po = existing[0]
            items = list(po.get("items") or [])
            have = {(it.get("product_id"), it.get("variant_id")) for it in items}
            new_ones = [l for l in new_lines if (l["product_id"], l["variant_id"]) not in have]
            if new_ones:
                items.extend(new_ones)
                db.table("commerce_purchase_orders").update({"items": items}).eq("id", po["id"]).execute()
                po["items"] = items
                results.append(po)
            # else: everything already on the existing draft — nothing new to surface.
        else:
            row = {
                "tenant_id": tenant_id, "supplier_id": supplier_id, "supplier_name": group["supplier_name"],
                "status": "draft", "items": new_lines, "total_cents": 0,
                "notes": "Auto-drafted from low stock — confirm pricing before sending.",
            }
            res = db.table("commerce_purchase_orders").insert(row).execute()
            if res.data:
                results.append(res.data[0])
    return results


def render_po_email(tenant_id: str, po: Dict[str, Any]) -> Tuple[str, str]:
    """Deterministic plain-text PO formatting — no LLM, accuracy matters more than prose here."""
    from vula.api import tenants as _tenants
    biz_name = _tenants.get_config(tenant_id).get("display_name") or tenant_id
    ref = str(po.get("id") or "")[:8]

    lines = [f"Purchase order from {biz_name} (ref {ref})", ""]
    for it in (po.get("items") or []):
        lines.append(f"  - {it.get('name', 'item')} x {it.get('quantity', 0)}")
    lines += ["", "Please confirm pricing, availability, and an expected delivery date.",
              "", "Thanks,", biz_name]
    return f"Purchase order {ref} — {biz_name}", "\n".join(lines)


async def send_purchase_order(tenant_id: str, po_id: str, channel: str) -> Dict[str, Any]:
    """channel: 'email' | 'whatsapp' | 'both'. Each leg is independent — a failure on one
    doesn't block the other; the PO is only marked sent if at least one leg succeeded."""
    db = _client()
    rows = (db.table("commerce_purchase_orders").select("*")
            .eq("tenant_id", tenant_id).eq("id", po_id).limit(1).execute().data or [])
    if not rows:
        return {"error": "purchase order not found"}
    po = rows[0]

    supplier = None
    if po.get("supplier_id"):
        srows = (db.table("commerce_suppliers").select("*")
                 .eq("tenant_id", tenant_id).eq("id", po["supplier_id"]).limit(1).execute().data or [])
        supplier = srows[0] if srows else None
    if not supplier:
        return {"error": "no supplier on this purchase order"}

    channels = ["email", "whatsapp"] if channel == "both" else [channel]
    sent_via: List[str] = []
    errors: List[str] = []

    if "email" in channels:
        email = supplier.get("contact_email")
        if not email:
            errors.append("supplier has no contact_email on file")
        else:
            try:
                from vula.email_imap.credentials import get_email_creds
                from vula.email_imap.service import send as email_send
                creds = get_email_creds(tenant_id)
                if not creds:
                    errors.append("no connected mailbox for this tenant")
                else:
                    subject, body = render_po_email(tenant_id, po)
                    await email_send(creds, email, subject, body)
                    sent_via.append("email")
            except Exception as exc:
                log.warning("PO email send failed for %s: %s", po_id, exc)
                errors.append(f"email send failed: {exc}")

    if "whatsapp" in channels:
        phone = supplier.get("contact_phone")
        if not phone:
            errors.append("supplier has no contact_phone on file")
        else:
            try:
                from vula.api.whatsapp import _send_wa_template
                total = f"R{(po.get('total_cents') or 0) / 100:.2f}"
                ok = await _send_wa_template(
                    tenant_id, phone, "supplier_po_notice",
                    str(po.get("id") or "")[:8], str(len(po.get("items") or [])), total)
                if ok:
                    sent_via.append("whatsapp")
                else:
                    errors.append(
                        "whatsapp send failed — the 'supplier_po_notice' template likely "
                        "doesn't exist or isn't Meta-approved yet")
            except Exception as exc:
                log.warning("PO whatsapp send failed for %s: %s", po_id, exc)
                errors.append(f"whatsapp send failed: {exc}")

    if not sent_via:
        return {"error": "; ".join(errors) or "send failed on all requested channels"}

    patch = {"status": "sent", "sent_at": _now(), "sent_channel": "+".join(sent_via)}
    db.table("commerce_purchase_orders").update(patch).eq("tenant_id", tenant_id).eq("id", po_id).execute()
    result: Dict[str, Any] = {"ok": True, "sent_via": sent_via}
    if errors:
        result["warnings"] = errors
    return result

"""
vula/commerce/approvals.py

Generic multi-approver engine. An approval can require several approvers
(architect + QS + client…); ALL must approve before the action fires. Works for
any entity — currently invoices (→ deliver to client) and could extend to tasks,
quotes, etc.

Flow:
  create_approval()  → inserts approval + one step per approver, WhatsApps each.
  record_decision()  → an approver replies APPROVE/REJECT; records their step,
                       and when all are in, finalises (fires the action or rejects).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)


def _client():
    from supabase import create_client
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key or settings.supabase_service_key,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digits(phone: str) -> str:
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    return "27" + d[1:] if d.startswith("0") and len(d) == 10 else d


async def tenant_admin_approvers(tenant_id: str) -> list[dict]:
    """Default approvers for an internal (non-client-facing) approval — the tenant's own
    admin/owner team. Unlike an outbound invoice (an external architect/QS/client picks who
    signs off), there's no one to choose for "is this really Supplier X?" — it's always the
    tenant's own team confirming their own books."""
    from vula.integrations.notify import _members, _fallback_phone
    approvers = [
        {"phone": m["whatsapp"], "name": m.get("name", ""), "role": m.get("role", "admin")}
        for m in _members(tenant_id)
        if m.get("whatsapp") and m.get("role") in ("admin", "owner")
    ]
    if not approvers:
        fb = _fallback_phone(tenant_id)
        if fb:
            approvers = [{"phone": fb, "name": "", "role": "admin"}]
    return approvers


async def create_approval(
    tenant_id: str, entity_type: str, entity_id: str, title: str,
    approvers: list[dict], requested_by: str = "", deliver_via: str = "", meta: dict | None = None,
) -> dict:
    """Create an approval requiring every approver to sign off.

    approvers: [{"phone": "...", "name": "...", "role": "..."}]
    Returns the approval row.
    """
    from vula.api.whatsapp import _send_reply  # lazy: avoid circular import

    sb = _client()
    appr = sb.table("vula_approvals").insert({
        "tenant_id": tenant_id, "entity_type": entity_type, "entity_id": entity_id,
        "title": title, "requested_by": requested_by, "status": "pending",
        "deliver_via": deliver_via, "meta": meta or {},
    }).execute().data[0]

    steps = [{
        "approval_id": appr["id"],
        "approver_phone": _digits(a["phone"]),
        "approver_name": a.get("name", ""),
        "role": a.get("role", "approver"),
        "status": "pending",
    } for a in approvers if a.get("phone")]
    if steps:
        sb.table("vula_approval_steps").insert(steps).execute()

    # Notify each approver on WhatsApp
    for s in steps:
        await _send_reply(
            s["approver_phone"],
            f"🔔 Approval needed: *{title}*\n\n"
            f"Reply *APPROVE* to authorise, or *REJECT <reason>* to decline.",
            tenant_id,
        )
    return appr


async def record_decision(approver_phone: str, decision: str, notes: str = "") -> Optional[dict]:
    """Record an approver's APPROVE/REJECT against their most recent pending step.

    Returns the approval dict if a decision was recorded, else None (so the caller
    can fall back to other handlers, e.g. field-ops task sign-off).
    """
    from vula.api.whatsapp import _send_reply

    sb = _client()
    phone = _digits(approver_phone)

    step_res = (sb.table("vula_approval_steps")
                .select("*").eq("approver_phone", phone).eq("status", "pending")
                .order("id", desc=True).limit(1).execute())
    steps = step_res.data or []
    if not steps:
        return None  # nothing pending for this approver → not an approval reply
    step = steps[0]

    appr = (sb.table("vula_approvals").select("*").eq("id", step["approval_id"])
            .limit(1).execute()).data
    if not appr or appr[0]["status"] != "pending":
        return None
    approval = appr[0]

    # Record this approver's decision
    sb.table("vula_approval_steps").update({
        "status": decision, "notes": notes, "decided_at": _now(),
    }).eq("id", step["id"]).execute()

    tenant_id = approval["tenant_id"]
    title = approval["title"]

    if decision == "rejected":
        sb.table("vula_approvals").update(
            {"status": "rejected", "completed_at": _now()}
        ).eq("id", approval["id"]).execute()
        await _send_reply(phone, f"❌ You rejected *{title}*. The requester has been notified.", tenant_id)
        if approval.get("requested_by"):
            await _send_reply(
                _digits(approval["requested_by"]),
                f"❌ *{title}* was rejected by {step.get('approver_name') or 'an approver'}"
                f"{f': {notes}' if notes else ''}.",
                tenant_id,
            )
        return approval

    # Approved — are we still waiting on anyone?
    remaining = (sb.table("vula_approval_steps")
                 .select("id", count="exact")
                 .eq("approval_id", approval["id"]).eq("status", "pending").execute())
    pending_left = remaining.count if remaining.count is not None else len(remaining.data or [])

    if pending_left > 0:
        await _send_reply(
            phone,
            f"✅ Thanks — you approved *{title}*. Waiting on {pending_left} more approver"
            f"{'s' if pending_left != 1 else ''}.",
            tenant_id,
        )
        return approval

    # Everyone has approved → finalise + fire the action
    sb.table("vula_approvals").update(
        {"status": "approved", "completed_at": _now()}
    ).eq("id", approval["id"]).execute()
    await _send_reply(phone, f"✅ *{title}* is fully approved. Delivering now.", tenant_id)
    await _on_approved(approval)
    return approval


async def _on_approved(approval: dict) -> None:
    """Fire the action for a fully-approved entity."""
    if approval["entity_type"] == "invoice":
        await _deliver_invoice(approval)
    elif approval["entity_type"] == "task":
        from vula.models.field_ops import get_field_ops_db
        get_field_ops_db().update_task_status(approval["entity_id"], "complete")
    elif approval["entity_type"] == "order":
        from vula.commerce.order_workflow import dispatch_order
        meta = approval.get("meta") or {}
        await dispatch_order(approval["tenant_id"], approval["entity_id"],
                             meta.get("summary") or approval.get("title", ""),
                             meta.get("customer_name", ""))
    elif approval["entity_type"] == "inbound_invoice":
        await _apply_supplier_match(approval)


async def _apply_supplier_match(approval: dict) -> None:
    """A tenant admin confirmed an ambiguous supplier match (commit_inbound_document's
    needs_review case) — link the candidate supplier onto the invoice row and clear
    needs_review on the source filed document, same fields commit_inbound_document itself
    would have set had the match been confident enough to auto-apply."""
    meta = approval.get("meta") or {}
    supplier_id = meta.get("candidate_supplier_id")
    if not supplier_id:
        return
    sb = _client()
    tenant_id, invoice_id = approval["tenant_id"], approval["entity_id"]
    try:
        sb.table("commerce_invoices").update(
            {"supplier_id": supplier_id}
        ).eq("tenant_id", tenant_id).eq("id", invoice_id).execute()
    except Exception as exc:
        logger.warning("Failed to apply approved supplier match to invoice %s: %s", invoice_id, exc)
    filed_document_id = meta.get("filed_document_id")
    if filed_document_id:
        try:
            sb.table("vula_filed_documents").update(
                {"supplier_id": supplier_id, "needs_review": False}
            ).eq("id", filed_document_id).execute()
        except Exception as exc:
            logger.warning("Failed to clear needs_review on filed_document %s: %s", filed_document_id, exc)


async def _deliver_invoice(approval: dict) -> None:
    """Send an approved invoice to the client via WhatsApp / email / both."""
    import httpx
    tenant_id = approval["tenant_id"]
    invoice_id = approval["entity_id"]
    via = (approval.get("deliver_via") or "whatsapp").lower()
    base = f"http://localhost:{settings.api_port}/v1/commerce/{tenant_id}/admin/invoices/{invoice_id}"
    headers = {"X-API-Key": settings.api_key, "Content-Type": "application/json"}

    targets = []
    if via in ("whatsapp", "both"):
        targets.append("send-whatsapp")
    if via in ("email", "both"):
        targets.append("send-email")
    if not targets:
        targets = ["send-whatsapp"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for ep in targets:
            try:
                r = await client.post(f"{base}/{ep}", headers=headers, json={})
                logger.info("Invoice %s %s → %s", invoice_id, ep, r.status_code)
            except Exception as exc:
                logger.warning("Invoice %s %s failed: %s", invoice_id, ep, exc)

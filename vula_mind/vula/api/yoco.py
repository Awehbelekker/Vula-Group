"""
vula/api/yoco.py — Yoco payment webhook handler.
Mounted at /v1/yoco in server.py.

Yoco POSTs here when a payment succeeds or fails.
We update the order status and fire the WhatsApp confirmation.

Per-tenant credentials are pulled from Supabase (vula_yoco_accounts).
Falls back to global YOCO_WEBHOOK_SECRET env var for backwards compat.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from config import settings
from vula.commerce import service as commerce

log = logging.getLogger(__name__)
router = APIRouter(tags=["yoco"])


_yoco_creds_cache: dict[str, dict] = {}

# Per-tenant team phones — receive order alerts when a payment lands.
# Format: tenant_id → list of (name, phone_e164, role)
_TENANT_TEAM: dict[str, list[tuple[str, str, str]]] = {
    "off-the-hook": [
        ("Stacy", "27737815979", "owner"),
    ],
    "digg-demo": [
        ("Judy", "27827077080", "owner"),
    ],
}


def _tenant_team(tenant_id: str) -> list[tuple[str, str, str]]:
    """Who gets order alerts — DB-driven (vula_team_members) so a new tenant's team
    needs no code change; falls back to the static map. Owners/managers/operations
    and anyone who opted into an order notify event."""
    try:
        from vula.commerce import service as cs
        rows = (cs._client().table("vula_team_members")
                .select("name,whatsapp,role,notify,active")
                .eq("tenant_id", tenant_id).eq("active", True).execute().data or [])
        team = []
        for r in rows:
            phone = (r.get("whatsapp") or "").strip()
            if not phone:
                continue
            notify = r.get("notify") or []
            if r.get("role") in ("owner", "manager", "operations") or \
               any(n in notify for n in ("order_paid", "new_order", "orders")):
                team.append((r.get("name") or "", phone, r.get("role") or "staff"))
        if team:
            return team
    except Exception as exc:
        log.debug("team DB lookup skipped: %s", exc)
    return _TENANT_TEAM.get(tenant_id, [])

# Last-resort fallback if the vula_whatsapp_accounts DB lookup fails below — keep in sync
# with the tenant's CURRENT number (this table went stale for weeks pointing at a retired
# number and silently 400'd every send; the DB lookup is the source of truth, this is backup only).
_TENANT_PHONE_IDS: dict[str, str] = {
    "off-the-hook": "1216487374874418",  # +27 79 178 3933
    "digg-demo": "1180015145200511",     # +27 66 566 9387
}


async def _notify_order_paid(
    tenant_id: str,
    display_id: str,
    order_id: str,
    customer_phone: Optional[str],
    customer_name: str,
    amount_cents: int,
) -> None:
    """Send WhatsApp order confirmation to customer and alert the team."""
    # Payment confirmed → deduct stock once (idempotent via the order's stock_adjusted flag).
    try:
        from vula.commerce import service as _cs
        await _cs.apply_order_stock(order_id, restore=False)
    except Exception as exc:
        log.debug("order-paid stock decrement skipped: %s", exc)
    # Resolve the tenant's LIVE WhatsApp creds (phone_id + token), not a hardcoded
    # (retired) number. Falls back to env/global.
    creds = None
    try:
        from vula.api.whatsapp import _get_tenant_wa_creds
        creds = await _get_tenant_wa_creds(tenant_id)
    except Exception:
        creds = None
    if not creds and settings.whatsapp_token and (settings.whatsapp_phone_id or _TENANT_PHONE_IDS.get(tenant_id)):
        creds = {"token": settings.whatsapp_token,
                 "phone_id": _TENANT_PHONE_IDS.get(tenant_id) or settings.whatsapp_phone_id}
    if not creds:
        log.info("Order notify: no WhatsApp creds for %s", tenant_id)
        return

    phone_id = creds["phone_id"]
    amount = f"R{amount_cents / 100:.2f}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Content-Type": "application/json",
        }

        async def _send(to: str, body: str) -> None:
            number = to.lstrip("+").replace(" ", "").replace("-", "")
            if number.startswith("0"):
                number = "27" + number[1:]
            try:
                await client.post(
                    f"https://graph.facebook.com/v19.0/{phone_id}/messages",
                    headers=headers,
                    json={
                        "messaging_product": "whatsapp",
                        "to": number,
                        "type": "text",
                        "text": {"body": body[:4096]},
                    },
                )
            except Exception as exc:
                log.warning("WhatsApp notify failed to %s: %s", to, exc)

        # Customer confirmation
        if customer_phone:
            from vula.api import tenants as _tenants
            # Every tenant's customers used to be told their "Off the Hook order" was confirmed,
            # with a link to offthehook.co.za.
            store_url = _tenants.store_url(tenant_id)
            track = f"Track at {store_url} or reply" if store_url else "Reply"
            await _send(
                customer_phone,
                f"Hi {customer_name or 'there'}! Your {_tenants.display_name(tenant_id)} order "
                f"*{display_id}* is confirmed ({amount}). We'll be in touch with your delivery "
                f"time. {track} here with any questions. Thank you!"
            )

        # Order item summary (once)
        try:
            order = await commerce.get_order(order_id)
            items = order.get("commerce_order_items") or []
            item_lines = "\n".join(
                f"  • {i.get('product_name','?')} x{i.get('quantity',1)}" for i in items[:10]
            ) or "  (see dashboard)"
        except Exception:
            item_lines = "  (see dashboard)"
        summary = (f"Order {display_id} — {amount}\n"
                   f"Customer: {customer_name or 'Unknown'} ({customer_phone or 'no phone'})\n{item_lines}")

        # Per-tenant workflow: require owner approval before fulfilment, or dispatch now.
        from vula.commerce.order_workflow import get_order_settings, dispatch_order
        cfg = get_order_settings(tenant_id)
        team = _tenant_team(tenant_id)

        if cfg.get("require_approval") and team:
            owners = [{"phone": p, "name": n, "role": r} for (n, p, r) in team if r in ("owner", "manager")] \
                     or [{"phone": p, "name": n, "role": r} for (n, p, r) in team]
            try:
                from vula.commerce.approvals import create_approval
                await create_approval(
                    tenant_id, "order", order_id, title=summary, approvers=owners,
                    deliver_via=cfg.get("dispatch_channel", "whatsapp"),
                    meta={"summary": summary, "customer_name": customer_name or "",
                          "display_id": display_id, "amount": amount},
                )  # WhatsApps each owner the summary + APPROVE/REJECT
            except Exception as exc:
                log.warning("order approval create failed: %s", exc)
                for name, phone, role in team:
                    await _send(phone, summary + "\n\nVula dashboard → Orders")
        else:
            for name, phone, role in team:
                await _send(phone, summary + "\n\nVula dashboard → Orders")
            try:
                await dispatch_order(tenant_id, order_id, summary, customer_name)
            except Exception as exc:
                log.warning("order dispatch failed: %s", exc)


async def _get_tenant_yoco_creds(tenant_id: str) -> Optional[dict]:
    """
    Get Yoco credentials for a tenant from Supabase, falling back to env vars.
    Returns: {'secret_key': str, 'webhook_secret': str, 'public_key': str, 'mode': str}
    """
    if tenant_id in _yoco_creds_cache:
        return _yoco_creds_cache[tenant_id]

    try:
        from supabase import create_client
        client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key or settings.supabase_service_key,
        )
        result = (
            client.table("vula_yoco_accounts")
            .select("secret_key,webhook_secret,public_key,mode,status")
            .eq("tenant_id", tenant_id)
            .eq("status", "connected")
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if rows and rows[0].get("secret_key"):
            from vula.email_imap.credentials import decrypt_secret
            creds = dict(rows[0])
            creds["secret_key"] = decrypt_secret(creds["secret_key"])
            if creds.get("webhook_secret"):
                creds["webhook_secret"] = decrypt_secret(creds["webhook_secret"])
            _yoco_creds_cache[tenant_id] = creds
            return creds
    except Exception as exc:
        log.debug("Supabase Yoco creds lookup failed for %s: %s", tenant_id, exc)

    # Fallback to env vars
    if settings.yoco_secret_key:
        return {
            "secret_key": settings.yoco_secret_key,
            "webhook_secret": settings.yoco_webhook_secret,
            "public_key": settings.yoco_public_key,
            "mode": "live",
        }
    return None


async def refund_yoco_payment(tenant_id: str, checkout_id: str, amount_cents: int,
                              idempotency_key: Optional[str] = None) -> dict:
    """Issue a real refund via Yoco's Checkout API (POST /api/checkouts/{id}/refund).

    A 200/202 means Yoco *accepted* the refund request — the money movement itself is
    confirmed asynchronously via a refund.succeeded/refund.failed webhook (best-effort logged
    below), same "trust the synchronous accept, treat the webhook as secondary confirmation"
    pattern this file already uses for checkout creation. Returns {'ok': True, 'refund_id'}
    or {'ok': False, 'detail': <reason>} — never raises, so callers can surface a clear
    message without a stack trace.
    """
    creds = await _get_tenant_yoco_creds(tenant_id)
    if not creds or not creds.get("secret_key"):
        return {"ok": False, "detail": "No Yoco account connected for this tenant."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://payments.yoco.com/api/checkouts/{checkout_id}/refund",
                headers={
                    "Authorization": f"Bearer {creds['secret_key']}",
                    "Content-Type": "application/json",
                    # Stable per refund target so a double-click / retry can't refund twice;
                    # a fresh uuid per call made every retry a brand-new refund.
                    "Idempotency-Key": idempotency_key or f"refund-{checkout_id}-{int(amount_cents)}",
                },
                json={"amount": int(amount_cents)},
            )
    except Exception as exc:
        log.error("Yoco refund request failed for checkout %s: %s", checkout_id, exc)
        return {"ok": False, "detail": f"Could not reach Yoco: {exc}"}
    if resp.status_code not in (200, 202):
        detail = "Refund rejected by Yoco."
        try:
            detail = resp.json().get("message") or detail
        except Exception:
            pass
        log.error("Yoco refund rejected for checkout %s: %s %s", checkout_id, resp.status_code, resp.text[:300])
        return {"ok": False, "detail": detail}
    refund_id = None
    try:
        refund_id = (resp.json() or {}).get("id")
    except Exception:
        pass
    return {"ok": True, "refund_id": refund_id}


@router.post("/webhook")
async def yoco_webhook(request: Request) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("yoco-signature", "")

    # Try to identify the tenant from the payload metadata
    try:
        payload_preview: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    data = payload_preview.get("payload", payload_preview)
    metadata = data.get("metadata", {}) or {}
    tenant_id = metadata.get("tenant_id", "")

    # Verify HMAC using per-tenant webhook secret if we have it
    webhook_secret = None
    if tenant_id:
        creds = await _get_tenant_yoco_creds(tenant_id)
        if creds:
            webhook_secret = creds.get("webhook_secret")
    webhook_secret = webhook_secret or settings.yoco_webhook_secret

    if webhook_secret:
        if not _yoco_signature_ok(webhook_secret, raw_body, signature, request.headers):
            raise HTTPException(status_code=401, detail="Invalid Yoco webhook signature")
    elif not settings.debug:
        # Fail closed: with no secret configured anywhere, an unsigned POST here could mark any
        # order/invoice paid and trigger fulfilment. Only a local dev run may skip verification.
        log.error("Rejecting Yoco webhook: no webhook secret configured (set YOCO_WEBHOOK_SECRET)")
        raise HTTPException(status_code=403, detail="Webhook signature verification not configured")

    # payload_preview was already parsed above; reuse it
    payload = payload_preview
    event_type = payload.get("type", "")
    data = payload.get("payload", payload)  # Yoco wraps in 'payload' key
    metadata = data.get("metadata", {})

    # Refund confirmations — logged best-effort only. Yoco's refund webhook payload doesn't
    # reliably carry back the order/invoice metadata the way payment events do, so this is NOT
    # treated as authoritative: the refund is already recorded (refund_status='pending') at the
    # moment Vula's own refund call is accepted by Yoco (see refund_yoco_payment above). This
    # just gives visibility into the async outcome without depending on unverified correlation.
    if event_type in ("refund.succeeded", "refund.failed"):
        log.info("Yoco %s: %s", event_type, data)
        return {"received": True}

    order_id = metadata.get("order_id")
    # tenant_id already extracted above for webhook verification
    customer_phone = metadata.get("customer_phone")
    customer_name = metadata.get("customer_name", "")
    display_id = metadata.get("display_id", "")
    amount_cents = data.get("amount", 0)

    # Invoice pay-links carry invoice_id (not order_id) → mark the invoice paid.
    invoice_id = metadata.get("invoice_id")

    # Links made through vula.payments.create_pay_link (WhatsApp orders, invoice "Pay now")
    # carry only {reference, tenant_id}: reference is the invoice id or the order display_id.
    # Before this, those webhooks hit "missing order_id" and were never auto-marked paid.
    if tenant_id and not order_id and not invoice_id and metadata.get("reference"):
        order_id, invoice_id, found = await _resolve_reference(tenant_id, str(metadata["reference"]))
        if found:
            display_id = display_id or found.get("display_id") or ""
            customer_phone = customer_phone or found.get("customer_phone")
            customer_name = customer_name or found.get("customer_name") or ""

    if invoice_id and event_type in ("payment.succeeded", "checkout.completed"):
        try:
            inv = (commerce._client().table("commerce_invoices").select("status")
                   .eq("tenant_id", tenant_id).eq("id", invoice_id).limit(1).execute().data or [])
            if inv and inv[0].get("status") == "paid":
                log.info("Invoice %s already paid — duplicate Yoco event ignored", invoice_id)
                return {"received": True}
            # Routed through the shared service function (not a direct table write) so this
            # also fires the general-ledger posting hook — a direct write here would silently
            # skip journal posting for pay-link invoices.
            await commerce.update_invoice_status(tenant_id, invoice_id, "paid")
            log.info("Invoice %s paid via Yoco", metadata.get("invoice_number", invoice_id))
        except Exception as exc:
            log.warning("invoice mark-paid failed: %s", exc)
        return {"received": True}

    if not order_id:
        log.warning("Yoco webhook missing order_id in metadata")
        return {"received": True}

    current = await _order_status(order_id)

    if event_type in ("payment.succeeded", "checkout.completed"):
        # Idempotent: Yoco retries and sends both payment.succeeded and checkout.completed for
        # one payment. Only the first transition out of pending_payment notifies anyone —
        # repeats used to re-send the customer confirmation and re-create approvals/dispatch.
        if current is not None and current != "pending_payment":
            log.info("Order %s already %s — duplicate Yoco %s ignored", display_id or order_id, current, event_type)
            return {"received": True}
        await commerce.update_order_status(order_id, "paid")
        log.info("Order %s paid via Yoco", display_id)

        # Send WhatsApp confirmation to customer + team alerts
        await _notify_order_paid(
            tenant_id=tenant_id,
            display_id=display_id,
            order_id=order_id,
            customer_phone=customer_phone,
            customer_name=customer_name,
            amount_cents=amount_cents,
        )

        # Also fire n8n if configured (for advanced automation)
        n8n_base = settings.n8n_webhook_base
        if n8n_base:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{n8n_base}/yoco-payment-success",
                        json={
                            "order_id": order_id, "display_id": display_id,
                            "tenant_id": tenant_id, "customer_phone": customer_phone,
                            "customer_name": customer_name, "amount_cents": amount_cents,
                        },
                    )
            except Exception as exc:
                log.warning("n8n notification failed (non-fatal): %s", exc)

    elif event_type in ("payment.failed", "checkout.expired"):
        # Only an unpaid order is cancelled (a late "expired" for a checkout that was paid via
        # another link must not cancel a paid order), and the stock reserved for it at checkout
        # (migration 122) is released — it used to stay reserved forever.
        if current is not None and current != "pending_payment":
            log.info("Order %s is %s — Yoco %s ignored", display_id or order_id, current, event_type)
            return {"received": True}
        await commerce.update_order_status(order_id, "cancelled")
        try:
            await commerce.apply_order_stock(order_id, restore=True)
        except Exception as exc:
            log.warning("stock release after failed payment skipped for %s: %s", order_id, exc)
        log.info("Order %s payment failed/expired", display_id)

    return {"received": True}


def _yoco_signature_ok(secret: str, raw_body: bytes, legacy_sig: str, headers) -> bool:
    """Accept Yoco's signed webhooks in either form: the legacy hex HMAC-SHA256 of the body in
    `yoco-signature`, or the standard-webhooks form (`webhook-id`, `webhook-timestamp`,
    `webhook-signature: v1,<base64 HMAC-SHA256 of "id.timestamp.body">`, keyed with the
    base64 part of a `whsec_` secret)."""
    import base64
    if legacy_sig:
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(legacy_sig, expected):
            return True
    wid = headers.get("webhook-id") or ""
    wts = headers.get("webhook-timestamp") or ""
    wsig = headers.get("webhook-signature") or ""
    if not (wid and wts and wsig):
        return False
    try:
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except Exception:
        key = secret.encode()
    signed = f"{wid}.{wts}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in wsig.split():
        _, _, sig = part.partition(",")
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False


async def _order_status(order_id: str) -> Optional[str]:
    try:
        rows = (commerce._client().table("commerce_orders").select("status")
                .eq("id", order_id).limit(1).execute().data or [])
        return rows[0].get("status") if rows else None
    except Exception as exc:
        log.debug("order status lookup skipped for %s: %s", order_id, exc)
        return None


async def _resolve_reference(tenant_id: str, reference: str) -> tuple[Optional[str], Optional[str], dict]:
    """reference -> (order_id, invoice_id, row). Invoice pay-links use the invoice id, order
    pay-links the order display_id — both scoped to the tenant named in the signed payload."""
    db = commerce._client()
    try:
        inv = (db.table("commerce_invoices").select("id").eq("tenant_id", tenant_id)
               .eq("id", reference).limit(1).execute().data or [])
        if inv:
            return None, inv[0]["id"], {}
    except Exception:
        pass  # reference isn't a uuid — it's an order display_id
    try:
        rows = (db.table("commerce_orders").select("id,display_id,customer_phone,customer_name")
                .eq("tenant_id", tenant_id).eq("display_id", reference).limit(1).execute().data or [])
        if rows:
            return rows[0]["id"], None, rows[0]
    except Exception as exc:
        log.debug("Yoco reference lookup failed for %s: %s", reference, exc)
    return None, None, {}

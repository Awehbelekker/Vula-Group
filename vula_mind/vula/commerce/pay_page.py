"""
vula/commerce/pay_page.py — the tenant's own "pay this invoice" page and the gateway link behind it.

One public page per invoice, GET /v1/commerce/{tenant}/pay/{invoice_id}: the tenant's brand, the
amount owed read from the database, a card button (the tenant's connected gateway: Yoco, PayFast,
Ozow, Peach or iKhokha) and the EFT details the tenant set. It is also where the gateway sends the
customer back to afterwards — before this every tenant without a store URL was sent to Off the
Hook's website.

The gateway's signed webhook (vula/api/payments.py, vula/api/yoco.py) is what marks the invoice
paid; this page never does, and its "thank you" never claims the money has arrived.
"""
from __future__ import annotations

import html
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class NoGateway(Exception):
    """The tenant has no payment gateway connected — said out loud, never silently skipped."""


def page_url(tenant_id: str, invoice_id: str) -> str:
    from config import settings
    return f"{settings.public_base_url.rstrip('/')}/v1/commerce/{tenant_id}/pay/{invoice_id}"


async def has_gateway(tenant_id: str) -> bool:
    from vula import payments
    if payments.default_provider_row(tenant_id):
        return True
    try:     # legacy: a Yoco account connected before the providers table existed
        from vula.api.yoco import _get_tenant_yoco_creds
        yc = await _get_tenant_yoco_creds(tenant_id)
        return bool(yc and yc.get("secret_key"))
    except Exception:
        return False


async def gateway_link(tenant_id: str, inv: dict) -> Tuple[str, str]:
    """Create (and store on the invoice) a hosted card-payment link. Returns (url, provider).
    Raises NoGateway when nothing is connected, RuntimeError when the gateway refuses."""
    from config import settings
    from vula import payments
    from vula.commerce import service
    row = payments.default_provider_row(tenant_id)
    provider = row["provider"] if row else "yoco"
    back = page_url(tenant_id, inv["id"])
    notify_url = f"{settings.public_base_url.rstrip('/')}/v1/payments/webhook/{tenant_id}/{provider}"
    try:
        link = await payments.create_pay_link(
            tenant_id, amount_cents=owed_cents(inv), reference=inv["id"],
            description=f"Invoice {inv.get('invoice_number') or ''}".strip(),
            success_url=f"{back}?result=success", cancel_url=f"{back}?result=cancel",
            notify_url=notify_url,
            customer={"email": inv.get("customer_email"), "phone": inv.get("customer_phone")})
    except Exception as exc:
        logger.error("Pay-link create failed for %s (%s): %s", tenant_id, provider, exc)
        raise RuntimeError(f"the {provider} gateway refused the request — check its keys") from exc
    if not link or not link.url:
        logger.error("Pay link requested for %s but no payment gateway is connected", tenant_id)
        raise NoGateway("No payment gateway is connected — connect one under Settings › Payments.")
    try:
        (service._client().table("commerce_invoices")
         .update({"pay_url": link.url, "yoco_checkout_id": (link.raw or {}).get("id"),
                  "updated_at": service._now()})
         .eq("id", inv["id"]).eq("tenant_id", tenant_id).execute())
    except Exception as exc:
        logger.warning("pay_url not stored on %s: %s", inv.get("id"), exc)
    return link.url, link.provider


async def notify_paid(tenant_id: str, invoice_id: str, provider: str) -> int:
    """Tell the owner/admins on WhatsApp that a customer paid an invoice online (the gateway's
    signed webhook already marked it paid and posted it to the ledger). Figures from the DB;
    once per invoice (idem_key). Returns how many people were told."""
    from vula.api.whatsapp import _send_reply
    from vula.commerce import service
    from vula.commerce.approvals import tenant_admin_approvers
    inv = await service.get_invoice(tenant_id, invoice_id) or {}
    if inv.get("status") != "paid":
        return 0
    msg = (f"💰 *{inv.get('customer_name') or 'A customer'}* paid invoice "
           f"*{inv.get('invoice_number')}* — {_r(int(inv.get('total_cents') or 0))} by "
           f"{provider.title()}. It's marked paid and booked.")
    sent = 0
    for a in await tenant_admin_approvers(tenant_id):
        try:
            if await _send_reply(a["phone"], msg, tenant_id,
                                 idem_key=f"invoice_paid:{invoice_id}") is not False:
                sent += 1
        except Exception as exc:
            logger.warning("paid notification to %s failed: %s", a.get("phone"), exc)
    return sent


def eft_details(tenant_id: str) -> Optional[str]:
    """The tenant's EFT block: the order-settings text if set, else the bank fields saved with
    the invoice settings (the same ones printed on the PDF)."""
    try:
        from vula.commerce.order_workflow import get_order_settings
        text = ((get_order_settings(tenant_id) or {}).get("eft_details") or "").strip()
        if text:
            return text
        from vula.commerce import service
        from vula.commerce.pdf import _payment_info_from_settings
        rows = (service._client().table("commerce_invoice_settings")
                .select("bank_name,account_name,account_number,branch_code")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        info = _payment_info_from_settings(rows[0]) if rows else ""
        return info.replace("EFT Payment:\n", "").replace("\nPlease use your invoice number as reference.", "") or None
    except Exception as exc:
        logger.debug("EFT details lookup failed for %s: %s", tenant_id, exc)
        return None


def owed_cents(inv: dict) -> int:
    return max(0, int(inv.get("total_cents") or 0) - int(inv.get("total_paid_cents") or 0))


def _r(cents: int) -> str:
    return f"R{cents / 100:,.2f}".replace(",", " ")


def render(brand: dict, inv: dict, eft_details: Optional[str], card_url: Optional[str],
           result: str = "") -> str:
    """The page. Every value is escaped; every figure comes from the invoice row."""
    e = html.escape
    name = e(brand.get("name") or "")
    accent = e(brand.get("accent_color") or "#2C5545")
    logo = brand.get("logo_url")
    number = e(inv.get("invoice_number") or "")
    owed = owed_cents(inv)
    paid = inv.get("status") == "paid" or owed == 0
    if paid:
        body = "<p class='big'>✅ Paid — thank you.</p>"
    elif result == "success":
        body = ("<p class='big'>Thank you.</p><p>Your payment is being confirmed by the bank. "
                f"{name} will let you know once it has cleared.</p>")
    else:
        body = (f"<p class='muted'>Amount due</p><p class='big'>{_r(owed)}</p>"
                + ("<p class='muted'>Payment was cancelled — you can try again.</p>"
                   if result == "cancel" else ""))
        if card_url:
            body += f"<a class='btn' href='{e(card_url)}'>💳 Pay {_r(owed)} by card</a>"
        if eft_details:
            body += ("<div class='eft'><h3>🏦 Or pay by EFT</h3>"
                     f"<pre>{e(eft_details)}</pre>"
                     f"<p>Use <b>{number}</b> as the reference and send the proof of payment on "
                     "WhatsApp.</p></div>")
        if not card_url and not eft_details:
            body += f"<p>Please contact {name} for payment details.</p>"
    logo_html = f"<img src='{e(logo)}' alt='{name}' class='logo'>" if logo else f"<h2>{name}</h2>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Invoice {number} · {name}</title><style>
body{{margin:0;background:#f5f3ee;font-family:system-ui,-apple-system,sans-serif;color:#2a2a2a}}
.card{{max-width:440px;margin:32px auto;background:#fff;border-radius:14px;padding:28px 24px;
box-shadow:0 2px 10px rgba(0,0,0,.06)}}
.logo{{max-height:56px;max-width:200px}}.muted{{color:#6e6a63;margin:16px 0 4px}}
.big{{font-size:30px;font-weight:700;margin:0 0 18px}}
.btn{{display:block;text-align:center;background:{accent};color:#fff;text-decoration:none;
padding:14px;border-radius:10px;font-weight:700}}
.eft{{margin-top:22px;border-top:1px solid #e5e1d8;padding-top:14px}}
pre{{white-space:pre-wrap;font-family:inherit;background:#f5f3ee;padding:10px;border-radius:8px}}
</style></head><body><div class="card">{logo_html}
<p class="muted">Invoice {number}</p>{body}</div></body></html>"""

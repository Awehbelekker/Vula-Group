"""Production adapters: WhatsApp messenger (reuses the existing Meta senders) and PayFast gateway.

PayFast specifics here are UNVERIFIED against current PayFast docs (the docs host was unreachable
from the build environment): the server-side ITN confirmation endpoint and the fee sign convention.
Both are isolated in this file, fail CLOSED, and must be confirmed in the PayFast sandbox before the
pilot (see docs/tap-to-pay.md)."""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from vula import payments
from vula.tap.service import REF_PREFIX

logger = logging.getLogger(__name__)


class WhatsAppMessenger:
    async def text(self, tenant_id: str, phone: str, body: str) -> bool:
        from vula.api.whatsapp import _send_reply
        return await _send_reply(phone, body, tenant_id)

    async def buttons(self, tenant_id: str, phone: str, body: str, buttons: list[dict]) -> bool:
        from vula.api import whatsapp as wa
        creds = await wa._get_tenant_wa_creds(tenant_id)
        if creds and await wa._send_wa_buttons(creds, wa._wa_number(phone), body, buttons):
            return True
        return await wa._send_reply(phone, body, tenant_id)       # plain-text fallback

    async def list(self, tenant_id: str, phone: str, header: str, body: str, button: str,
                   rows: list[dict]) -> bool:
        from vula.api import whatsapp as wa
        creds = await wa._get_tenant_wa_creds(tenant_id)
        trimmed = [{"id": r["id"], "title": r["title"][:24],
                    **({"description": r["description"][:72]} if r.get("description") else {})}
                   for r in rows[:10]]
        if creds and await wa._send_wa_list(creds, wa._wa_number(phone), header, body, "",
                                            button, [{"title": "Options", "rows": trimmed}]):
            return True
        return await wa._send_reply(phone, body + "\n" + "\n".join(r["title"] for r in trimmed), tenant_id)


    async def template(self, tenant_id: str, phone: str, name: str, params: list[str]) -> bool:
        from vula.api.whatsapp import _send_wa_template
        return await _send_wa_template(tenant_id, phone, name, *params)

    async def document(self, tenant_id: str, phone: str, data: bytes, filename: str, caption: str) -> bool:
        from vula.api.whatsapp import _send_invoice_document
        return await _send_invoice_document(phone, data, filename, caption, tenant_id)


def _payfast_row(tenant_id: str) -> Optional[dict]:
    rows = (payments._client().table("vula_payment_providers").select("credentials,mode,active")
            .eq("tenant_id", tenant_id).eq("provider", "payfast").limit(1).execute().data or [])
    if not rows or rows[0].get("active") is False:
        return None
    return {"creds": payments._decrypt_creds(rows[0].get("credentials")), "mode": rows[0].get("mode", "live")}


class PayFastGateway:
    def __init__(self, public_base_url: str, server_validate: bool = True):
        self.base = public_base_url.rstrip("/")
        self.server_validate = server_validate

    async def create_checkout(self, *, tenant_id: str, session: dict, description: str) -> Optional[str]:
        row = _payfast_row(tenant_id)
        if not row or not row["creds"].get("merchant_id"):
            return None
        total = int(session["bill_cents"]) + int(session["tip_cents"])
        link = await payments.PayFast().create_link(
            row["creds"], amount_cents=total, reference=REF_PREFIX + session["id"],
            description=description, success_url=f"{self.base}/v1/tap/done/{session['id']}",
            cancel_url=f"{self.base}/v1/tap/cancelled/{session['id']}",
            notify_url=f"{self.base}/v1/payments/webhook/{tenant_id}/payfast", customer={}, mode=row["mode"])
        return link.url

    async def verify_itn(self, *, tenant_id: str, headers: dict, body: bytes, form: dict) -> Optional[dict[str, Any]]:
        row = _payfast_row(tenant_id)
        if not row:
            return None
        res = await payments.PayFast().verify_webhook(row["creds"], headers, body, form)
        if not res:
            return None                                    # signature failed (logged by PayFast.verify_webhook)
        if res.get("paid") and self.server_validate and not await self._server_confirms(row["mode"], body, form):
            logger.warning("PayFast ITN failed server-side validation for %s", res.get("reference"))
            return None
        fee = payments._rands_to_cents(form.get("amount_fee"))
        return {"reference": res["reference"], "paid": res["paid"], "amount_cents": res["amount_cents"],
                "pf_payment_id": form.get("pf_payment_id"), "fee_cents": abs(fee) if fee else 0,
                "method": form.get("payment_method")}

    async def _server_confirms(self, mode: str, body: bytes, form: dict) -> bool:
        """PayFast ITN step: POST the notification back to PayFast and require 'VALID'. Fails closed."""
        host = "sandbox.payfast.co.za" if mode == "test" else "www.payfast.co.za"
        payload = urlencode([(k, v) for k, v in form.items() if k != "signature"])
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(f"https://{host}/eng/query/validate", content=payload,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
            return r.status_code == 200 and r.text.strip().upper().startswith("VALID")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PayFast server validation unreachable: %s", exc)
            return False

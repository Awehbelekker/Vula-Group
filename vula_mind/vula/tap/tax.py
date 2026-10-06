"""Tax invoice on request: a customer who has paid asks (WhatsApp "TAX", or the receipt page) and gets a
VAT tax invoice PDF made by the existing invoice renderer.

Rules (see core/taxinvoice.py): VAT-registered merchants only (VAT number + registered address on file);
one immutable invoice per payment (asking again returns the same one); sequential per-tenant numbers;
supplier + buyer details are snapshotted at issue; the invoice covers the BILL, never the tip.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

from vula.tap.core import money as mo
from vula.tap.core import taxinvoice as ti

if TYPE_CHECKING:
    from vula.tap.service import TapService

logger = logging.getLogger(__name__)

_TAX_RE = re.compile(r"^\s*(?:vat\s+|tax\s+)?(?:tax(?:\s+invoice)?|invoice)\b(.*)$", re.IGNORECASE | re.DOTALL)
_CANCEL_RE = re.compile(r"^\s*(cancel|skip|no|nope|never\s*mind|no thanks?)\s*[.!]?\s*$", re.IGNORECASE)
REQUEST_TTL = timedelta(minutes=30)
WINDOW = timedelta(days=30)               # how long after paying a customer can ask by WhatsApp

MSG_ASK = "Send your company name and VAT number for a tax invoice."
MSG_BAD_VAT = ("I couldn't find a valid VAT number (10 digits starting with 4). "
               "Send your company name and VAT number, for example: Acme Trading (Pty) Ltd 4123456789")
MSG_NO_NAME = "I also need your company name. Send it with your VAT number in one message."
MSG_CANCELLED = "No problem - no tax invoice will be made."


class TaxError(Exception):
    """Something the customer can fix or must be told; the message is shown to them as is."""


def _wants_tax(text: str) -> Optional[str]:
    """The text after the keyword when this message is a tax-invoice request, else None."""
    if not text or len(text) > 200:
        return None
    m = _TAX_RE.match(text)
    return m.group(1) if m else None


class TaxDesk:
    def __init__(self, svc: "TapService"):
        self.svc = svc

    # ── merchant side ────────────────────────────────────────────────────────────────────────
    def supplier(self, tenant_id: str) -> Optional[dict]:
        """Supplier identity for a tax invoice, or None when the merchant can't issue one
        (not VAT registered, or VAT number / registered address missing)."""
        s = self.svc.repo.invoice_settings(tenant_id) or {}
        vat, addr = (s.get("vat_number") or "").strip(), (s.get("registered_address") or "").strip()
        if not s.get("vat_registered", False) or not vat or not addr:
            return None
        return {"name": s.get("company_name") or self.svc.repo.merchant_name(tenant_id),
                "trading_as": s.get("trading_as") or "", "vat": vat, "address": addr,
                "reg": s.get("company_reg") or "", "email": s.get("company_email") or "",
                "phone": s.get("company_phone") or ""}

    def can_issue(self, tenant_id: str) -> bool:
        return self.supplier(tenant_id) is not None

    # ── issuing ──────────────────────────────────────────────────────────────────────────────
    def issue(self, payment_id: str, company: str, vat: str, address: str = "") -> tuple[dict, bool]:
        """(invoice row, created). Idempotent per payment; raises TaxError for anything fixable."""
        repo = self.svc.repo
        src = repo.receipt_source(payment_id)
        if not src or src.get("revoked_at") or src.get("is_test"):
            raise TaxError("We can't find that payment.")
        tenant = src["tenant_id"]
        existing = repo.get_tax_invoice(tenant, payment_id)
        if existing:
            return existing, False
        supplier = self.supplier(tenant)
        if supplier is None:
            raise TaxError(f"{repo.merchant_name(tenant)} can't issue tax invoices through this chat. "
                           "Please ask them directly.")
        name = ti.clean_text(company, 120)
        vat_no = ti.normalise_vat(vat)
        if len(name) < 2:
            raise TaxError(MSG_NO_NAME)
        if not vat_no:
            raise TaxError(MSG_BAD_VAT)
        bill = int(src["bill_cents"])
        if bill <= 0:
            raise TaxError("This payment was a tip only, so there is nothing to invoice.")
        _, vat_cents = ti.vat_split(bill)
        for _attempt in range(5):                      # another request may take our number first
            row = {"tenant_id": tenant, "payment_id": payment_id, "number": repo.max_tax_number(tenant) + 1,
                   "buyer_name": name, "buyer_vat": vat_no, "buyer_address": ti.clean_text(address, 200) or None,
                   "bill_cents": bill, "vat_cents": vat_cents, "supplier": supplier,
                   "description": src.get("description") or "Payment"}
            made = repo.insert_tax_invoice(row)
            if made:
                logger.info("tax invoice %s issued tenant=%s", ti.format_number(made["number"]), tenant)
                return made, True
            existing = repo.get_tax_invoice(tenant, payment_id)
            if existing:
                return existing, False
        raise TaxError("We couldn't make your invoice just now. Please try again in a minute.")

    def invoice_dict(self, inv: dict, paid_at: str, tip_cents: int, ref: str) -> dict:
        excl, vat = inv["bill_cents"] - inv["vat_cents"], inv["vat_cents"]
        note = f"Paid in full by card/online via PayFast. Payment ref {ref}."
        if tip_cents:
            note += (f" A gratuity of {mo.format_rands(tip_cents)} was paid separately to the person who served "
                     "you; it is not included on this invoice.")
        return {"tenant_id": inv["tenant_id"], "doc_type": "invoice", "direction": "outbound", "status": "paid",
                "invoice_number": ti.format_number(inv["number"]), "issue_date": str(inv["issued_at"])[:10],
                "customer_name": inv["buyer_name"], "customer_vat": inv["buyer_vat"],
                "customer_address": inv.get("buyer_address") or "",
                "line_items": [{"description": inv.get("description") or "Payment", "quantity": 1,
                                "unit_price_cents": excl, "total_cents": excl}],
                "subtotal_cents": excl, "vat_rate": float(ti.VAT_RATE_PCT), "vat_cents": vat,
                "total_cents": inv["bill_cents"], "notes": note}

    def render_pdf(self, inv: dict) -> bytes:
        from vula.commerce.pdf import merge_branding, render_invoice_pdf
        repo = self.svc.repo
        src = repo.receipt_source(inv["payment_id"]) or {}
        sup = inv["supplier"]
        branding = merge_branding(inv["tenant_id"], repo.invoice_settings(inv["tenant_id"]))
        branding.update({"name": sup["name"], "trading_as": sup.get("trading_as", ""), "vat": sup["vat"],
                         "address": sup["address"], "reg": sup.get("reg", ""), "email": sup.get("email", ""),
                         "phone": sup.get("phone", ""), "vat_registered": True})     # issued details never drift
        return render_invoice_pdf(self.invoice_dict(inv, src.get("paid_at", ""), int(src.get("tip_cents") or 0),
                                                    str(inv["payment_id"])[:8].upper()), branding)

    # ── WhatsApp ─────────────────────────────────────────────────────────────────────────────
    async def handle_text(self, tenant_id: str, phone: str, text: str) -> bool:
        """True when the message belonged to the tax-invoice conversation."""
        from vula.tap.service import _ts
        svc, repo = self.svc, self.svc.repo
        payer = svc.phone_hash(phone)
        pending = repo.open_tax_request(tenant_id, payer, svc.now().isoformat())
        rest = _wants_tax(text)
        if pending and rest is None:
            if _CANCEL_RE.match(text or ""):
                repo.close_tax_request(pending["id"], "cancelled")
                await svc._say(tenant_id, phone, MSG_CANCELLED)
                return True
            name, vat = ti.parse_details(text)
            if not vat and (len(text or "") > 80 or "?" in (text or "")):
                return False                              # clearly something else; leave the request open
            await self._finish(tenant_id, phone, pending["payment_id"], name, vat, pending)
            return True
        if rest is None:
            return False
        pay_id = repo.latest_paid_payment(tenant_id, payer)
        src = repo.receipt_source(pay_id) if pay_id else None
        if not src or (svc.now() - _ts(src["paid_at"])) > WINDOW:
            return False                                  # not a customer of ours: normal routing applies
        if repo.get_tax_invoice(tenant_id, pay_id):
            await self._send(tenant_id, phone, repo.get_tax_invoice(tenant_id, pay_id), again=True)
            return True
        if not self.can_issue(tenant_id):
            await svc._say(tenant_id, phone, f"{repo.merchant_name(tenant_id)} can't issue tax invoices through "
                                             "this chat. Please ask them directly.")
            return True
        name, vat = ti.parse_details(rest)
        if name and vat:
            await self._finish(tenant_id, phone, pay_id, name, vat, None)
            return True
        repo.put_tax_request({"tenant_id": tenant_id, "payer_hash": payer, "payment_id": pay_id,
                              "expires_at": (svc.now() + REQUEST_TTL).isoformat()})
        await svc._say(tenant_id, phone, MSG_ASK)
        return True

    async def _finish(self, tenant_id, phone, payment_id, name, vat, pending) -> None:
        svc = self.svc
        try:
            inv, _ = self.issue(payment_id, name or "", vat or "")
        except TaxError as exc:
            await svc._say(tenant_id, phone, str(exc))     # request stays open so they can correct it
            return
        if pending:
            svc.repo.close_tax_request(pending["id"], "done")
        await self._send(tenant_id, phone, inv, again=False)

    async def _send(self, tenant_id: str, phone: str, inv: dict, again: bool) -> None:
        import asyncio
        svc = self.svc
        num = ti.format_number(inv["number"])
        try:
            pdf = await asyncio.to_thread(self.render_pdf, inv)
        except Exception as exc:  # noqa: BLE001
            logger.error("tax invoice render failed %s: %s", num, exc)
            await svc._say(tenant_id, phone, "We couldn't make the PDF just now. Reply TAX to try again.")
            return
        cap = f"Tax invoice {num}" + (" (again)" if again else "") + f" from {svc.repo.merchant_name(tenant_id)}."
        ok = await svc.messenger.document(tenant_id, phone, pdf, f"Tax-Invoice-{num}.pdf", cap)
        if not ok:
            await svc._say(tenant_id, phone, f"Your tax invoice {num} is ready but couldn't be sent here. "
                                             "Reply TAX to try again.")

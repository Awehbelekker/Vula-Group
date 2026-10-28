"""Express pay: tap the tag, pay on one web page (28 Oct 2026).

The WhatsApp flow (tap -> WhatsApp opens -> press Send -> wait for Vula -> pick a tip -> press Pay ->
PayFast) is five steps before the card page, too slow at a bar counter. Express pay is:

    tap -> one page (amount, tip buttons, Pay) -> PayFast (card or phone wallet) -> done

with the slip on WhatsApp as an optional last step (a button on the done page sends "SLIP <ref>").

Who is paying: the web flow has no phone number, so the payer is a random id kept in a browser
cookie (`kb_web`), hashed with the pepper in a separate namespace from phone hashes. The same
browser tapping again (after a cancelled card page, say) is the same payer and picks its own claim
back up; any other phone is locked out exactly as in the WhatsApp flow.

Everything that matters is reused from TapService: the claim is the same compare-and-set on the
bill, the session goes through the same states, the PayFast notification (confirm_payment) is the
only thing that marks anything paid, and the ledger, staff alert and test-payment rules are
unchanged. A web session the customer walks away from goes back to open (there is no number to
send reminders to), instead of becoming an abandoned bill.

Bills sent to a customer's number (bill code / several bills) still need the WhatsApp flow, which
proves the number: the page hands those over to it.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from urllib.parse import quote

from vula.tap.core import matching as mt
from vula.tap.core import money as mo
from vula.tap.core import states as st

COOKIE = "kb_web"
PAGE_TTL = timedelta(minutes=10)            # how long the page's one-time token stays valid
_SLIP_RE = re.compile(r"^\s*slip\s+([0-9A-Za-z-]{1,64})\.([0-9a-f]{16})\s*$", re.IGNORECASE)


class ExpressError(ValueError):
    """Shown to the customer on the page as is."""


@dataclass
class ExpressPay:
    svc: "object"                           # TapService (typed loosely to avoid an import cycle)

    # ── identity ─────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def new_cookie() -> str:
        return secrets.token_urlsafe(18)

    def payer(self, cookie: str) -> str:
        return hmac.new(self.svc.cfg.pepper.encode(), f"web:{cookie}".encode(), hashlib.sha256).hexdigest()

    def _slip_mac(self, session_id: str, payer_hash: str) -> str:
        # bound to the paying browser's payer hash, not just the session id: knowing a payment's id
        # is not enough to have its slip sent to another number (security review, 28 Oct)
        return hmac.new(self.svc.cfg.pepper.encode(), f"slip:{session_id}:{payer_hash}".encode(),
                        hashlib.sha256).hexdigest()[:16]

    # ── the page ─────────────────────────────────────────────────────────────────────────────
    def view(self, code: str, cookie: str) -> Optional[dict]:
        """What the tap page shows. None = tag not verified. kind is one of: bill, quick_tip,
        open_amount, whatsapp (needs the number-proving flow), locked, no_bill."""
        svc = self.svc
        tag = svc.repo.get_tag_by_code(code)
        if not tag or tag.get("status") != "active" or not svc.cfg.enabled(tag["tenant_id"]):
            return None
        tenant = tag["tenant_id"]
        d = self._decide(tenant, tag, self.payer(cookie))
        out = {"tenant_id": tenant, "merchant": svc.repo.merchant_name(tenant),
               "staff": svc.repo.staff_name(tenant, tag.get("bound_id")), "kind": _KIND.get(d.kind, "no_bill"),
               "tip_presets": [(bp // 100) for bp in svc.cfg.tip_presets_bp],
               "quick_presets": list(svc.cfg.quick_tip_presets_cents[:3])}
        if d.kind == mt.CLAIM:
            bill = svc.repo.get_bill(tenant, d.bill_id) or {}
            out.update(bill_cents=int(bill.get("subtotal_cents") or 0),
                       description=bill.get("description") or "", is_test=bool(bill.get("is_test")),
                       staff=svc.repo.staff_name(tenant, bill.get("staff_id")) or out["staff"])
        if out["kind"] in ("bill", "quick_tip", "open_amount"):
            token = secrets.token_urlsafe(16)
            svc.repo.create_claim_token(tenant_id=tenant, tag_id=tag["id"], token_hash=svc._token_hash(token),
                                        expires_at=svc.now() + PAGE_TTL)
            out["token"] = token
        return out

    def _decide(self, tenant: str, tag: dict, payer: str) -> mt.Decision:
        svc = self.svc
        bills = [mt.BillView(b["id"], b["status"], b.get("customer_hash"), b.get("claimed_by_hash"))
                 for b in svc.repo.live_bills_for_tag(tenant, tag["id"])]
        return mt.resolve_tap(tag_mode=tag.get("mode", "appointment"), payer_hash=payer, bills=bills,
                              tag_allows_open_amount=bool(tag.get("allow_open_amount")),
                              code_locked=svc._code_locked(tenant, bills, payer))

    # ── Pay ──────────────────────────────────────────────────────────────────────────────────
    async def checkout(self, code: str, token: str, cookie: str, *, tip: str = "0",
                       custom_tip: str = "", amount: str = "") -> str:
        """Claim, price and hand over to PayFast. Returns the checkout URL; raises ExpressError."""
        svc = self.svc
        tag = svc.repo.get_tag_by_code(code)
        if not tag or tag.get("status") != "active" or not svc.cfg.enabled(tag["tenant_id"]):
            raise ExpressError("We couldn't verify this tag. Ask staff to take payment another way.")
        tenant = tag["tenant_id"]
        row = svc.repo.consume_claim_token(svc._token_hash(token or ""), svc.now())
        if not row or row["tenant_id"] != tenant or row.get("tag_id") != tag["id"]:
            raise ExpressError("This page has expired. Tap the tag again.")
        payer = self.payer(cookie)
        d = self._decide(tenant, tag, payer)
        if d.kind == mt.CLAIM:
            s = self._claim(tenant, d.bill_id, payer)
            s = self._set_tip(s, tip, custom_tip)
        elif d.kind == mt.QUICK_TIP:
            cents = self._money(mo.validate_quick_tip, amount)
            s = self._session(tenant, None, payer, 0, staff_ref=tag.get("bound_id"))
            s = self._advance(s, "awaiting_tip", "amount_set")
            s = self._advance(s, "awaiting_confirm", "tip_chosen", tip_cents=cents)
        elif d.kind == mt.ASK_AMOUNT:
            cents = self._money(mo.validate_open_amount, amount)
            s = self._session(tenant, None, payer, cents, staff_ref=tag.get("bound_id"))
            s = self._advance(s, "awaiting_tip", "amount_set")
            s = self._set_tip(s, tip, custom_tip)
        elif d.kind == mt.LOCKED:
            raise ExpressError(svc_msg("MSG_LOCKED"))
        else:
            raise ExpressError(svc_msg("MSG_NO_BILL"))
        s = self._advance(s, "awaiting_payment", "pay_now", checkout_ref=f"kb-{s['id']}")
        bill = svc.repo.get_bill(tenant, s["bill_id"]) if s.get("bill_id") else None
        url = await svc.gateway.create_checkout(
            tenant_id=tenant, session=s,
            description=f"{svc.repo.merchant_name(tenant)} - {(bill or {}).get('description') or 'Payment'}")
        if not url:
            self._drop(s)
            raise ExpressError("Card payments aren't set up here yet. Pay staff another way.")
        return url

    def _claim(self, tenant: str, bill_id: str, payer: str) -> dict:
        svc = self.svc
        bill = svc.repo.get_bill(tenant, bill_id)
        if not bill:
            raise ExpressError(svc_msg("MSG_NO_BILL"))
        if bill["status"] == "claimed":
            if bill.get("claimed_by_hash") != payer:
                raise ExpressError(svc_msg("MSG_LOCKED"))
            svc.repo.cancel_other_sessions(tenant, bill_id, None)     # same browser, fresh attempt
        elif not svc.repo.cas_bill(tenant, bill_id, expected="open", new="claimed",
                                   fields={"claimed_by_hash": payer}):
            raise ExpressError(svc_msg("MSG_LOCKED"))
        s = self._session(tenant, bill_id, payer, int(bill["subtotal_cents"]))
        return self._advance(s, "awaiting_tip", "amount_set")

    def _session(self, tenant: str, bill_id: Optional[str], payer: str, bill_cents: int,
                 staff_ref: Optional[str] = None) -> dict:
        svc = self.svc
        s = svc.repo.create_session({
            "tenant_id": tenant, "bill_id": bill_id, "payer_hash": payer, "payer_enc": None,
            "state": "claimed", "bill_cents": bill_cents, "tip_cents": 0,
            "idempotency_key": f"{bill_id or 'web'}:{payer}:{secrets.token_hex(6)}",
            "expires_at": (svc.now() + svc.cfg.session_ttl).isoformat(), "expecting": None})
        if staff_ref:
            svc.repo.cas_session(tenant, s["id"], expected="claimed", new="claimed", fields={"staff_ref": staff_ref})
            s["staff_ref"] = staff_ref
        return s

    def _advance(self, s: dict, new: str, event: str, **fields) -> dict:
        if st.session_next(s["state"], event) != new:          # raises on an illegal event too
            raise ExpressError("Something changed while you were paying. Tap the tag again.")
        if not self.svc.repo.cas_session(s["tenant_id"], s["id"], expected=s["state"], new=new, fields=fields):
            raise ExpressError("Something changed while you were paying. Tap the tag again.")
        return {**s, **fields, "state": new}

    def _set_tip(self, s: dict, choice: str, custom: str) -> dict:
        cfg = self.svc.cfg
        if (choice or "0") == "custom":
            tip = self._money(lambda t: mo.validate_custom_tip(t, s["bill_cents"], max_pct_bp=cfg.custom_tip_cap_bp),
                              custom, s)
        else:
            try:
                pct = int(choice or "0")
            except ValueError:
                pct = -1
            if pct != 0 and pct * 100 not in cfg.tip_presets_bp:
                self._drop(s)
                raise ExpressError("Choose one of the tips shown.")
            tip = mo.pct_tip(s["bill_cents"], pct * 100)
        return self._advance(s, "awaiting_confirm", "tip_chosen", tip_cents=tip)

    def _money(self, validate, text: str, s: Optional[dict] = None) -> int:
        try:
            return validate(text or "")
        except mo.MoneyError:
            if s:
                self._drop(s)
            raise ExpressError("That amount doesn't look right. Type it in rands, for example 15.") from None

    def _drop(self, s: dict) -> None:
        """Abandon this attempt before the card page: the bill goes back to open."""
        if self.svc.repo.cas_session(s["tenant_id"], s["id"], expected=s["state"],
                                     new=st.session_next(s["state"], "cancel")):
            self.svc._ended(s, "cancelled")

    # ── after the card page ──────────────────────────────────────────────────────────────────
    def cancel(self, session_id: str, cookie: str) -> bool:
        """The customer backed out on PayFast's page: free the bill now (not in 10 minutes) so the
        next tap works. Only the browser that started it can do this."""
        s = self.svc.repo.get_session_any_tenant(session_id)
        if (not s or s.get("payer_enc") or s["state"] != "awaiting_payment"
                or not cookie or not hmac.compare_digest(s["payer_hash"], self.payer(cookie))):
            return False
        if self.svc.repo.cas_session(s["tenant_id"], s["id"], expected="awaiting_payment",
                                     new=st.session_next("awaiting_payment", "cancel")):
            self.svc._ended(s, "cancelled")
            return True
        return False

    def slip_link(self, session_id: str, cookie: str) -> Optional[str]:
        """wa.me link that asks Vula for this payment's slip, or None (unknown, not a web payment,
        or not the browser that paid)."""
        s = self.svc.repo.get_session_any_tenant(session_id)
        if (not s or s.get("payer_enc") or not cookie
                or not hmac.compare_digest(s["payer_hash"], self.payer(cookie))):
            return None
        number = re.sub(r"\D", "", self.svc.repo.tenant_wa_number(s["tenant_id"]) or "")
        if not number:
            return None
        mac = self._slip_mac(session_id, s["payer_hash"])
        return f"https://wa.me/{number}?text={quote(f'SLIP {session_id}.{mac}')}"

    async def claim_slip(self, tenant_id: str, phone: str, text: str) -> bool:
        """WhatsApp "SLIP <ref>": link the number to the web payment and send its slip (now if it is
        paid, else when PayFast confirms it). True if the message was a slip request."""
        m = _SLIP_RE.match(text or "")
        if not m:
            return False
        svc = self.svc
        sid, mac = m.group(1), m.group(2)
        s = svc.repo.get_session(tenant_id, sid)
        mine = bool(s) and bool(s.get("payer_enc")) and s["payer_hash"] == svc.phone_hash(phone)
        if not s or (not mine and (s.get("payer_enc")             # linked to another number already
                                   or not hmac.compare_digest(mac, self._slip_mac(sid, s["payer_hash"])))):
            await svc._say(tenant_id, phone, "We couldn't find that payment. Ask staff for help.")
            return True
        if not mine:
            svc.repo.cas_session(tenant_id, sid, expected=s["state"], new=s["state"], fields={
                "payer_enc": svc.cfg.encrypt(re.sub(r"\D", "", phone)), "payer_hash": svc.phone_hash(phone)})
        if s["state"] == "paid":
            pay = svc.repo.payment_for_session(tenant_id, sid)
            bill = svc.repo.get_bill(tenant_id, s["bill_id"]) if s.get("bill_id") else None
            if pay:
                await svc._send_slip(tenant_id, s, pay, bool((bill or {}).get("is_test")), phone)
                return True
        await svc._say(tenant_id, phone, "Thanks. Your slip will arrive here as soon as the payment "
                                         "is confirmed.")
        return True


_KIND = {mt.CLAIM: "bill", mt.QUICK_TIP: "quick_tip", mt.ASK_AMOUNT: "open_amount", mt.LOCKED: "locked",
         mt.CHOOSE: "whatsapp", mt.CODE_NEEDED: "whatsapp", mt.CODE_LOCKED: "whatsapp",
         mt.NO_OPEN_BILL: "no_bill"}


def svc_msg(name: str) -> str:
    from vula.tap import service
    return getattr(service, name)

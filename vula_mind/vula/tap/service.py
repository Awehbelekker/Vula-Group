"""Tap-to-pay orchestration. All rules come from `vula.tap.core`; this module sequences them and
talks to the outside world only through `ports` (DB, WhatsApp, gateway), so it is fully testable.

Conversation, per customer phone:
  tap -> "PAY <token>" -> match bill -> tip list -> confirm buttons -> one-time pay link -> PayFast
  ITN -> ledger + slip + staff alert.

Security notes (see tests/test_tap_service.py):
  * claim tokens are random, stored only as SHA-256, single-use, ~2 minutes;
  * every interactive reply/typed message is checked against the session's payer hash;
  * a payment is only marked paid from a VERIFIED gateway notification whose amount equals the
    session total; the browser redirect is never trusted;
  * gateway events are de-duplicated before any side effect (ledger/slip/alerts).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote

from vula.tap.core import matching as mt
from vula.tap.core import money as mo
from vula.tap.core import states as st
from vula.tap.ports import Gateway, Messenger, Repo

logger = logging.getLogger(__name__)

_PAY_RE = re.compile(r"^\s*pay\s+([A-Za-z0-9_-]{8,64})\s*$", re.IGNORECASE)
REF_PREFIX = "kb-"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TapConfig:
    pepper: str
    public_base_url: str                       # e.g. https://api.example.com (for /v1/tap/pay links)
    encrypt: Callable[[str], str]
    decrypt: Callable[[str], str]
    tip_presets_bp: tuple = (1000, 1500, 2000)
    custom_tip_cap_bp: int = 10_000            # 100% of the bill
    token_ttl: timedelta = timedelta(seconds=120)
    session_ttl: timedelta = timedelta(minutes=10)
    quick_tip_presets_cents: tuple = (500, 1000, 2000)
    clock: Callable[[], datetime] = _utcnow
    enabled: Callable[[str], bool] = lambda tenant_id: True   # per-tenant switch (api.tenant_enabled)


# ── customer copy (rands, 2 decimals, merchant named, < 3 lines) ─────────────────────────────
def _m(c: int) -> str:
    return mo.format_rands(c)


MSG_NOT_VERIFIED = "We couldn't verify this tag. Ask staff to take payment another way."
MSG_NO_BILL = "There's no open bill for this tag. Ask the merchant to create one."
MSG_LOCKED = "This bill is being paid from another phone. Ask the merchant to release it."
MSG_CODE = "This bill was sent to another number. Enter the 4-digit code from the merchant."
MSG_CODE_LOCKED = "Too many wrong codes. Try again in 15 minutes or ask the merchant."
MSG_CODE_BAD = "That code isn't right. Try again."
MSG_EXPIRED = "This payment link has expired. Tap the tag again to restart."
MSG_FAILED = "That payment didn't go through. You haven't been charged. Tap the tag to try again."
MSG_BAD_AMOUNT = "That amount doesn't look right. Type it in rands, for example 15."


@dataclass
class TapService:
    repo: Repo
    messenger: Messenger
    gateway: Gateway
    cfg: TapConfig

    # ── helpers ──────────────────────────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return self.cfg.clock()

    def phone_hash(self, phone: str) -> str:
        digits = re.sub(r"\D", "", phone or "")
        return hmac.new(self.cfg.pepper.encode(), digits.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def mask(phone: str) -> str:
        d = re.sub(r"\D", "", phone or "")
        return f"ending {d[-3:]}" if len(d) >= 3 else "a customer"

    def _live(self, session: Optional[dict]) -> Optional[dict]:
        """Lazily expire: a session past its TTL that never reached payment is expired on touch."""
        if not session:
            return None
        if session["state"] in st.SESSION_PRE_PAYMENT and _ts(session["expires_at"]) <= self.now():
            if self.repo.cas_session(session["tenant_id"], session["id"],
                                     expected=session["state"], new="expired"):
                self._release_bill_for(session)
            return None
        return session

    def _release_bill_for(self, session: dict) -> None:
        if session.get("bill_id"):
            self.repo.cas_bill(session["tenant_id"], session["bill_id"], expected="claimed", new="open",
                               fields={"claimed_by_hash": None})

    async def _say(self, tenant_id: str, phone: str, body: str) -> None:
        await self.messenger.text(tenant_id, phone, body)

    # ── 1. the tap (HTTP) ────────────────────────────────────────────────────────────────────
    def tap(self, code: str) -> Optional[str]:
        """Verify the tag and mint a one-time claim token. Returns the wa.me URL, or None when the
        tag is unknown/disabled/unbound (caller shows MSG_NOT_VERIFIED)."""
        tag = self.repo.get_tag_by_code(code)
        if not tag or tag.get("status") != "active" or not self.cfg.enabled(tag["tenant_id"]):
            return None
        number = re.sub(r"\D", "", self.repo.tenant_wa_number(tag["tenant_id"]) or "")
        if not number:
            return None
        token = secrets.token_urlsafe(16)
        self.repo.create_claim_token(tenant_id=tag["tenant_id"], tag_id=tag["id"],
                                     token_hash=self._token_hash(token),
                                     expires_at=self.now() + self.cfg.token_ttl)
        return f"https://wa.me/{number}?text={quote('PAY ' + token)}"

    # ── 2. inbound WhatsApp text ─────────────────────────────────────────────────────────────
    async def handle_text(self, tenant_id: str, phone: str, text: str) -> bool:
        """True if this message belonged to the tap flow (caller must then not route it elsewhere)."""
        m = _PAY_RE.match(text or "")
        if m:
            await self._start(tenant_id, phone, m.group(1))
            return True
        session = self._live(self.repo.active_session_for_payer(
            tenant_id, self.phone_hash(phone), self.now()))
        if session and session.get("expecting"):
            await self._typed(session, phone, text)
            return True
        return False

    async def _start(self, tenant_id: str, phone: str, token: str) -> None:
        row = self.repo.consume_claim_token(self._token_hash(token), self.now())
        if not row or row["tenant_id"] != tenant_id:       # a token only works on its own tenant's line
            await self._say(tenant_id, phone, MSG_NOT_VERIFIED)
            return
        tag = self.repo.get_tag(tenant_id, row["tag_id"])
        if not tag or tag.get("status") != "active":
            await self._say(tenant_id, phone, MSG_NOT_VERIFIED)
            return
        payer = self.phone_hash(phone)
        bills = [mt.BillView(b["id"], b["status"], b.get("customer_hash"), b.get("claimed_by_hash"))
                 for b in self.repo.live_bills_for_tag(tenant_id, tag["id"])]
        locked = self._code_locked(tenant_id, bills, payer)
        d = mt.resolve_tap(tag_mode=tag.get("mode", "appointment"), payer_hash=payer, bills=bills,
                           tag_allows_open_amount=bool(tag.get("allow_open_amount")), code_locked=locked)
        merchant = self.repo.merchant_name(tenant_id)

        if d.kind == mt.QUICK_TIP:
            await self._begin_quick_tip(tenant_id, phone, tag, merchant)
        elif d.kind == mt.CLAIM:
            await self._claim_and_begin(tenant_id, phone, d.bill_id, payer)
        elif d.kind == mt.CHOOSE:
            await self._offer_choice(tenant_id, phone, d.bill_ids, merchant)
        elif d.kind == mt.LOCKED:
            await self._say(tenant_id, phone, MSG_LOCKED)
        elif d.kind == mt.CODE_NEEDED:
            await self._begin_code(tenant_id, phone, payer, bills)
        elif d.kind == mt.CODE_LOCKED:
            await self._say(tenant_id, phone, MSG_CODE_LOCKED)
        elif d.kind == mt.ASK_AMOUNT:
            await self._begin_open_amount(tenant_id, phone, tag, merchant)
        else:
            await self._say(tenant_id, phone, MSG_NO_BILL)

    def _code_locked(self, tenant_id: str, bills: list, payer: str) -> bool:
        for b in bills:
            if b.status == "open" and b.customer_hash and b.customer_hash != payer:
                full = self.repo.get_bill(tenant_id, b.id) or {}
                lu = full.get("code_locked_until")
                if lu and _ts(lu) > self.now():
                    return True
        return False

    # ── bill claim → session → tip ───────────────────────────────────────────────────────────
    async def _claim_and_begin(self, tenant_id: str, phone: str, bill_id: str, payer: str) -> None:
        bill = self.repo.get_bill(tenant_id, bill_id)
        if not bill:
            await self._say(tenant_id, phone, MSG_NO_BILL)
            return
        if bill["status"] == "claimed" and bill.get("claimed_by_hash") == payer:
            existing = self._live(self.repo.active_session_for_payer(tenant_id, payer, self.now()))
            if existing and existing.get("bill_id") == bill_id:
                await self._resume(existing, phone)          # double tap: reuse the open session
                return
        elif not self.repo.cas_bill(tenant_id, bill_id, expected="open", new="claimed",
                                    fields={"claimed_by_hash": payer}):
            await self._say(tenant_id, phone, MSG_LOCKED)    # lost the atomic claim
            return
        session = self._new_session(tenant_id, bill_id, payer, phone, int(bill["subtotal_cents"]))
        self.repo.cas_session(tenant_id, session["id"], expected="claimed", new=st.session_next("claimed", "amount_set"))
        session["state"] = "awaiting_tip"
        await self._ask_tip(session, phone)

    def _new_session(self, tenant_id: str, bill_id: Optional[str], payer: str, phone: str,
                     bill_cents: int, *, expecting: Optional[str] = None) -> dict:
        return self.repo.create_session({
            "tenant_id": tenant_id, "bill_id": bill_id, "payer_hash": payer,
            "payer_enc": self.cfg.encrypt(re.sub(r"\D", "", phone)), "state": "claimed",
            "bill_cents": bill_cents, "tip_cents": 0,
            "idempotency_key": f"{bill_id or 'qt'}:{payer}:{secrets.token_hex(6)}",
            "expires_at": (self.now() + self.cfg.session_ttl).isoformat(), "expecting": expecting})

    async def _resume(self, s: dict, phone: str) -> None:
        if s["state"] == "awaiting_tip":
            await self._ask_tip(s, phone)
        elif s["state"] == "awaiting_confirm":
            await self._ask_confirm(s, phone)
        elif s["state"] == "awaiting_payment":
            await self._send_pay_link(s, phone)
        else:
            await self._say(s["tenant_id"], phone, MSG_EXPIRED)

    async def _ask_tip(self, s: dict, phone: str) -> None:
        bill = self.repo.get_bill(s["tenant_id"], s["bill_id"]) if s.get("bill_id") else None
        staff = self.repo.staff_name(s["tenant_id"], (bill or {}).get("staff_id"))
        who = f" with {staff}" if staff else ""
        what = (bill or {}).get("description") or "your bill"
        head = f"{self.repo.merchant_name(s['tenant_id'])}. {what}{who}, {_m(s['bill_cents'])}."
        rows = [{"id": f"kb:tip:{s['id']}:0", "title": "No tip", "description": ""}]
        for bp in self.cfg.tip_presets_bp:
            pct = bp // 100
            rows.append({"id": f"kb:tip:{s['id']}:{pct}", "title": f"{pct}%",
                         "description": _m(mo.pct_tip(s["bill_cents"], bp))})
        rows.append({"id": f"kb:tip:{s['id']}:custom", "title": "Custom", "description": "Type your own"})
        await self.messenger.list(s["tenant_id"], phone, "Add a tip?", head + " Add a tip" +
                                  (f" for {staff}?" if staff else "?"), "Choose tip", rows)

    async def _ask_confirm(self, s: dict, phone: str) -> None:
        body = (f"Total {_m(s['bill_cents'] + s['tip_cents'])} (bill {_m(s['bill_cents'])} + tip "
                f"{_m(s['tip_cents'])}) to {self.repo.merchant_name(s['tenant_id'])}.")
        await self.messenger.buttons(s["tenant_id"], phone, body, [
            {"id": f"kb:pay:{s['id']}", "title": "Pay now"},
            {"id": f"kb:chg:{s['id']}", "title": "Change tip"}])

    # ── other entry modes ────────────────────────────────────────────────────────────────────
    async def _offer_choice(self, tenant_id: str, phone: str, bill_ids: tuple, merchant: str) -> None:
        rows = []
        for bid in bill_ids[:10]:
            b = self.repo.get_bill(tenant_id, bid) or {}
            rows.append({"id": f"kb:pick:{bid}", "title": _m(int(b.get("subtotal_cents", 0))),
                         "description": (b.get("description") or "")[:70]})
        await self.messenger.list(tenant_id, phone, merchant[:60], "Which bill are you paying?",
                                  "Choose bill", rows)

    async def _begin_open_amount(self, tenant_id: str, phone: str, tag: dict, merchant: str) -> None:
        payer = self.phone_hash(phone)
        s = self._new_session(tenant_id, None, payer, phone, 0, expecting="amount")
        self.repo.cas_session(tenant_id, s["id"], expected="claimed", new="awaiting_amount",
                              fields={"staff_ref": tag.get("bound_id")})
        await self._say(tenant_id, phone, f"Enter the amount to pay {merchant}, in rands.")

    async def _begin_quick_tip(self, tenant_id: str, phone: str, tag: dict, merchant: str) -> None:
        payer = self.phone_hash(phone)
        s = self._new_session(tenant_id, None, payer, phone, 0, expecting="quick_tip")
        self.repo.cas_session(tenant_id, s["id"], expected="claimed", new="awaiting_amount",
                              fields={"staff_ref": tag.get("bound_id")})
        who = self.repo.staff_name(tenant_id, tag.get("bound_id"))
        buttons = [{"id": f"kb:qt:{s['id']}:{c}", "title": _m(c).replace(".00", "")}
                   for c in self.cfg.quick_tip_presets_cents[:3]]
        await self.messenger.buttons(tenant_id, phone,
                                     f"Tip {who or 'the staff member'} at {merchant}. How much? "
                                     f"Tap an amount or type one in rands.", buttons)

    async def _begin_code(self, tenant_id: str, phone: str, payer: str, bills: list) -> None:
        target = sorted((b for b in bills if b.status == "open" and b.customer_hash),
                        key=lambda b: b.id)[0]
        self._new_session(tenant_id, target.id, payer, phone, 0, expecting="bill_code")
        await self._say(tenant_id, phone, MSG_CODE)

    # ── typed replies ────────────────────────────────────────────────────────────────────────
    async def _typed(self, s: dict, phone: str, text: str) -> None:
        tenant_id, kind = s["tenant_id"], s["expecting"]
        if kind == "bill_code":
            await self._typed_code(s, phone, text)
            return
        try:
            if kind == "custom_tip":
                tip = mo.validate_custom_tip(text, s["bill_cents"], max_pct_bp=self.cfg.custom_tip_cap_bp)
                ok = self.repo.cas_session(tenant_id, s["id"], expected="awaiting_tip",
                                           new=st.session_next("awaiting_tip", "tip_chosen"),
                                           fields={"tip_cents": tip, "expecting": None})
            elif kind == "amount":
                amt = mo.validate_open_amount(text)
                ok = self.repo.cas_session(tenant_id, s["id"], expected="awaiting_amount",
                                           new=st.session_next("awaiting_amount", "amount_set"),
                                           fields={"bill_cents": amt, "expecting": None})
                if ok:
                    s = {**s, "bill_cents": amt, "state": "awaiting_tip", "expecting": None}
                    await self._ask_tip(s, phone)
                return
            elif kind == "quick_tip":
                tip = mo.validate_quick_tip(text)
                ok = self.repo.cas_session(tenant_id, s["id"], expected="awaiting_amount",
                                           new="awaiting_tip",
                                           fields={"tip_cents": tip, "expecting": None})
                if ok:
                    ok = self.repo.cas_session(tenant_id, s["id"], expected="awaiting_tip",
                                               new=st.session_next("awaiting_tip", "tip_chosen"))
            else:
                return
        except mo.MoneyError:
            await self._say(tenant_id, phone, MSG_BAD_AMOUNT)
            return
        if ok:
            fresh = self.repo.get_session(tenant_id, s["id"])
            await self._ask_confirm(fresh, phone)

    async def _typed_code(self, s: dict, phone: str, text: str) -> None:
        tenant_id = s["tenant_id"]
        bill = self.repo.get_bill(tenant_id, s["bill_id"])
        if not bill or bill["status"] != "open":
            await self._say(tenant_id, phone, MSG_NO_BILL)
            return
        gate = mt.CodeGate(int(bill.get("code_attempts") or 0),
                           _ts(bill["code_locked_until"]) if bill.get("code_locked_until") else None)
        ok, gate = mt.check_bill_code(bill.get("bill_code") or "", text, gate, self.now())
        self.repo.update_bill(tenant_id, bill["id"], {
            "code_attempts": gate.attempts,
            "code_locked_until": gate.locked_until.isoformat() if gate.locked_until else None})
        if not ok:
            await self._say(tenant_id, phone, MSG_CODE_LOCKED if gate.locked_until else MSG_CODE_BAD)
            return
        payer = s["payer_hash"]
        self.repo.cas_session(tenant_id, s["id"], expected="claimed", new="cancelled",
                              fields={"expecting": None})  # the code-entry stub is done
        await self._claim_and_begin(tenant_id, phone, bill["id"], payer)

    # ── interactive replies ──────────────────────────────────────────────────────────────────
    async def handle_interactive(self, tenant_id: str, phone: str, reply_id: str) -> bool:
        if not reply_id.startswith("kb:"):
            return False
        parts = reply_id.split(":")
        action = parts[1] if len(parts) > 1 else ""
        payer = self.phone_hash(phone)
        if action == "pick" and len(parts) == 3:
            bill = self.repo.get_bill(tenant_id, parts[2])
            if bill and bill.get("customer_hash") == payer and bill["status"] == "open":
                await self._claim_and_begin(tenant_id, phone, bill["id"], payer)
            else:
                await self._say(tenant_id, phone, MSG_LOCKED)
            return True
        if len(parts) < 3:
            return True
        s = self._live(self.repo.get_session(tenant_id, parts[2]))
        if not s or s["payer_hash"] != payer:               # not yours / gone / expired
            await self._say(tenant_id, phone, MSG_EXPIRED)
            return True
        if action == "tip" and s["state"] == "awaiting_tip" and len(parts) == 4:
            await self._on_tip(s, phone, parts[3])
        elif action == "qt" and s["state"] == "awaiting_amount" and len(parts) == 4:
            await self._on_quick_tip(s, phone, parts[3])
        elif action == "chg" and s["state"] == "awaiting_confirm":
            if self.repo.cas_session(tenant_id, s["id"], expected="awaiting_confirm",
                                     new=st.session_next("awaiting_confirm", "change_tip"),
                                     fields={"tip_cents": 0, "expecting": None}):
                await self._ask_tip({**s, "tip_cents": 0, "state": "awaiting_tip"}, phone)
        elif action == "pay" and s["state"] == "awaiting_confirm":
            await self._on_pay_now(s, phone)
        elif action == "pay" and s["state"] == "awaiting_payment":
            await self._send_pay_link(s, phone)
        return True

    async def _on_tip(self, s: dict, phone: str, choice: str) -> None:
        tenant_id = s["tenant_id"]
        if choice == "custom":
            self.repo.cas_session(tenant_id, s["id"], expected="awaiting_tip", new="awaiting_tip",
                                  fields={"expecting": "custom_tip"})
            await self._say(tenant_id, phone, "Type your tip in rands, for example 15.")
            return
        try:
            pct = int(choice)
        except ValueError:
            return
        if pct != 0 and pct * 100 not in self.cfg.tip_presets_bp:
            return                                           # not an offered preset
        tip = mo.pct_tip(s["bill_cents"], pct * 100)
        if self.repo.cas_session(tenant_id, s["id"], expected="awaiting_tip",
                                 new=st.session_next("awaiting_tip", "tip_chosen"),
                                 fields={"tip_cents": tip, "expecting": None}):
            await self._ask_confirm(self.repo.get_session(tenant_id, s["id"]), phone)

    async def _on_quick_tip(self, s: dict, phone: str, choice: str) -> None:
        try:
            cents = mo.validate_quick_tip(str(int(choice) / 100)) if choice != "other" else None
        except (ValueError, mo.MoneyError):
            return
        if cents is None:
            await self._say(s["tenant_id"], phone, "Type the amount in rands, for example 15.")
            return
        await self._typed({**s, "expecting": "quick_tip"}, phone, f"{cents // 100}.{cents % 100:02d}")

    async def _on_pay_now(self, s: dict, phone: str) -> None:
        nonce = secrets.token_urlsafe(16)
        if self.repo.cas_session(s["tenant_id"], s["id"], expected="awaiting_confirm",
                                 new=st.session_next("awaiting_confirm", "pay_now"),
                                 fields={"pay_nonce_hash": self._token_hash(nonce),
                                         "checkout_ref": REF_PREFIX + s["id"]}):
            await self._send_pay_link({**s, "state": "awaiting_payment"}, phone, nonce)

    async def _send_pay_link(self, s: dict, phone: str, nonce: Optional[str] = None) -> None:
        if nonce is None:                                   # re-issue after a repeat "Pay now" tap
            nonce = secrets.token_urlsafe(16)
            self.repo.cas_session(s["tenant_id"], s["id"], expected="awaiting_payment",
                                  new="awaiting_payment", fields={"pay_nonce_hash": self._token_hash(nonce)})
        url = f"{self.cfg.public_base_url.rstrip('/')}/v1/tap/pay/{s['id']}/{nonce}"
        total = s["bill_cents"] + s["tip_cents"]
        await self._say(s["tenant_id"], phone,
                        f"Pay {_m(total)} to {self.repo.merchant_name(s['tenant_id'])}:\n{url}\n"
                        f"Link works for 10 minutes.")

    # ── 3. pay redirect (HTTP) ───────────────────────────────────────────────────────────────
    async def open_pay_link(self, session_id: str, nonce: str) -> Optional[str]:
        """Resolve the short one-time link to the hosted-checkout URL, or None."""
        s = self._live(self.repo.get_session_any_tenant(session_id))
        if (not s or not self.cfg.enabled(s["tenant_id"]) or s["state"] != "awaiting_payment"
                or not s.get("pay_nonce_hash")
                or not hmac.compare_digest(s["pay_nonce_hash"], self._token_hash(nonce))):
            return None
        bill = self.repo.get_bill(s["tenant_id"], s["bill_id"]) if s.get("bill_id") else None
        desc = (bill or {}).get("description") or "Payment"
        return await self.gateway.create_checkout(tenant_id=s["tenant_id"], session=s,
                                                  description=f"{self.repo.merchant_name(s['tenant_id'])} - {desc}")

    # ── 4. gateway notification ──────────────────────────────────────────────────────────────
    async def confirm_payment(self, tenant_id: str, headers: dict, body: bytes, form: dict) -> str:
        """Process a PayFast ITN. Returns a short outcome label (for logs/tests)."""
        ev = await self.gateway.verify_itn(tenant_id=tenant_id, headers=headers, body=body, form=form)
        if not ev:
            return "rejected"
        ref = str(ev.get("reference") or "")
        if not ref.startswith(REF_PREFIX):
            return "not_tap"
        s = self.repo.get_session(tenant_id, ref[len(REF_PREFIX):])
        if not s:
            return "unknown_session"
        status_key = "paid" if ev.get("paid") else "unpaid"
        if not self.repo.record_event(tenant_id=tenant_id, source="payfast",
                                      event_id=f"{ev.get('pf_payment_id') or ref}:{status_key}"):
            return "duplicate"
        phone = self.cfg.decrypt(s["payer_enc"]) if s.get("payer_enc") else ""
        if not ev.get("paid"):
            if self.repo.cas_session(tenant_id, s["id"], expected="awaiting_payment",
                                     new=st.session_next("awaiting_payment", "payment_failed")):
                self._release_bill_for(s)
                if phone:
                    await self._say(tenant_id, phone, MSG_FAILED)
            return "failed"
        total = s["bill_cents"] + s["tip_cents"]
        if ev.get("amount_cents") != total:
            await self._alert(tenant_id, s, f"Payment of {_m(int(ev.get('amount_cents') or 0))} did not "
                                            f"match the bill total {_m(total)}. NOT marked paid.")
            return "amount_mismatch"
        if s["state"] != "awaiting_payment":
            await self._alert(tenant_id, s, f"A payment of {_m(total)} arrived for a bill that is "
                                            f"no longer payable ({s['state']}). Check and refund if needed.")
            return "late_payment"
        if not self.repo.cas_session(tenant_id, s["id"], expected="awaiting_payment",
                                     new=st.session_next("awaiting_payment", "payment_succeeded")):
            return "raced"
        pay = self.repo.record_payment({
            "tenant_id": tenant_id, "session_id": s["id"], "provider": "payfast",
            "provider_ref": REF_PREFIX + s["id"], "pf_payment_id": ev.get("pf_payment_id"),
            "status": "succeeded", "method": ev.get("method"), "amount_cents": total,
            "fee_cents": int(ev.get("fee_cents") or 0)})
        if pay is None:
            return "duplicate"
        bill = self.repo.get_bill(tenant_id, s["bill_id"]) if s.get("bill_id") else None
        is_test = bool((bill or {}).get("is_test"))
        if not is_test:
            await self._post_ledger(tenant_id, s, pay)
        if s.get("bill_id"):
            self.repo.cas_bill(tenant_id, s["bill_id"], expected="claimed", new="paid")
        if is_test:                                  # setup test: unlocks "Go live", books nothing
            self.repo.upsert_settings(tenant_id, {"tested_at": self.now().isoformat()})
            for p in self.repo.team_phones(tenant_id, None):
                await self._say(tenant_id, p, "Tap to Pay test payment received. PayFast is set up "
                                              "correctly - you can go live in the dashboard.")
            return "test_paid"
        if phone:
            await self._say(tenant_id, phone,
                            f"Paid {_m(total)} to {self.repo.merchant_name(tenant_id)}. Thank you.\n"
                            f"Bill {_m(s['bill_cents'])}, tip {_m(s['tip_cents'])}. Ref {s['id'][:8]}.")
        await self._notify_staff(tenant_id, s, phone)
        return "paid"

    async def _post_ledger(self, tenant_id: str, s: dict, pay: dict) -> None:
        bill = self.repo.get_bill(tenant_id, s["bill_id"]) if s.get("bill_id") else None
        staff = (bill or {}).get("staff_id") or s.get("staff_ref")
        rule = self.repo.get_split_rule(tenant_id, staff) or {}
        alloc = mo.allocate_payment(
            bill_cents=s["bill_cents"], tip_cents=s["tip_cents"], merchant="merchant", staff=staff,
            staff_share_bp=int(rule.get("staff_share_bp", 0)) if s["bill_cents"] else 0,
            tip_rule=rule.get("tip_rule", mo.TIP_DIRECT), house_cut_bp=int(rule.get("house_cut_bp", 0)),
            house_cut_then=rule.get("house_cut_then", mo.TIP_DIRECT),
            provider_fee_cents=int(pay.get("fee_cents") or 0),
            merchant_absorbs_provider_fee=bool(rule.get("merchant_absorbs_provider_fee")))
        self.repo.insert_ledger_lines([
            {"tenant_id": tenant_id, "payment_id": pay["id"], "kind": l.kind,
             "party_id": l.party, "cents": l.cents} for l in alloc.lines])
        s["_alloc"] = alloc

    async def _notify_staff(self, tenant_id: str, s: dict, phone: str) -> None:
        alloc = s.get("_alloc")
        bill = self.repo.get_bill(tenant_id, s["bill_id"]) if s.get("bill_id") else None
        staff = (bill or {}).get("staff_id") or s.get("staff_ref")
        share = alloc.net_by_party().get(staff, 0) if alloc and staff else 0
        what = (bill or {}).get("description") or "payment"
        msg = (f"Paid {_m(s['bill_cents'])} ({what}) + {_m(s['tip_cents'])} tip."
               + (f" Your share {_m(share)}." if staff else "") + f" Customer {self.mask(phone)}.")
        for p in self.repo.team_phones(tenant_id, staff):
            await self._say(tenant_id, p, msg)

    async def _alert(self, tenant_id: str, s: dict, msg: str) -> None:
        logger.warning("tap alert tenant=%s session=%s: %s", tenant_id, s["id"], msg)
        for p in self.repo.team_phones(tenant_id, None):
            await self._say(tenant_id, p, msg)

    # ── 5. merchant actions ──────────────────────────────────────────────────────────────────
    def create_bill(self, *, tenant_id: str, tag_id: str, amount_cents: int, description: str,
                    staff_id: Optional[str], created_by: str, customer_phone: Optional[str] = None,
                    is_test: bool = False) -> dict:
        if amount_cents <= 0:
            raise mo.MoneyError("amount must be positive")
        row = {"tenant_id": tenant_id, "tag_id": tag_id, "bill_type": "fixed", "status": "open",
               "description": description[:120], "subtotal_cents": amount_cents,
               "staff_id": staff_id, "created_by": created_by, "is_test": is_test,
               "expires_at": (self.now() + timedelta(hours=24)).isoformat()}
        if customer_phone:
            row.update(customer_hash=self.phone_hash(customer_phone),
                       customer_enc=self.cfg.encrypt(re.sub(r"\D", "", customer_phone)),
                       bill_code=f"{secrets.randbelow(10000):04d}")
        return self.repo.create_bill(row)

    def cancel_bill(self, tenant_id: str, bill_id: str) -> bool:
        bill = self.repo.get_bill(tenant_id, bill_id)
        if not bill or bill["status"] not in ("open", "claimed"):
            return False
        return self.repo.cas_bill(tenant_id, bill_id, expected=bill["status"],
                                  new=st.bill_next(bill["status"], "cancel"))

    def release_bill(self, tenant_id: str, bill_id: str) -> bool:
        """Free a wrongly claimed bill (cancels any unpaid session first so nobody pays a stale one)."""
        bill = self.repo.get_bill(tenant_id, bill_id)
        if not bill or bill["status"] != "claimed":
            return False
        s = self.repo.active_session_for_payer(tenant_id, bill.get("claimed_by_hash") or "", self.now())
        if s and s.get("bill_id") == bill_id and s["state"] in st.SESSION_PRE_PAYMENT:
            self.repo.cas_session(tenant_id, s["id"], expected=s["state"],
                                  new=st.session_next(s["state"], "cancel"))
        return self.repo.cas_bill(tenant_id, bill_id, expected="claimed", new="open",
                                  fields={"claimed_by_hash": None})


def _ts(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

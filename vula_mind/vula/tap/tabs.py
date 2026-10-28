"""Bar tabs (28 Oct 2026, migration 231).

A customer taps the bar's tag and presses "Open a tab": WhatsApp opens with "TAB <token>" typed in,
they press Send, and a tab (a kb_bills row, bill_type='tab') is opened under their WhatsApp number
and name. Staff add drinks to it from their phone (kb_tab_items). To pay, the customer taps again
("Pay my tab", the PAY flow, which proves their number and finds their own tab) or replies PAY TAB;
from there it is the normal tip -> pay link -> PayFast -> slip flow.

- The number is the key: a tab is only ever offered to the number it was opened by. Tabs never show
  up for other customers tapping the same tag (no 4-digit code path for tabs).
- The total is worked out from the items again when payment starts; the stored subtotal is for
  display. While the customer is paying (bill claimed), staff can't add to it.
- The owner sees the name and full number (POPIA: the customer is told when the tab opens). Staff
  apps only see the name and the last digits.
- A tab with no new item for tab_idle_hours gets one WhatsApp prompt to pay, and the owner is told.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from urllib.parse import quote

from vula.tap.core import money as mo

_TAB_RE = re.compile(r"^\s*tab\s+([A-Za-z0-9_-]{8,64})\s*$", re.IGNORECASE)
_PAY_TAB_RE = re.compile(r"^\s*(pay\s+(my\s+)?tab|close\s+(my\s+)?tab)\s*[.!]?\s*$", re.IGNORECASE)
_SHOW_TAB_RE = re.compile(r"^\s*((my|show(\s+my)?)\s+tab|tab|what'?s\s+my\s+tab\??)\s*$", re.IGNORECASE)
OPEN_TTL = timedelta(minutes=10)
TAB_TTL = timedelta(hours=24)
LIVE = ("open", "claimed", "abandoned", "needs_follow_up")
MAX_ITEM_CENTS = 10_000_00            # R10,000 a line: a typo guard, not a business limit


class TabError(ValueError):
    """Shown to staff as is."""


@dataclass
class TabDesk:
    svc: "object"                              # TapService

    # ── settings ─────────────────────────────────────────────────────────────────────────────
    def enabled(self, tenant_id: str) -> bool:
        return bool((self.svc.repo.get_settings(tenant_id) or {}).get("tabs_enabled"))

    def idle_hours(self, tenant_id: str) -> int:
        v = (self.svc.repo.get_settings(tenant_id) or {}).get("tab_idle_hours")
        return 3 if v is None else max(1, min(int(v), 24))

    # ── links on the tap page ────────────────────────────────────────────────────────────────
    def open_link(self, tag: dict) -> Optional[str]:
        """wa.me link that opens a tab from this tag ("TAB <one-time token>")."""
        svc = self.svc
        number = re.sub(r"\D", "", svc.repo.tenant_wa_number(tag["tenant_id"]) or "")
        if not number:
            return None
        token = secrets.token_urlsafe(16)
        svc.repo.create_claim_token(tenant_id=tag["tenant_id"], tag_id=tag["id"],
                                    token_hash=svc._token_hash(token), expires_at=svc.now() + OPEN_TTL)
        return f"https://wa.me/{number}?text={quote('TAB ' + token)}"

    # ── customer messages ────────────────────────────────────────────────────────────────────
    async def handle_text(self, tenant_id: str, phone: str, text: str, name: str = "") -> bool:
        m = _TAB_RE.match(text or "")
        if m:
            await self._open(tenant_id, phone, m.group(1), name)
            return True
        if not (_PAY_TAB_RE.match(text or "") or _SHOW_TAB_RE.match(text or "")):
            return False
        if not self.enabled(tenant_id):
            return False
        svc = self.svc
        payer = svc.phone_hash(phone)
        tab = svc.repo.tab_for_customer(tenant_id, payer)
        if not tab:
            await svc._say(tenant_id, phone, "You don't have an open tab here. Tap the bar's tag to open one.")
            return True
        if _PAY_TAB_RE.match(text):
            await svc._claim_and_begin(tenant_id, phone, tab["id"], payer)
        else:
            await svc._say(tenant_id, phone, self.summary(tab) + "\nReply PAY TAB to pay.")
        return True

    async def _open(self, tenant_id: str, phone: str, token: str, name: str) -> None:
        svc = self.svc
        row = svc.repo.consume_claim_token(svc._token_hash(token), svc.now())
        tag = svc.repo.get_tag(tenant_id, row["tag_id"]) if row and row["tenant_id"] == tenant_id else None
        if not tag or tag.get("status") != "active" or not self.enabled(tenant_id):
            await svc._say(tenant_id, phone, "We couldn't open a tab from that link. Tap the tag again.")
            return
        merchant = svc.repo.merchant_name(tenant_id)
        payer = svc.phone_hash(phone)
        existing = svc.repo.tab_for_customer(tenant_id, payer)
        if existing:
            await svc._say(tenant_id, phone, f"You already have a tab open at {merchant}. "
                                             + self.summary(existing) + "\nReply PAY TAB to pay.")
            return
        name = re.sub(r"\s+", " ", (name or "").strip())[:60]
        now = svc.now()
        tab = svc.repo.create_bill({
            "tenant_id": tenant_id, "tag_id": tag["id"], "bill_type": "tab", "status": "open",
            "description": f"Tab: {name or 'customer'}"[:120], "subtotal_cents": 0,
            "staff_id": tag.get("bound_id"), "created_by": "customer", "is_test": False,
            "customer_hash": payer, "customer_enc": svc.cfg.encrypt(re.sub(r"\D", "", phone)),
            "customer_name": name or None, "last_item_at": now.isoformat(),
            "expires_at": (now + TAB_TTL).isoformat()})
        await svc._say(tenant_id, phone,
                       f"Your tab at {merchant} is open{', ' + name if name else ''}. Show this message to "
                       f"the staff when you order.\nTo pay, tap the tag again or reply PAY TAB.\n"
                       f"{merchant} can see your name and number while your tab is open.")
        who = f"{name or 'A customer'} ({svc.mask(phone)})"
        for p in svc.repo.team_phones(tenant_id, tag.get("bound_id")):
            await svc._say(tenant_id, p, f"New tab: {who}.")
        await svc._push(tenant_id, tag.get("bound_id"), "New tab", who, tag=tab["id"])

    # ── staff actions ────────────────────────────────────────────────────────────────────────
    def _tab(self, tenant_id: str, bill_id: str) -> dict:
        tab = self.svc.repo.get_bill(tenant_id, bill_id)
        if not tab or tab.get("bill_type") != "tab":
            raise TabError("Tab not found.")
        return tab

    def add_item(self, tenant_id: str, bill_id: str, *, description: str, quantity: int,
                 unit_cents: int, by: str) -> dict:
        tab = self._tab(tenant_id, bill_id)
        if tab["status"] == "claimed":
            raise TabError("The customer is paying this tab right now. Add it after, or start a new tab.")
        if tab["status"] != "open":
            raise TabError("This tab is closed.")
        desc = (description or "").strip()[:80]
        if not desc:
            raise TabError("What was it? Add a description.")
        if not isinstance(quantity, int) or not 1 <= quantity <= 99:
            raise TabError("Quantity must be between 1 and 99.")
        if not isinstance(unit_cents, int) or not 0 <= unit_cents <= MAX_ITEM_CENTS:
            raise TabError("That price doesn't look right.")
        self.svc.repo.add_tab_item({"tenant_id": tenant_id, "bill_id": bill_id, "description": desc,
                                    "quantity": quantity, "unit_cents": unit_cents,
                                    "line_cents": quantity * unit_cents, "added_by": by})
        return self._refresh(tenant_id, bill_id, item_added=True)

    def void_item(self, tenant_id: str, bill_id: str, item_id: str) -> dict:
        tab = self._tab(tenant_id, bill_id)
        if tab["status"] != "open":
            raise TabError("Only an open tab can be changed.")
        it = self.svc.repo.void_tab_item(tenant_id, item_id, self.svc.now().isoformat())
        if not it or it["bill_id"] != bill_id:
            raise TabError("Item not found.")
        return self._refresh(tenant_id, bill_id)

    def total(self, tenant_id: str, bill_id: str) -> int:
        return sum(int(i["line_cents"]) for i in self.svc.repo.tab_items(tenant_id, bill_id))

    def _refresh(self, tenant_id: str, bill_id: str, item_added: bool = False) -> dict:
        fields = {"subtotal_cents": self.total(tenant_id, bill_id)}
        if item_added:
            fields.update(last_item_at=self.svc.now().isoformat(), tab_nudged_at=None,
                          expires_at=(self.svc.now() + TAB_TTL).isoformat())
        self.svc.repo.update_bill(tenant_id, bill_id, fields)
        return self.view(self.svc.repo.get_bill(tenant_id, bill_id), reveal=False)

    def close_empty(self, tenant_id: str, bill_id: str) -> bool:
        tab = self._tab(tenant_id, bill_id)
        if self.total(tenant_id, bill_id):
            raise TabError("This tab has items. The customer pays it, or the owner closes it as unpaid.")
        return self.svc.cancel_bill(tenant_id, tab["id"])

    # ── views ────────────────────────────────────────────────────────────────────────────────
    def view(self, tab: dict, reveal: bool) -> dict:
        """A tab for a staff list. reveal=True (owner only) adds the full number."""
        svc = self.svc
        digits = svc.cfg.decrypt(tab["customer_enc"]) if tab.get("customer_enc") else ""
        items = svc.repo.tab_items(tab["tenant_id"], tab["id"])
        out = {"id": tab["id"], "name": tab.get("customer_name") or "Customer", "number": svc.mask(digits),
               "status": tab["status"], "total_cents": sum(int(i["line_cents"]) for i in items),
               "items": [{"id": i["id"], "description": i["description"], "quantity": i["quantity"],
                          "unit_cents": i["unit_cents"], "line_cents": i["line_cents"]} for i in items],
               "opened_at": tab.get("created_at"), "last_item_at": tab.get("last_item_at"),
               "staff_id": tab.get("staff_id")}
        if reveal:
            out["phone"] = digits
        return out

    def list_open(self, tenant_id: str, reveal: bool) -> list[dict]:
        return [self.view(t, reveal) for t in self.svc.repo.open_tabs(tenant_id)]

    def summary(self, tab: dict) -> str:
        items = self.svc.repo.tab_items(tab["tenant_id"], tab["id"])
        if not items:
            return "Your tab is empty so far."
        lines = [f"{i['quantity']} x {i['description']}  {mo.format_rands(int(i['line_cents']))}" for i in items[-10:]]
        more = f"\n…and {len(items) - 10} more" if len(items) > 10 else ""
        total = sum(int(i["line_cents"]) for i in items)
        return "Your tab:\n" + "\n".join(lines) + more + f"\nTotal {mo.format_rands(total)}."

    # ── idle tabs (background sweep) ─────────────────────────────────────────────────────────
    async def sweep_idle(self, tenant_id: str) -> int:
        """One prompt to pay for each tab idle past tab_idle_hours, and a note to the owner."""
        svc = self.svc
        if not self.enabled(tenant_id):
            return 0
        cutoff = svc.now() - timedelta(hours=self.idle_hours(tenant_id))
        sent = 0
        for tab in svc.repo.open_tabs(tenant_id):
            last = tab.get("last_item_at") or tab.get("created_at")
            if (tab["status"] != "open" or tab.get("tab_nudged_at") or not last
                    or _ts(last) > cutoff or not self.total(tenant_id, tab["id"])):
                continue
            if not svc.repo.cas_bill(tenant_id, tab["id"], expected="open", new="open",
                                     fields={"tab_nudged_at": svc.now().isoformat()}):
                continue
            phone = svc.cfg.decrypt(tab["customer_enc"]) if tab.get("customer_enc") else ""
            if phone:
                await svc._say(tenant_id, phone, f"Your tab at {svc.repo.merchant_name(tenant_id)} is still "
                                                 f"open. " + self.summary(tab) + "\nReply PAY TAB to pay.")
            total = mo.format_rands(self.total(tenant_id, tab["id"]))
            for p in svc.repo.team_phones(tenant_id, None):
                await svc._say(tenant_id, p, f"Tab still open: {tab.get('customer_name') or 'customer'} "
                                             f"({svc.mask(phone)}), {total}. We've reminded them to pay.")
            sent += 1
        return sent


def _ts(v):
    from vula.tap.service import _ts as ts
    return ts(v)

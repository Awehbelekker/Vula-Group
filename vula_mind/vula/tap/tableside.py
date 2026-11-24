"""Service at the table (24 Nov 2026, migration 300).

A tag on a restaurant table (`kb_tags.bound_type` 'table', `label` "Table 7", `bound_id` = the waiter
serving it now). From the page the tag opens, a customer can:

  * call the waiter, ask for the bill, or ask for the card machine (`kb_table_calls`);
  * order from the menu (`kb_table_orders`).

The waiter gets a push in the staff app and a WhatsApp message, accepts the call and marks it done.
A call nobody has accepted after a few minutes goes to the owners and managers, once.

An order is only a request until a waiter confirms it: confirming puts its lines on the table's bill
(`kb_bills.bill_type` 'table', lines in `kb_tab_items`). A waiter can also add lines directly. The
customer pays that bill by tapping the tag — the payment flow that already exists; nothing here
takes money.

Rules that keep this honest:
  * every price is the product row's, read by the server when the order is made; the page sends
    product ids and quantities only;
  * what is owed is the sum of the bill's lines, worked out again when payment starts
    (`TapService._claim_and_begin`, `ExpressPay._claim`) — the stored subtotal is for display;
  * lines can't be added while the customer is paying: the add is read back, and undone if the bill
    was claimed in between;
  * a call or an order changes hands by a compare-and-set, so two waiters tapping at once get one
    winner;
  * the customer is a random cookie, hashed — no phone number is asked for or kept.
"""
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, List, Optional

from vula.tap.core import money as mo

logger = logging.getLogger(__name__)

CALLS, ORDERS = "kb_table_calls", "kb_table_orders"
KINDS = {"waiter": "Call a waiter", "bill": "Ask for the bill", "machine": "Bring the card machine"}
ASKED = {"waiter": "is asking for a waiter", "bill": "is asking for the bill", "machine": "wants to pay by card machine"}
MAX_LINES, MAX_QTY, MAX_PENDING = 30, 20, 5
BILL_TTL = timedelta(hours=24)
LIVE = ("open", "claimed", "abandoned", "needs_follow_up")


class TableError(ValueError):
    """Shown to the person as is."""


def _text(raw: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit].strip()


@dataclass
class TableDesk:
    svc: "object"                                   # TapService

    # ── settings ─────────────────────────────────────────────────────────────────────────────
    def settings(self, tenant_id: str) -> dict:
        cfg = self.svc.repo.get_settings(tenant_id) or {}
        mins = cfg.get("call_escalate_minutes")
        return {"calls": bool(cfg.get("table_calls_enabled")), "orders": bool(cfg.get("table_orders_enabled")),
                "escalate_minutes": 3 if mins is None else max(1, min(int(mins), 30))}

    def save_settings(self, tenant_id: str, *, calls: Optional[bool] = None, orders: Optional[bool] = None,
                      escalate_minutes: Optional[int] = None) -> dict:
        fields: dict = {}
        if calls is not None:
            fields["table_calls_enabled"] = bool(calls)
        if orders is not None:
            fields["table_orders_enabled"] = bool(orders)
        if escalate_minutes is not None:
            if isinstance(escalate_minutes, bool) or not isinstance(escalate_minutes, int) or not 1 <= escalate_minutes <= 30:
                raise TableError("Tell a manager after between 1 and 30 minutes.")
            fields["call_escalate_minutes"] = escalate_minutes
        if fields:
            self.svc.repo.upsert_settings(tenant_id, fields)
        return self.settings(tenant_id)

    # ── tables ───────────────────────────────────────────────────────────────────────────────
    def _tag(self, tenant_id: str, tag_id: str) -> dict:
        tag = self.svc.repo.get_tag(tenant_id, tag_id)
        if not tag or tag.get("bound_type") != "table" or tag.get("status") != "active":
            raise TableError("Table not found.")
        return tag

    def _links(self, tag: dict) -> dict:
        base = self.svc.cfg.public_base_url.rstrip("/")
        return {"url": f"{base}/t/{tag['code']}", "qr": f"{base}/t/{tag['code']}/qr.svg"}

    def view(self, tag: dict, with_bill: bool = True) -> dict:
        svc = self.svc
        out = {"id": tag["id"], "label": tag.get("label") or "Table", "waiter_id": tag.get("bound_id"),
               "waiter": svc.repo.staff_name(tag["tenant_id"], tag.get("bound_id")), **self._links(tag)}
        if with_bill:
            out["bill"] = self.bill_view(tag["tenant_id"], tag)
        return out

    def tables(self, tenant_id: str, with_bill: bool = True) -> List[dict]:
        tags = [t for t in self.svc.repo.list_tags(tenant_id)
                if t.get("bound_type") == "table" and t.get("status") == "active"]
        tags.sort(key=lambda t: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", t.get("label") or "")])
        return [self.view(t, with_bill) for t in tags]

    def create_table(self, tenant_id: str, label: str, waiter_id: Optional[str] = None) -> dict:
        name = _text(label, 30)
        if not name:
            raise TableError("Give the table a name, like Table 7.")
        if any((t.get("label") or "").lower() == name.lower() for t in self.svc.repo.list_tags(tenant_id)
               if t.get("bound_type") == "table" and t.get("status") == "active"):
            raise TableError(f"There is already a {name}.")
        self._member(tenant_id, waiter_id)
        tag = self.svc.repo.create_tag({"tenant_id": tenant_id, "code": secrets.token_urlsafe(9),
                                        "mode": "appointment", "bound_type": "table", "bound_id": waiter_id,
                                        "label": name})
        return self.view(tag, with_bill=False)

    def _member(self, tenant_id: str, member_id: Optional[str]) -> None:
        if member_id and member_id not in {m["id"] for m in self.svc.repo.team_members(tenant_id)}:
            raise TableError("Unknown team member.")

    def assign(self, tenant_id: str, tag_id: str, member_id: Optional[str]) -> dict:
        """Who is serving this table now (None = nobody: calls go to the managers)."""
        tag = self._tag(tenant_id, tag_id)
        self._member(tenant_id, member_id)
        self.svc.repo.update_tag(tenant_id, tag_id, {"bound_id": member_id})
        back = self.svc.repo.get_tag(tenant_id, tag_id) or {}
        if back.get("bound_id") != member_id:
            raise TableError("That didn't save. Try again.")
        bill = self._bill(tenant_id, tag)
        if bill and bill["status"] == "open":
            self.svc.repo.update_bill(tenant_id, bill["id"], {"staff_id": member_id})
        return self.view(back)

    def remove_table(self, tenant_id: str, tag_id: str) -> None:
        tag = self._tag(tenant_id, tag_id)
        if self._bill(tenant_id, tag):
            raise TableError("This table has a bill open. Finish or cancel it first.")
        self.svc.repo.update_tag(tenant_id, tag_id, {"status": "disabled"})

    # ── the table's bill ─────────────────────────────────────────────────────────────────────
    def _bill(self, tenant_id: str, tag: dict) -> Optional[dict]:
        live = [b for b in self.svc.repo.live_bills_for_tag(tenant_id, tag["id"]) if b.get("bill_type") == "table"]
        return live[-1] if live else None

    def total(self, tenant_id: str, bill_id: str) -> int:
        return sum(int(i["line_cents"]) for i in self.svc.repo.tab_items(tenant_id, bill_id))

    def bill_view(self, tenant_id: str, tag: dict) -> Optional[dict]:
        bill = self._bill(tenant_id, tag)
        if not bill:
            return None
        items = self.svc.repo.tab_items(tenant_id, bill["id"])
        return {"id": bill["id"], "status": bill["status"],
                "total_cents": sum(int(i["line_cents"]) for i in items),
                "items": [{"id": i["id"], "description": i["description"], "quantity": i["quantity"],
                           "unit_cents": i["unit_cents"], "line_cents": i["line_cents"]} for i in items]}

    def _open_bill(self, tenant_id: str, tag: dict, by: str) -> dict:
        """The table's open bill, started if there is none. Refuses while the customer is paying,
        or while another kind of bill is open on the tag (a tag carries one bill at a time)."""
        svc = self.svc
        bill = self._bill(tenant_id, tag)
        if bill:
            if bill["status"] != "open":
                raise TableError("The customer is paying this table's bill right now. Add it once they've paid.")
            return bill
        others = [b for b in svc.repo.live_bills_for_tag(tenant_id, tag["id"])
                  if b.get("bill_type") != "tab" and not b.get("customer_hash")]
        if others:
            raise TableError("This table already has a bill open. Finish or cancel it first.")
        now = svc.now()
        return svc.repo.create_bill({
            "tenant_id": tenant_id, "tag_id": tag["id"], "bill_type": "table", "status": "open",
            "description": (tag.get("label") or "Table")[:120], "subtotal_cents": 0,
            "staff_id": tag.get("bound_id") or by, "created_by": by, "is_test": False,
            "last_item_at": now.isoformat(), "expires_at": (now + BILL_TTL).isoformat()})

    def _put_lines(self, tenant_id: str, tag: dict, lines: List[dict], by: str) -> dict:
        """Add priced lines to the table's bill and read the bill back. If the customer started
        paying in between, the lines just added are taken off again and the add is refused."""
        svc = self.svc
        bill = self._open_bill(tenant_id, tag, by)
        added = []
        for ln in lines:
            added.append(svc.repo.add_tab_item({
                "tenant_id": tenant_id, "bill_id": bill["id"], "description": ln["name"][:80],
                "quantity": ln["quantity"], "unit_cents": ln["unit_cents"],
                "line_cents": ln["quantity"] * ln["unit_cents"], "added_by": by}))
        total = self.total(tenant_id, bill["id"])
        if not svc.repo.cas_bill(tenant_id, bill["id"], expected="open", new="open",
                                 fields={"subtotal_cents": total, "last_item_at": svc.now().isoformat(),
                                         "expires_at": (svc.now() + BILL_TTL).isoformat()}):
            for it in added:
                svc.repo.void_tab_item(tenant_id, it["id"], svc.now().isoformat())
            raise TableError("The customer started paying just now. Add it once they've paid.")
        self._audit(tenant_id, by, "table.items_add", bill["id"], table=tag.get("label"),
                    lines=len(lines), line_cents=sum(ln["quantity"] * ln["unit_cents"] for ln in lines))
        return self.bill_view(tenant_id, tag)

    def add_item(self, tenant_id: str, tag_id: str, by: str, *, product_id: Optional[str] = None,
                 quantity: int = 1, description: str = "", unit_cents: Optional[int] = None,
                 note: str = "") -> dict:
        """A waiter adds a line: a product off the menu (its price is the product's), or — for
        something not on it — a description and a price. A note ("no onions") is kept on the line."""
        tag = self._tag(tenant_id, tag_id)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 99:
            raise TableError("Quantity must be between 1 and 99.")
        if product_id:
            item = next((m for m in self.menu(tenant_id) if m["id"] == product_id), None)
            if not item:
                raise TableError("That item isn't on the menu any more.")
            line = {"name": item["name"], "quantity": quantity, "unit_cents": item["price_cents"]}
        else:
            name = _text(description, 80)
            if not name:
                raise TableError("What was it? Add a description.")
            if isinstance(unit_cents, bool) or not isinstance(unit_cents, int) or not 0 <= unit_cents <= 10_000_00:
                raise TableError("That price doesn't look right.")
            line = {"name": name, "quantity": quantity, "unit_cents": unit_cents}
        said = _text(note, 40)
        if said:
            line["name"] = f"{line['name'][:80 - len(said) - 3]} ({said})"
        return self._put_lines(tenant_id, tag, [line], by)

    def void_item(self, tenant_id: str, tag_id: str, item_id: str, by: str) -> dict:
        svc = self.svc
        tag = self._tag(tenant_id, tag_id)
        bill = self._bill(tenant_id, tag)
        if not bill or bill["status"] != "open":
            raise TableError("Only an open bill can be changed.")
        if not any(i["id"] == item_id for i in svc.repo.tab_items(tenant_id, bill["id"])):
            raise TableError("Item not found.")
        it = svc.repo.void_tab_item(tenant_id, item_id, svc.now().isoformat())
        if not svc.repo.cas_bill(tenant_id, bill["id"], expected="open", new="open",
                                 fields={"subtotal_cents": self.total(tenant_id, bill["id"])}):
            raise TableError("The customer started paying just now, so the bill can't be changed.")
        self._audit(tenant_id, by, "table.item_void", bill["id"], table=tag.get("label"),
                    description=(it or {}).get("description"), line_cents=(it or {}).get("line_cents"))
        return self.bill_view(tenant_id, tag)

    def _audit(self, tenant_id: str, by: str, action: str, bill_id: Optional[str], **detail) -> None:
        from vula.tap import audit
        audit.record(self.svc, tenant_id, actor_kind="staff", actor=by, action=action, bill_id=bill_id, **detail)

    # ── telling people ───────────────────────────────────────────────────────────────────────
    async def _tell(self, tenant_id: str, member_id: Optional[str], title: str, body: str, ref: str) -> None:
        """The waiter serving the table: a push in the staff app and a WhatsApp message. With nobody
        assigned, the owners and managers hear it instead."""
        svc = self.svc
        ids = [member_id] if member_id else self._manager_ids(tenant_id)
        for mid in ids:
            await svc._push(tenant_id, mid, title, body, tag=ref)
        for p in svc.repo.team_phones(tenant_id, member_id):
            try:
                await svc._say(tenant_id, p, f"{title}: {body}")
            except Exception:  # noqa: BLE001 — a message that doesn't send never loses the request
                logger.exception("table message failed (tenant=%s)", tenant_id)

    def _manager_ids(self, tenant_id: str) -> List[str]:
        return [m["id"] for m in self.svc.repo.team_members(tenant_id) if m.get("role") in ("owner", "manager")]

    # ── calls ────────────────────────────────────────────────────────────────────────────────
    def _customer_tag(self, code: str) -> Optional[dict]:
        svc = self.svc
        tag = svc.repo.get_tag_by_code(code)
        if not tag or tag.get("status") != "active" or not svc.cfg.enabled(tag["tenant_id"]):
            return None
        return tag

    async def call(self, code: str, caller: str, kind: str) -> dict:
        """A customer asks for someone. Asking again for the same thing is the same request."""
        tag = self._customer_tag(code)
        if not tag or not self.settings(tag["tenant_id"])["calls"]:
            raise TableError("You can't call staff from this tag. Please wave someone over.")
        if kind not in KINDS:
            raise TableError("Choose what you need.")
        tenant = tag["tenant_id"]
        row = self.svc.repo.add_row(CALLS, {"tenant_id": tenant, "tag_id": tag["id"], "kind": kind,
                                            "status": "open", "caller_hash": caller, "staff_id": tag.get("bound_id")})
        if row is None:                                   # already asked: nothing new to send
            live = self.svc.repo.find_rows(CALLS, tenant, ("open", "accepted"), tag_id=tag["id"], kind=kind)
            return live[-1] if live else {}
        where = tag.get("label") or "A customer"
        extra = ""
        if kind in ("bill", "machine"):
            bill = self.bill_view(tenant, tag)
            if bill and bill["total_cents"]:
                extra = f" Their bill is {mo.format_rands(bill['total_cents'])}."
        await self._tell(tenant, tag.get("bound_id"), where, f"{where} {ASKED[kind]}.{extra}", row["id"])
        return row

    def _call_view(self, tenant_id: str, c: dict, labels: dict) -> dict:
        waited = int((self.svc.now() - _ts(c["created_at"])).total_seconds() // 60)
        return {"id": c["id"], "kind": c["kind"], "what": KINDS.get(c["kind"], c["kind"]), "status": c["status"],
                "table": labels.get(c["tag_id"], "Table"), "tag_id": c["tag_id"], "waited_minutes": max(0, waited),
                "accepted_by": self.svc.repo.staff_name(tenant_id, c.get("accepted_by")) if c.get("accepted_by") else None,
                "escalated": bool(c.get("escalated_at"))}

    def _labels(self, tenant_id: str) -> dict:
        return {t["id"]: t.get("label") or self.svc.repo.staff_name(tenant_id, t.get("bound_id")) or "Table"
                for t in self.svc.repo.list_tags(tenant_id)}

    def calls_for(self, tenant_id: str, member_id: Optional[str], sees_all: bool) -> List[dict]:
        """Live calls for the staff app: a waiter's own tables, plus any that nobody is serving or
        that have been passed up; owners and managers see them all."""
        tags = {t["id"]: t for t in self.svc.repo.list_tags(tenant_id)}
        labels = self._labels(tenant_id)
        out = []
        for c in self.svc.repo.find_rows(CALLS, tenant_id, ("open", "accepted")):
            waiter = (tags.get(c["tag_id"]) or {}).get("bound_id")
            if sees_all or waiter in (member_id, None) or c.get("accepted_by") == member_id:
                out.append(self._call_view(tenant_id, c, labels))
        return out

    def accept(self, tenant_id: str, call_id: str, member_id: str) -> dict:
        if not self.svc.repo.cas_row(CALLS, tenant_id, call_id, expected="open",
                                     fields={"status": "accepted", "accepted_by": member_id,
                                             "accepted_at": self.svc.now().isoformat()}):
            c = self.svc.repo.get_row(CALLS, tenant_id, call_id)
            if not c:
                raise TableError("That request isn't there any more.")
            who = self.svc.repo.staff_name(tenant_id, c.get("accepted_by")) or "Someone"
            raise TableError(f"{who} already has this one." if c["status"] == "accepted" else "This one is already done.")
        return self._call_view(tenant_id, self.svc.repo.get_row(CALLS, tenant_id, call_id), self._labels(tenant_id))

    def done(self, tenant_id: str, call_id: str, member_id: str) -> None:
        c = self.svc.repo.get_row(CALLS, tenant_id, call_id)
        if not c or c["status"] not in ("open", "accepted"):
            raise TableError("That request isn't open.")
        fields = {"status": "done", "done_at": self.svc.now().isoformat()}
        if c["status"] == "open":
            fields.update(accepted_by=member_id, accepted_at=self.svc.now().isoformat())
        if not self.svc.repo.cas_row(CALLS, tenant_id, call_id, expected=c["status"], fields=fields):
            raise TableError("Someone else just updated this. Refresh.")

    async def sweep(self, tenant_id: str) -> int:
        """A call nobody has accepted after the business's few minutes goes to the owners and
        managers, once (the row is marked first)."""
        svc = self.svc
        cfg = self.settings(tenant_id)
        if not cfg["calls"]:
            return 0
        cutoff = svc.now() - timedelta(minutes=cfg["escalate_minutes"])
        labels, sent = self._labels(tenant_id), 0
        for c in svc.repo.find_rows(CALLS, tenant_id, ("open",)):
            if c.get("escalated_at") or _ts(c["created_at"]) > cutoff:
                continue
            if not svc.repo.cas_row(CALLS, tenant_id, c["id"], expected="open",
                                    fields={"escalated_at": svc.now().isoformat()}):
                continue
            where = labels.get(c["tag_id"], "A table")
            mins = int((svc.now() - _ts(c["created_at"])).total_seconds() // 60)
            body = f"{where} {ASKED.get(c['kind'], 'needs someone')} and has waited {mins} minutes. Nobody has answered."
            for mid in self._manager_ids(tenant_id):
                await svc._push(tenant_id, mid, "Table waiting", body, tag=c["id"])
            for p in svc.repo.team_phones(tenant_id, None):
                try:
                    await svc._say(tenant_id, p, f"Table waiting: {body}")
                except Exception:  # noqa: BLE001
                    logger.exception("escalation message failed (tenant=%s)", tenant_id)
            sent += 1
        return sent

    # ── the menu ─────────────────────────────────────────────────────────────────────────────
    def menu(self, tenant_id: str) -> List[dict]:
        """What can be ordered from a table: the business's listed, in-stock products with one price
        each (no options, not sold by weight), at the price they sell for now."""
        from vula.commerce import service as commerce
        out = []
        for p in self.svc.repo.menu_products(tenant_id):
            if (p.get("archived") or (p.get("status") or "active") != "active" or p.get("choices")
                    or p.get("product_type") == commerce.TICKET_PRODUCT_TYPE
                    or p.get("pricing_mode") == "per_kg" or p.get("sold_by") == "kg"):
                continue
            price = int(commerce.effective_price_cents(p) or 0)
            if price <= 0:
                continue
            img = str(p.get("image_url") or "")
            out.append({"id": p["id"], "name": p.get("name") or "Item", "price_cents": price,
                        "category": (p.get("category") or "menu").replace("_", " ").title(),
                        "description": _text(p.get("description"), 140),
                        "image": img if img.startswith("https://") else ""})
        return out

    # ── orders ───────────────────────────────────────────────────────────────────────────────
    async def order(self, code: str, caller: str, raw_items: Any, note: Any = "") -> dict:
        """A customer sends an order from the table. Priced here from the product rows; nothing is
        owed until a waiter confirms it."""
        tag = self._customer_tag(code)
        if not tag or not self.settings(tag["tenant_id"])["orders"]:
            raise TableError("You can't order from this tag. Please ask your waiter.")
        tenant = tag["tenant_id"]
        menu = {m["id"]: m for m in self.menu(tenant)}
        want: dict = {}
        for it in (raw_items if isinstance(raw_items, list) else [])[:MAX_LINES * 2]:
            if not isinstance(it, dict):
                continue
            pid, q = str(it.get("product_id") or ""), it.get("quantity")
            if isinstance(q, bool) or not isinstance(q, int) or q < 0:
                raise TableError("How many of each must be a whole number.")
            if q == 0:
                continue
            if pid not in menu:
                raise TableError("Something you chose isn't on the menu any more. Open the menu again.")
            want[pid] = want.get(pid, 0) + q
        if not want:
            raise TableError("Choose something to order.")
        if len(want) > MAX_LINES or any(q > MAX_QTY for q in want.values()):
            raise TableError(f"That's a big order: up to {MAX_QTY} of each. Ask your waiter for more.")
        if len(self.svc.repo.find_rows(ORDERS, tenant, ("pending",), tag_id=tag["id"])) >= MAX_PENDING:
            raise TableError("Your waiter is still getting to your earlier orders. Give them a moment.")
        items = [{"product_id": pid, "name": menu[pid]["name"], "quantity": q, "unit_cents": menu[pid]["price_cents"],
                  "line_cents": q * menu[pid]["price_cents"]} for pid, q in want.items()]
        row = self.svc.repo.add_row(ORDERS, {
            "tenant_id": tenant, "tag_id": tag["id"], "status": "pending", "items": items,
            "total_cents": sum(i["line_cents"] for i in items), "note": _text(note, 200) or None,
            "caller_hash": caller, "staff_id": tag.get("bound_id")})
        where = tag.get("label") or "A table"
        summary = ", ".join(f"{i['quantity']} x {i['name']}" for i in items[:4]) + ("…" if len(items) > 4 else "")
        await self._tell(tenant, tag.get("bound_id"), f"New order, {where}",
                         f"{summary} ({mo.format_rands(row['total_cents'])}). Confirm it in the app.", row["id"])
        return row

    def _order_view(self, o: dict, labels: dict) -> dict:
        return {"id": o["id"], "table": labels.get(o["tag_id"], "Table"), "tag_id": o["tag_id"], "status": o["status"],
                "items": o["items"], "total_cents": int(o["total_cents"]), "note": o.get("note") or "",
                "created_at": o["created_at"], "reject_reason": o.get("reject_reason") or ""}

    def orders_for(self, tenant_id: str, member_id: Optional[str], sees_all: bool) -> List[dict]:
        tags = {t["id"]: t for t in self.svc.repo.list_tags(tenant_id)}
        labels = self._labels(tenant_id)
        return [self._order_view(o, labels) for o in self.svc.repo.find_rows(ORDERS, tenant_id, ("pending",))
                if sees_all or (tags.get(o["tag_id"]) or {}).get("bound_id") in (member_id, None)]

    def confirm(self, tenant_id: str, order_id: str, member_id: str) -> dict:
        """The waiter takes the order: it is claimed first (one winner), then its lines go on the
        table's bill at the prices it was made at. If they can't go on, the order is pending again."""
        svc = self.svc
        o = svc.repo.get_row(ORDERS, tenant_id, order_id)
        if not o or o["status"] != "pending":
            raise TableError("This order has already been dealt with.")
        tag = svc.repo.get_tag(tenant_id, o["tag_id"])
        if not tag or tag.get("status") != "active":
            raise TableError("That table's tag is no longer in use.")
        if not svc.repo.cas_row(ORDERS, tenant_id, order_id, expected="pending",
                                fields={"status": "confirmed", "decided_by": member_id,
                                        "decided_at": svc.now().isoformat()}):
            raise TableError("Someone else just took this order.")
        try:
            bill = self._put_lines(tenant_id, tag, [{"name": i["name"], "quantity": int(i["quantity"]),
                                                     "unit_cents": int(i["unit_cents"])} for i in o["items"]], member_id)
        except Exception:
            svc.repo.cas_row(ORDERS, tenant_id, order_id, expected="confirmed",
                             fields={"status": "pending", "decided_by": None, "decided_at": None})
            raise
        svc.repo.cas_row(ORDERS, tenant_id, order_id, expected="confirmed", fields={"bill_id": bill["id"]})
        return bill

    def reject(self, tenant_id: str, order_id: str, member_id: str, reason: str = "") -> None:
        if not self.svc.repo.cas_row(ORDERS, tenant_id, order_id, expected="pending",
                                     fields={"status": "rejected", "decided_by": member_id,
                                             "decided_at": self.svc.now().isoformat(),
                                             "reject_reason": _text(reason, 120) or None}):
            raise TableError("This order has already been dealt with.")

    # ── what the customer's page shows ───────────────────────────────────────────────────────
    def page(self, code: str, caller: str) -> Optional[dict]:
        """For the tag's page: what this table can do, and how this browser's own requests stand."""
        tag = self._customer_tag(code)
        if not tag:
            return None
        tenant = tag["tenant_id"]
        cfg = self.settings(tenant)
        if not (cfg["calls"] or cfg["orders"]):
            return None
        svc = self.svc
        cutoff = svc.now() - timedelta(hours=6)
        calls = [c for c in svc.repo.find_rows(CALLS, tenant, ("open", "accepted"), tag_id=tag["id"])]
        orders = [o for o in svc.repo.find_rows(ORDERS, tenant, ("pending", "confirmed", "rejected"), tag_id=tag["id"])
                  if o.get("caller_hash") == caller and _ts(o["created_at"]) > cutoff]
        return {"label": tag.get("label") or "", "calls_on": cfg["calls"], "orders_on": cfg["orders"],
                "waiter": svc.repo.staff_name(tenant, tag.get("bound_id")),
                "calls": [{"kind": c["kind"], "status": c["status"],
                           "by": svc.repo.staff_name(tenant, c.get("accepted_by")) if c.get("accepted_by") else None}
                          for c in calls],
                "orders": [{"status": o["status"], "total_cents": int(o["total_cents"]),
                            "summary": ", ".join(f"{i['quantity']} x {i['name']}" for i in o["items"][:4]),
                            "reason": o.get("reject_reason") or ""} for o in orders[-5:]],
                "bill": self.bill_view(tenant, tag) if tag.get("bound_type") == "table" else None}


def _ts(v):
    from vula.tap.service import _ts as ts
    return ts(v)

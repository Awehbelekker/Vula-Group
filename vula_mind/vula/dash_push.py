"""
vula/dash_push.py — the dashboard's notifications on a phone or computer (2026-10-27, PWA pass 3).

A device that turned notifications on (Settings › On this device › Notifications, or the menu)
is a row of vula_dash_push_subs (migration 228), tied to the signed-in person's team member by
their WhatsApp number. It gets the alerts that person already gets on WhatsApp — the same
choices (vula_team_members.notify), the same rules — so there is one place to change them:

  notify_team(event)                  vula/integrations/notify.py  → push_event()
  a paid order for the team           vula/api/yoco.py             → push_to_phones()
  an approval for its approvers       vula/commerce/approvals.py   → push_to_phones()

Sent with the coach app's VAPID keys and sender (vula/tap/push.py); without the keys nothing is
pushed and WhatsApp still alerts. The message is encrypted to the device (Web Push); none of it
is stored here. A device the push service says is gone is removed. Never raises.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

TABLE = "vula_dash_push_subs"

# What the notification says on the lock screen, and the dashboard page a tap opens (#/go/<page>).
TITLES = {
    "new_order": "New order", "order_paid": "New paid order", "approval": "Approval needed",
    "customer_unanswered": "A customer is waiting", "payment_received": "Payment received",
    "new_invoice": "New invoice", "low_stock": "Running low", "which_project": "Which project?",
    "followup_digest": "Emails waiting for a reply", "procurement_done": "Order placed",
    "project_margin": "Project margin", "bank_details_changed": "Check these bank details",
}
PAGES = {
    "new_order": "orders", "order_paid": "orders", "approval": "orders", "customer_unanswered": "inbox",
    "payment_received": "invoices", "new_invoice": "invoices", "low_stock": "stock",
    "which_project": "documents", "procurement_done": "stock", "project_margin": "projects",
    "bank_details_changed": "suppliers",
}
MAX_FAILURES = 5


def _db():
    from vula.commerce import service
    return service._client()


def _tail(phone) -> str:
    """The last 9 digits: the same number written 27…, 0… or +27 … matches."""
    return re.sub(r"\D", "", str(phone or ""))[-9:]


def clean(text: str, limit: int = 180) -> str:
    """WhatsApp text as a notification line: no *bold*/_italic_ marks, one line, short."""
    t = re.sub(r"[*_~`]", "", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def subscriptions(tenant_id: str) -> list:
    try:
        return _db().table(TABLE).select("*").eq("tenant_id", tenant_id).execute().data or []
    except Exception as exc:          # migration 228 not applied yet: no devices
        logger.debug("push subscriptions unreadable: %s", exc)
        return []


def _pusher():
    from config import settings
    from vula.tap.push import build_pusher
    return build_pusher(settings)


async def push_to_phones(tenant_id: str, phones: Iterable[str], title: str, body: str,
                         page: Optional[str] = None, tag: Optional[str] = None,
                         owner_fallback: bool = False) -> int:
    """Push to every device of the people with these WhatsApp numbers. owner_fallback: also a
    device whose login has no team member row, when it's an owner's or manager's. Returns sent."""
    try:
        pusher = _pusher()
        if not pusher:
            return 0
        want = {_tail(p) for p in phones or [] if _tail(p)}
        targets = [s for s in subscriptions(tenant_id)
                   if (s.get("member_phone") and _tail(s["member_phone"]) in want)
                   or (owner_fallback and not s.get("member_phone")
                       and (s.get("role") or "").lower() in ("owner", "manager"))]
        payload = {"title": title, "body": clean(body), "tag": tag or "vula",
                   "url": f"/#/go/{page}" if page else "/"}
        sent = 0
        for s in targets:
            outcome = await pusher.send(s, payload)
            _record(s, outcome)
            sent += outcome == "ok"
        return sent
    except Exception as exc:
        logger.info("dashboard push skipped for %s: %s", tenant_id, exc)
        return 0


async def push_event(tenant_id: str, event_type: str, message: str, phones: Iterable[str],
                     idem_key: Optional[str] = None, owner_fallback: bool = False) -> int:
    """notify_team's alert, on the devices of the people it went to on WhatsApp."""
    if idem_key:
        try:                          # a re-run scheduled alert isn't pushed twice
            from vula.api.whatsapp import _durable_dedup_claim
            if not _durable_dedup_claim(f"push:{tenant_id}:{idem_key}"):
                return 0
        except Exception:
            pass
    return await push_to_phones(tenant_id, phones, TITLES.get(event_type, "Vula"), message,
                                PAGES.get(event_type), tag=event_type, owner_fallback=owner_fallback)


def _record(sub: dict, outcome: str) -> None:
    try:
        q = _db().table(TABLE)
        if outcome == "gone" or (outcome == "error" and (sub.get("failures") or 0) + 1 >= MAX_FAILURES):
            q.delete().eq("id", sub["id"]).execute()
        elif outcome == "ok":
            q.update({"last_sent_at": datetime.now(timezone.utc).isoformat(), "failures": 0}).eq("id", sub["id"]).execute()
        else:
            q.update({"failures": (sub.get("failures") or 0) + 1}).eq("id", sub["id"]).execute()
    except Exception as exc:
        logger.debug("push bookkeeping skipped: %s", exc)


# ── Devices ───────────────────────────────────────────────────────────────────

def save_device(tenant_id: str, sub: dict, scope: dict, user_agent: str = "") -> dict:
    """Keep (or refresh) this device's subscription for the signed-in person (`scope` from
    tenant_auth.member_scope). One row per push endpoint: a device moved to another login or
    business takes the new owner."""
    member = (scope or {}).get("member") or {}
    keys = sub.get("keys") or {}
    row = {"tenant_id": tenant_id, "endpoint": sub["endpoint"], "p256dh": keys["p256dh"], "auth": keys["auth"],
           "member_id": member.get("id"), "member_phone": re.sub(r"\D", "", member.get("whatsapp") or "") or None,
           "role": (scope or {}).get("role") or member.get("role"), "user_email": (scope or {}).get("email"),
           "user_agent": (user_agent or "")[:200], "failures": 0}
    _db().table(TABLE).upsert(row, on_conflict="endpoint").execute()
    return row


def forget_device(tenant_id: str, endpoint: str) -> None:
    _db().table(TABLE).delete().eq("tenant_id", tenant_id).eq("endpoint", endpoint).execute()

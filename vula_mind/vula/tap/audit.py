"""Tap to Pay audit trail (kb_audit, migration 232, append-only): who did what to money.

Every owner and staff write on the tap side records one row after it succeeds: bills (create,
cancel, release, remind, close as paid another way / written off), tab items (add, remove), tabs
settings, tags, phones signed in or out, shares, go-live / pause. Customer numbers are never
written here. A failed audit write is logged and never blocks the action itself (the action has
already happened; losing a payment because the log was down would be worse)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from vula.tap.core import money as mo

log = logging.getLogger(__name__)


def owner_actor(identity: dict) -> str:
    return str(identity.get("email") or identity.get("user_id") or "owner")[:120]


def record(svc, tenant_id: str, *, actor_kind: str, actor: str, action: str,
           bill_id: Optional[str] = None, **detail: Any) -> None:
    try:
        svc.repo.add_audit({"tenant_id": tenant_id, "actor_kind": actor_kind, "actor": (actor or "")[:120],
                            "action": action, "bill_id": bill_id,
                            "detail": {k: v for k, v in detail.items() if v is not None}})
    except Exception:  # noqa: BLE001 — see module docstring
        log.exception("tap audit write failed (tenant=%s action=%s)", tenant_id, action)


def _r(cents) -> str:
    try:
        return mo.format_rands(int(cents))
    except (TypeError, ValueError):
        return ""


def summary(row: dict) -> str:
    """One plain sentence for the owner's activity list."""
    d, a = row.get("detail") or {}, row.get("action", "")
    what = f" ({d['description']})" if d.get("description") else ""
    amount = f" {_r(d['amount_cents'])}" if d.get("amount_cents") is not None else ""
    item = f"{d.get('quantity', 1)} × {d.get('description', 'item')}"
    return {
        "bill.create": f"Created a bill{amount}{what}",
        "bill.cancel": f"Cancelled a bill{amount}{what}",
        "bill.release": "Released a bill so another phone can pay it",
        "bill.remind": "Sent a payment reminder",
        "bill.close.paid_other": f"Marked a bill paid another way ({d.get('reason') or 'other'})",
        "bill.close.release": "Released an unpaid bill",
        "bill.close.write_off": "Wrote off an unpaid bill",
        "tab.item_add": f"Added {item} ({_r(d.get('line_cents'))}) to {d.get('tab') or 'a tab'}",
        "tab.item_void": f"Removed {item} ({_r(d.get('line_cents'))}) from {d.get('tab') or 'a tab'}",
        "tab.close": f"Closed an empty tab{(' for ' + d['tab']) if d.get('tab') else ''}",
        "tabs.settings": (f"Switched bar tabs {'on' if d.get('enabled') else 'off'}"
                          + (f" (reminder after {d.get('idle_hours')} h)" if d.get("enabled") else "")),
        "tag.create": f"Created a tag for {d.get('member') or 'a team member'}",
        "tag.replace": f"Replaced {d.get('member') or 'a team member'}'s tag (the old one stopped working)",
        "test.start": "Started a test payment",
        "mode.go_live": "Switched Tap to Pay live",
        "mode.pause": "Paused Tap to Pay",
        "shares.save": "Changed the team's shares",
        "reminders.set": f"Set unpaid-bill reminders to {d.get('max')}",
        "device.enrol_code": f"Issued a sign-in code for {d.get('member') or 'a team member'}",
        "device.enrol": "Set up a phone in Vula Pay",
        "device.revoke": "Signed a phone out of Vula Pay",
        "receipt.revoke": "Revoked a receipt link",
    }.get(a, a)


def view(svc, tenant_id: str, limit: int = 100) -> list[dict]:
    names: dict = {}

    def who(r: dict) -> str:
        if r.get("actor_kind") != "staff":
            return r.get("actor") or r.get("actor_kind") or ""
        mid = r.get("actor") or ""
        if mid not in names:
            names[mid] = svc.repo.staff_name(tenant_id, mid) or "Team member"
        return names[mid]

    return [{"id": r["id"], "at": r.get("created_at"), "who": who(r), "kind": r.get("actor_kind"),
             "action": r.get("action"), "bill_id": r.get("bill_id"), "summary": summary(r)}
            for r in svc.repo.audit_log(tenant_id, max(1, min(int(limit), 500)))]

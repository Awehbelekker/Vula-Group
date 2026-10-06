"""
vula/owner_digest.py — the weekday-morning money digest to each owner (finance brief, capability 3).

07:00–09:00 SAST, Monday to Friday, one WhatsApp per owner per day: what was paid yesterday,
what is overdue (by customer), and what falls due in the next 7 days. Every figure is a DB
query here, in integer cents — the message is built in code, no model involved. Nothing to
say (no invoices moving) → nothing is sent. Outside the 24-hour window _send_reply falls back
to the approved notification template.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from vula.owner_advisor import SAST

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def _r(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


def build(tenant_id: str, today: date) -> Dict[str, Any]:
    db = _client()
    y0 = (today - timedelta(days=1)).isoformat()
    paid = [p for p in (db.table("commerce_invoice_payments")
                        .select("invoice_id,amount_cents,paid_at,payment_method")
                        .eq("tenant_id", tenant_id).gte("paid_at", y0).lt("paid_at", today.isoformat())
                        .limit(500).execute().data or [])
            if p.get("payment_method") != "credit_note"]     # a credit isn't money in
    open_rows = (db.table("commerce_invoices")
                 .select("id,invoice_number,customer_name,total_cents,total_paid_cents,due_date,status")
                 .eq("tenant_id", tenant_id).eq("direction", "outbound").eq("doc_type", "invoice")
                 .in_("status", ["sent", "overdue", "part_paid"]).limit(1000).execute().data or [])
    ids = list({p["invoice_id"] for p in paid if p.get("invoice_id")})
    names = {}
    if ids:
        for r in (db.table("commerce_invoices").select("id,customer_name,invoice_number")
                  .eq("tenant_id", tenant_id).in_("id", ids).execute().data or []):
            names[r["id"]] = r
    overdue: Dict[str, int] = {}
    upcoming_cents, upcoming_n = 0, 0
    for r in open_rows:
        left = max(0, int(r.get("total_cents") or 0) - int(r.get("total_paid_cents") or 0))
        if not left or not r.get("due_date"):
            continue
        due = date.fromisoformat(str(r["due_date"])[:10])
        if due < today:
            k = r.get("customer_name") or "Unknown customer"
            overdue[k] = overdue.get(k, 0) + left
        elif due <= today + timedelta(days=7):
            upcoming_cents += left
            upcoming_n += 1
    return {
        "paid": [{"customer": (names.get(p.get("invoice_id")) or {}).get("customer_name") or "?",
                  "invoice": (names.get(p.get("invoice_id")) or {}).get("invoice_number"),
                  "cents": int(p.get("amount_cents") or 0)} for p in paid],
        "overdue": sorted(overdue.items(), key=lambda kv: -kv[1]),
        "upcoming_cents": upcoming_cents, "upcoming_n": upcoming_n,
    }


def render(d: Dict[str, Any]) -> Optional[str]:
    if not (d["paid"] or d["overdue"] or d["upcoming_n"]):
        return None
    lines = ["☀️ *Good morning — your money today*"]
    if d["paid"]:
        total = sum(p["cents"] for p in d["paid"])
        lines.append(f"\n💰 *Paid yesterday:* {_r(total)}")
        lines += [f"• {p['customer']} — {_r(p['cents'])}" + (f" ({p['invoice']})" if p["invoice"] else "")
                  for p in d["paid"][:8]]
    if d["overdue"]:
        total = sum(c for _, c in d["overdue"])
        lines.append(f"\n🔴 *Overdue:* {_r(total)} from {len(d['overdue'])} customer"
                     f"{'s' if len(d['overdue']) != 1 else ''}")
        lines += [f"• {name} — {_r(c)}" for name, c in d["overdue"][:8]]
    if d["upcoming_n"]:
        lines.append(f"\n📅 *Due in the next 7 days:* {_r(d['upcoming_cents'])} "
                     f"({d['upcoming_n']} invoice{'s' if d['upcoming_n'] != 1 else ''})")
    lines.append("\nReply *who owes me* for detail, or *send reminders*.")
    return "\n".join(lines)


def due(now: datetime) -> bool:
    return now.weekday() < 5 and 7 <= now.hour < 9


async def send_all(now: Optional[datetime] = None) -> Dict[str, Any]:
    from config import settings
    if not settings.owner_digest_enabled:
        return {"sent": 0, "reason": "owner_digest_enabled is off"}
    from vula import team_index
    from vula.api import tenants as _t
    from vula.api.whatsapp import _send_reply
    now = now or datetime.now(SAST)
    day = now.date().isoformat()
    try:
        tenants = [r["tenant_id"] for r in (_client().table("vula_tenant_config")
                   .select("tenant_id").execute().data or [])
                   if r.get("tenant_id") and _t.is_active(r["tenant_id"])]
    except Exception as exc:
        log.warning("digest: tenant list failed: %s", exc)
        return {"sent": 0}
    sent = 0
    for tid in tenants:
        try:
            text = render(build(tid, now.date()))
            if not text:
                continue
            for m in team_index.active_members(tid):
                if (m.get("role") or "") == "owner" and m.get("whatsapp"):
                    if await _send_reply(m["whatsapp"], text, tid,
                                         idem_key=f"digest:{tid}:{day}:{m['whatsapp']}"):
                        sent += 1
        except Exception as exc:
            log.warning("digest failed for %s: %s", tid, exc)
    return {"sent": sent, "day": day}

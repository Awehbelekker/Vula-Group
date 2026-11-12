"""
vula/owner_digest.py — the weekday-morning brief to each owner (finance brief, capability 3).

07:00–09:00 SAST, Monday to Friday, one WhatsApp per owner per day:

  today     what needs a person today — customers waiting for a person, follow-ups due, emails
            needing a reply, quotes Vula drafted, documents to check, today's bookings. The same
            rows as the dashboard's Today list (command_centre.compute_today), so a business
            that doesn't open the dashboard still sees them.
  money     what was paid yesterday, what is overdue (by customer), what falls due in the next
            7 days, and tax deadlines.

Every business type gets it (shops, project and service businesses alike). Every figure is a DB
query here, in integer cents — the message is built in code, no model involved. Nothing to
say → nothing is sent. Outside the 24-hour window _send_reply falls back to the approved
notification template. Each business can switch it on or off (vula/owner_briefs.py); one that
hasn't chosen follows settings.owner_digest_enabled.
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


def today_items(tenant_id: str, now: datetime) -> Dict[str, Any]:
    """The dashboard's Today list, counted for a WhatsApp line each. The owner gets the full
    list (scope full), the same as the owner sees on the dashboard."""
    from vula.api.command_centre import compute_today
    items = compute_today(_client(), tenant_id, {"full": True}, now).get("items") or []
    out: Dict[str, Any] = {"waiting": 0, "follow_ups": 0, "follow_ups_overdue": 0, "emails": 0,
                           "emails_urgent": 0, "quotes": 0, "quotes_cents": 0, "documents": 0,
                           "bookings": []}
    for it in items:
        kind = it.get("kind")
        if kind == "chat":
            out["waiting"] += 1
        elif kind == "follow_up":
            out["follow_ups"] += 1
            if "overdue" in (it.get("label") or ""):
                out["follow_ups_overdue"] += 1
        elif kind == "email":
            out["emails"] += 1
            if it.get("detail") == "urgent":
                out["emails_urgent"] += 1
        elif kind == "quote":
            out["quotes"] += 1
            out["quotes_cents"] += int(it.get("amount_cents") or 0)
        elif kind == "document":
            out["documents"] += 1
        elif kind == "booking":
            at = _when(it.get("at"))
            what = (it.get("label") or "").replace("Booking · ", "", 1) or "appointment"
            out["bookings"].append(f"{at} {what}" if at else what)
    return out


def _when(value: Any) -> str:
    """'09:30' in SAST for an ISO timestamp, or '' when it can't be read."""
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if t.tzinfo is None:
            return ""
        return t.astimezone(SAST).strftime("%H:%M")
    except Exception:
        return ""


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def build(tenant_id: str, today: date, now: Optional[datetime] = None) -> Dict[str, Any]:
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
    try:
        from vula.commerce.tax import digest_lines
        tax_lines = digest_lines(tenant_id, today)
    except Exception as exc:
        log.debug("digest tax lines skipped: %s", exc)
        tax_lines = []
    try:
        day = now or datetime.combine(today, datetime.min.time().replace(hour=7), tzinfo=SAST)
        today_list = today_items(tenant_id, day)
    except Exception as exc:
        log.debug("digest today list skipped: %s", exc)
        today_list = {}
    return {
        "today": today_list,
        "tax": tax_lines,
        "paid": [{"customer": (names.get(p.get("invoice_id")) or {}).get("customer_name") or "?",
                  "invoice": (names.get(p.get("invoice_id")) or {}).get("invoice_number"),
                  "cents": int(p.get("amount_cents") or 0)} for p in paid],
        "overdue": sorted(overdue.items(), key=lambda kv: -kv[1]),
        "upcoming_cents": upcoming_cents, "upcoming_n": upcoming_n,
    }


def _today_lines(t: Dict[str, Any]) -> list:
    out = []
    if t.get("waiting"):
        out.append(f"• {_n(t['waiting'], 'customer is', 'customers are')} waiting for a person")
    if t.get("follow_ups"):
        late = f" ({t['follow_ups_overdue']} overdue)" if t.get("follow_ups_overdue") else ""
        out.append(f"• {_n(t['follow_ups'], 'follow-up', 'follow-ups')} due{late}")
    if t.get("emails"):
        urgent = f" ({t['emails_urgent']} urgent)" if t.get("emails_urgent") else ""
        out.append(f"• {_n(t['emails'], 'email needs', 'emails need')} a reply{urgent}")
    if t.get("quotes"):
        out.append(f"• {_n(t['quotes'], 'quote', 'quotes')} drafted by Vula, {_r(t.get('quotes_cents') or 0)} "
                   f"— waiting for your approval")
    if t.get("documents"):
        out.append(f"• {_n(t['documents'], 'document', 'documents')} to check")
    if t.get("bookings"):
        out.append(f"• {_n(len(t['bookings']), 'booking', 'bookings')} today: " + ", ".join(t["bookings"][:6])
                   + (f" and {len(t['bookings']) - 6} more" if len(t["bookings"]) > 6 else ""))
    return out


def render(d: Dict[str, Any]) -> Optional[str]:
    today = _today_lines(d.get("today") or {})
    money = bool(d["paid"] or d["overdue"] or d["upcoming_n"] or d.get("tax"))
    if not (today or money):
        return None
    lines = ["☀️ *Good morning — here's your day*"]
    if today:
        lines.append("\n📋 *Needs you today:*")
        lines += today
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
    if d.get("tax"):
        lines.append("\n🧾 *Tax coming up:*")
        lines += d["tax"]
        lines.append("Reply *tax* for the estimate.")
    if d["overdue"] or d["upcoming_n"]:
        lines.append("\nReply *who owes me* for detail, or *send reminders*.")
    if today:
        lines.append("The full list is under *Today* on your dashboard.")
    return "\n".join(lines)


def due(now: datetime) -> bool:
    return now.weekday() < 5 and 7 <= now.hour < 9


async def send_all(now: Optional[datetime] = None) -> Dict[str, Any]:
    from vula import owner_briefs, team_index
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
        if not owner_briefs.enabled(tid, "morning"):
            continue
        try:
            text = render(build(tid, now.date(), now))
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

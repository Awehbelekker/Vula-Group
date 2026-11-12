"""
vula/meeting_brief.py — a short brief before a meeting, from the business's own records.

12 Nov 2026 (Ian): "Vula sees the company name … summary brief of what the meeting is about, based
on all data (emails, last responses on WhatsApp)." Step 2 of meeting prep. For the person a meeting
is with, in a few lines:

  last WhatsApp      the customer's last messages to the business line, with dates
  emails             recent emails from them waiting for a reply (vula_email_followups) and when
                     they last wrote (vula_email_contacts)
  money              open quotes and unpaid invoices for them — only for someone who may see money
                     (owner, manager, or Invoices/Finances access)
  orders             their last orders
  last meeting       the most recent meeting notes about them, and how many meetings before

Built in code from rows — no model, nothing invented; a section with nothing is left out, and a
brief with nothing says so. Only sent when the person asked for it (a brief on the reminder, or
"brief me on the Acme meeting"). Public research on the company is a later step.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from vula.when import SAST

log = logging.getLogger(__name__)

_GENERIC = {"the", "and", "pty", "ltd", "cc", "inc", "co", "company", "group", "meeting", "with",
            "site", "visit", "builders", "construction", "holdings", "trading", "services", "sa"}
_MONEY_SCOPES = {"invoices", "finances", "money", "payments", "books"}


def _client():
    from vula.commerce import service
    return service._client()


def _rows(fn) -> List[Dict[str, Any]]:
    try:
        return fn() or []
    except Exception as exc:
        log.debug("brief read skipped: %s", exc)
        return []


def _key_word(name: str) -> str:
    """The most telling word of a name ('Acme Builders (Pty) Ltd' → 'acme')."""
    words = [w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(w) > 2 and w not in _GENERIC]
    return words[0] if words else (name or "").strip().lower()


def _day(v: Any) -> str:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(SAST).strftime("%-d %b")
    except Exception:
        return str(v or "")[:10]


def _r(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


def may_see_money(tenant_id: str, phone: Optional[str]) -> bool:
    try:
        from core.skills.commerce_admin import _member_access
        access = _member_access(tenant_id, phone)
    except Exception:
        return False
    return access is None or bool(set(access) & _MONEY_SCOPES)


def gather(tenant_id: str, meeting: Dict[str, Any], money: bool) -> Dict[str, Any]:
    db = _client()
    who = (meeting.get("with_name") or "").strip()
    word = _key_word(who)
    like = f"%{word}%"
    phone = meeting.get("contact_phone")
    contact = None
    if not phone and word:
        found = _rows(lambda: db.table("commerce_contacts").select("phone,name,email")
                      .eq("tenant_id", tenant_id).ilike("name", like).limit(1).execute().data)
        contact = found[0] if found else None
        phone = (contact or {}).get("phone")
    out: Dict[str, Any] = {"who": who, "chats": [], "emails": [], "last_emailed": None, "quotes": [],
                           "invoices": [], "orders": [], "notes": None, "met_before": 0}
    if phone:
        sess = _rows(lambda: db.table("commerce_conversation_sessions").select("id")
                     .eq("tenant_id", tenant_id).eq("customer_phone", phone).limit(1).execute().data)
        if sess:
            msgs = _rows(lambda: db.table("commerce_conversation_messages").select("role,content,created_at")
                         .eq("session_id", sess[0]["id"]).eq("role", "user")
                         .order("created_at", desc=True).limit(3).execute().data)
            out["chats"] = list(reversed(msgs))
    if word:
        out["emails"] = _rows(lambda: db.table("vula_email_followups").select("subject,sender_name,sender,received_at,status")
                              .eq("tenant_id", tenant_id).eq("status", "open").ilike("sender_name", like)
                              .order("received_at", desc=True).limit(3).execute().data)
        seen = _rows(lambda: db.table("vula_email_contacts").select("name,email,last_seen")
                     .eq("tenant_id", tenant_id).ilike("name", like).order("last_seen", desc=True).limit(1).execute().data)
        out["last_emailed"] = seen[0].get("last_seen") if seen else None
        if money:
            docs = _rows(lambda: db.table("commerce_invoices")
                         .select("invoice_number,doc_type,total_cents,total_paid_cents,status,issue_date,due_date,direction")
                         .eq("tenant_id", tenant_id).ilike("customer_name", like)
                         .order("issue_date", desc=True).limit(30).execute().data)
            docs = [d for d in docs if d.get("direction") != "inbound"]
            out["quotes"] = [d for d in docs if d.get("doc_type") == "quote" and d.get("status") in ("draft", "sent")][:3]
            out["invoices"] = [d for d in docs if d.get("doc_type") == "invoice"
                               and d.get("status") in ("sent", "overdue", "part_paid")
                               and int(d.get("total_cents") or 0) > int(d.get("total_paid_cents") or 0)][:3]
        notes = _rows(lambda: db.table("vula_filed_documents").select("summary,created_at")
                      .eq("tenant_id", tenant_id).eq("category", "meeting_notes").ilike("summary", like)
                      .order("created_at", desc=True).limit(1).execute().data)
        out["notes"] = notes[0] if notes else None
        before = _rows(lambda: db.table("vula_meetings").select("id,starts_at")
                       .eq("tenant_id", tenant_id).ilike("with_name", like).neq("id", meeting.get("id") or "")
                       .neq("status", "cancelled")
                       .lt("starts_at", meeting.get("starts_at") or datetime.now(SAST).isoformat())
                       .limit(50).execute().data)
        out["met_before"] = len(before)
    if phone and money:
        out["orders"] = _rows(lambda: db.table("commerce_orders").select("display_id,total_cents,status,created_at")
                              .eq("tenant_id", tenant_id).eq("customer_phone", phone)
                              .order("created_at", desc=True).limit(3).execute().data)
    return out


def render(b: Dict[str, Any]) -> str:
    out = [f"🗂️ *Before you meet {b['who']}*"]
    if b["chats"]:
        out.append("💬 Last on WhatsApp:")
        out += [f"• {_day(m.get('created_at'))}: “{(m.get('content') or '').strip()[:140]}”" for m in b["chats"]]
    if b["emails"]:
        out.append("✉️ Emails waiting for a reply:")
        out += [f"• {_day(e.get('received_at'))}: {e.get('subject') or '(no subject)'}" for e in b["emails"]]
    elif b["last_emailed"]:
        out.append(f"✉️ Last emailed us {_day(b['last_emailed'])}.")
    if b["quotes"]:
        out.append("🧾 Open quotes: " + ", ".join(f"{q.get('invoice_number')} {_r(int(q.get('total_cents') or 0))}"
                                                 for q in b["quotes"]))
    if b["invoices"]:
        owed = sum(int(i.get("total_cents") or 0) - int(i.get("total_paid_cents") or 0) for i in b["invoices"])
        out.append(f"🔴 Unpaid: {_r(owed)} on " + ", ".join(str(i.get("invoice_number")) for i in b["invoices"]))
    if b["orders"]:
        out.append("📦 Last orders: " + ", ".join(f"{o.get('display_id') or ''} {_r(int(o.get('total_cents') or 0))} "
                                                 f"({_day(o.get('created_at'))})".strip() for o in b["orders"]))
    if b["notes"]:
        out.append(f"📝 Last meeting ({_day(b['notes'].get('created_at'))}): {(b['notes'].get('summary') or '').strip()[:220]}")
    if b["met_before"]:
        out.append(f"🤝 Met {b['met_before']} time{'s' if b['met_before'] != 1 else ''} before.")
    if len(out) == 1:
        out.append("Nothing on record for them yet — no chats, emails, quotes or past meetings.")
    return "\n".join(out)


def build(tenant_id: str, meeting: Dict[str, Any], viewer_phone: Optional[str]) -> str:
    return render(gather(tenant_id, meeting, may_see_money(tenant_id, viewer_phone)))

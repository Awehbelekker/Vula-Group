"""
vula/team_activity.py — what each person on the team has on and has done, from the rows that hold it.

12 Nov 2026 (Ian): "Can a manager say how does X's schedule look this week and see meetings and
activities … summary per rep or per team. Reporting?" One place reads it for the WhatsApp tool
(commerce_admin team_activity), the dashboard (Team › Activity, vula/api/team_activity.py) and
custom sheets (vula/commerce/sheet_builder.py meetings/bookings/follow_ups/meeting_notes):

  meetings        vula_meetings           created_by     starts_at
  bookings        commerce_bookings       booked_by      start_at
  meeting notes   vula_filed_documents    filed_by       created_at   (category meeting_notes)
  follow-ups      vula_reminders          created_by     due_at / completed_at
  contacts added  commerce_contacts       created_by     created_at
  call sheet      vula_call_sheets        rep_whatsapp   entries[].created_at
  check-ins       vula_checkins           person         at           (vula/checkins.py)

Quotes, invoices and orders count for the person who made them (created_by, migration 260);
a customer's own checkout, and anything from before, belongs to no one.
Counts are listed by name, never ranked. Every read is scoped to the tenant and a table that
isn't there counts as nothing (it never fails the answer). Times are SAST (vula/when.py).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from vula.when import SAST, now_sast

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def _ts(v: Any) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=SAST)
    except Exception:
        return None


# ── who ──────────────────────────────────────────────────────────────────────────────────

def phone_variants(phone: Optional[str]) -> List[str]:
    """Every way a number is stored across these tables: 2782…, +2782…, 082…"""
    from vula.team_index import digits_za
    d = digits_za(phone or "")
    if not d:
        return []
    out = {d, "+" + d}
    if d.startswith("27"):
        out.add("0" + d[2:])
    if phone:
        out.add(phone.strip())
    return sorted(out)


def members(tenant_id: str, role: Optional[str] = None, team: Optional[str] = None) -> List[Dict[str, Any]]:
    from vula.team_index import active_members
    rows = [m for m in active_members(tenant_id) if m.get("whatsapp")]
    if role:
        rows = [m for m in rows if (m.get("role") or "").lower() == role.lower()]
    if team:
        rows = [m for m in rows if (m.get("team") or "") == team]
    return sorted(rows, key=lambda m: (m.get("name") or m.get("whatsapp") or "").lower())


def match_member(tenant_id: str, words: str) -> List[Dict[str, Any]]:
    """Team members whose name (any part) or number matches — all of them, so the caller can
    ask "which one?" when there are two Thabos."""
    from vula.team_index import digits_za
    w = (words or "").strip().lower()
    if not w:
        return []
    everyone = members(tenant_id)
    d = re.sub(r"\D", "", w)
    if len(d) >= 9:
        return [m for m in everyone if digits_za(m["whatsapp"])[-9:] == digits_za(d)[-9:]]
    name = re.sub(r"'s\b", "", w)
    exact = [m for m in everyone if (m.get("name") or "").lower() == name]
    if exact:
        return exact
    parts = [p for p in re.findall(r"[a-z]+", name) if len(p) > 1]
    return [m for m in everyone
            if parts and all(any(n.startswith(p) for n in re.findall(r"[a-z]+", (m.get("name") or "").lower()))
                             for p in parts)]


# ── when ─────────────────────────────────────────────────────────────────────────────────

def period(words: Optional[str], now: Optional[datetime] = None) -> Tuple[datetime, datetime, str]:
    """'this week' (default), 'last week', 'next week', 'today', 'tomorrow', 'this month',
    'last month' → (start, end, label), SAST, end exclusive. Weeks start on Monday."""
    now = (now or now_sast()).astimezone(SAST)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    w = (words or "").lower()
    if "tomorrow" in w:
        return day0 + timedelta(days=1), day0 + timedelta(days=2), "tomorrow"
    if "today" in w:
        return day0, day0 + timedelta(days=1), "today"
    if "month" in w:
        m0 = day0.replace(day=1)
        if "last" in w or "previous" in w:
            prev = (m0 - timedelta(days=1)).replace(day=1)
            return prev, m0, prev.strftime("%B")
        nxt = (m0 + timedelta(days=32)).replace(day=1)
        return m0, nxt, "this month"
    week0 = day0 - timedelta(days=day0.weekday())
    if "last" in w or "previous" in w:
        return week0 - timedelta(days=7), week0, "last week"
    if "next" in w:
        return week0 + timedelta(days=7), week0 + timedelta(days=14), "next week"
    return week0, week0 + timedelta(days=7), "this week"


# ── reading the rows ─────────────────────────────────────────────────────────────────────

def _rows(fn) -> List[Dict[str, Any]]:
    try:
        return fn() or []
    except Exception as exc:
        log.debug("team activity read skipped: %s", exc)
        return []


def collect(tenant_id: str, since: datetime, until: datetime,
            phones: Optional[List[str]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """All activity rows in [since, until) — for these numbers only when given."""
    db = _client()
    a, b = since.isoformat(), until.isoformat()

    def q(table, cols, person, date_col):
        query = db.table(table).select(cols).eq("tenant_id", tenant_id).gte(date_col, a).lt(date_col, b)
        if phones is not None:
            query = query.in_(person, phones)
        return query

    out = {
        "meetings": _rows(lambda: q("vula_meetings", "id,created_by,with_name,address,starts_at,status",
                                    "created_by", "starts_at").neq("status", "cancelled")
                          .order("starts_at").limit(1000).execute().data),
        "bookings": _rows(lambda: q("commerce_bookings", "id,booked_by,customer_name,service_name,start_at,status",
                                    "booked_by", "start_at").neq("status", "cancelled")
                          .order("start_at").limit(1000).execute().data),
        "notes": _rows(lambda: q("vula_filed_documents", "id,filed_by,summary,fields,created_at",
                                 "filed_by", "created_at").eq("category", "meeting_notes")
                       .order("created_at").limit(1000).execute().data),
        "done": _rows(lambda: q("vula_reminders", "id,created_by,text,due_at,completed_at,status",
                                "created_by", "completed_at").eq("status", "done").limit(1000).execute().data),
        "contacts": _rows(lambda: q("commerce_contacts", "id,created_by,name,created_at",
                                    "created_by", "created_at").limit(1000).execute().data),
    }
    # Follow-ups still open whose due time has passed (as of now, or the period's end if earlier).
    cut = min(until, now_sast()).isoformat()
    oq = (db.table("vula_reminders").select("id,created_by,text,due_at,status").eq("tenant_id", tenant_id)
          .eq("status", "open").lt("due_at", cut))
    if phones is not None:
        oq = oq.in_("created_by", phones)
    out["overdue"] = _rows(lambda: oq.order("due_at").limit(1000).execute().data)
    # Call-sheet entries written in the period (sheets from a little before, since an open sheet
    # collects a whole week).
    sq = (db.table("vula_call_sheets").select("rep_whatsapp,entries,created_at").eq("tenant_id", tenant_id)
          .gte("created_at", (since - timedelta(days=31)).isoformat()).lt("created_at", b))
    if phones is not None:
        sq = sq.in_("rep_whatsapp", phones)
    calls = []
    for s in _rows(lambda: sq.limit(500).execute().data):
        for e in s.get("entries") or []:
            t = _ts(e.get("created_at"))
            if t and since <= t < until and e.get("source") == "manual":   # log_meeting ones are notes
                calls.append({"rep_whatsapp": s.get("rep_whatsapp"), "text": e.get("text"), "created_at": e.get("created_at")})
    out["calls"] = calls
    from vula import checkins
    out["checkins"] = checkins.for_period(tenant_id, since, until, phones)
    # Quotes, invoices and orders a team member made (created_by, migration 260 — set from the
    # WhatsApp sender or the signed-in dashboard member; a customer's own checkout has none).
    docs = _rows(lambda: q("commerce_invoices", "id,created_by,doc_type,direction,total_cents,status,issue_date",
                           "created_by", "issue_date").limit(2000).execute().data)
    docs = [r for r in docs if r.get("created_by") and r.get("direction") != "inbound"
            and (r.get("status") or "") not in ("cancelled", "void")]
    out["quotes"] = [r for r in docs if r.get("doc_type") == "quote"]
    out["invoices"] = [r for r in docs if r.get("doc_type") == "invoice"]
    out["orders"] = [r for r in _rows(lambda: q("commerce_orders", "id,created_by,total_cents,status,created_at",
                                                "created_by", "created_at").limit(2000).execute().data)
                     if r.get("created_by") and (r.get("status") or "") not in ("cancelled", "abandoned")]
    return out


_PERSON_COL = {"meetings": "created_by", "bookings": "booked_by", "notes": "filed_by", "done": "created_by",
               "contacts": "created_by", "overdue": "created_by", "calls": "rep_whatsapp", "checkins": "person",
               "quotes": "created_by", "invoices": "created_by", "orders": "created_by"}


def _owner_key(phone: Any) -> str:
    """A stored person → a comparable key: an email as itself, a number by its last 9 digits."""
    from vula.team_index import digits_za
    w = str(phone or "").strip()
    return w.lower() if "@" in w else digits_za(w)[-9:]


def _keys(member: Dict[str, Any]) -> set:
    keys = {_owner_key(member.get("whatsapp"))}
    if member.get("email"):
        keys.add(str(member["email"]).lower())
    return {k for k in keys if k}


def _rands(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


def team_summary(tenant_id: str, since: datetime, until: datetime, role: Optional[str] = None,
                 now: Optional[datetime] = None, team: Optional[str] = None) -> List[Dict[str, Any]]:
    """One row of counts per active team member (of one team when given), listed by name."""
    now = now or now_sast()
    people = members(tenant_id, role, team)
    data = collect(tenant_id, since, until)
    rows = []
    for m in people:
        keys = _keys(m)

        def mine(kind: str) -> List[Dict[str, Any]]:
            return [r for r in data[kind] if _owner_key(r.get(_PERSON_COL[kind])) in keys]
        meetings = mine("meetings")
        rows.append({
            "member_id": m.get("id"), "name": m.get("name") or m["whatsapp"], "role": m.get("role"),
            "meetings_held": sum(1 for r in meetings if (_ts(r["starts_at"]) or now) < now),
            "meetings_coming": sum(1 for r in meetings if (_ts(r["starts_at"]) or now) >= now),
            "bookings": len(mine("bookings")), "notes": len(mine("notes")),
            "follow_ups_done": len(mine("done")), "follow_ups_overdue": len(mine("overdue")),
            "contacts_added": len(mine("contacts")), "call_sheet_entries": len(mine("calls")),
            "check_ins": len(mine("checkins")),
            **{f"{k}": len(mine(k)) for k in ("quotes", "invoices", "orders")},
            **{f"{k}_cents": sum(int(r.get("total_cents") or 0) for r in mine(k)) for k in ("quotes", "invoices", "orders")},
        })
    return rows


def person_week(tenant_id: str, member: Dict[str, Any], since: datetime, until: datetime,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or now_sast()
    who = phone_variants(member.get("whatsapp")) + ([str(member["email"]).lower()] if member.get("email") else [])
    d = collect(tenant_id, since, until, who)
    coming = sorted([("meeting", _ts(r["starts_at"]), r) for r in d["meetings"]]
                    + [("booking", _ts(r["start_at"]), r) for r in d["bookings"]],
                    key=lambda x: x[1] or now)
    from vula import checkins
    for c in d["checkins"][:30]:                 # photos sent in the hour after each check-in
        t = _ts(c.get("at"))
        c["photos"] = checkins.photos_after(tenant_id, member.get("whatsapp") or "", t) if t else 0
    by_meeting = {c.get("meeting_id"): c for c in d["checkins"] if c.get("meeting_id")}
    return {"name": member.get("name") or member.get("whatsapp"), "since": since, "until": until,
            "diary": coming, "notes": d["notes"], "done": d["done"], "overdue": d["overdue"],
            "contacts": d["contacts"], "calls": d["calls"], "checkins": d["checkins"],
            "quotes": d["quotes"], "invoices": d["invoices"], "orders": d["orders"],
            "checked_in": by_meeting}


def checkin_note(c: Optional[Dict[str, Any]]) -> str:
    """' · checked in 09:58 (40 m away)' for a meeting's check-in, or ''."""
    if not c:
        return ""
    t = _ts(c.get("at"))
    dist = c.get("distance_m")
    far = "" if dist is None else (f" ({dist / 1000:.1f} km away)" if dist >= 1000 else f" ({dist} m away)")
    pics = c.get("photos") or 0
    snaps = f", {pics} photo{'s' if pics != 1 else ''}" if pics else ""
    return f" · checked in {t.astimezone(SAST).strftime('%H:%M') if t else ''}{far}{snaps}"


# ── WhatsApp text ────────────────────────────────────────────────────────────────────────

def _when(t: Optional[datetime]) -> str:
    return t.astimezone(SAST).strftime("%a %-d %b %H:%M") if t else "?"


def person_text(p: Dict[str, Any], label: str, now: Optional[datetime] = None) -> str:
    now = now or now_sast()
    out = [f"📅 *{p['name']}* — {label}"]
    if p["diary"]:
        out.append("")
        for kind, t, r in p["diary"][:15]:
            if kind == "meeting":
                done = "✓ " if t and t < now else ""
                out.append(f"• {done}{_when(t)} — meeting with {r.get('with_name')}"
                           + (f", {r['address']}" if r.get("address") else "")
                           + checkin_note((p.get("checked_in") or {}).get(r.get("id"))))
            else:
                out.append(f"• {_when(t)} — booking: {r.get('service_name') or 'appointment'}"
                           + (f" for {r['customer_name']}" if r.get("customer_name") else ""))
        if len(p["diary"]) > 15:
            out.append(f"…and {len(p['diary']) - 15} more")
    else:
        out.append("No meetings or bookings in the diary.")
    done_bits = [(len(p.get("checkins") or []), "check-in", "check-ins"),
                 (len(p["notes"]), "meeting note", "meeting notes"), (len(p["done"]), "follow-up done", "follow-ups done"),
                 (len(p["calls"]), "call-sheet entry", "call-sheet entries"),
                 (len(p["contacts"]), "new contact", "new contacts")]
    done_bits = [f"{n} {one if n == 1 else many}" for n, one, many in done_bits if n]
    if done_bits:
        out.append("\n✅ " + ", ".join(done_bits))
    sales = [f"{len(p.get(k) or [])} {one if len(p.get(k) or []) == 1 else many} "
             f"({_rands(sum(int(r.get('total_cents') or 0) for r in p.get(k) or []))})"
             for k, one, many in (("quotes", "quote", "quotes"), ("invoices", "invoice", "invoices"),
                                  ("orders", "order", "orders")) if p.get(k)]
    if sales:
        out.append("💰 Made: " + ", ".join(sales))
    if p["overdue"]:
        out.append(f"\n🔴 {len(p['overdue'])} follow-up{'s' if len(p['overdue']) != 1 else ''} overdue:")
        out += [f"• {r.get('text')}" for r in p["overdue"][:5]]
    return "\n".join(out)


def team_text(rows: List[Dict[str, Any]], label: str, team_label: Optional[str] = None) -> str:
    if not rows:
        return (f"There's nobody in {team_label} with a WhatsApp number yet." if team_label
                else "There's nobody on the team list with a WhatsApp number yet.")
    out = [f"👥 *{team_label or 'Team'}* — {label}"]
    for r in rows:
        bits = [(r["meetings_held"], "meeting held", "meetings held"),
                (r["meetings_coming"], "coming up", "coming up"),
                (r.get("check_ins", 0), "check-in", "check-ins"),
                (r["bookings"], "booking", "bookings"), (r["notes"], "note", "notes"),
                (r["call_sheet_entries"], "call-sheet entry", "call-sheet entries"),
                (r["follow_ups_done"], "follow-up done", "follow-ups done"),
                (r["contacts_added"], "new contact", "new contacts")]
        bits += [(r.get(k, 0), f"{one} ({_rands(r.get(k + '_cents', 0))})", f"{many} ({_rands(r.get(k + '_cents', 0))})")
                 for k, one, many in (("quotes", "quote", "quotes"), ("invoices", "invoice", "invoices"),
                                      ("orders", "order", "orders"))]
        line = ", ".join(f"{n} {one if n == 1 else many}" for n, one, many in bits if n) or "nothing logged"
        if r["follow_ups_overdue"]:
            line += f" · 🔴 {r['follow_ups_overdue']} overdue"
        out.append(f"• *{r['name']}*: {line}")
    out.append("\nAsk e.g. *how does Thabo's week look?* for one person, or *team activity as a spreadsheet*.")
    return "\n".join(out)

"""One record per client: everything the business has on a person, found from any one handle.

What is known about a client was scattered — orders and invoices by phone or email, chats by
phone, the contact card by phone, the mailbox by email — and nothing joined it. A staff member
asking "what's the story with Sarah?" got a name, a phone, an order count and a lifetime value.
A client coming back after a month was a stranger to Vula apart from their last delivery address.

`resolve` finds the person from a phone number, an email address or a name, and gathers every
handle the business's own records tie together (a contact card or an invoice carrying both a
phone and an email links the two). `brief` reads the record: contact details, orders, invoices
and what is owed, the conversation so far, open follow-ups and how much email there has been.

Every figure is read from the database and formatted here. Nothing is computed by a model, and
the conversation summary (vula/client_memory.py) is never the source for an amount.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_PAID_OUT = ("pending_payment", "cancelled", "refunded")       # orders that aren't a sale
_OWING = ("sent", "overdue", "part_paid")                      # invoices with money still due
_MAX_ORDERS, _MAX_INVOICES, _MAX_MESSAGES, _MAX_REMINDERS = 5, 8, 6, 5


def _client():
    from vula.commerce import service
    return service._client()


def rands(cents: Any) -> str:
    return f"R{int(cents or 0) / 100:,.2f}"


def _digits(v: Any) -> str:
    return "".join(c for c in str(v or "") if c.isdigit())


def _tail(v: Any) -> str:
    """The last 9 digits — what a number is matched on, whatever spelling it was stored in."""
    d = _digits(v)
    return d[-9:] if len(d) >= 9 else ""


def _email(v: Any) -> str:
    e = str(v or "").strip().lower()
    return e if "@" in e else ""


def _day(v: Any) -> str:
    return str(v or "")[:10]


@dataclass
class Client:
    tenant_id: str
    name: str = ""
    phones: List[str] = field(default_factory=list)      # as stored, newest first
    emails: List[str] = field(default_factory=list)
    company: str = ""

    @property
    def tails(self) -> Set[str]:
        return {t for t in (_tail(p) for p in self.phones) if t}

    @property
    def phone(self) -> str:
        return self.phones[0] if self.phones else ""

    @property
    def key(self) -> str:
        """Stable handle for anything stored per client: their number, in 27… form."""
        d = _digits(self.phone)
        if not d:
            return ""
        return "27" + d[1:] if d.startswith("0") else d


# ── finding the person ───────────────────────────────────────────────────────────────────────

_SOURCES = (
    # table, name column, phone column, email column, extra columns
    ("commerce_contacts", "name", "phone", "email", "company"),
    ("commerce_invoice_clients", "name", "phone", "email", ""),
    ("commerce_orders", "customer_name", "customer_phone", "customer_email", ""),
    ("commerce_invoices", "customer_name", "customer_phone", "customer_email", ""),
    ("commerce_conversation_sessions", "customer_name", "customer_phone", "", ""),
)


def _rows(table: str, name_col: str, phone_col: str, email_col: str, extra: str,
          tenant_id: str, col: str, pattern: str) -> List[Dict[str, Any]]:
    cols = ",".join(c for c in (name_col, phone_col, email_col, extra) if c)
    try:
        rows = (_client().table(table).select(cols).eq("tenant_id", tenant_id)
                .ilike(col, pattern).limit(60).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — one source failing must not hide the others
        logger.debug("client lookup: %s skipped: %s", table, exc)
        return []
    return [{"name": (r.get(name_col) or "").strip(), "phone": r.get(phone_col) or "",
             "email": _email(r.get(email_col)) if email_col else "",
             "company": (r.get(extra) or "").strip() if extra else ""}
            for r in rows if isinstance(r, dict)]


def _search(tenant_id: str, kind: str, value: str) -> List[Dict[str, Any]]:
    """Records matching one handle, across every source."""
    out: List[Dict[str, Any]] = []
    for table, name_col, phone_col, email_col, extra in _SOURCES:
        if kind == "phone":
            out += _rows(table, name_col, phone_col, email_col, extra, tenant_id, phone_col, f"%{value}")
        elif kind == "email" and email_col:
            out += _rows(table, name_col, phone_col, email_col, extra, tenant_id, email_col, value)
        elif kind == "name":
            out += _rows(table, name_col, phone_col, email_col, extra, tenant_id, name_col, f"%{value}%")
    if kind == "phone":       # the DB match is on a suffix; keep only real last-9 matches
        out = [r for r in out if _tail(r["phone"]) == value]
    # a staff member's own thread is keyed admin:{phone}; it isn't a client record
    return [r for r in out if not str(r["phone"]).startswith(("admin:", "webadmin:"))]


def _handles(r: Dict[str, Any]) -> Set[str]:
    return {h for h in (("p:" + _tail(r["phone"])) if _tail(r["phone"]) else "",
                        ("e:" + r["email"]) if r["email"] else "") if h}


def _group(records: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Records that share a phone or an email are one person."""
    groups: List[Dict[str, Any]] = []
    for r in records:
        hs = _handles(r)
        if not hs:
            continue
        hit = [g for g in groups if g["handles"] & hs]
        merged = {"handles": set(hs), "rows": [r]}
        for g in hit:
            merged["handles"] |= g["handles"]
            merged["rows"] += g["rows"]
            groups.remove(g)
        groups.append(merged)
    return [g["rows"] for g in groups]


def _as_client(tenant_id: str, rows: List[Dict[str, Any]]) -> Client:
    phones: List[str] = []
    emails: List[str] = []
    for r in rows:
        if _tail(r["phone"]) and _tail(r["phone"]) not in {_tail(p) for p in phones}:
            phones.append(r["phone"])
        if r["email"] and r["email"] not in emails:
            emails.append(r["email"])
    names = [r["name"] for r in rows if r["name"]]
    name = max(set(names), key=lambda n: (names.count(n), len(n))) if names else ""
    return Client(tenant_id=tenant_id, name=name, phones=phones, emails=emails,
                  company=next((r["company"] for r in rows if r["company"]), ""))


def _expand(tenant_id: str, client: Client) -> Client:
    """One more pass: an email found on their contact card may be on invoices that carry no
    phone, and the other way round."""
    rows: List[Dict[str, Any]] = []
    for t in client.tails:
        rows += _search(tenant_id, "phone", t)
    for e in client.emails:
        rows += _search(tenant_id, "email", e)
    return _as_client(tenant_id, rows) if rows else client


def resolve(tenant_id: str, query: str) -> Dict[str, Any]:
    """Find the client a phone number, email address or name refers to.

    {"client": Client}, or {"ambiguous": [labels]} when a name fits several people, or
    {"none": True}. A name is never guessed between two people — the caller asks which one."""
    q = (query or "").strip()
    if not tenant_id or not q:
        return {"none": True}
    if _email(q):
        rows = _search(tenant_id, "email", _email(q))
    elif _tail(q):
        rows = _search(tenant_id, "phone", _tail(q))
    else:
        rows = _search(tenant_id, "name", q)
    groups = _group(rows)
    if not groups:
        return {"none": True}
    if len(groups) > 1:
        labels = []
        for g in groups[:6]:
            c = _as_client(tenant_id, g)
            hint = f"…{_digits(c.phone)[-4:]}" if c.phone else (c.emails[0] if c.emails else "")
            labels.append(f"{c.name or 'No name'} ({hint})" if hint else (c.name or "No name"))
        return {"ambiguous": labels}
    return {"client": _expand(tenant_id, _as_client(tenant_id, groups[0]))}


def for_phone(tenant_id: str, phone: str) -> Optional[Client]:
    """The client writing from this number, or None. Used on the customer side, where the only
    handle trusted is the WhatsApp number the message came from — never a name or an email
    someone typed."""
    if not _tail(phone):
        return None
    rows = _search(tenant_id, "phone", _tail(phone))
    return _as_client(tenant_id, rows) if rows else None


# ── reading the record ───────────────────────────────────────────────────────────────────────

def _orders(tenant_id: str, c: Client) -> List[Dict[str, Any]]:
    from vula.commerce import service
    cols = "display_id,customer_phone,total_cents,status,created_at"
    seen: Dict[str, Dict[str, Any]] = {}
    for phone in c.phones:
        for o in service.orders_for_phone(tenant_id, phone, cols, limit=200):
            seen[str(o.get("display_id") or id(o))] = o
    return sorted(seen.values(), key=lambda o: str(o.get("created_at") or ""), reverse=True)


def _invoices(tenant_id: str, c: Client) -> List[Dict[str, Any]]:
    cols = ("id,invoice_number,doc_type,direction,status,total_cents,total_paid_cents,issue_date,"
            "due_date,created_at,customer_phone,customer_email")
    seen: Dict[str, Dict[str, Any]] = {}

    def add(col: str, pattern: str, keep) -> None:
        rows = (_client().table("commerce_invoices").select(cols).eq("tenant_id", tenant_id)
                .ilike(col, pattern).order("created_at", desc=True).limit(200).execute().data or [])
        for r in rows:
            if isinstance(r, dict) and keep(r):
                seen[str(r.get("id") or r.get("invoice_number"))] = r

    for t in c.tails:
        add("customer_phone", f"%{t}", lambda r, t=t: _tail(r.get("customer_phone")) == t)
    for e in c.emails:
        add("customer_email", e, lambda r, e=e: _email(r.get("customer_email")) == e)
    # only what the business billed this client — not a supplier's bill that names them
    rows = [r for r in seen.values() if (r.get("direction") or "outbound") == "outbound"]
    return sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)


def _conversation(tenant_id: str, c: Client) -> Dict[str, Any]:
    keys = [k for p in c.phones for k in _phone_spellings(p)]
    if not keys:
        return {}
    sessions = (_client().table("commerce_conversation_sessions").select("id,last_at,updated_at,agent_note,tags")
                .eq("tenant_id", tenant_id).in_("session_key", keys).execute().data or [])
    ids = [s["id"] for s in sessions if isinstance(s, dict) and s.get("id")]
    out: Dict[str, Any] = {}
    notes = [s.get("agent_note") for s in sessions if isinstance(s, dict) and s.get("agent_note")]
    if notes:
        out["staff_note"] = notes[0]
    tags = sorted({t for s in sessions if isinstance(s, dict) for t in (s.get("tags") or [])})
    if tags:
        out["tags"] = tags
    if ids:
        msgs = (_client().table("commerce_conversation_messages").select("role,content,created_at")
                .eq("tenant_id", tenant_id).in_("session_id", ids)
                .order("created_at", desc=True).limit(_MAX_MESSAGES).execute().data or [])
        if msgs:
            out["last_contact"] = _day(msgs[0].get("created_at"))
            who = {"user": "client", "assistant": "Vula", "agent": "staff"}
            out["recent_messages"] = [
                f"{_day(m.get('created_at'))} {who.get(m.get('role'), m.get('role'))}: "
                f"{(m.get('content') or '').strip()[:240]}" for m in reversed(msgs)]
    return out


def _phone_spellings(phone: str) -> List[str]:
    from vula.privacy_data import phone_variants
    return phone_variants(phone)


def _reminders(tenant_id: str, c: Client) -> List[str]:
    keys = [k for p in c.phones for k in _phone_spellings(p)]
    if not keys:
        return []
    rows = (_client().table("vula_reminders").select("text,due_at,status")
            .eq("tenant_id", tenant_id).in_("linked_contact_phone", keys)
            .order("due_at").limit(40).execute().data or [])
    open_ = [r for r in rows if isinstance(r, dict) and (r.get("status") or "open") not in ("done", "completed", "cancelled")]
    return [f"{(r.get('text') or '').strip()}" + (f" (due {_day(r.get('due_at'))})" if r.get("due_at") else "")
            for r in open_[:_MAX_REMINDERS]]


def _contact_card(tenant_id: str, c: Client) -> Dict[str, Any]:
    for t in c.tails:
        rows = (_client().table("commerce_contacts").select("name,phone,email,company,title,notes,tags,area")
                .eq("tenant_id", tenant_id).ilike("phone", f"%{t}").limit(5).execute().data or [])
        for r in rows:
            if isinstance(r, dict) and _tail(r.get("phone")) == t:
                return r
    return {}


def _email_activity(tenant_id: str, c: Client) -> List[str]:
    out = []
    for e in c.emails:
        rows = (_client().table("vula_email_contacts").select("email,message_count,last_seen")
                .eq("tenant_id", tenant_id).ilike("email", e).limit(1).execute().data or [])
        if rows and isinstance(rows[0], dict) and rows[0].get("message_count"):
            out.append(f"{e}: {int(rows[0]['message_count'])} emails, last on {_day(rows[0].get('last_seen'))}")
    return out


def _section(label: str, fn, missing: List[str]):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — say what couldn't be read rather than fail the lot
        logger.warning("client brief: %s failed: %s", label, exc)
        missing.append(label)
        return None


def brief(tenant_id: str, c: Client, *, money: bool = True, summary: Optional[str] = None) -> Dict[str, Any]:
    """Everything on file about this client, as a result a model can relay without doing any
    arithmetic. `money=False` leaves out invoices and amounts (a staff member whose access
    doesn't include Money). `not_read` lists any part that couldn't be loaded, so a gap is never
    read as "nothing there"."""
    missing: List[str] = []
    out: Dict[str, Any] = {"name": c.name or None, "phones": c.phones, "emails": c.emails}
    if c.company:
        out["company"] = c.company

    card = _section("contact card", lambda: _contact_card(tenant_id, c), missing) or {}
    for k in ("title", "area", "notes"):
        if card.get(k):
            out[k] = card[k]
    if card.get("tags"):
        out["contact_tags"] = card["tags"]

    orders = _section("orders", lambda: _orders(tenant_id, c), missing)
    if orders is not None:
        sales = [o for o in orders if o.get("status") not in _PAID_OUT]
        out["orders"] = {"count": len(sales), "first": _day(orders[-1].get("created_at")) if orders else None,
                         "last": _day(orders[0].get("created_at")) if orders else None}
        if money:
            out["orders"]["lifetime_value"] = rands(sum(int(o.get("total_cents") or 0) for o in sales))
        out["orders"]["recent"] = [
            {"order": o.get("display_id"), "date": _day(o.get("created_at")), "status": o.get("status"),
             **({"total": rands(o.get("total_cents"))} if money else {})} for o in orders[:_MAX_ORDERS]]

    if money:
        invoices = _section("invoices", lambda: _invoices(tenant_id, c), missing)
        if invoices is not None:
            today = date.today()
            owed, lines = 0, []
            for inv in invoices:
                is_invoice = (inv.get("doc_type") or "invoice") == "invoice"
                left = 0
                if is_invoice and inv.get("status") in _OWING:
                    left = max(0, int(inv.get("total_cents") or 0) - int(inv.get("total_paid_cents") or 0))
                    owed += left
                line = {"number": inv.get("invoice_number"), "type": inv.get("doc_type") or "invoice",
                        "date": _day(inv.get("issue_date") or inv.get("created_at")),
                        "status": inv.get("status"), "total": rands(inv.get("total_cents"))}
                if left:
                    line["still_owed"] = rands(left)
                    try:
                        days = (today - date.fromisoformat(_day(inv.get("due_date")))).days
                        if days > 0:
                            line["days_overdue"] = days
                    except (TypeError, ValueError):
                        pass
                lines.append(line)
            out["invoices"] = {"count": len(invoices), "total_owed": rands(owed), "recent": lines[:_MAX_INVOICES]}
    else:
        out["invoices"] = "not shown: your access doesn't include Money"

    convo = _section("conversations", lambda: _conversation(tenant_id, c), missing)
    if convo:
        out["conversation"] = convo
    if summary:
        out.setdefault("conversation", {})["earlier_summary"] = summary

    reminders = _section("follow-ups", lambda: _reminders(tenant_id, c), missing)
    if reminders:
        out["open_follow_ups"] = reminders
    emails = _section("email", lambda: _email_activity(tenant_id, c), missing)
    if emails:
        out["email"] = emails
    if missing:
        out["not_read"] = missing
    return out

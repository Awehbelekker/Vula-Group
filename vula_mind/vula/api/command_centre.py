"""
vula/api/command_centre.py — Home, Today and the All-activity feed (2026-10-17, UX redesign E).

    GET /v1/commerce/{tenant}/admin/activity   everything that happened, newest first, one feed
    GET /v1/commerce/{tenant}/admin/home       the redesigned Home's figures, front & back house
    GET /v1/commerce/{tenant}/admin/today      what needs a person today

Under /admin, so the tenant guard covers them; each ALSO requires a signed-in member whatever
ENFORCE_TENANT_AUTH says, and filters by that member's own scope (tenant_auth.member_scope —
the same rule the dashboard nav uses): a login without Money never receives money items, a
login without the Inbox never receives chat items.

POPIA: the feed carries labels, references and amounts — never a message body, an escalation
question, a reminder's text or a document's contents. Opening a chat goes to the Inbox, which
records the view in the merchant audit trail (audit_conversation_view below).

Every figure is computed here from the tables, in integer cents; nothing is computed by an LLM.
A source a tenant has no data in returns None (the screen hides it) rather than 0.

Separate module (like shopify_admin / manufacturing) rather than more of commerce.py's 7.8k lines.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query

from vula.api.tenant_auth import member_scope, scope_allows

log = logging.getLogger(__name__)
router = APIRouter(tags=["command-centre"])

SAST = ZoneInfo("Africa/Johannesburg")

# Tab ids (navConfig) that grant each kind of item. Kept in step with server._SECTION_RULES'
# money set (test_command_centre asserts they match).
MONEY_TABS = frozenset({"invoices", "expenses", "bank", "books", "payments", "taptopay",
                        "budget", "scanner", "finances"})
INBOX = frozenset({"inbox"})
ORDERS = frozenset({"orders"})
BOOKINGS = frozenset({"bookings"})
PEOPLE = frozenset({"customers", "contacts"})
FOLLOWUPS = frozenset({"followups"})
DOCUMENTS = frozenset({"documents"})
STOCK = frozenset({"stock", "products"})
VULA_LOG = frozenset({"agentlog"})

# Vula's own tool calls that move money (shown only to Money logins, on the back-house side).
_MONEY_TOOLS = {"record_payment", "create_invoice", "send_invoice", "convert_quote_to_invoice",
                "update_quote_status", "add_expense", "create_purchase_order", "send_purchase_order"}
_TOOL_LABELS = {
    "update_stock": "updated stock", "create_purchase_order": "drafted a purchase order",
    "send_purchase_order": "sent a purchase order", "update_po_status": "updated a purchase order",
    "create_discount_code": "created a discount code", "update_discount_code": "changed a discount code",
    "delete_discount_code": "removed a discount code", "cancel_booking": "cancelled a booking",
    "record_payment": "recorded a payment", "send_broadcast": "sent a broadcast",
    "draft_storefront_page": "drafted a storefront page", "add_storefront_section": "added to the storefront",
    "create_automation_rule": "set up an automation", "create_invoice": "drafted an invoice or quote",
    "send_invoice": "sent an invoice", "convert_quote_to_invoice": "turned a quote into an invoice",
    "update_quote_status": "updated a quote", "update_order_status": "updated an order",
    "create_booking": "made a booking", "add_expense": "logged an expense",
    "create_manual_order": "created an order", "create_product": "added a product",
    "update_product": "updated a product", "upsert_supplier": "saved a supplier",
    "create_subscription": "set up a subscription",
}
_ORDER_STATUS = {"pending_payment": "awaiting payment", "paid": "paid", "confirmed": "confirmed",
                 "packing": "packing", "dispatched": "dispatched", "delivered": "delivered",
                 "cancelled": "cancelled", "refunded": "refunded"}
_PAID_ORDER = {"paid", "confirmed", "packing", "dispatched", "delivered"}
_DOC_KIND = {"invoice": "Invoice", "quote": "Quote", "proforma": "Pro forma", "credit_note": "Credit note",
             "delivery_note": "Delivery note"}


def _client():
    from vula.commerce import service
    return service._client()


async def _scope(tenant_id: str, authorization: str) -> dict:
    """The signed-in member's scope, or 401/403 — always, not only when the guard is armed:
    this feed spans every part of the business, so it never answers an anonymous caller."""
    if not (authorization or "").removeprefix("Bearer ").strip():
        raise HTTPException(status_code=401, detail="Sign in required.")
    scope = await member_scope(authorization, tenant_id)
    if scope is None:
        raise HTTPException(status_code=403, detail="You don't have access to this workspace.")
    return scope


def _ts(value: Any) -> Optional[datetime]:
    """Supabase timestamps / dates → aware UTC datetime (None if unparsable)."""
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s if "T" in s or " " in s else f"{s}T00:00:00+00:00")
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ── The feed ──────────────────────────────────────────────────────────────────────────────
# A source = (name, tab ids that may see it, side, table, timestamp column, columns, builder).
# The builder turns one row into one item; it only ever reads the selected columns, and those
# never include a message body or free text.

def _phone(row: dict) -> Optional[str]:
    return row.get("customer_phone") or row.get("phone") or None


def _b_escalation(r: dict) -> dict:
    state = r.get("status") or "open"
    label = {"open": "A customer is waiting for a person", "answered": "A customer's question was answered",
             "expired": "A waiting question was closed"}.get(state, "A customer asked for a person")
    detail = f"Answered by {r['helper_name']}" if state == "answered" and r.get("helper_name") else None
    return {"type": "escalation", "actor": "vula", "label": label, "detail": detail, "status": state,
            "contact_id": _phone(r), "link": {"section": "inbox"}}


def _b_chat(r: dict) -> dict:
    who = r.get("customer_name") or "a customer"
    role, paused = r.get("last_role"), r.get("paused")
    detail = ("Taken over by your team" if paused else "Vula answered" if role == "assistant"
              else "Answered by your team" if role == "agent" else "Waiting for a reply" if role == "user" else None)
    actor = "team" if paused or role == "agent" else "vula" if role == "assistant" else "customer"
    return {"type": "chat", "actor": actor, "label": f"Chat with {who}", "detail": detail,
            "contact_id": r.get("customer_phone") or r.get("session_key"), "channel": r.get("channel"),
            "session_id": r.get("id"), "link": {"section": "inbox"}}


def _b_order(r: dict) -> dict:
    st = _ORDER_STATUS.get(r.get("status"), r.get("status") or "")
    return {"type": "order", "actor": "customer" if r.get("channel") in ("web", "whatsapp") else "team",
            "label": f"Order {r.get('display_id') or ''}".strip(), "detail": st or None,
            "amount_cents": int(r.get("total_cents") or 0), "contact_id": _phone(r), "status": r.get("status"),
            "channel": r.get("channel"), "link": {"section": "sell", "tab": "orders"}}


def _b_invoice(r: dict) -> dict:
    kind = _DOC_KIND.get(r.get("doc_type"), "Invoice")
    if r.get("direction") == "inbound":
        kind = "Supplier quote" if r.get("doc_type") == "quote" else "Supplier bill"
    return {"type": r.get("doc_type") or "invoice", "actor": "team" if (r.get("source") or "manual") == "manual" else "vula",
            "label": f"{kind} {r.get('invoice_number') or ''}".strip(), "detail": r.get("status"),
            "amount_cents": int(r.get("total_cents") or 0), "contact_id": _phone(r), "status": r.get("status"),
            "link": {"section": "money", "tab": "invoices"}}


def _b_expense(r: dict) -> dict:
    return {"type": "expense", "actor": "team" if (r.get("source") or "manual") in ("manual", "dashboard") else "vula",
            "label": f"Expense · {r.get('supplier') or r.get('category') or 'unnamed'}", "detail": r.get("status"),
            "amount_cents": int(r.get("amount_cents") or 0), "status": r.get("status"),
            "link": {"section": "money", "tab": "expenses"}}


def _b_document(r: dict) -> dict:
    review = bool(r.get("needs_review"))
    conf = r.get("match_confidence")
    bits = [r.get("category") or None, "needs review" if review else None,
            f"{round(float(conf) * 100)}% sure" if isinstance(conf, (int, float)) else None]
    return {"type": "document", "actor": "team" if r.get("source") == "dashboard" else "vula",
            "label": f"Filed {r.get('filename') or 'a document'}", "detail": " · ".join(b for b in bits if b) or None,
            "document_id": r.get("id"), "contact_id": _phone(r), "needs_review": review,
            "link": {"section": "operate", "tab": "documents"}}


def _b_reminder(r: dict) -> dict:
    due = _ts(r.get("due_at"))
    return {"type": "follow_up", "actor": "team", "label": "Follow-up set",
            "detail": f"Due {due.astimezone(SAST).strftime('%-d %b %H:%M')}" if due else None,
            "status": r.get("status"), "contact_id": r.get("linked_contact_phone"),
            "link": {"section": "people", "tab": "followups"}}


def _b_booking(r: dict) -> dict:
    start = _ts(r.get("start_at"))
    return {"type": "booking", "actor": "team" if r.get("channel") == "dashboard" else "customer",
            "label": f"Booking · {r.get('service_name') or 'appointment'}",
            "detail": start.astimezone(SAST).strftime("%-d %b %H:%M") if start else None,
            "status": r.get("status"), "contact_id": _phone(r), "link": {"section": "sell", "tab": "bookings"}}


def _b_consent(r: dict) -> dict:
    out = r.get("action") == "opted_out"
    return {"type": "consent", "actor": "customer", "label": "Opted out of messages" if out else "Opted in to messages",
            "detail": r.get("source"), "contact_id": _phone(r), "link": {"section": "people", "tab": "customers"}}


def _b_vula(r: dict) -> dict:
    tool = r.get("task") or ""
    return {"type": "vula_action", "actor": "vula", "label": f"Vula {_TOOL_LABELS.get(tool, 'used ' + tool.replace('_', ' '))}",
            "detail": None, "tool": tool, "link": {"section": "assistant-hub", "tab": "agentlog"}}


SOURCES: list[dict] = [
    {"name": "escalation", "tabs": INBOX, "side": "front", "table": "vula_escalations", "at": "created_at",
     "cols": "id,customer_phone,status,helper_name,created_at", "build": _b_escalation},
    {"name": "chat", "tabs": INBOX, "side": "front", "table": "commerce_conversation_sessions", "at": "last_at",
     "cols": "id,session_key,customer_phone,customer_name,channel,paused,last_role,last_at", "build": _b_chat},
    {"name": "order", "tabs": ORDERS, "side": "front", "table": "commerce_orders", "at": "created_at",
     "cols": "id,display_id,total_cents,status,channel,customer_phone,created_at", "build": _b_order},
    {"name": "booking", "tabs": BOOKINGS, "side": "front", "table": "commerce_bookings", "at": "created_at",
     "cols": "id,service_name,start_at,status,channel,customer_phone,created_at", "build": _b_booking},
    {"name": "follow_up", "tabs": FOLLOWUPS, "side": "front", "table": "vula_reminders", "at": "created_at",
     "cols": "id,due_at,status,linked_contact_phone,created_at", "build": _b_reminder},
    {"name": "consent", "tabs": PEOPLE, "side": "front", "table": "commerce_consent_log", "at": "created_at",
     "cols": "id,phone,action,source,created_at", "build": _b_consent},
    {"name": "invoice", "tabs": MONEY_TABS, "side": "back", "table": "commerce_invoices", "at": "created_at",
     "cols": "id,invoice_number,doc_type,direction,status,total_cents,source,customer_phone,created_at", "build": _b_invoice},
    {"name": "expense", "tabs": MONEY_TABS, "side": "back", "table": "commerce_expenses", "at": "created_at",
     "cols": "id,supplier,category,status,amount_cents,source,created_at", "build": _b_expense},
    {"name": "document", "tabs": DOCUMENTS, "side": "back", "table": "vula_filed_documents", "at": "created_at",
     "cols": "id,filename,category,needs_review,match_confidence,source,customer_phone,created_at", "build": _b_document},
    {"name": "vula", "tabs": VULA_LOG, "side": None, "table": "vula_reasoning_telemetry", "at": "created_at",
     "cols": "id,task,created_at", "build": _b_vula, "where": {"system": "vula-agent-tool"}},
]
# Contact filter: the phone column each source can be narrowed by (None = can't, skipped).
_PHONE_COL = {"escalation": "customer_phone", "chat": "customer_phone", "order": "customer_phone",
              "booking": "customer_phone", "follow_up": "linked_contact_phone", "consent": "phone",
              "invoice": "customer_phone", "document": "customer_phone"}


def _key(item: dict) -> tuple:
    return (item["at"], item["source"], item["ref"])


def _encode_cursor(item: dict) -> str:
    return f"{item['at']}|{item['source']}|{item['ref']}"


def _decode_cursor(cursor: str) -> Optional[tuple]:
    parts = (cursor or "").split("|")
    return tuple(parts) if len(parts) == 3 and _ts(parts[0]) else None


def visible_sources(scope: dict) -> list[dict]:
    """The sources this member may see — a source is never even queried otherwise."""
    return [s for s in SOURCES if scope_allows(scope, s["tabs"])]


def fetch_feed(db, tenant_id: str, scope: dict, *, limit: int = 30, cursor: str = "", side: str = "",
               actor: str = "", types: Optional[list] = None, contact: str = "", since: str = "", until: str = "") -> dict:
    """Merge the visible sources newest-first; cursor = the last item's (at, source, ref)."""
    limit = max(1, min(int(limit or 30), 100))
    after = _decode_cursor(cursor)
    items: list[dict] = []
    for src in visible_sources(scope):
        if contact and src["name"] not in _PHONE_COL:
            continue
        try:
            q = db.table(src["table"]).select(src["cols"]).eq("tenant_id", tenant_id)
            for k, v in (src.get("where") or {}).items():
                q = q.eq(k, v)
            if contact:
                q = q.eq(_PHONE_COL[src["name"]], contact)
            if since:
                q = q.gte(src["at"], since)
            if until:
                q = q.lte(src["at"], until)
            if after:
                q = q.lte(src["at"], after[0])
            rows = q.order(src["at"], desc=True).limit(limit + 25).execute().data or []
        except Exception as exc:  # a missing table/column must not blank the whole feed
            log.debug("activity source %s skipped for %s: %s", src["name"], tenant_id, exc)
            continue
        for r in rows:
            at = _ts(r.get(src["at"]))
            if not at:
                continue
            item = src["build"](r)
            item_side = src["side"] or ("back" if item.get("tool") in _MONEY_TOOLS else "front")
            # Vula's money actions need Money as well as the activity log
            if item.get("tool") in _MONEY_TOOLS and not scope_allows(scope, MONEY_TABS):
                continue
            item.update({"id": f"{src['name']}:{r.get('id')}", "source": src["name"], "ref": str(r.get("id")),
                         "side": item_side, "at": _iso(at)})
            items.append(item)
    if after:
        items = [i for i in items if _key(i) < after]
    if side in ("front", "back"):
        items = [i for i in items if i["side"] == side]
    if actor in ("vula", "team", "customer"):
        items = [i for i in items if i.get("actor") == actor]
    if types:
        items = [i for i in items if i["type"] in types or i["source"] in types]
    items.sort(key=_key, reverse=True)
    page = items[:limit]
    return {"items": page, "next_cursor": _encode_cursor(page[-1]) if len(items) > limit else None}


@router.get("/{tenant_id}/admin/activity")
async def admin_activity(tenant_id: str, authorization: str = Header(default=""),
                         limit: int = Query(30, ge=1, le=100), cursor: str = "", side: str = "", actor: str = "",
                         type: str = "", contact: str = "", since: str = "", until: str = ""):
    """Everything that happened in this business — chats, orders, money, documents, follow-ups,
    bookings, consent and Vula's own actions — newest first, filtered to this login's scope."""
    scope = await _scope(tenant_id, authorization)
    types = [t for t in type.split(",") if t] or None
    return fetch_feed(_client(), tenant_id, scope, limit=limit, cursor=cursor, side=side, actor=actor,
                      types=types, contact=contact, since=since, until=until)


# ── Viewing a customer's conversation is recorded (POPIA) ────────────────────────────────
_VIEWED: dict[tuple, float] = {}
_VIEW_WINDOW = 1800.0   # the Inbox re-reads an open thread every 8s; one audit row per 30 min


async def audit_conversation_view(tenant_id: str, session_id: str, authorization: str) -> None:
    """Best-effort: who opened which conversation, once per person per conversation per 30 min."""
    try:
        scope = await member_scope(authorization, tenant_id) if authorization else None
        who = (scope or {}).get("email") or "unknown"
        key = (tenant_id, who, session_id)
        now = time.monotonic()
        if _VIEWED.get(key, 0) > now:
            return
        if len(_VIEWED) > 5000:
            _VIEWED.clear()
        _VIEWED[key] = now + _VIEW_WINDOW
        from vula.api import merchant_audit
        merchant_audit.audit(tenant_id, {"email": (scope or {}).get("email")}, "conversation_viewed",
                             session_id=session_id)
    except Exception as exc:
        log.debug("conversation view audit skipped: %s", exc)


# ── Home ──────────────────────────────────────────────────────────────────────────────────

def _month_start_utc(now: datetime) -> datetime:
    local = now.astimezone(SAST)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as exc:
        log.debug("home figure skipped: %s", exc)
        return None


def _count(q) -> int:
    res = q.execute()
    return res.count if isinstance(res.count, int) else len(res.data or [])


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def first_reply_seconds(rows: list[dict]) -> Optional[int]:
    """Median seconds from a customer's message to the next reply (Vula or the team) in the
    same chat. rows: messages sorted by (session_id, created_at)."""
    waits, pending, last_session = [], None, None
    for r in rows:
        if r.get("session_id") != last_session:
            pending, last_session = None, r.get("session_id")
        at = _ts(r.get("created_at"))
        if not at:
            continue
        if r.get("role") == "user":
            pending = pending or at
        elif r.get("role") in ("assistant", "agent") and pending:
            waits.append((at - pending).total_seconds())
            pending = None
    m = _median(waits)
    return int(round(m)) if m is not None else None


def compute_home(db, tenant_id: str, scope: dict, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    month0 = _month_start_utc(now)
    week0 = now - timedelta(days=7)
    front: dict[str, Any] = {}
    back: dict[str, Any] = {}

    if scope_allows(scope, ORDERS):
        def _orders():
            rows = (db.table("commerce_orders").select("total_cents,status,created_at")
                    .eq("tenant_id", tenant_id).gte("created_at", _iso(now - timedelta(days=400)))
                    .limit(10000).execute().data or [])
            if not rows:
                return None
            open_n = sum(1 for o in rows if o.get("status") in ("pending_payment", "paid", "confirmed", "packing"))
            won = [o for o in rows if o.get("status") in _PAID_ORDER and (_ts(o.get("created_at")) or now) >= month0]
            return {"open_orders": {"value": open_n, "link": {"section": "sell", "tab": "orders"}},
                    "won_month": {"cents": sum(int(o.get("total_cents") or 0) for o in won), "count": len(won),
                                  "target_cents": None, "link": {"section": "sell", "tab": "orders"}}}
        front.update(_safe(_orders) or {})

    if scope_allows(scope, INBOX):
        def _chats():
            res = (db.table("vula_escalations").select("customer_phone,created_at", count="exact")
                   .eq("tenant_id", tenant_id).eq("status", "open").order("created_at").limit(3).execute())
            rows = res.data or []
            total = res.count if isinstance(res.count, int) else len(rows)
            return {"chats_waiting": {"value": total, "link": {"section": "inbox"},
                                      "hot": [{"contact_id": r.get("customer_phone"), "since": r.get("created_at")} for r in rows]}}
        front.update(_safe(_chats) or {})

        def _reply():
            rows = (db.table("commerce_conversation_messages").select("session_id,role,created_at")
                    .eq("tenant_id", tenant_id).gte("created_at", _iso(week0)).in_("role", ["user", "assistant", "agent"])
                    .order("session_id").order("created_at").limit(5000).execute().data or [])
            secs = first_reply_seconds(rows)
            return None if secs is None else {"first_reply": {"seconds": secs, "link": {"section": "inbox"}}}
        front.update(_safe(_reply) or {})

    if scope_allows(scope, PEOPLE):
        def _people():
            if not (db.table("commerce_contacts").select("id").eq("tenant_id", tenant_id).limit(1).execute().data):
                return None
            n = _count(db.table("commerce_contacts").select("id", count="exact").eq("tenant_id", tenant_id)
                       .gte("created_at", _iso(week0)).limit(1))
            return {"new_contacts": {"value": n, "link": {"section": "people", "tab": "customers"}}}
        front.update(_safe(_people) or {})

    if scope_allows(scope, MONEY_TABS):
        def _bank():
            rows = (db.table("commerce_bank_transactions").select("amount_cents,direction,match_status,txn_date")
                    .eq("tenant_id", tenant_id).gte("txn_date", month0.astimezone(SAST).date().isoformat())
                    .limit(10000).execute().data or [])
            any_bank = rows or db.table("commerce_bank_transactions").select("id").eq("tenant_id", tenant_id).limit(1).execute().data
            if not any_bank:
                return None
            live = [r for r in rows if r.get("match_status") != "ignored"]
            unmatched = _count(db.table("commerce_bank_transactions").select("id", count="exact").eq("tenant_id", tenant_id)
                               .in_("match_status", ["unmatched", "asked"]).limit(1))
            return {"money_in": {"cents": sum(abs(int(r.get("amount_cents") or 0)) for r in live if r.get("direction") == "in"),
                                 "link": {"section": "money", "tab": "bank"}},
                    "money_out": {"cents": sum(abs(int(r.get("amount_cents") or 0)) for r in live if r.get("direction") == "out"),
                                  "link": {"section": "money", "tab": "bank"}},
                    "bank_unmatched": {"value": unmatched, "link": {"section": "money", "tab": "bank"}}}
        back.update(_safe(_bank) or {})

        def _invoices():
            today = now.astimezone(SAST).date().isoformat()
            rows = (db.table("commerce_invoices").select("total_cents,status,due_date,doc_type,direction")
                    .eq("tenant_id", tenant_id).eq("doc_type", "invoice").in_("status", ["sent", "overdue", "part_paid"])
                    .limit(5000).execute().data or [])
            out = [r for r in rows if (r.get("direction") or "outbound") == "outbound"
                   and (r.get("status") == "overdue" or (r.get("due_date") and str(r["due_date"]) < today))]
            return {"invoices_overdue": {"value": len(out), "cents": sum(int(r.get("total_cents") or 0) for r in out),
                                         "link": {"section": "money", "tab": "invoices"}}}
        back.update(_safe(_invoices) or {})

        def _bills():
            today = now.astimezone(SAST).date()
            soon = (today + timedelta(days=7)).isoformat()
            exp = (db.table("commerce_expenses").select("amount_cents,due_date").eq("tenant_id", tenant_id)
                   .eq("status", "pending").lte("due_date", soon).not_.is_("due_date", "null").limit(2000).execute().data or [])
            inv = (db.table("commerce_invoices").select("total_cents,due_date,doc_type").eq("tenant_id", tenant_id)
                   .eq("direction", "inbound").neq("doc_type", "quote").not_.in_("status", ["paid", "cancelled"])
                   .lte("due_date", soon).not_.is_("due_date", "null").limit(2000).execute().data or [])
            rows = [int(r.get("amount_cents") or 0) for r in exp] + [int(r.get("total_cents") or 0) for r in inv]
            return {"bills_due": {"value": len(rows), "cents": sum(rows), "link": {"section": "money", "tab": "budget"}}}
        back.update(_safe(_bills) or {})

    if scope_allows(scope, DOCUMENTS):
        def _docs():
            n = _count(db.table("vula_filed_documents").select("id", count="exact").eq("tenant_id", tenant_id)
                       .eq("needs_review", True).limit(1))
            return {"documents_to_review": {"value": n, "link": {"section": "operate", "tab": "documents"}}}
        back.update(_safe(_docs) or {})

    if scope_allows(scope, STOCK):
        def _stock():
            # same rule as service.get_low_stock_products (the current Home): in stock, under 5
            rows = (db.table("commerce_products").select("id,stock_quantity,in_stock").eq("tenant_id", tenant_id)
                    .not_.is_("stock_quantity", "null").limit(5000).execute().data or [])
            if not rows:
                return None
            low = sum(1 for r in rows if r.get("in_stock") and (r.get("stock_quantity") or 0) < 5)
            return {"stock_alerts": {"value": low, "link": {"section": "sell", "tab": "stock"}}}
        back.update(_safe(_stock) or {})

    return {"front": front or None, "back": back or None,
            "month_days_left": _days_left(now), "as_of": _iso(now)}


def _days_left(now: datetime) -> int:
    local = now.astimezone(SAST).date()
    nxt = (local.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (nxt - local).days - 1


@router.get("/{tenant_id}/admin/home")
async def admin_home(tenant_id: str, authorization: str = Header(default="")):
    """The redesigned Home's figures, split front house / back house, only what this login may
    see; each figure says which list it came from (`link`)."""
    scope = await _scope(tenant_id, authorization)
    return compute_home(_client(), tenant_id, scope)


# ── Today ─────────────────────────────────────────────────────────────────────────────────

def compute_today(db, tenant_id: str, scope: dict, now: Optional[datetime] = None) -> dict:
    """What needs a person today, from the data that already holds it — escalations, reminders,
    email follow-ups, Vula's draft quotes, documents to review, today's bookings. No new task
    system. A sales rep's own reminders only (created_by = their WhatsApp number)."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(SAST)
    day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day1 = day0 + timedelta(days=1)
    items: list[dict] = []

    def add(rows, build):
        for r in rows or []:
            items.append(build(r))

    if scope_allows(scope, INBOX):
        add(_safe(lambda: db.table("vula_escalations").select("id,customer_phone,created_at").eq("tenant_id", tenant_id)
                  .eq("status", "open").order("created_at").limit(20).execute().data),
            lambda r: {"id": f"chat:{r['id']}", "kind": "chat", "label": "A customer is waiting for a person",
                       "contact_id": r.get("customer_phone"), "at": r.get("created_at"), "link": {"section": "inbox"}})

    own = (scope.get("member") or {}).get("whatsapp")
    rep_only = not scope.get("full") and not scope_allows(scope, FOLLOWUPS) and "rep-reminders" in (scope.get("access") or [])
    if scope_allows(scope, FOLLOWUPS) or (rep_only and own):
        def _rem():
            q = (db.table("vula_reminders").select("id,due_at,linked_contact_phone").eq("tenant_id", tenant_id)
                 .eq("status", "open").lte("due_at", _iso(day1)))
            if rep_only:
                q = q.eq("created_by", own)
            return q.order("due_at").limit(30).execute().data
        add(_safe(_rem), lambda r: {"id": f"follow_up:{r['id']}", "kind": "follow_up",
                                    "label": "Follow-up due" if (_ts(r.get("due_at")) or now) >= day0 else "Follow-up overdue",
                                    "contact_id": r.get("linked_contact_phone"), "at": r.get("due_at"),
                                    "link": {"section": "my-work", "tab": "rep-reminders"} if rep_only
                                    else {"section": "people", "tab": "followups"}})

    if scope_allows(scope, FOLLOWUPS):
        add(_safe(lambda: db.table("vula_email_followups").select("id,received_at,urgency").eq("tenant_id", tenant_id)
                  .eq("status", "open").order("received_at").limit(20).execute().data),
            lambda r: {"id": f"email:{r['id']}", "kind": "email", "label": "An email needs a reply",
                       "detail": "urgent" if r.get("urgency") == "high" else None, "at": r.get("received_at"),
                       "link": {"section": "people", "tab": "followups"}})

    if scope_allows(scope, MONEY_TABS):
        add(_safe(lambda: db.table("commerce_invoices").select("id,invoice_number,total_cents,created_at").eq("tenant_id", tenant_id)
                  .eq("doc_type", "quote").eq("status", "draft").neq("source", "manual")
                  .order("created_at").limit(20).execute().data),
            lambda r: {"id": f"quote:{r['id']}", "kind": "quote", "label": f"Quote {r.get('invoice_number') or ''} drafted by Vula — approve".replace("  ", " "),
                       "amount_cents": int(r.get("total_cents") or 0), "at": r.get("created_at"),
                       "link": {"section": "money", "tab": "invoices"}})

    if scope_allows(scope, DOCUMENTS):
        add(_safe(lambda: db.table("vula_filed_documents").select("id,filename,created_at").eq("tenant_id", tenant_id)
                  .eq("needs_review", True).order("created_at").limit(20).execute().data),
            lambda r: {"id": f"document:{r['id']}", "kind": "document", "label": f"Check {r.get('filename') or 'a document'}",
                       "document_id": r.get("id"), "at": r.get("created_at"), "link": {"section": "operate", "tab": "documents"}})

    if scope_allows(scope, BOOKINGS):
        add(_safe(lambda: db.table("commerce_bookings").select("id,service_name,start_at,customer_phone").eq("tenant_id", tenant_id)
                  .gte("start_at", _iso(day0)).lt("start_at", _iso(day1)).neq("status", "cancelled")
                  .order("start_at").limit(30).execute().data),
            lambda r: {"id": f"booking:{r['id']}", "kind": "booking", "label": f"Booking · {r.get('service_name') or 'appointment'}",
                       "contact_id": r.get("customer_phone"), "at": r.get("start_at"), "link": {"section": "sell", "tab": "bookings"}})

    return {"items": items, "date": day0.date().isoformat()}


@router.get("/{tenant_id}/admin/today")
async def admin_today(tenant_id: str, authorization: str = Header(default="")):
    scope = await _scope(tenant_id, authorization)
    return compute_today(_client(), tenant_id, scope)

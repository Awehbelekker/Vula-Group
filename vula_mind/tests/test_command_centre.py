"""Home, Today and the All-activity feed (vula/api/command_centre.py, UX redesign Phase E).

Run against a small in-memory stand-in for the Supabase client that really applies each filter
and returns only the selected columns — so tenant isolation, role filtering and "no message
body ever leaves" are tested on behaviour, not on mocked call shapes.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from vula.api import command_centre as cc

NOW = datetime(2026, 10, 17, 10, 30, tzinfo=timezone.utc)
SECRET = "SECRET-BODY-should-never-leave"
A, B = "harbour-deli", "other-shop"


class _Res:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.cols, self.count = db, table, [], None, None
        self._order, self._limit, self._neg = [], None, False

    def select(self, cols, count=None):
        self.cols = [c.strip() for c in cols.split(",")]
        self.count = count
        self.db.selected.append((self.table, tuple(self.cols)))
        return self

    @property
    def not_(self):
        self._neg = True
        return self

    def _add(self, fn):
        neg, self._neg = self._neg, False
        self.filters.append((lambda r: not fn(r)) if neg else fn)
        return self

    def eq(self, c, v): return self._add(lambda r: r.get(c) == v)
    def neq(self, c, v): return self._add(lambda r: r.get(c) != v)
    def gte(self, c, v): return self._add(lambda r: r.get(c) is not None and str(r[c]) >= str(v))
    def lte(self, c, v): return self._add(lambda r: r.get(c) is not None and str(r[c]) <= str(v))
    def lt(self, c, v): return self._add(lambda r: r.get(c) is not None and str(r[c]) < str(v))
    def in_(self, c, vs): return self._add(lambda r: r.get(c) in vs)
    def is_(self, c, v): return self._add(lambda r: r.get(c) is None if v == "null" else r.get(c) == v)

    def order(self, c, desc=False):
        self._order.append((c, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        rows = [r for r in self.db.tables.get(self.table, []) if all(f(r) for f in self.filters)]
        for c, desc in reversed(self._order):
            rows.sort(key=lambda r: str(r.get(c) or ""), reverse=desc)
        total = len(rows)
        rows = rows[: self._limit] if self._limit is not None else rows
        cols = self.cols or []
        return _Res([{k: r.get(k) for k in cols} for r in rows], total if self.count else None)


class FakeDB:
    def __init__(self, tables):
        self.tables, self.selected = tables, []

    def table(self, name):
        if name not in self.tables:
            raise RuntimeError(f"relation {name} does not exist")
        return _Q(self, name)


def ts(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def seed():
    t = {k: [] for k in ["vula_escalations", "commerce_conversation_sessions", "commerce_orders", "commerce_bookings",
                         "vula_reminders", "commerce_consent_log", "commerce_invoices", "commerce_expenses",
                         "vula_filed_documents", "vula_reasoning_telemetry", "commerce_conversation_messages",
                         "commerce_contacts", "commerce_bank_transactions", "commerce_products", "vula_email_followups"]}
    for tenant, n0 in ((A, 0), (B, 1000)):
        t["vula_escalations"] += [{"id": f"e{n0+i}", "tenant_id": tenant, "customer_phone": "27820000101", "status": "open",
                                   "question": SECRET, "answer": SECRET, "helper_name": None, "created_at": ts(5 + i)} for i in range(3)]
        t["commerce_conversation_sessions"] += [{"id": f"s{n0+i}", "tenant_id": tenant, "session_key": "27820000102",
                                                 "customer_phone": "27820000102", "customer_name": "Thandi", "channel": "whatsapp",
                                                 "paused": False, "last_role": "assistant", "last_message": SECRET,
                                                 "last_at": ts(10 + i)} for i in range(5)]
        t["commerce_orders"] += [{"id": f"o{n0+i}", "tenant_id": tenant, "display_id": f"HV-{1050+i}", "total_cents": 48200,
                                  "status": "paid", "channel": "whatsapp", "customer_phone": "27820000103",
                                  "delivery_notes": SECRET, "created_at": ts(20 + i)} for i in range(6)]
        t["commerce_invoices"] += [{"id": f"i{n0+i}", "tenant_id": tenant, "invoice_number": f"INV-{i}", "doc_type": "invoice",
                                    "direction": "outbound", "status": "overdue", "total_cents": 125000, "source": "manual",
                                    "customer_phone": "27820000104", "notes": SECRET, "due_date": "2026-10-01",
                                    "created_at": ts(30 + i)} for i in range(4)]
        t["commerce_expenses"] += [{"id": f"x{n0+i}", "tenant_id": tenant, "supplier": "Atlantic Dairy", "category": "stock",
                                    "status": "pending", "amount_cents": 33000, "source": "whatsapp", "description": SECRET,
                                    "due_date": "2026-10-20", "date": "2026-10-10", "created_at": ts(40 + i)} for i in range(3)]
        t["vula_filed_documents"] += [{"id": f"d{n0+i}", "tenant_id": tenant, "filename": f"bill-{i}.pdf", "category": "invoice",
                                       "needs_review": i == 0, "match_confidence": 0.62, "source": "whatsapp", "summary": SECRET,
                                       "fields": {"text": SECRET}, "customer_phone": None, "created_at": ts(50 + i)} for i in range(3)]
        t["vula_reminders"] += [{"id": f"r{n0+i}", "tenant_id": tenant, "text": SECRET, "due_at": ts(-60 * (i + 1)), "status": "open",
                                 "created_by": "27820000009" if i == 0 else "27820000001", "linked_contact_phone": "27820000105",
                                 "created_at": ts(60 + i)} for i in range(3)]
        t["commerce_bookings"] += [{"id": f"b{n0+i}", "tenant_id": tenant, "service_name": "Platter tasting", "start_at": ts(-120),
                                    "status": "confirmed", "channel": "web", "customer_phone": "27820000106", "notes": SECRET,
                                    "created_at": ts(70 + i)} for i in range(2)]
        t["commerce_consent_log"] += [{"id": f"c{n0}", "tenant_id": tenant, "phone": "27820000107", "action": "opted_out",
                                       "source": "stop_keyword", "created_at": ts(80)}]
        t["vula_reasoning_telemetry"] += [
            {"id": n0 + 1, "tenant_id": tenant, "system": "vula-agent-tool", "task": "record_payment",
             "extra": {"args": {"note": SECRET}}, "created_at": ts(2)},
            {"id": n0 + 2, "tenant_id": tenant, "system": "vula-agent-tool", "task": "update_order_status",
             "extra": {"args": {"note": SECRET}}, "created_at": ts(3)},
            {"id": n0 + 3, "tenant_id": tenant, "system": "vula-llm-router", "task": "chat", "created_at": ts(4)},
        ]
    return t


OWNER = {"full": True, "access": [], "member": None}
STAFF = {"full": False, "access": ["inbox", "orders", "bookings", "customers", "contacts", "followups"], "member": {}}
REP = {"full": False, "access": ["rep-contacts", "rep-reminders", "rep-expenses"], "member": {"whatsapp": "27820000009"}}


def all_pages(db, scope, **kw):
    items, cursor = [], ""
    for _ in range(50):
        page = cc.fetch_feed(db, A, scope, cursor=cursor, **kw)
        items += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    return items


# ── the feed ────────────────────────────────────────────────────────────────────────────────

def test_tenant_isolation_a_never_sees_b():
    db = FakeDB(seed())
    items = all_pages(db, OWNER, limit=7)
    assert items
    ids_b = {r["id"] for rows in seed().values() for r in rows if r.get("tenant_id") == B}
    assert not ({i["ref"] for i in items} & {str(x) for x in ids_b})


def test_owner_sees_every_kind_newest_first():
    items = all_pages(FakeDB(seed()), OWNER, limit=50)
    kinds = {i["source"] for i in items}
    assert kinds == {"escalation", "chat", "order", "booking", "follow_up", "consent", "invoice", "expense", "document", "vula"}
    ats = [i["at"] for i in items]
    assert ats == sorted(ats, reverse=True)


def test_staff_without_money_gets_no_money_items_and_money_tables_are_never_read():
    db = FakeDB(seed())
    items = all_pages(db, STAFF, limit=50)
    assert items
    assert not [i for i in items if i["source"] in ("invoice", "expense") or i.get("amount_cents") and i["source"] != "order"]
    read = {t for t, _ in db.selected}
    assert not read & {"commerce_invoices", "commerce_expenses", "vula_filed_documents", "vula_reasoning_telemetry"}


def test_money_actions_of_vula_need_money_too():
    scope = {"full": False, "access": ["agentlog"], "member": {}}
    items = all_pages(FakeDB(seed()), scope, limit=50)
    tools = {i.get("tool") for i in items}
    assert "update_order_status" in tools and "record_payment" not in tools


def test_a_sales_rep_gets_no_tenant_wide_items():
    assert all_pages(FakeDB(seed()), REP, limit=50) == []


def test_pagination_has_no_gaps_or_repeats():
    whole = all_pages(FakeDB(seed()), OWNER, limit=200)
    paged = all_pages(FakeDB(seed()), OWNER, limit=4)
    assert [i["id"] for i in paged] == [i["id"] for i in whole]
    assert len({i["id"] for i in paged}) == len(paged)


def test_filters_side_actor_type_contact():
    db = FakeDB(seed())
    assert {i["side"] for i in all_pages(db, OWNER, side="back", limit=50)} == {"back"}
    assert {i["actor"] for i in all_pages(db, OWNER, actor="vula", limit=50)} == {"vula"}
    assert {i["type"] for i in all_pages(db, OWNER, types=["order"], limit=50)} == {"order"}
    one = all_pages(db, OWNER, contact="27820000103", limit=50)
    assert one and {i["contact_id"] for i in one} == {"27820000103"}


def test_popia_no_message_body_ever_leaves():
    db = FakeDB(seed())
    payload = json.dumps(all_pages(db, OWNER, limit=50))
    assert SECRET not in payload
    body_cols = {"question", "answer", "content", "last_message", "text", "notes", "description", "summary", "fields",
                 "delivery_notes", "extra"}
    for table, cols in db.selected:
        assert not body_cols & set(cols), f"{table} selected {body_cols & set(cols)}"


@pytest.mark.asyncio
async def test_endpoint_needs_a_signed_in_member():
    with pytest.raises(HTTPException) as e:
        await cc.admin_activity(A, authorization="")
    assert e.value.status_code == 401
    with patch("vula.api.command_centre.member_scope", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as e:
            await cc.admin_activity(A, authorization="Bearer other-tenant")
    assert e.value.status_code == 403


def test_money_tabs_match_the_guard():
    from vula.api import server
    assert cc.MONEY_TABS == server._MONEY_TABS


# ── Home ──────────────────────────────────────────────────────────────────────────────────

def test_home_owner_front_and_back():
    h = cc.compute_home(FakeDB(seed()), A, OWNER, now=NOW)
    assert h["front"]["chats_waiting"]["value"] == 3
    assert h["front"]["won_month"]["cents"] == 6 * 48200
    assert h["back"]["invoices_overdue"] == {"value": 4, "cents": 4 * 125000, "link": {"section": "money", "tab": "invoices"}}
    assert h["back"]["documents_to_review"]["value"] == 1
    assert "hot" in h["front"]["chats_waiting"] and SECRET not in json.dumps(h)


def test_home_staff_without_money_has_no_back_money():
    h = cc.compute_home(FakeDB(seed()), A, STAFF, now=NOW)
    assert h["back"] is None
    assert "won_month" in h["front"]          # orders tab shows order totals already


def test_home_hides_what_the_tenant_has_no_data_for():
    t = seed()
    t["commerce_orders"] = []
    t["commerce_bank_transactions"] = []
    h = cc.compute_home(FakeDB(t), A, OWNER, now=NOW)
    assert "open_orders" not in h["front"] and "money_in" not in (h["back"] or {})


def test_first_reply_is_the_median_wait():
    rows = [
        {"session_id": "a", "role": "user", "created_at": ts(30)}, {"session_id": "a", "role": "assistant", "created_at": ts(29)},
        {"session_id": "b", "role": "user", "created_at": ts(30)}, {"session_id": "b", "role": "user", "created_at": ts(28)},
        {"session_id": "b", "role": "agent", "created_at": ts(20)},
        {"session_id": "c", "role": "user", "created_at": ts(10)},   # no reply yet: not counted
    ]
    assert cc.first_reply_seconds(rows) == (60 + 600) // 2


def test_days_left_in_month():
    assert cc._days_left(NOW) == 14   # 17 Oct → 14 more days after today


# ── Today ─────────────────────────────────────────────────────────────────────────────────

def test_today_owner_kinds_and_no_bodies():
    d = cc.compute_today(FakeDB(seed()), A, OWNER, now=NOW)
    kinds = {i["kind"] for i in d["items"]}
    assert {"chat", "follow_up", "document", "booking"} <= kinds
    assert SECRET not in json.dumps(d)


def test_today_rep_sees_only_their_own_reminders():
    d = cc.compute_today(FakeDB(seed()), A, REP, now=NOW)
    assert [i["kind"] for i in d["items"]] == ["follow_up"]


# ── audit ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewing_a_conversation_is_audited_once_per_window():
    cc._VIEWED.clear()
    with (patch("vula.api.command_centre.member_scope", AsyncMock(return_value={"email": "owner@x"})),
          patch("vula.api.merchant_audit.audit") as audit):
        await cc.audit_conversation_view(A, "s1", "Bearer t")
        await cc.audit_conversation_view(A, "s1", "Bearer t")
        await cc.audit_conversation_view(A, "s2", "Bearer t")
    assert audit.call_count == 2
    assert audit.call_args_list[0].args[2] == "conversation_viewed"

"""The pre-meeting brief: from the business's own rows, money only for those who may see it."""
import asyncio
import fnmatch
from datetime import datetime

import pytest

from vula import meeting_brief as mb
from vula import meetings as mt
from vula.when import SAST

NOW = datetime(2026, 11, 13, 8, 0, tzinfo=SAST)
REP, OWNER = "27820000002", "27820000001"
MEETING = {"id": "m9", "tenant_id": "t1", "created_by": REP, "with_name": "Acme Builders (Pty) Ltd",
           "address": "12 Main Rd", "starts_at": "2026-11-13T10:00:00+02:00", "status": "scheduled",
           "remind_minutes": 60, "reminded_at": None, "with_brief": True}


def _t(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.f, self.desc, self.col, self.n = store, name, [], False, None, None
        self.op, self.payload = "select", None

    def select(self, *_): return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def limit(self, n): self.n = n; return self
    def order(self, col, desc=False): self.col, self.desc = col, desc; return self
    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self
    def neq(self, k, v): self.f.append(lambda r: r.get(k) != v); return self
    def is_(self, k, v): self.f.append(lambda r: r.get(k) is None); return self
    def gt(self, k, v): self.f.append(lambda r: _t(r[k]) > _t(v)); return self
    def gte(self, k, v): self.f.append(lambda r: _t(r[k]) >= _t(v)); return self
    def lt(self, k, v): self.f.append(lambda r: _t(r[k]) < _t(v)); return self
    def lte(self, k, v): self.f.append(lambda r: _t(r[k]) <= _t(v)); return self
    def ilike(self, k, pat): self.f.append(lambda r: fnmatch.fnmatch((r.get(k) or "").lower(), pat.lower().replace("%", "*"))); return self

    @property
    def not_(self):
        outer = self

        class Neg:
            def is_(self, k, v):
                outer.f.append(lambda r: r.get(k) is not None)
                return outer
        return Neg()

    def execute(self):
        rows = [r for r in self.store.get(self.name, []) if all(f(r) for f in self.f)]
        if self.op == "update":
            for r in rows:
                r.update(self.payload)
        if self.col:
            rows = sorted(rows, key=lambda r: str(r.get(self.col) or ""), reverse=self.desc)
        return type("R", (), {"data": rows[:self.n] if self.n else rows})()


@pytest.fixture
def world(monkeypatch):
    store = {
        "vula_meetings": [dict(MEETING),
                          {"id": "m1", "tenant_id": "t1", "with_name": "Acme Builders", "starts_at": "2026-10-01T10:00:00+02:00", "status": "scheduled"},
                          {"id": "m2", "tenant_id": "t1", "with_name": "Acme", "starts_at": "2026-10-20T10:00:00+02:00", "status": "cancelled"}],
        "commerce_contacts": [{"tenant_id": "t1", "name": "Acme Builders", "phone": "27831112222"}],
        "commerce_conversation_sessions": [{"id": "s1", "tenant_id": "t1", "customer_phone": "27831112222"}],
        "commerce_conversation_messages": [
            {"session_id": "s1", "role": "user", "content": "Can you bring the oak samples?", "created_at": "2026-11-10T09:00:00+02:00"},
            {"session_id": "s1", "role": "assistant", "content": "Sure", "created_at": "2026-11-10T09:01:00+02:00"},
            {"session_id": "s1", "role": "user", "content": "And the price for 120 m²", "created_at": "2026-11-11T09:00:00+02:00"}],
        "vula_email_followups": [{"tenant_id": "t1", "status": "open", "sender_name": "Acme Builders", "subject": "Revised drawings",
                                  "received_at": "2026-11-12T15:00:00+02:00"}],
        "commerce_invoices": [
            {"tenant_id": "t1", "customer_name": "Acme Builders", "doc_type": "quote", "status": "sent", "invoice_number": "Q-12",
             "total_cents": 1_250_000, "total_paid_cents": 0, "issue_date": "2026-11-05", "direction": "outbound"},
            {"tenant_id": "t1", "customer_name": "Acme Builders", "doc_type": "invoice", "status": "overdue", "invoice_number": "INV-7",
             "total_cents": 400_000, "total_paid_cents": 100_000, "issue_date": "2026-10-01", "direction": "outbound"}],
        "commerce_orders": [],
        "vula_filed_documents": [{"tenant_id": "t1", "category": "meeting_notes", "summary": "Acme wants oak, budget tight",
                                  "created_at": "2026-10-01T11:00:00+02:00"}],
    }
    client = type("C", (), {"table": lambda s, n: _Q(store, n)})()
    monkeypatch.setattr(mb, "_client", lambda: client)
    monkeypatch.setattr(mt, "_client", lambda: client)
    import vula.commerce.service as service
    monkeypatch.setattr(service, "_client", lambda: client)
    access = {REP: ["rep-contacts"], OWNER: None}
    import core.skills.commerce_admin as ca
    monkeypatch.setattr(ca, "_member_access", lambda tid, phone: access.get(phone, ["inbox"]))
    return store


def test_the_owner_s_brief_has_everything(world):
    text = mb.build("t1", MEETING, OWNER)
    assert "🗂️ *Before you meet Acme Builders (Pty) Ltd*" in text
    assert "“Can you bring the oak samples?”" in text and "“And the price for 120 m²”" in text and "Sure" not in text
    assert "11 Nov:" in text and "12 Nov: Revised drawings" in text
    assert "Open quotes: Q-12 R12,500.00" in text and "Unpaid: R3,000.00 on INV-7" in text
    assert "Last meeting (1 Oct): Acme wants oak, budget tight" in text
    assert "Met 1 time before." in text                              # the cancelled one isn't counted


def test_a_rep_without_money_access_gets_no_figures(world):
    text = mb.build("t1", MEETING, REP)
    assert "Q-12" not in text and "Unpaid" not in text and "oak samples" in text


def test_nothing_on_record(world):
    text = mb.build("t1", {**MEETING, "id": "x", "with_name": "Brand New Co"}, OWNER)
    assert "Nothing on record for them yet" in text


def test_reminder_carries_the_brief_only_when_asked(world, monkeypatch):
    sent = []

    async def send(phone, text, tid, idem_key=None):
        sent.append(text)
        return True
    import vula.api.whatsapp as wa
    monkeypatch.setattr(wa, "_send_reply", send)
    world["vula_meetings"].append({**MEETING, "id": "m10", "with_brief": False, "with_name": "Plain Co"})
    asyncio.run(mt.send_due_reminders(datetime(2026, 11, 13, 9, 0, tzinfo=SAST)))
    with_brief = [t for t in sent if "Acme" in t][0]
    plain = [t for t in sent if "Plain Co" in t][0]
    assert "Before you meet Acme" in with_brief and "Before you meet" not in plain


def test_brief_me_on_the_meeting(world, monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    monkeypatch.setattr(mt, "now_sast", lambda: NOW)
    import vula.when as when
    monkeypatch.setattr(when, "now_sast", lambda: NOW)
    out = CommerceAdminSkill()._meeting("t1", {"action": "brief", "with": "Acme"}, {"phone": REP})
    assert "*Acme Builders (Pty) Ltd* — today at 10:00" in out["reply_verbatim"] and "oak samples" in out["reply_verbatim"]


def test_key_word():
    assert mb._key_word("Acme Builders (Pty) Ltd") == "acme" and mb._key_word("The Bongi Group") == "bongi"

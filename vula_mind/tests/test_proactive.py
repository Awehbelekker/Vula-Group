"""Asked-for messages reach people outside WhatsApp's 24-hour window via their own template."""
import asyncio
import re

import pytest

from vula import proactive
from vula.commerce import wa_templates


@pytest.fixture
def wa(monkeypatch):
    import vula.api.whatsapp as w
    state = {"text_ok": False, "window": True, "template_ok": True, "seen_fallback": None,
             "templates": [], "notify": 0, "claims": set()}

    async def send_reply(phone, text, tid, idem_key=None):
        state["seen_fallback"] = w.NOTIFY_FALLBACK.get()
        if not state["text_ok"] and state["window"]:
            w.WINDOW_CLOSED.set(True)
        return state["text_ok"]

    async def send_template(tid, phone, name, params, lang=None):
        state["templates"].append((name, params))
        return state["template_ok"]

    async def creds(tid, to=None):
        return {"phone_id": "p", "token": "t"}

    async def notify(creds, number, tid):
        state["notify"] += 1
        return True

    def claim(tid, key):
        if key in state["claims"]:
            return False
        state["claims"].add(key)
        return True
    monkeypatch.setattr(w, "_send_reply", send_reply)
    monkeypatch.setattr(w, "send_template", send_template)
    monkeypatch.setattr(w, "_send_creds", creds)
    monkeypatch.setattr(w, "_send_notify_template", notify)
    monkeypatch.setattr(w, "_claim_outbound", claim)
    return state


def _send(**kw):
    return asyncio.run(proactive.send("seaview", "27820000001", "full text", **kw))


def test_inside_the_window_the_full_text_goes(wa):
    wa["text_ok"] = True
    assert _send(suffix="reminder", params=["x"]) == "text" and wa["templates"] == []


def test_outside_the_window_its_own_template_goes_with_the_facts(wa):
    out = _send(suffix="meeting_reminder", params=["today at 10:00", "Acme\nBuilders", "12  Main Rd"])
    assert out == "template" and wa["seen_fallback"] is False         # no generic nudge on the way
    assert wa["templates"] == [("seaview_meeting_reminder", ["today at 10:00", "Acme Builders", "12 Main Rd"])]
    assert wa["notify"] == 0


def test_template_not_approved_yet_falls_back_to_the_generic_nudge(wa):
    wa["template_ok"] = False
    assert _send(suffix="morning_brief", params=["Thandi", "2 meetings"]) == "notify" and wa["notify"] == 1


def test_another_failure_sends_no_template(wa):
    wa["window"] = False
    assert _send(suffix="reminder", params=["x"]) is None and wa["templates"] == [] and wa["notify"] == 0


def test_the_template_goes_once_per_message(wa):
    assert _send(suffix="reminder", params=["x"], idem_key="reminder:1") == "template"
    assert _send(suffix="reminder", params=["x"], idem_key="reminder:1") == "template"
    assert len(wa["templates"]) == 1


def test_one_line():
    assert proactive.one_line("a\n\tb   c") == "a b c" and proactive.one_line("") == "—"
    assert len(proactive.one_line("x" * 500)) == 180


# ── the templates themselves ──────────────────────────────────────────────────────────────

def test_the_standard_set_has_the_proactive_templates_with_examples():
    names = {t["name"]: t for t in wa_templates.standard_templates("seaview", set(), "services")}
    for suffix, body, examples in wa_templates.PROACTIVE_TEMPLATES:
        t = names[f"seaview_{suffix}"]
        n = len(set(re.findall(r"\{\{(\d+)\}\}", body)))
        assert t["category"] == "UTILITY" and len(t["examples"]) == n
        assert not re.match(r"^\s*\{\{", body) and not re.search(r"\{\{\d+\}\}\W*$", body)   # Meta's rule


# ── the one-line summaries ────────────────────────────────────────────────────────────────

def test_morning_summary_line():
    from vula import owner_digest
    d = {"paid": [{"cents": 120000}], "overdue": [("Lerato", 450000)], "upcoming_cents": 0, "upcoming_n": 0,
         "today": {"follow_ups": 3, "meetings": ["10:00 Acme", "14:00 Bongi"], "quotes": 1}}
    assert owner_digest.summary_line(d) == ("3 follow-ups due, 1 quote to approve, 2 meetings, "
                                            "R4,500.00 overdue, R1,200.00 paid yesterday")


def test_weekly_summary_line():
    from vula import owner_advisor
    d = {"overdue": {"count": 2, "total_cents": 1_250_000}, "spending": [{}], "low_stock": [{}, {}, {}],
         "unanswered": {"unanswered": 1}, "waiting_docs": 0}
    assert owner_advisor.summary_line(d) == "R12,500 overdue from 2 customers, 1 spending jump, 3 low on stock, 1 unanswered question"


def test_meeting_reminder_uses_its_template(monkeypatch):
    from datetime import datetime
    from vula import meetings as mt
    from vula.when import SAST
    calls = []

    async def send(tid, phone, text, *, suffix, params, idem_key=None):
        calls.append((suffix, params))
        return "template"
    monkeypatch.setattr(proactive, "send", send)
    row = {"id": "m1", "tenant_id": "t1", "created_by": "27820000002", "with_name": "Acme", "address": None,
           "starts_at": "2026-11-13T10:00:00+02:00", "remind_minutes": 60, "reminded_at": None, "status": "scheduled"}

    class Q:
        def __getattr__(self, n): return lambda *a, **k: self
        @property
        def not_(self): return self
        def execute(self): return type("R", (), {"data": [row]})()
    monkeypatch.setattr(mt, "_client", lambda: type("C", (), {"table": lambda s, n: Q()})())
    assert asyncio.run(mt.send_due_reminders(datetime(2026, 11, 13, 9, 0, tzinfo=SAST))) == 1
    assert calls == [("meeting_reminder", ["today at 10:00", "Acme", "no address saved"])]


def test_template_replies_route_to_the_assistant():
    from core.skills.base import looks_like_owner_admin_question as q
    assert q("brief") and q("Weekly check") and q("meeting brief") and not q("brief history of tiles")

"""Tests for the staged overdue-invoice reminder cadence (2026-08-15, migration 131).

_process_overdue_invoices (vula/api/commerce.py) replaced a single one-shot "past due" nudge
with 4 stages (pre_due -3d / due 0d / firm +7d / escalated +14d), each claimed via a conditional
update matching the invoice's previous reminder_stage — the same idempotency shape
commerce_orders.followup_sent_at uses, generalized from a boolean to an ordered stage so a daily
job re-run (or a genuine race between two overlapping runs) can never double-send a stage.
"""
import copy
from datetime import date, timedelta

import pytest

import vula.api.commerce as commerce
from vula.commerce import service


TENANT = "off-the-hook"


def _days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Mimics postgrest-py enough for select/eq/in_/is_/update chains. select() snapshots
    (deep-copies) rows so mutating the live store after a select doesn't retroactively change
    what an in-flight caller already "read" — mirrors a real race between two overlapping
    workers, each holding their own stale snapshot."""

    def __init__(self, rows, live_store):
        self._rows = rows          # the snapshot this query operates over
        self._live = live_store    # the real, mutable backing list
        self._filters = []
        self._patch = None

    def select(self, *_a, **_kw):
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def eq(self, key, val):
        self._filters.append(("eq", key, val))
        return self

    def in_(self, key, vals):
        self._filters.append(("in", key, set(vals)))
        return self

    def is_(self, key, _val):
        self._filters.append(("is_null", key, None))
        return self

    def _matches(self, row):
        for op, key, val in self._filters:
            if op == "eq" and row.get(key) != val:
                return False
            if op == "in" and row.get(key) not in val:
                return False
            if op == "is_null" and row.get(key) is not None:
                return False
        return True

    def execute(self):
        if self._patch is None:
            matched = [copy.deepcopy(r) for r in self._rows if self._matches(r)]
            return _Result(matched)
        # Update: re-check the precondition against the LIVE store, not the snapshot —
        # this is what makes a stale claim correctly fail.
        matched = [r for r in self._live if r["id"] in {x["id"] for x in self._rows} and self._matches(r)]
        for r in matched:
            r.update(self._patch)
        return _Result([copy.deepcopy(r) for r in matched])


class _FakeClient:
    def __init__(self, invoices):
        self.store = invoices  # live, mutable

    def table(self, name):
        assert name == "commerce_invoices"
        return _FakeQuery(self.store, self.store)


def _inv(id_, days_over, status="sent", reminder_stage=None, doc_type="invoice", phone="27821234567"):
    return {
        "id": id_, "tenant_id": TENANT, "invoice_number": f"OTH-{id_}", "customer_name": "Thabo",
        "customer_phone": phone, "total_cents": 15000,
        "due_date": _days_ago(days_over), "doc_type": doc_type, "status": status,
        "reminder_stage": reminder_stage,
    }


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    monkeypatch.setattr(service, "_now", lambda: "2026-08-15T00:00:00Z")


@pytest.fixture
def sent_reply(monkeypatch):
    calls = []

    async def _fake_send(phone, message, tenant_id=""):
        calls.append((phone, message, tenant_id))
        return True

    monkeypatch.setattr("vula.api.whatsapp._send_reply", _fake_send)
    return calls


@pytest.fixture
def notified(monkeypatch):
    calls = []

    async def _fake_notify(tenant_id, event_type, message):
        calls.append((tenant_id, event_type, message))
        return 1

    monkeypatch.setattr("vula.integrations.notify.notify_team", _fake_notify)
    return calls


# ── _reminder_stage_for boundaries ──────────────────────────────────────────────

def test_reminder_stage_boundaries():
    f = commerce._reminder_stage_for
    assert f(-4) is None            # more than 3 days before due — too early
    assert f(-3) == "pre_due"
    assert f(-1) == "pre_due"
    assert f(0) == "due"
    assert f(6) == "due"
    assert f(7) == "firm"
    assert f(13) == "firm"
    assert f(14) == "escalated"
    assert f(90) == "escalated"


# ── Stage progression ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pre_due_stage_sends_and_claims_without_flipping_status(monkeypatch, sent_reply):
    invoices = [_inv("i1", days_over=-2, status="sent")]
    client = _FakeClient(invoices)
    monkeypatch.setattr(service, "_client", lambda: client)

    reminded = await commerce._process_overdue_invoices(TENANT)

    assert reminded == 1
    assert client.store[0]["reminder_stage"] == "pre_due"
    assert client.store[0]["status"] == "sent"  # not yet overdue
    assert len(sent_reply) == 1
    assert "due in 2 day" in sent_reply[0][1]


@pytest.mark.asyncio
async def test_due_stage_flips_status_to_overdue(monkeypatch, sent_reply):
    invoices = [_inv("i1", days_over=0, status="sent")]
    client = _FakeClient(invoices)
    monkeypatch.setattr(service, "_client", lambda: client)

    reminded = await commerce._process_overdue_invoices(TENANT)

    assert reminded == 1
    assert client.store[0]["reminder_stage"] == "due"
    assert client.store[0]["status"] == "overdue"


@pytest.mark.asyncio
async def test_firm_stage_fires_even_if_due_stage_was_skipped(monkeypatch, sent_reply):
    # An invoice that was already 8 days overdue the first time this job ever saw it
    # (e.g. created with a past due_date) — reminder_stage is still null, but the target
    # stage should be "firm" directly, not stuck waiting for "due" first.
    invoices = [_inv("i1", days_over=8, status="sent", reminder_stage=None)]
    client = _FakeClient(invoices)
    monkeypatch.setattr(service, "_client", lambda: client)

    reminded = await commerce._process_overdue_invoices(TENANT)

    assert reminded == 1
    assert client.store[0]["reminder_stage"] == "firm"
    assert client.store[0]["status"] == "overdue"


@pytest.mark.asyncio
async def test_escalated_stage_notifies_internal_team(monkeypatch, sent_reply, notified):
    invoices = [_inv("i1", days_over=20, status="overdue", reminder_stage="firm")]
    client = _FakeClient(invoices)
    monkeypatch.setattr(service, "_client", lambda: client)

    reminded = await commerce._process_overdue_invoices(TENANT)

    assert reminded == 1
    assert client.store[0]["reminder_stage"] == "escalated"
    assert len(notified) == 1
    assert notified[0][1] == "invoice_overdue_escalated"
    assert "OTH-i1" in notified[0][2]


@pytest.mark.asyncio
async def test_already_at_target_stage_is_a_noop(monkeypatch, sent_reply):
    invoices = [_inv("i1", days_over=2, status="overdue", reminder_stage="due")]
    client = _FakeClient(invoices)
    monkeypatch.setattr(service, "_client", lambda: client)

    reminded = await commerce._process_overdue_invoices(TENANT)

    assert reminded == 0
    assert client.store[0]["reminder_stage"] == "due"
    assert sent_reply == []


# ── Idempotency across repeated/overlapping runs ────────────────────────────────

@pytest.mark.asyncio
async def test_running_job_twice_only_sends_each_stage_once(monkeypatch, sent_reply, notified):
    invoices = [_inv("i1", days_over=0, status="sent")]
    client = _FakeClient(invoices)
    monkeypatch.setattr(service, "_client", lambda: client)

    first = await commerce._process_overdue_invoices(TENANT)
    second = await commerce._process_overdue_invoices(TENANT)

    assert first == 1
    assert second == 0
    assert len(sent_reply) == 1
    assert client.store[0]["reminder_stage"] == "due"


@pytest.mark.asyncio
async def test_stale_claim_loses_to_a_faster_writer(monkeypatch, sent_reply):
    """Simulates two overlapping workers both reading the same invoice before either writes:
    the first worker's update wins (its precondition still matches live state); a second
    update attempt built from the SAME stale snapshot must find the row has moved on and
    claim nothing."""
    invoices = [_inv("i1", days_over=0, status="sent", reminder_stage=None)]
    client = _FakeClient(invoices)

    # Worker A: reads current=None, claims successfully.
    q_a = client.table("commerce_invoices").select("*").eq("id", "i1")
    stale_snapshot = q_a.execute().data
    upd_a = (client.table("commerce_invoices").update({"reminder_stage": "due"})
             .eq("id", "i1").is_("reminder_stage", "null"))
    result_a = upd_a.execute()
    assert result_a.data  # won the claim

    # Worker B: built its own update from the SAME pre-write snapshot (current=None),
    # but the live row has already moved to "due" — its precondition must now fail.
    assert stale_snapshot[0]["reminder_stage"] is None  # confirms B's view was genuinely stale
    upd_b = (client.table("commerce_invoices").update({"reminder_stage": "due"})
             .eq("id", "i1").is_("reminder_stage", "null"))
    result_b = upd_b.execute()
    assert result_b.data == []  # lost the race — correctly claimed nothing


# ── Scope guards ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quotes_and_credit_notes_never_get_reminders(monkeypatch, sent_reply):
    invoices = [
        _inv("q1", days_over=5, status="sent", doc_type="quote"),
        _inv("c1", days_over=5, status="sent", doc_type="credit_note"),
    ]
    client = _FakeClient(invoices)
    monkeypatch.setattr(service, "_client", lambda: client)

    reminded = await commerce._process_overdue_invoices(TENANT)

    assert reminded == 0
    assert sent_reply == []


@pytest.mark.asyncio
async def test_missing_due_date_is_skipped(monkeypatch, sent_reply):
    invoices = [_inv("i1", days_over=5, status="sent")]
    invoices[0]["due_date"] = None
    client = _FakeClient(invoices)
    monkeypatch.setattr(service, "_client", lambda: client)

    reminded = await commerce._process_overdue_invoices(TENANT)

    assert reminded == 0
    assert sent_reply == []


@pytest.mark.asyncio
async def test_no_phone_still_claims_stage_but_sends_nothing(monkeypatch, sent_reply):
    invoices = [_inv("i1", days_over=0, status="sent", phone="")]
    client = _FakeClient(invoices)
    monkeypatch.setattr(service, "_client", lambda: client)

    reminded = await commerce._process_overdue_invoices(TENANT)

    assert reminded == 0
    assert client.store[0]["reminder_stage"] == "due"  # still claimed/staged
    assert sent_reply == []

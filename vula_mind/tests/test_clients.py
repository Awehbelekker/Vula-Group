"""One record per client (vula/clients.py): found from a phone, an email or a name; every handle
the business's own records tie together; every figure read from the database."""
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from core.skills import commerce_admin as ca
from tests.fake_supabase import FakeSupabase
from vula import clients
from vula.commerce import service

T = "deli"
SARAH, SARAH_LOCAL, SARAH_MAIL = "27821112222", "082 111 2222", "sarah@example.test"
OVERDUE = (date.today() - timedelta(days=12)).isoformat()


def _db():
    return FakeSupabase({
        "commerce_contacts": [
            {"tenant_id": T, "name": "Sarah Dlamini", "phone": SARAH, "email": SARAH_MAIL, "company": "Dlamini Catering",
             "title": "Owner", "notes": "Prefers deliveries before 10", "tags": ["wholesale"]},
            {"tenant_id": T, "name": "Sarah Botha", "phone": "27835550000", "email": ""},
            {"tenant_id": "other", "name": "Sarah Dlamini", "phone": "27829990000", "email": "x@other.test"},
        ],
        "commerce_invoice_clients": [],
        "commerce_orders": [
            {"tenant_id": T, "display_id": "HV-1001", "customer_name": "Sarah Dlamini", "customer_phone": "0821112222",
             "customer_email": "", "total_cents": 45000, "status": "delivered", "created_at": "2026-09-01T08:00:00Z"},
            {"tenant_id": T, "display_id": "HV-1040", "customer_name": "Sarah", "customer_phone": SARAH,
             "customer_email": "", "total_cents": 30050, "status": "paid", "created_at": "2026-10-05T08:00:00Z"},
            {"tenant_id": T, "display_id": "HV-1041", "customer_name": "Sarah", "customer_phone": SARAH,
             "customer_email": "", "total_cents": 99900, "status": "cancelled", "created_at": "2026-10-06T08:00:00Z"},
            {"tenant_id": T, "display_id": "HV-2000", "customer_name": "Sarah Botha", "customer_phone": "27835550000",
             "customer_email": "", "total_cents": 5000, "status": "paid", "created_at": "2026-10-07T08:00:00Z"},
        ],
        "commerce_invoices": [
            # billed to her email only — no phone on it
            {"tenant_id": T, "id": "i1", "invoice_number": "INV-104", "doc_type": "invoice", "direction": "outbound",
             "status": "overdue", "total_cents": 120000, "total_paid_cents": 20000, "issue_date": "2026-10-01",
             "due_date": OVERDUE, "created_at": "2026-10-01T08:00:00Z", "customer_name": "Dlamini Catering",
             "customer_phone": "", "customer_email": SARAH_MAIL},
            {"tenant_id": T, "id": "i2", "invoice_number": "INV-090", "doc_type": "invoice", "direction": "outbound",
             "status": "paid", "total_cents": 50000, "total_paid_cents": 50000, "issue_date": "2026-08-01",
             "due_date": "2026-08-15", "created_at": "2026-08-01T08:00:00Z", "customer_name": "Sarah Dlamini",
             "customer_phone": SARAH, "customer_email": ""},
            {"tenant_id": T, "id": "q1", "invoice_number": "QUO-12", "doc_type": "quote", "direction": "outbound",
             "status": "sent", "total_cents": 800000, "total_paid_cents": 0, "issue_date": "2026-10-20",
             "due_date": None, "created_at": "2026-10-20T08:00:00Z", "customer_name": "Sarah Dlamini",
             "customer_phone": SARAH, "customer_email": ""},
            # a supplier's bill that happens to carry her number: not something she owes us
            {"tenant_id": T, "id": "b1", "invoice_number": "SUP-7", "doc_type": "invoice", "direction": "inbound",
             "status": "sent", "total_cents": 700000, "total_paid_cents": 0, "issue_date": "2026-10-02",
             "due_date": OVERDUE, "created_at": "2026-10-02T08:00:00Z", "customer_name": "x",
             "customer_phone": SARAH, "customer_email": ""},
        ],
        "commerce_conversation_sessions": [
            {"id": "s1", "tenant_id": T, "session_key": SARAH, "customer_phone": SARAH, "customer_name": "Sarah",
             "agent_note": "Call before delivering", "tags": ["vip"]},
            {"id": "s-staff", "tenant_id": T, "session_key": f"admin:{SARAH}", "customer_phone": SARAH},
        ],
        "commerce_conversation_messages": [
            {"tenant_id": T, "session_id": "s1", "role": "user", "content": "Can you do platters for 40?",
             "created_at": "2026-10-04T09:00:00Z"},
            {"tenant_id": T, "session_id": "s1", "role": "assistant", "content": "Yes — I'll send a quote.",
             "created_at": "2026-10-04T09:00:05Z"},
        ],
        "vula_reminders": [
            {"tenant_id": T, "text": "Send platter quote", "due_at": "2026-10-21T08:00:00Z", "status": "open",
             "linked_contact_phone": SARAH},
            {"tenant_id": T, "text": "Old one", "due_at": "2026-09-01T08:00:00Z", "status": "done",
             "linked_contact_phone": SARAH},
        ],
        "vula_email_contacts": [{"tenant_id": T, "email": SARAH_MAIL, "message_count": 14, "last_seen": "2026-10-19T10:00:00Z"}],
    })


@pytest.fixture()
def db():
    d = _db()
    with patch.object(service, "_client", return_value=d):
        yield d


# ── finding the person ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("handle", [SARAH, SARAH_LOCAL, "+27 82 111 2222", SARAH_MAIL, "SARAH@Example.Test", "dlamini"])
def test_any_one_handle_finds_the_same_person_with_all_their_handles(db, handle):
    c = clients.resolve(T, handle)["client"]
    assert c.name == "Sarah Dlamini" and c.company == "Dlamini Catering"
    assert c.tails == {"821112222"} and c.emails == [SARAH_MAIL]
    assert c.key == "27821112222"


def test_a_name_that_fits_two_people_is_never_guessed(db):
    found = clients.resolve(T, "sarah")
    assert "client" not in found and len(found["ambiguous"]) == 2
    assert any("Dlamini" in x and "2222" in x for x in found["ambiguous"])
    assert any("Botha" in x and "0000" in x for x in found["ambiguous"])


def test_nobody_and_another_business_are_not_found(db):
    assert clients.resolve(T, "nobody here") == {"none": True}
    assert clients.resolve(T, "x@other.test") == {"none": True}       # another business's client
    assert clients.resolve(T, "") == {"none": True}


def test_a_staff_members_own_thread_is_not_a_client_record(db):
    assert clients.for_phone(T, SARAH).name == "Sarah Dlamini"
    db.rows["commerce_contacts"] = []
    db.rows["commerce_orders"] = []
    db.rows["commerce_invoices"] = []
    db.rows["commerce_conversation_sessions"] = [db.rows["commerce_conversation_sessions"][1]]   # only admin:…
    db.rows["commerce_conversation_sessions"][0]["customer_phone"] = f"admin:{SARAH}"
    assert clients.for_phone(T, SARAH) is None


# ── reading the record ──────────────────────────────────────────────────────────────────────

def test_brief_joins_orders_invoices_conversation_follow_ups_and_email(db):
    c = clients.resolve(T, SARAH)["client"]
    b = clients.brief(T, c, summary="2026-09-03: asked for gluten-free options.")
    assert b["name"] == "Sarah Dlamini" and b["notes"] == "Prefers deliveries before 10" and b["title"] == "Owner"
    # orders: the cancelled one isn't a sale; both spellings of her number are hers
    assert b["orders"]["count"] == 2 and b["orders"]["lifetime_value"] == "R750.50"
    assert [o["order"] for o in b["orders"]["recent"]] == ["HV-1041", "HV-1040", "HV-1001"]
    assert (b["orders"]["first"], b["orders"]["last"]) == ("2026-09-01", "2026-10-06")
    # invoices: the one billed to her email is included; the supplier bill is not
    inv = b["invoices"]
    assert inv["count"] == 3 and inv["total_owed"] == "R1,000.00"          # 1200 − 200 part-paid; the quote isn't owed
    numbers = {i["number"]: i for i in inv["recent"]}
    assert set(numbers) == {"INV-104", "INV-090", "QUO-12"}
    assert numbers["INV-104"]["still_owed"] == "R1,000.00" and numbers["INV-104"]["days_overdue"] == 12
    assert "still_owed" not in numbers["INV-090"] and "still_owed" not in numbers["QUO-12"]
    # conversation, follow-ups, email
    convo = b["conversation"]
    assert convo["last_contact"] == "2026-10-04" and convo["staff_note"] == "Call before delivering"
    assert convo["recent_messages"] == ["2026-10-04 client: Can you do platters for 40?",
                                        "2026-10-04 Vula: Yes — I'll send a quote."]
    assert convo["earlier_summary"].startswith("2026-09-03")
    assert b["open_follow_ups"] == ["Send platter quote (due 2026-10-21)"]
    assert b["email"] == [f"{SARAH_MAIL}: 14 emails, last on 2026-10-19"]
    assert "not_read" not in b


def test_a_member_without_money_access_sees_no_amounts(db):
    c = clients.resolve(T, SARAH)["client"]
    b = clients.brief(T, c, money=False)
    assert b["invoices"] == "not shown: your access doesn't include Money"
    assert "lifetime_value" not in b["orders"] and all("total" not in o for o in b["orders"]["recent"])
    assert "R" not in str(b["orders"])


def test_a_part_that_cannot_be_read_is_reported_not_treated_as_empty(db):
    c = clients.resolve(T, SARAH)["client"]
    db.fail.add("commerce_invoices")
    b = clients.brief(T, c)
    assert b["not_read"] == ["invoices"] and "invoices" not in b
    assert b["orders"]["count"] == 2            # the rest still comes back


# ── the staff tool ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customer_lookup_returns_the_whole_record(db):
    out = await ca.CommerceAdminSkill()._customer_lookup(T, "sarah dlamini", {"phone": None, "access": None})
    assert out["invoices"]["total_owed"] == "R1,000.00" and out["orders"]["count"] == 2
    assert out["conversation"]["last_contact"] == "2026-10-04"


@pytest.mark.asyncio
async def test_customer_lookup_asks_which_one_and_says_when_nobody_matches(db):
    skill = ca.CommerceAdminSkill()
    out = await skill._customer_lookup(T, "sarah", {"access": None})
    assert out["status"] == "need_info" and "Which one?" in out["message"] and "Botha" in out["message"]
    assert (await skill._customer_lookup(T, "zzz", {"access": None})) == {"message": "No client found for 'zzz'."}
    assert "error" in await skill._customer_lookup(T, "  ", {"access": None})


@pytest.mark.asyncio
async def test_customer_lookup_hides_money_from_a_restricted_member(db):
    out = await ca.CommerceAdminSkill()._customer_lookup(T, SARAH, {"phone": None, "access": ["customers"]})
    assert out["invoices"].startswith("not shown") and "lifetime_value" not in out["orders"]
    out = await ca.CommerceAdminSkill()._customer_lookup(T, SARAH, {"phone": None, "access": ["customers", "invoices"]})
    assert out["invoices"]["total_owed"] == "R1,000.00"


def test_client_figures_count_as_grounded_for_the_price_check():
    """An amount read from the client's record must not be thrown out as an invented price when
    another lookup ran in the same turn."""
    from core.skills.base import tool_source, unverified_prices
    sources = [tool_source("lookup_business_info", {"text": "Platters from R350.00"}),
               tool_source("customer_lookup", {"invoices": {"total_owed": "R1,000.00"}})]
    grounding = {"lookup_business_info", "competitor_check", "find_document", "email_thread_summary", "customer_lookup"}
    assert unverified_prices("Sarah still owes R1,000.00.", sources, grounding) == []
    assert unverified_prices("Sarah still owes R1,000.00.", sources, grounding - {"customer_lookup"}) != []

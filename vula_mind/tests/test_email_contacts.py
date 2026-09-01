"""Resolve a NAME to a real address before emailing anyone (2026-09-01).

email_draft took `to` as a raw string with no way to look anyone up, even though sync.py has
been building vula_email_contacts from every real message all along — 333 contacts across
digg-demo and off-the-hook. So "email Jack about the invoice", the natural thing to dictate in a
WhatsApp voice note, left the model to invent an address.

Both hazards below are taken from the REAL production contact table:

  Staci Brits   -> messaging-service@post.xero.com     a person's name on a robot's address
  Judy Downing  -> judy@digg-ct.co.za    (1376 msgs)   the same name on two addresses
  Judy Downing  -> judydowning0@gmail.com  (67 msgs)
"""
from unittest.mock import patch

import pytest

from vula.email_imap import contacts as ct
from core.skills.email_admin import EmailAdminSkill, TOOL_SPECS

TENANT = "digg-demo"

# Shaped exactly like the real rows.
ROWS = [
    {"name": "Judy Downing", "email": "judy@digg-ct.co.za", "domain": "digg-ct.co.za",
     "kind": "internal", "message_count": 1376, "last_seen": "2026-09-01"},
    {"name": "Judy Downing", "email": "judydowning0@gmail.com", "domain": "gmail.com",
     "kind": "external", "message_count": 67, "last_seen": "2026-08-01"},
    {"name": "Staci Brits", "email": "messaging-service@post.xero.com", "domain": "post.xero.com",
     "kind": "external", "message_count": 58, "last_seen": "2026-08-20"},
    {"name": "Donavan Daniels", "email": "ddaniels@oroafrica.com", "domain": "oroafrica.com",
     "kind": "external", "message_count": 68, "last_seen": "2026-08-15"},
    {"name": "Michelle Le Sueur", "email": "mlesueur@oroafrica.com", "domain": "oroafrica.com",
     "kind": "external", "message_count": 55, "last_seen": "2026-08-10"},
]


def _db(rows=ROWS):
    class _Q:
        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            return type("R", (), {"data": rows})()
    return type("C", (), {"table": lambda self, n: _Q()})()


# ── the ambiguity that must never be auto-resolved ──────────────────────────────

def test_two_people_with_the_same_name_both_come_back():
    with patch.object(ct, "_client", _db):
        out = ct.search_contacts(TENANT, "judy")
    addrs = [m["email"] for m in out]
    assert "judy@digg-ct.co.za" in addrs
    assert "judydowning0@gmail.com" in addrs, "must not silently drop the other Judy"


def test_a_robot_address_wearing_a_persons_name_is_flagged():
    with patch.object(ct, "_client", _db):
        out = ct.search_contacts(TENANT, "staci")
    assert out and out[0]["email"] == "messaging-service@post.xero.com"
    assert out[0]["looks_automated"] is True, "emailing Xero's robot instead of Staci"


@pytest.mark.parametrize("addr", [
    "no-reply@x.com", "noreply@x.com", "notifications@x.com",
    "messaging-service@post.xero.com", "mailer@x.com", "billing@x.com",
])
def test_automated_addresses_are_recognised(addr):
    assert ct.is_probably_automated(addr) is True


@pytest.mark.parametrize("addr", ["judy@digg-ct.co.za", "ddaniels@oroafrica.com"])
def test_real_people_are_not_flagged_as_automated(addr):
    assert ct.is_probably_automated(addr) is False


# ── ranking ─────────────────────────────────────────────────────────────────────

def test_exact_address_wins_outright():
    with patch.object(ct, "_client", _db):
        out = ct.search_contacts(TENANT, "judydowning0@gmail.com")
    assert out[0]["email"] == "judydowning0@gmail.com"


def test_company_search_returns_everyone_there():
    with patch.object(ct, "_client", _db):
        out = ct.search_contacts(TENANT, "oroafrica")
    assert {m["email"] for m in out} == {"ddaniels@oroafrica.com", "mlesueur@oroafrica.com"}


def test_the_company_is_reported_with_each_match():
    with patch.object(ct, "_client", _db):
        out = ct.search_contacts(TENANT, "donavan")
    assert out[0]["company"] == "oroafrica.com"
    assert out[0]["emails_exchanged"] == 68


def test_unknown_name_returns_nothing_rather_than_a_bad_guess():
    with patch.object(ct, "_client", _db):
        assert ct.search_contacts(TENANT, "zxqv") == []


def test_empty_query_returns_nothing():
    with patch.object(ct, "_client", _db):
        assert ct.search_contacts(TENANT, "") == []


def test_missing_table_degrades_quietly():
    def _boom():
        raise Exception("relation vula_email_contacts does not exist")
    with patch.object(ct, "_client", _boom):
        assert ct.search_contacts(TENANT, "judy") == []


# ── the tool and the draft guard ────────────────────────────────────────────────

def test_find_contact_tool_is_registered_and_tells_the_model_to_confirm():
    spec = next(t for t in TOOL_SPECS if t["function"]["name"] == "find_contact")
    d = spec["function"]["description"].lower()
    assert "before email_draft" in d
    assert "ask the user" in d


@pytest.mark.asyncio
async def test_find_contact_flags_multiple_matches_for_confirmation():
    skill = EmailAdminSkill()
    with patch("vula.email_imap.contacts.search_contacts", return_value=ROWS[:2]):
        out = await skill._dispatch("find_contact", {"query": "judy"}, TENANT,
                                    {"send_mode": "draft"})
    assert out["count"] == 2
    assert "ask which one" in out["note"].lower()


@pytest.mark.asyncio
async def test_find_contact_with_no_match_says_do_not_guess():
    skill = EmailAdminSkill()
    with patch("vula.email_imap.contacts.search_contacts", return_value=[]):
        out = await skill._dispatch("find_contact", {"query": "nobody"}, TENANT,
                                    {"send_mode": "draft"})
    assert out["count"] == 0
    assert "do not guess" in out["message"].lower()


@pytest.mark.asyncio
async def test_a_bare_name_is_refused_in_draft_mode_too():
    """The guard used to run only in send mode, so a draft addressed to 'Jack' sailed through."""
    skill = EmailAdminSkill()
    out = await skill._dispatch("email_draft",
                                {"to": "Jack", "subject": "Invoice", "body": "hi"},
                                TENANT, {"send_mode": "draft"})
    assert out["status"] == "need_info"
    assert "find_contact" in out["message"]


@pytest.mark.asyncio
async def test_a_real_address_still_drafts_normally():
    skill = EmailAdminSkill()
    with patch("vula.email_imap.service.save_draft",
               return_value={"status": "drafted"}) as saved:
        out = await skill._dispatch(
            "email_draft", {"to": "judy@digg-ct.co.za", "subject": "Hi", "body": "x"},
            TENANT, {"send_mode": "draft"})
    assert saved.called or out

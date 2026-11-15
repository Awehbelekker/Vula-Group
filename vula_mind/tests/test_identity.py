"""One Person for a staff member on WhatsApp and in the dashboard, and the access rules that hang
off it (vula/identity.py)."""
from unittest.mock import AsyncMock, patch

import pytest

from vula import identity
from vula.api import wa_login
from vula.api.tenant_auth import _WA_LOGIN_EMAIL, scope_from_rows

T = "deli"
REP = {"id": "m-rep", "name": "Thandi", "email": "", "whatsapp": "0821112222", "role": "sales_rep",
       "access": ["crm"], "active": True}
OWNER = {"id": "m-own", "name": "Ian", "email": "ian@deli.example", "whatsapp": "27835550000",
         "role": "owner", "access": [], "active": True}


# ── the WhatsApp-login hole ─────────────────────────────────────────────────────────────────

def test_wa_login_address_is_the_one_wa_login_issues():
    assert _WA_LOGIN_EMAIL.match(f"wa-27821112222@{wa_login.SYNTHETIC_EMAIL_DOMAIN}")


def test_restricted_member_signing_in_by_whatsapp_stays_restricted():
    """Their login email is wa-<number>@…, which is on no team row. It used to match nobody and
    fall through to full access."""
    s = scope_from_rows(T, "wa-27821112222@users.vula-ai.com", [{"tenant_id": T, "role": "staff"}], [REP])
    assert s["full"] is False and s["access"] == ["crm"] and s["member"]["id"] == "m-rep"


def test_unrelated_login_with_no_team_row_is_unchanged():
    s = scope_from_rows(T, "old@deli.example", [{"tenant_id": T, "role": "staff"}], [REP])
    assert s["full"] is True and s["member"] is None


def test_wa_login_for_a_number_not_on_the_team_matches_nobody():
    s = scope_from_rows(T, "wa-27829999999@users.vula-ai.com", [{"tenant_id": T, "role": "staff"}], [REP])
    assert s["member"] is None


# ── Person ──────────────────────────────────────────────────────────────────────────────────

def test_person_for_phone_matches_either_spelling_of_the_number():
    with patch("vula.team_index.active_members", return_value=[REP, OWNER]):
        p = identity.person_for_phone(T, "27821112222")
        o = identity.person_for_phone(T, "0835550000")
    assert (p.member_id, p.name, p.role, p.access, p.phone) == ("m-rep", "Thandi", "sales_rep", ["crm"], "27821112222")
    assert p.key == "m:m-rep" and p.web_session_key == "webadmin:m:m-rep"
    assert o.access is None                      # owner: full


def test_person_for_phone_unknown_or_failed_lookup_is_nobody():
    with patch("vula.team_index.active_members", return_value=[REP]):
        assert identity.person_for_phone(T, "27820000001") is None
        assert identity.person_for_phone(T, "") is None
    with patch("vula.team_index.active_members", side_effect=RuntimeError("db down")):
        assert identity.person_for_phone(T, "27821112222") is None


@pytest.mark.asyncio
async def test_person_for_login_is_the_same_person_as_on_whatsapp():
    scope = {"full": False, "access": ["crm"], "role": "sales_rep", "member": REP, "email": "x"}
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value=scope)), \
         patch("vula.api.master_auth._verify_jwt", AsyncMock(return_value={"id": "u-1"})), \
         patch("vula.team_index.active_members", return_value=[REP]):
        web = await identity.person_for_login("Bearer tok", T)
        wa = identity.person_for_phone(T, "0821112222")
    assert web.key == wa.key == "m:m-rep"
    assert web.access == ["crm"] and web.user_id == "u-1"


@pytest.mark.asyncio
async def test_owner_login_with_no_team_row_is_keyed_on_the_login():
    scope = {"full": True, "access": [], "role": "owner", "member": None, "email": "boss@deli.example"}
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value=scope)), \
         patch("vula.api.master_auth._verify_jwt", AsyncMock(return_value={"id": "u-9"})):
        p = await identity.person_for_login("Bearer tok", T)
    assert p.key == "u:u-9" and p.access is None and p.role == "owner"


@pytest.mark.asyncio
async def test_no_login_or_not_a_member_is_nobody():
    assert await identity.person_for_login("", T) is None
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value=None)):
        assert await identity.person_for_login("Bearer tok", T) is None


@pytest.mark.asyncio
async def test_inactive_member_has_an_empty_access_list_not_full_access():
    scope = {"full": False, "access": [], "role": "staff", "member": {**REP, "active": False}, "email": "x"}
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value=scope)), \
         patch("vula.api.master_auth._verify_jwt", AsyncMock(return_value={"id": "u-1"})):
        p = await identity.person_for_login("Bearer tok", T)
    assert p.access == []


# ── whose thread is it ──────────────────────────────────────────────────────────────────────

def test_staff_session_keys():
    assert identity.is_staff_session_key("admin:27821112222")
    assert identity.is_staff_session_key("webadmin:m:m-rep")
    assert not identity.is_staff_session_key("27821112222")
    assert not identity.is_staff_session_key(None)


def test_only_the_member_owns_their_thread():
    rep = identity.Person(T, member_id="m-rep", phone="27821112222")
    other = identity.Person(T, member_id="m-own", phone="27835550000")
    for key in ("admin:27821112222", "admin:0821112222", "webadmin:m:m-rep"):
        assert identity.owns_session(rep, key), key
        assert not identity.owns_session(other, key), key
        assert not identity.owns_session(None, key), key
    assert not identity.owns_session(rep, "27821112222")          # a customer thread is nobody's "own"
    assert not identity.owns_session(identity.Person(T), "webadmin:")   # nobody owns the empty key


def test_staff_threads_are_recognised_with_or_without_a_project_suffix():
    staff = {"27821112222"}
    assert identity.is_staff_thread("27821112222", staff)
    assert identity.is_staff_thread("0821112222:proj-7", staff)
    assert not identity.is_staff_thread("27820000001", staff)
    assert not identity.is_staff_thread("", staff)

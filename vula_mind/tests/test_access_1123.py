"""23 Nov 2026 full audit, access: a team login that isn't an owner or manager (a sales rep, say)
could change the bank details printed on invoices and every other setting through the API — the
screens were hidden in the browser only; an empty tab list meant FULL access, so "Add member"
with nothing ticked made a full user; "My contacts / reminders / expenses" and the rep's day
took the number from the browser, and an empty one returned every rep's records; every member
could read colleagues' numbers and birthdays. Made-up data."""
from unittest.mock import AsyncMock

import pytest

from tests.fake_supabase import FakeSupabase
from vula.api import commerce, server, team, tenant_auth
from vula.api.tenant_auth import normalize_access, scope_allows, scope_from_rows
from vula.commerce import service

T = "deli"
OWNER = {"full": True, "access": [], "role": "owner", "member": None}
REP = {"full": False, "access": ["rep-contacts", "rep-reminders", "rep-expenses"], "role": "sales_rep",
       "member": {"id": "m-rep", "whatsapp": "27825550101"}}
REP_NO_NUMBER = {**REP, "member": {"id": "m-rep2", "whatsapp": None}}
MONEY = {"full": False, "access": ["invoices"], "role": "bookkeeper", "member": {"id": "m-bk", "whatsapp": "27825550102"}}


def _first_rule(scope, path, method):
    """As the server's guard does: the first rule that matches decides."""
    for methods, rx, tabs in server._SECTION_RULES:
        if (methods is None or method in methods) and rx.match(path):
            return scope_allows(scope, tabs)
    return True


# ── settings and connections: owner and managers only ─────────────────────────

@pytest.mark.parametrize("method,path", [
    ("POST", f"/v1/commerce/{T}/admin/invoice-settings"),            # bank details on invoices
    ("POST", f"/v1/commerce/{T}/admin/invoice-settings/clone-from-upload"),
    ("POST", f"/v1/commerce/{T}/admin/order-settings"),              # opening hours, delivery fees
    ("POST", f"/v1/commerce/{T}/admin/business-profile"),
    ("POST", f"/v1/commerce/{T}/admin/persona"),
    ("POST", f"/v1/commerce/{T}/admin/learned-answers/a1/approve"),
    ("POST", f"/v1/commerce/{T}/admin/wa-profile"),
    ("POST", f"/v1/commerce/{T}/admin/shop-domain"),
    ("DELETE", f"/v1/commerce/{T}/admin/link-domain"),
    ("POST", f"/v1/commerce/{T}/admin/shopify"),
    ("POST", f"/v1/commerce/{T}/admin/ai/topup"),
    ("POST", f"/v1/payments/{T}/providers"),                         # gateway keys
    ("POST", f"/v1/payments/{T}/providers/payfast/default"),
    ("DELETE", f"/v1/payments/{T}/providers/payfast"),
    ("POST", f"/v1/whatsapp/disconnect/{T}"),
    ("POST", f"/v1/email/disconnect/{T}/acc1"),
    ("POST", f"/v1/email/set-primary/{T}/acc1"),
    ("POST", f"/v1/yoco/disconnect/{T}"),
    ("DELETE", f"/v1/connections/{T}/google"),
])
def test_settings_and_connections_are_owners_and_managers_only(method, path):
    assert _first_rule(OWNER, path, method)
    assert not _first_rule(REP, path, method)
    assert not _first_rule(MONEY, path, method)       # Money access doesn't change the bank details


def test_reading_settings_is_unchanged():
    # creating an invoice reads the business's invoice settings
    assert _first_rule(MONEY, f"/v1/commerce/{T}/admin/invoice-settings", "GET")
    assert _first_rule(REP, f"/v1/commerce/{T}/admin/order-settings", "GET")


# ── no accidental full access ────────────────────────────────────────────────

@pytest.mark.parametrize("role,given,kept", [
    ("staff", [], ["overview"]),
    ("sales_rep", None, ["overview"]),
    ("bookkeeper", ["invoices"], ["invoices"]),
    ("manager", [], []),
    ("owner", [], []),
])
def test_a_member_who_is_not_a_manager_starts_with_nothing(role, given, kept):
    assert normalize_access(role, given) == kept


def test_nothing_ticked_is_no_longer_full_access():
    member = {"email": "s@deli.co.za", "role": "staff", "access": normalize_access("staff", []), "active": True}
    scope = scope_from_rows(T, "s@deli.co.za", [{"tenant_id": T, "role": "staff"}], [member])
    assert scope["full"] is False
    assert not scope_allows(scope, frozenset({"invoices"}))


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({"vula_team_members": [
        {"id": "m1", "tenant_id": T, "name": "Sipho", "role": "manager", "access": [], "whatsapp": "27825550111",
         "email": "sipho@deli.co.za", "birthday": "03-14", "active": True},
        {"id": "m-rep", "tenant_id": T, "name": "Thandi", "role": "sales_rep", "access": ["rep-contacts"],
         "whatsapp": "27825550101", "email": "thandi@deli.co.za", "birthday": "07-02", "active": True},
    ], "commerce_contacts": [
        {"id": "c1", "tenant_id": T, "phone": "27821110001", "name": "Acme", "created_by": "27825550101"},
        {"id": "c2", "tenant_id": T, "phone": "27821110002", "name": "Beta", "created_by": "27825550199"},
    ], "vula_merchant_audit": []})
    monkeypatch.setattr(service, "_client", lambda: d)
    monkeypatch.setattr(team, "_client", lambda: d)
    return d


@pytest.mark.asyncio
async def test_adding_a_member_with_nothing_ticked_gives_nothing(db):
    await team.add_member(T, team.MemberIn(name="Lindiwe", whatsapp="0825550120", role="staff"),
                          identity={"email": "owner@deli.co.za"})
    row = [r for r in db.rows["vula_team_members"] if r.get("name") == "Lindiwe"][0]
    assert row["access"] == ["overview"]


@pytest.mark.asyncio
async def test_unticking_the_last_tab_or_demoting_never_makes_someone_full(db):
    await team.update_member(T, "m-rep", team.MemberPatch(access=[]), identity={"email": "o@deli.co.za"})
    assert [r for r in db.rows["vula_team_members"] if r["id"] == "m-rep"][0]["access"] == ["overview"]
    await team.update_member(T, "m1", team.MemberPatch(role="staff"), identity={"email": "o@deli.co.za"})
    assert [r for r in db.rows["vula_team_members"] if r["id"] == "m1"][0]["access"] == ["overview"]


# ── the team list ────────────────────────────────────────────────────────────

class _Req:
    headers = {"authorization": "Bearer x"}


@pytest.mark.asyncio
async def test_colleagues_numbers_and_birthdays_are_for_owners_and_managers(db, monkeypatch):
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value=REP))
    rows = {r["id"]: r for r in (await team.list_members(T, _Req()))["members"]}
    assert "whatsapp" not in rows["m1"] and "birthday" not in rows["m1"] and rows["m1"]["name"] == "Sipho"
    assert rows["m-rep"]["whatsapp"] == "27825550101"            # their own row is whole
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value=OWNER))
    rows = {r["id"]: r for r in (await team.list_members(T, _Req()))["members"]}
    assert rows["m1"]["whatsapp"] == "27825550111"


# ── "my" lists come from the signed-in person ─────────────────────────────────

@pytest.mark.asyncio
async def test_my_contacts_are_mine_whatever_the_browser_asks(db, monkeypatch):
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value=REP))
    got = (await commerce.admin_list_contacts(T, created_by="", request=_Req()))["contacts"]
    assert [c["id"] for c in got] == ["c1"]
    got = (await commerce.admin_list_contacts(T, created_by="27825550199", request=_Req()))["contacts"]
    assert [c["id"] for c in got] == ["c1"]


@pytest.mark.asyncio
async def test_a_login_with_no_number_sees_nobodys(db, monkeypatch):
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value=REP_NO_NUMBER))
    assert (await commerce.admin_list_contacts(T, created_by="", request=_Req()))["contacts"] == []


@pytest.mark.asyncio
async def test_the_owner_still_sees_everyones(db, monkeypatch):
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value=OWNER))
    assert len((await commerce.admin_list_contacts(T, created_by="", request=_Req()))["contacts"]) == 2


@pytest.mark.asyncio
async def test_a_rep_cannot_overwrite_a_colleagues_contact(db, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value=REP))
    with pytest.raises(HTTPException) as e:
        await commerce.admin_upsert_contact(T, commerce.ContactIn(name="Mine now", phone="0821110002"), request=_Req())
    assert e.value.status_code == 409
    assert [c for c in db.rows["commerce_contacts"] if c["id"] == "c2"][0]["name"] == "Beta"
    out = await commerce.admin_upsert_contact(T, commerce.ContactIn(name="New", phone="0821110003",
                                                                    created_by="27825550199"), request=_Req())
    assert out["contact"]["created_by"] == "27825550101"              # theirs, not the one the browser named


@pytest.mark.asyncio
async def test_my_expenses_are_mine(monkeypatch):
    seen = {}

    def claims(tid, **kw):
        seen.update(kw)
        return []

    from vula.commerce import accounting, expenses
    monkeypatch.setattr(expenses, "list_claims", claims)
    monkeypatch.setattr(expenses, "known_sections", lambda *a, **k: [])
    monkeypatch.setattr(expenses, "known_projects", lambda *a, **k: [])
    monkeypatch.setattr(accounting, "ensure_chart", lambda tid: [])
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value=REP))
    await commerce.admin_list_expenses(T, paid_by=None, request=_Req())
    assert seen["paid_by"] == "27825550101"
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value=MONEY))
    await commerce.admin_list_expenses(T, paid_by=None, request=_Req())
    assert seen["paid_by"] is None                                    # Money access sees every claim

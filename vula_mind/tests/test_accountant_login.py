"""19 Nov 2026 (Ian: "accountant login"): an outside accountant the owner invites sees the books and
documents, changes nothing, takes no paid seat, must use two-step sign-in, and everything they look at
or download is on the business's record (tenant_auth ACCOUNTANT, server.py's guard,
vula/accountant_activity.py, users.py accountants routes)."""
import asyncio
import re
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from vula import accountant_activity
from vula.api import server, tenant_auth, users
from tests.fake_supabase import FakeSupabase

T = "t1"
ACC = {"user_id": "u-acc", "email": "sam@numbers.co.za", "tenants": {T}, "only": True}


def _member(**kw):
    return {"id": "m1", "email": "sam@numbers.co.za", "role": "accountant", "access": [], "active": True, **kw}


# ── who they are ─────────────────────────────────────────────────────────────

def test_an_accountant_is_never_full_whatever_the_team_row_says():
    login = [{"tenant_id": T, "role": "accountant"}]
    s = tenant_auth.scope_from_rows(T, "sam@numbers.co.za", login, [_member()])
    assert s["full"] is False and s["read_only"] and s["access"] == tenant_auth.ACCOUNTANT_TABS
    assert tenant_auth.scope_from_rows(T, "sam@numbers.co.za", login, [])["access"] == []        # no row: nothing
    assert tenant_auth.scope_from_rows(T, "sam@numbers.co.za", login, [_member(role="manager")])["full"] is False
    gone = date.today() - timedelta(days=1)
    assert tenant_auth.scope_from_rows(T, "sam@numbers.co.za", login, [_member(expires_on=gone.isoformat())])["access"] == []
    assert tenant_auth.scope_from_rows(T, "sam@numbers.co.za", login, [_member(active=False)])["access"] == []
    assert not tenant_auth.scope_allows(tenant_auth.accountant_scope(_member()), frozenset())     # never "full only"


def test_two_step_sign_in_is_always_required(monkeypatch):
    from vula.api import master_auth
    monkeypatch.setattr(master_auth, "required_roles", lambda: set())
    monkeypatch.setattr(master_auth, "_is_accountant", lambda uid: True)
    assert master_auth.two_step_missing({"id": "u-acc", "factors": []}, "tok") == "setup"
    monkeypatch.setattr(master_auth, "_is_accountant", lambda uid: False)
    assert master_auth.two_step_missing({"id": "u-1", "factors": []}, "tok") is None


def test_an_accountant_takes_no_paid_seat(monkeypatch):
    from vula.commerce import plan_limits, service
    db = FakeSupabase({"vula_tenant_users": [{"id": "1", "tenant_id": T, "role": "owner"},
                                             {"id": "2", "tenant_id": T, "role": "accountant"},
                                             {"id": "3", "tenant_id": T, "role": "accountant"}]})
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(plan_limits, "_tenant_plan", lambda tid: "starter")       # two seats
    plan_limits.check_seat_quota(T)                                                # one used: no error


def test_whatsapp_never_treats_an_accountant_as_team():
    from vula.api import whatsapp
    assert "accountant" not in whatsapp._INSIDER_ROLES and "accountant" not in whatsapp._ADMIN_AGENT_ROLES


# ── the guard: read only, deny by default ────────────────────────────────────

@pytest.fixture
def acc(monkeypatch):
    monkeypatch.setattr(tenant_auth, "accountant_login", AsyncMock(return_value=ACC))
    seen = []
    monkeypatch.setattr(accountant_activity, "record", lambda *a, **k: seen.append(a) or True)
    return seen


def _check(method, path):
    return asyncio.run(server._accountant_check(method, path, "Bearer x"))


def test_every_write_on_every_route_is_refused(acc):
    tried = 0
    for template, ops in server.app.openapi()["paths"].items():
        path = re.sub(r"\{[^}]+\}", T, template)
        for method in ops:
            if method.upper() in ("GET", "HEAD") or method.startswith("x-") or method == "parameters":
                continue
            res = _check(method.upper(), path)
            assert res is not None and res.status_code == 403, f"{method} {path}"
            tried += 1
    assert tried > 300


def test_only_the_books_can_be_read(acc):
    for ok in ("/v1/commerce/t1/admin/reports/pnl", "/v1/commerce/t1/admin/reports/ledger.csv",
               "/v1/commerce/t1/admin/bank/transactions", "/v1/commerce/t1/admin/invoices",
               "/v1/commerce/t1/admin/finance-folders.zip", "/v1/documents/t1/filed",
               "/v1/team/t1/me", "/v1/tenants/t1", "/v1/accountant/clients"):
        assert _check("GET", ok) is None, ok
    for no in ("/v1/commerce/t1/admin/orders", "/v1/commerce/t1/admin/products", "/v1/commerce/t1/admin/escalations",
               "/v1/commerce/t1/admin/assistant", "/v1/team/t1", "/v1/team/t1/audit", "/v1/users/t1/users",
               "/v1/projects/t1/finances", "/v1/commerce/t1/admin/books-audit/statements",
               "/v1/commerce/t2/admin/reports/pnl",              # another business: not theirs
               "/v1/master/tenants", "/v1/chat/t1"):
        res = _check("GET", no)
        assert res is not None and res.status_code == 403, no
    assert len(acc) == 8                                  # every allowed read on the business goes to the record


def test_a_login_that_is_not_an_accountant_is_untouched(monkeypatch):
    monkeypatch.setattr(tenant_auth, "accountant_login", AsyncMock(return_value=None))
    assert _check("POST", "/v1/commerce/t1/admin/invoices") is None


# ── on the record ────────────────────────────────────────────────────────────

def test_views_are_noted_once_per_half_hour_downloads_every_time(monkeypatch):
    rows = []
    monkeypatch.setattr(accountant_activity, "_write", lambda *a: rows.append(a))
    accountant_activity._SEEN.clear()
    assert accountant_activity.record(T, ACC, "/v1/commerce/t1/admin/reports/pnl", "since=2026-03-01")
    assert not accountant_activity.record(T, ACC, "/v1/commerce/t1/admin/reports/pnl")
    assert accountant_activity.record(T, ACC, "/v1/commerce/t1/admin/reports/ledger.csv")
    assert accountant_activity.record(T, ACC, "/v1/commerce/t1/admin/reports/ledger.csv")
    assert not accountant_activity.record(T, ACC, "/v1/team/t1/me")
    acts = [(r[2], r[3]) for r in rows]
    assert acts == [("accountant_viewed", "reports/pnl"), ("accountant_downloaded", "reports/ledger.csv"),
                    ("accountant_downloaded", "reports/ledger.csv")]
    assert accountant_activity.page_of("/v1/commerce/t1/admin/invoices/3f2a1c4e-1111-2222-3333-444455556666/pdf") \
        == "invoices/:id/pdf"


# ── documents: opened through the server ─────────────────────────────────────

@pytest.fixture
def docs(monkeypatch):
    from vula.api import documents
    db = FakeSupabase({"vula_filed_documents": [
        {"id": "d1", "tenant_id": T, "filename": "FNB 62012345678 Sep.pdf", "category": "Bank Statement",
         "file_url": "https://x.supabase.co/storage/v1/object/public/documents/t1/a.pdf", "created_at": "2026-10-01"}]})
    monkeypatch.setattr(documents, "_client", lambda: db)
    monkeypatch.setattr("vula.storage_links.signed", lambda url, ttl=0: url + "?token=signed")
    monkeypatch.setattr("vula.storage_links.sign_row", lambda rows, *a, **k: rows)
    return documents


def test_an_accountant_gets_no_direct_links_and_each_open_is_noted(docs, monkeypatch):
    seen = []
    monkeypatch.setattr(accountant_activity, "record", lambda *a, **k: seen.append((a, k)) or True)
    monkeypatch.setattr(tenant_auth, "accountant_login", AsyncMock(return_value=ACC))
    listed = asyncio.run(docs.list_filed(T, authorization="Bearer x"))
    assert listed["documents"][0]["file_url"] is None and listed["documents"][0]["open"]
    got = asyncio.run(docs.open_filed(T, "d1", authorization="Bearer x"))
    assert got["url"].endswith("?token=signed")
    assert seen and seen[0][1]["target"] == "FNB …5678 Sep.pdf"
    with pytest.raises(HTTPException):
        asyncio.run(docs.open_filed(T, "nope", authorization="Bearer x"))


def test_the_team_still_gets_its_links(docs, monkeypatch):
    monkeypatch.setattr(tenant_auth, "accountant_login", AsyncMock(return_value=None))
    listed = asyncio.run(docs.list_filed(T, authorization="Bearer x"))
    assert listed["documents"][0]["file_url"].startswith("https://")


# ── invite, list, revoke ─────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._b, self.text = status, body or {}, "x"

    def json(self):
        return self._b


@pytest.fixture
def team(monkeypatch):
    db = FakeSupabase({"vula_tenant_users": [{"user_id": "u-owner", "tenant_id": T, "role": "owner"}],
                       "vula_team_members": [], "vula_merchant_audit": []})
    monkeypatch.setattr(users, "_client", lambda: db)
    monkeypatch.setattr(accountant_activity, "activity", lambda tid, limit=100: [])
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: db)
    monkeypatch.setattr("vula.team_index.invalidate", lambda *a, **k: None)
    return db


OWNER = {"email": "judy@digg.co.za", "user_id": "u-owner"}


def test_a_new_accountant_gets_a_view_only_login_and_no_seat(team, monkeypatch):
    monkeypatch.setattr(users, "_auth_admin", AsyncMock(return_value=_Resp(201, {"id": "u-acc"})))
    out = asyncio.run(users.invite_accountant(T, users.AccountantIn(email="Sam@Numbers.co.za", name="Sam",
                                                                     expires_on="2099-02-28"), identity=OWNER))
    assert out["temp_password"] and out["created"]
    assert {"user_id": "u-acc", "tenant_id": T, "role": "accountant"}.items() <= team.rows["vula_tenant_users"][1].items()
    m = team.rows["vula_team_members"][0]
    assert (m["role"], m["email"], m["access"], m.get("whatsapp"), m["expires_on"]) == \
        ("accountant", "sam@numbers.co.za", [], None, "2099-02-28")
    assert team.rows["vula_merchant_audit"][0]["action"] == "accountant_invited"
    listed = asyncio.run(users.list_accountants(T, identity=OWNER))
    assert [a["email"] for a in listed["accountants"]] == ["sam@numbers.co.za"]
    gone = asyncio.run(users.revoke_accountant(T, m["id"], identity=OWNER))
    assert gone["revoked"] and m["active"] is False


def test_the_same_accountant_for_another_client_keeps_their_password(team, monkeypatch):
    team.rows["vula_tenant_users"].append({"user_id": "u-acc", "tenant_id": "other", "role": "accountant"})
    monkeypatch.setattr(users, "_auth_admin", AsyncMock(return_value=_Resp(422)))
    monkeypatch.setattr(users, "_find_user_id", AsyncMock(return_value="u-acc"))
    out = asyncio.run(users.invite_accountant(T, users.AccountantIn(email="sam@numbers.co.za"), identity=OWNER))
    assert out["temp_password"] is None and "own password" in out["note"]
    assert any(r["tenant_id"] == T and r["role"] == "accountant" for r in team.rows["vula_tenant_users"])


def test_a_team_login_can_never_also_be_an_accountant(team, monkeypatch):
    team.rows["vula_tenant_users"].append({"user_id": "u-staff", "tenant_id": "other", "role": "staff"})
    monkeypatch.setattr(users, "_auth_admin", AsyncMock(return_value=_Resp(422)))
    monkeypatch.setattr(users, "_find_user_id", AsyncMock(return_value="u-staff"))
    out = asyncio.run(users.invite_accountant(T, users.AccountantIn(email="pat@x.co.za"), identity=OWNER))
    assert "already a team or owner login" in out["error"]
    with pytest.raises(HTTPException):
        asyncio.run(users.create_user(T, users.CreateUserIn(email="a@b.co", role="accountant"), identity=OWNER))
    with pytest.raises(HTTPException):
        asyncio.run(users.invite_accountant(T, users.AccountantIn(email="a@b.co", expires_on="2001-01-01"),
                                            identity=OWNER))


def test_clients_lists_only_the_callers_own_businesses(monkeypatch):
    from vula.api import accountant, tenants
    monkeypatch.setattr(tenant_auth, "accountant_login",
                        AsyncMock(return_value={**ACC, "tenants": {"t1", "t9"}}))
    monkeypatch.setattr(tenants, "is_active", lambda tid: tid != "t9")
    monkeypatch.setattr(tenants, "display_name", lambda tid: "DIGG")
    assert asyncio.run(accountant.clients(authorization="Bearer x")) == {"clients": [{"tenant_id": "t1", "name": "DIGG"}]}
    monkeypatch.setattr(tenant_auth, "accountant_login", AsyncMock(return_value=None))
    with pytest.raises(HTTPException):
        asyncio.run(accountant.clients(authorization="Bearer x"))

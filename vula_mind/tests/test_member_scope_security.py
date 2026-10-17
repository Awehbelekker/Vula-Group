"""Server-side member scope (2026-10-17 security pass, docs/ux-redesign-audit.md §1).

S2: /v1/team/{t}/me trusted ?email= and gave an unknown email full owner access.
S3: section gating (Money, Team) lived only in the browser — a rep could call money endpoints.
S4: any member could change another member's role/access or create logins.
S5: the email follow-up PATCH matched by id alone (cross-tenant write).
S7: escalation counts were len() of a capped page.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from vula.api import server, tenant_auth
from vula.api.tenant_auth import scope_allows, scope_from_rows

T = "harbour-deli"
REP = {"email": "rep@deli.example", "role": "sales_rep", "access": ["rep-contacts", "rep-expenses"],
       "active": True, "name": "Rep", "whatsapp": "27820000001", "notify": []}


# ── scope_from_rows: the one rule ───────────────────────────────────────────────

def test_master_is_full_everywhere():
    s = scope_from_rows(T, "ian@vula.example", [{"tenant_id": "other", "role": "master"}], [REP])
    assert s["full"] and s["role"] == "master"


def test_owner_login_is_full_even_with_a_restricted_directory_row():
    row = {**REP, "email": "owner@deli.example"}
    s = scope_from_rows(T, "owner@deli.example", [{"tenant_id": T, "role": "owner"}], [row])
    assert s["full"]


def test_restricted_member_matched_by_email_case_insensitively():
    s = scope_from_rows(T, "Rep@Deli.Example", [{"tenant_id": T, "role": "staff"}], [REP])
    assert not s["full"] and s["access"] == ["rep-contacts", "rep-expenses"]
    assert s["role"] == "sales_rep"


def test_manager_and_empty_access_are_full():
    mgr = {**REP, "role": "manager"}
    assert scope_from_rows(T, REP["email"], [{"tenant_id": T, "role": "staff"}], [mgr])["full"]
    open_ = {**REP, "access": []}
    assert scope_from_rows(T, REP["email"], [{"tenant_id": T, "role": "staff"}], [open_])["full"]


def test_inactive_member_gets_nothing():
    off = {**REP, "active": False}
    s = scope_from_rows(T, REP["email"], [{"tenant_id": T, "role": "staff"}], [off])
    assert not s["full"] and s["access"] == []


def test_staff_login_without_directory_row_keeps_todays_full_access():
    s = scope_from_rows(T, "old@deli.example", [{"tenant_id": T, "role": "staff"}], [REP])
    assert s["full"] and s["member"] is None


def test_scope_allows():
    rep = {"full": False, "access": ["rep-expenses"]}
    assert scope_allows({"full": True}, frozenset())
    assert scope_allows(rep, frozenset({"rep-expenses", "expenses"}))
    assert not scope_allows(rep, frozenset({"invoices"}))
    assert not scope_allows(rep, frozenset())          # full-only
    assert not scope_allows(None, frozenset({"rep-expenses"}))


@pytest.mark.asyncio
async def test_scope_lookup_failure_fails_closed_and_is_not_cached():
    tenant_auth._SCOPE_CACHE.clear()
    with (patch("vula.api.tenant_auth.is_tenant_member", AsyncMock(return_value=True)),
          patch("vula.api.master_auth._verify_jwt", AsyncMock(return_value={"id": "u1", "email": "o@x"})),
          patch("vula.commerce.service._client", side_effect=RuntimeError("db down"))):
        s = await tenant_auth.member_scope("Bearer tok", T)
    assert s["full"] is False and s["access"] == []
    assert not tenant_auth._SCOPE_CACHE


# ── S2: /me reads the verified login, not ?email= ───────────────────────────────

@pytest.mark.asyncio
async def test_me_ignores_the_email_param():
    from vula.api import team
    scope = {"full": False, "access": ["rep-contacts"], "role": "sales_rep", "member": REP}
    with patch("vula.api.team.member_scope", AsyncMock(return_value=scope)) as ms:
        out = await team.me(T, email="owner@deli.example", authorization="Bearer rep-token")
    ms.assert_awaited_once_with("Bearer rep-token", T)
    assert out["full"] is False and out["access"] == ["rep-contacts"] and out["role"] == "sales_rep"


@pytest.mark.asyncio
async def test_me_refuses_anonymous_and_outsiders():
    from vula.api import team
    with patch("vula.api.team.member_scope", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as e:
            await team.me(T, email="anyone@x", authorization="")
        assert e.value.status_code == 401
        with pytest.raises(HTTPException) as e:
            await team.me(T, email="anyone@x", authorization="Bearer other-tenant")
        assert e.value.status_code == 403


# ── S4: team + login writes need full access ────────────────────────────────────

@pytest.mark.asyncio
async def test_require_tenant_admin_refuses_restricted_members():
    with (patch("vula.api.tenant_auth.require_tenant_actor", AsyncMock(return_value={"email": "rep@x"})),
          patch("vula.api.tenant_auth.member_scope",
                AsyncMock(return_value={"full": False, "access": ["rep-contacts"]}))):
        with pytest.raises(HTTPException) as e:
            await tenant_auth.require_tenant_admin(T, "Bearer t")
    assert e.value.status_code == 403
    with (patch("vula.api.tenant_auth.require_tenant_actor", AsyncMock(return_value={"email": "o@x"})),
          patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value={"full": True}))):
        assert await tenant_auth.require_tenant_admin(T, "Bearer t") == {"email": "o@x"}


def test_team_and_user_writes_use_the_admin_dependency():
    from vula.api.team import router as team_router
    from vula.api.users import router as users_router
    for r in [*team_router.routes, *users_router.routes]:
        deps = {d.call for d in r.dependant.dependencies}
        writes = r.methods & {"POST", "PUT", "PATCH", "DELETE"}
        if writes or r.path.endswith("/audit") or r.path.endswith("/users"):
            assert tenant_auth.require_tenant_admin in deps, f"{r.methods} {r.path}"


# ── S3: guard section rules ─────────────────────────────────────────────────────

RESTRICTED = {"full": False, "access": ["rep-contacts", "rep-expenses"]}

DENIED_TO_REP = [
    ("GET", f"/v1/commerce/{T}/admin/invoices"),
    ("POST", f"/v1/commerce/{T}/admin/quotes"),
    ("GET", f"/v1/commerce/{T}/admin/bank/reconciliation"),
    ("GET", f"/v1/commerce/{T}/admin/accounts/ledger"),
    ("GET", f"/v1/commerce/{T}/admin/reports/pnl"),
    ("GET", f"/v1/commerce/{T}/admin/finances"),
    ("PUT", f"/v1/payments/{T}/providers/payfast"),
    ("GET", f"/v1/users/{T}/users"),
    ("GET", f"/v1/team/{T}/audit"),
    ("PATCH", f"/v1/team/{T}/m1"),
    ("POST", f"/v1/team/{T}"),
]

ALLOWED_TO_REP = [
    ("GET", f"/v1/commerce/{T}/admin/expenses"),        # their own expenses tab
    ("GET", f"/v1/commerce/{T}/admin/expense-sheet"),
    ("GET", f"/v1/commerce/{T}/admin/call-sheet"),      # not a money section
    ("GET", f"/v1/commerce/{T}/admin/orders"),
    ("GET", f"/v1/team/{T}/me"),
    ("GET", f"/v1/team/{T}"),
]


def _as(scope):
    return (patch("vula.api.tenant_auth.is_tenant_member", AsyncMock(return_value=True)),
            patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value=scope)))


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", DENIED_TO_REP)
async def test_restricted_member_is_refused_money_and_team_admin(method, path):
    a, b = _as(RESTRICTED)
    with a, b:
        res = await server._guard_check(method, path, "Bearer rep")
    assert res is not None and res.status_code == 403
    a, b = _as({"full": True})
    with a, b:
        assert await server._guard_check(method, path, "Bearer owner") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", ALLOWED_TO_REP)
async def test_restricted_member_keeps_their_own_screens(method, path):
    a, b = _as(RESTRICTED)
    with a, b:
        assert await server._guard_check(method, path, "Bearer rep") is None


@pytest.mark.asyncio
async def test_money_tab_grants_money_routes():
    a, b = _as({"full": False, "access": ["bank"]})
    with a, b:
        assert await server._guard_check("GET", f"/v1/commerce/{T}/admin/invoices", "Bearer b") is None


# ── S5: follow-up PATCH is tenant-scoped ────────────────────────────────────────

@pytest.mark.asyncio
async def test_followup_patch_is_scoped_to_the_path_tenant():
    from vula.api import email_connect
    db = MagicMock()
    chain = db.table.return_value.update.return_value.eq.return_value.eq.return_value
    chain.execute.return_value = MagicMock(data=[])        # id belongs to another tenant
    body = email_connect.FollowupStatusIn(status="done")
    with patch("vula.api.email_connect._client", return_value=db):
        with pytest.raises(HTTPException) as e:
            await email_connect.set_followup_status(T, "f-of-other-tenant", body)
    assert e.value.status_code == 404
    db.table.return_value.update.return_value.eq.return_value.eq.assert_called_with("tenant_id", T)

    chain.execute.return_value = MagicMock(data=[{"id": "f1"}])
    with patch("vula.api.email_connect._client", return_value=db):
        assert (await email_connect.set_followup_status(T, "f1", body))["status"] == "done"


# ── S7: exact escalation count ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_count_is_the_exact_total_not_the_page():
    from vula.api import commerce
    db = MagicMock()
    q = db.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value
    q.execute.return_value = MagicMock(data=[{"id": i} for i in range(50)], count=137)
    with patch("vula.commerce.service._client", return_value=db):
        out = await commerce.admin_list_escalations(T, status="open", limit=50)
    assert out["open_count"] == 137 and len(out["escalations"]) == 50

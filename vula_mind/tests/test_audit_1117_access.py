"""17 Nov 2026 audit of #314–#339, access: costing by trade needs Money or its own tab (what a
job actually cost, owners and managers only); build-ups are shown before they're confirmed; the
dashboard Confirm runs with the signed-in person's own access; a caller with no login can't name
someone's thread."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills import commerce_admin as ca
from tests.fake_supabase import FakeSupabase
from vula import identity
from vula.api import commerce
from vula.api import server
from vula.api.tenant_auth import scope_allows
from vula.commerce import service

T = "deli"
ORDERS_ONLY = {"full": False, "access": ["orders"]}
COSTING = {"full": False, "access": ["tradecosting"]}
MONEY = {"full": False, "access": ["invoices"]}
OWNER = {"full": True, "access": []}


def _allowed(scope, path, method="GET"):
    rules = [tabs for methods, rx, tabs in server._SECTION_RULES
             if (methods is None or method in methods) and rx.match(path)]
    return all(scope_allows(scope, tabs) for tabs in rules)


@pytest.mark.parametrize("path", [
    "/v1/commerce/deli/admin/estimates",
    "/v1/commerce/deli/admin/estimates/abc.xlsx",
    "/v1/commerce/deli/admin/buildup-suggestions",
])
def test_estimates_need_money_or_the_costing_tab(path):
    assert not _allowed(ORDERS_ONLY, path)
    assert _allowed(COSTING, path) and _allowed(MONEY, path) and _allowed(OWNER, path)


def test_what_a_job_actually_cost_is_owners_and_managers_only():
    path = "/v1/commerce/deli/admin/estimates/e1/actuals"
    assert not _allowed(COSTING, path) and not _allowed(MONEY, path)
    assert _allowed(OWNER, path)


def test_build_ups_stay_open_to_members():
    assert _allowed(ORDERS_ONLY, "/v1/commerce/deli/admin/buildups")


def test_estimate_cost_on_whatsapp_needs_the_costing_tab():
    tools = [{"function": {"name": "estimate_cost"}}, {"function": {"name": "trade_buildup"}}]
    assert [t["function"]["name"] for t in ca._restrict_to_access(tools, ["orders"])] == ["trade_buildup"]
    assert len(ca._restrict_to_access(tools, ["tradecosting"])) == 2


def test_build_ups_are_previewed_before_confirming():
    assert {"confirm_buildup", "buildup_suggestion"} <= ca._PREVIEW_FIRST


def _req():
    r = MagicMock()
    r.headers = {"authorization": "Bearer tok"}
    return r


@pytest.mark.asyncio
async def test_no_login_cannot_name_a_colleagues_thread():
    with patch("vula.identity.person_for_login", AsyncMock(return_value=None)):
        _, key = await commerce._assistant_session(T, _req(), "m:m-colleague")
        _, key2 = await commerce._assistant_session(T, _req(), " u:someone")
        _, ok = await commerce._assistant_session(T, _req(), "abc")
    assert key == key2 == "webadmin:default"
    assert ok == "webadmin:abc"


@pytest.mark.asyncio
async def test_confirm_runs_with_the_persons_own_access_and_name():
    rep = identity.Person(T, member_id="m-rep", name="Thandi", role="staff", access=["orders"],
                          phone="27821112222")
    db = FakeSupabase()
    later = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    db.rows["commerce_pending_confirmations"] = [{
        "id": "p1", "tenant_id": T, "phone": rep.web_session_key, "status": "pending",
        "expires_at": later, "tool_name": "confirm_buildup",
        "tool_args": {"buildup_id": "b1", "_caller": {"role": "staff", "name": "Thandi"}}}]
    seen = {}

    async def dispatch(self, name, args, ctx):
        seen.update(ctx)
        return {"confirmed": True}

    with patch.object(service, "_client", return_value=db), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=rep)), \
         patch.object(ca.CommerceAdminSkill, "_dispatch_tool", dispatch), \
         patch.object(service, "get_or_create_session", AsyncMock(return_value={"id": "s"})), \
         patch.object(service, "append_message", AsyncMock(), create=True):
        try:
            await commerce.admin_assistant_confirm(T, commerce.AssistantConfirm(id="p1"), _req())
        except Exception:  # noqa: BLE001 — only what the tool was run with matters here
            pass
    assert seen["access"] == ["orders"]
    assert seen["person_key"] == rep.key
    assert seen["session_id"] == rep.web_session_key


@pytest.mark.asyncio
async def test_the_chat_passes_who_is_asking_to_the_tools():
    rep = identity.Person(T, member_id="m-rep", name="Thandi", role="staff", access=["orders"])
    from core.skills.base import SkillOutput

    class _Skill:
        seen = None

        async def __call__(self, inp):
            _Skill.seen = inp
            return SkillOutput(answer="Done.", skill_name="commerce_admin")

    with patch.object(service, "_client", return_value=FakeSupabase()), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=rep)), \
         patch("core.skills.loader.get_skill", return_value=_Skill()):
        await commerce.admin_assistant(T, commerce.AssistantRequest(message="hi"), _req())
    assert _Skill.seen.metadata["person_key"] == rep.key

"""The capability benchmark (evals/benchmark.py) and the dry-run guard it runs inside
(core/dry_run.py). The guard is what makes it safe to run real skills on real tenants in the live
server: only read-only tools run, everything else is recorded, and nothing is sent."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core import dry_run
from evals import benchmark


class _Skill:
    @dry_run.guard_dispatch
    async def _dispatch(self, name, args, tenant_id):
        self.ran = getattr(self, "ran", []) + [name]
        return {"total_amount": "R28,647.50", "total_matches": 22}


@pytest.mark.asyncio
async def test_only_read_only_tools_run_in_a_dry_run():
    s = _Skill()
    with dry_run.session() as st:
        read = await s._dispatch("find_document", {"query": "jack hammer"}, "digg-demo")
        write = await s._dispatch("create_invoice", {"customer": "Regan"}, "off-the-hook")
    assert s.ran == ["find_document"]
    assert read["total_matches"] == 22
    assert write["status"] == "dry_run_not_performed"
    assert [(c["tool"], c["executed"]) for c in st["calls"]] == [("find_document", True), ("create_invoice", False)]


@pytest.mark.asyncio
async def test_outside_a_dry_run_everything_runs_as_normal():
    s = _Skill()
    await s._dispatch("create_invoice", {}, "t")
    assert s.ran == ["create_invoice"] and not dry_run.active()


@pytest.mark.asyncio
async def test_the_guard_is_per_task_so_real_traffic_is_untouched():
    s_bench, s_live = _Skill(), _Skill()

    async def bench():
        with dry_run.session():
            await asyncio.sleep(0.01)
            await s_bench._dispatch("send_broadcast", {"confirm": True}, "t")

    async def live():
        await asyncio.sleep(0.005)
        await s_live._dispatch("send_broadcast", {"confirm": True}, "t")

    await asyncio.gather(bench(), live())
    assert getattr(s_bench, "ran", []) == [] and s_live.ran == ["send_broadcast"]


@pytest.mark.asyncio
async def test_whatsapp_and_email_are_recorded_not_sent():
    from vula.api import whatsapp
    from vula.email_imap import service as email_service
    with patch("httpx.AsyncClient", side_effect=AssertionError("must not reach Meta")):
        with dry_run.session() as st:
            assert await whatsapp._send_reply("27827077080", "hello", "digg-demo") is True
            res = await email_service.send({"smtp_host": "x"}, "a@b.co", "Hi", "body")
    assert res["dry_run"] is True
    assert [s["kind"] for s in st["sent"]] == ["whatsapp", "email"]


def test_every_skill_dispatcher_is_guarded():
    import inspect
    from core.skills import (clickup_admin, commerce_admin, commerce_assistant, draft_admin,
                             email_admin, finance_admin, google_admin, microsoft_admin)
    pairs = [(commerce_admin.CommerceAdminSkill, "_dispatch_tool"),
             (commerce_assistant.CommerceAssistantSkill, "_dispatch_tool"),
             (clickup_admin.ClickUpAdminSkill, "_dispatch_tool"),
             (draft_admin.DraftAdminSkill, "_dispatch"), (email_admin.EmailAdminSkill, "_dispatch"),
             (finance_admin.FinanceAdminSkill, "_dispatch"), (google_admin.GoogleAdminSkill, "_dispatch"),
             (microsoft_admin.MicrosoftAdminSkill, "_dispatch")]
    for cls, meth in pairs:
        fn = getattr(cls, meth)
        assert fn.__code__.co_filename.endswith("dry_run.py"), f"{cls.__name__}.{meth} isn't guarded"


def test_write_tools_are_never_on_the_read_only_list():
    for t in ("create_invoice", "send_invoice", "send_broadcast", "update_stock", "add_expense",
              "place_order", "add_to_cart", "email_draft", "draft_letter", "create_contact",
              "log_meeting", "create_reminder", "update_order_status", "record_payment", "drive_pull"):
        assert t not in dry_run.READ_ONLY


# ── Grading ─────────────────────────────────────────────────────────────────

def test_an_invented_figure_is_caught():
    evidence = '{"total_amount": "R28,647.50", "total_amount_cents": 2864750}'
    assert benchmark.ungrounded_amounts("Gardens Handiman: R28,647.50 over 22 invoices", evidence) == []
    assert benchmark.ungrounded_amounts("You spent R31,200.00 with them", evidence) == ["R31,200.00"]


def test_rule_checks_name_what_failed():
    case = {"prompt": "Mark OTH-00042 as dispatched", "expect_tools": ["update_order_status"],
            "must_contain": ["OTH-00042"]}
    calls = [{"tool": "recent_orders", "args": {}, "executed": True, "result": "[]"}]
    checks = benchmark.rule_checks(case, skill="commerce_admin", answer="I've updated OTH-00042 to dispatched.",
                                   calls=calls, truth=[], offered=["update_order_status"], error=None)
    assert checks["tool"] is False                   # never called the tool that does it
    assert checks["no_false_action_claim"] is False  # and still said it was done
    assert checks["facts"] is True


@pytest.mark.asyncio
async def test_an_agent_case_runs_the_real_skill_inside_a_dry_run(monkeypatch):
    seen = {}

    class FakeSkill:
        async def __call__(self, inp):
            seen["dry"] = dry_run.active()
            seen["role"] = inp.metadata["caller_role"]
            dry_run.state()["calls"].append({"tool": "sales_summary", "args": {}, "executed": True,
                                             "result": '{"total": "R4,520.00"}'})
            from core.skills.base import SkillOutput
            return SkillOutput(answer="Sales this week: R4,520.00 from 9 orders.", skill_name="commerce_admin")

    monkeypatch.setattr("core.skills.loader.get_skill", lambda _n: FakeSkill())
    monkeypatch.setattr("vula.api.tenants.tenant_profile", lambda t: {"display_name": "Off the Hook", "business_type": "food"})
    case = {"id": "x", "component": "Owner admin (shop)", "tenant": "off-the-hook", "route_mode": "commerce",
            "prompt": "How were sales this week?", "expect_tools": ["sales_summary"]}
    with patch.object(benchmark, "judge", new=AsyncMock(return_value={"score": 5, "reason": "ok"})):
        row = await benchmark.run_agent_case(case, "openrouter/x/judge")
    assert seen == {"dry": True, "role": "owner"}
    assert row["ok"] and row["skill"] == "commerce_admin" and not dry_run.active()


@pytest.mark.asyncio
async def test_a_low_judge_score_fails_the_case_with_its_reason(monkeypatch):
    class FakeSkill:
        async def __call__(self, inp):
            from core.skills.base import SkillOutput
            return SkillOutput(answer="I'm not sure.", skill_name="reasoning")
    monkeypatch.setattr("core.skills.loader.get_skill", lambda _n: FakeSkill())
    monkeypatch.setattr("vula.api.tenants.tenant_profile", lambda t: {})
    monkeypatch.setattr(benchmark, "_entry_skill", lambda c: ("reasoning", {"caller_role": "owner"}))
    with patch.object(benchmark, "judge", new=AsyncMock(return_value={"score": 2, "reason": "didn't answer"})):
        row = await benchmark.run_agent_case({"id": "y", "component": "Conversation", "tenant": "digg-demo",
                                              "prompt": "Are you ok?"}, "j")
    assert not row["ok"] and "judge 2/5: didn't answer" in row["why"]


def test_scorecard_puts_the_weakest_component_first():
    rows = [{"component": "A", "ok": True}, {"component": "A", "ok": True},
            {"component": "B", "ok": False, "id": "b1", "prompt": "p", "why": ["tool"]},
            {"component": "B", "ok": True}]
    card = benchmark.scorecard(rows)
    assert [c["component"] for c in card["components"]] == ["B", "A"]
    assert card["components"][0]["pass_pct"] == 50 and card["passed"] == 3 and len(card["gaps"]) == 1


def test_cases_file_is_well_formed():
    cases = benchmark.load_cases()
    assert len(cases) >= 35
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    for c in cases:
        assert c["tenant"] and c["prompt"] and c["component"]
        for t in (c.get("expect_tools") or []):
            assert t == "none" or isinstance(t, str)


def test_deterministic_detectors_pass_today():
    rows = benchmark.run_detectors()
    assert rows and all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]

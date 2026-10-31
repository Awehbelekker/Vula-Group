"""The AI employee team's front desk (core/skills/employees.py), 31 Oct 2026.

Accuracy is held here, in CI: every labelled message routes exactly as labelled, no tool-choice
eval case loses the tool it expects, every tool belongs to an employee, and anything unclear stays
with the whole team (today's full tool set). Made-up names and figures.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from core.skills import commerce_admin as ca
from core.skills import employees as em

EVALS = Path(__file__).resolve().parent.parent / "evals" / "cases"
ROUTING = yaml.safe_load((EVALS / "employees.yaml").read_text())
TOOL_CHOICE = [c for c in yaml.safe_load((EVALS / "tool_choice.yaml").read_text())
               if c.get("skill") == "commerce_admin" and c.get("role") != "sales_rep"]


@pytest.fixture()
def all_modules():
    with patch("vula.api.tenants.enabled_modules", lambda t: []):
        yield


@pytest.mark.parametrize("case", ROUTING, ids=lambda c: c["prompt"][:50])
def test_every_labelled_message_routes_as_labelled(case):
    got = em.pick(case["prompt"])
    assert (got.id if got else None) == case["expect"], em.scores(case["prompt"])


def test_the_corpus_covers_every_employee_and_the_whole_team():
    labels = {c["expect"] for c in ROUTING}
    assert labels == {e.id for e in em.TEAM} | {None}
    assert len(ROUTING) >= 80


@pytest.mark.parametrize("case", TOOL_CHOICE, ids=lambda c: c["prompt"][:50])
def test_no_tool_choice_case_loses_its_tool(case, all_modules):
    full = ca._tools_for("eval-tenant", role=case.get("role"), message=case["prompt"])
    emp = em.pick(case["prompt"])
    narrowed = {t["function"]["name"] for t in em.narrow(full, emp, em.pick_task(emp, case["prompt"]))}
    if case.get("expect"):
        assert case["expect"] in narrowed
    full_names = {t["function"]["name"] for t in full}
    for f in case.get("forbid") or []:
        assert f not in narrowed or f in full_names      # narrowing never adds a tool


def test_every_tool_belongs_to_an_employee(all_modules):
    owner = {t["function"]["name"] for t in ca._tools_for("eval-tenant", role="owner", message="")}
    covered = set(em.SHARED_TOOLS).union(*(e.tools for e in em.TEAM))
    assert owner - covered == set()


def test_every_listed_tool_exists():
    real = {t["function"]["name"] for t in ca._ALL_TOOL_SPECS} | {
        t["function"]["name"] for t in ca.PROJECT_TOOLS + ca.DECISION_TOOLS + ca.MANUFACTURING_TOOLS
        + ca.AUTOMATION_TOOLS + ca.PRODUCT_DOC_TOOLS + ca.REMINDER_TOOLS} | {"hand_over"}
    for e in em.TEAM:
        assert e.tools - real == set(), e.id
    assert em.SHARED_TOOLS - real == set()


def test_narrowing_keeps_shared_tools_and_adds_hand_over(all_modules):
    full = ca._tools_for("eval-tenant", role="owner", message="")
    names = {t["function"]["name"] for t in em.narrow(full, em.BY_ID["marketing"])}
    assert {"record_decision", "decision_log", "calculate", "find_document", "hand_over"} <= names
    assert "add_expense" not in names and "create_purchase_order" not in names
    assert em.narrow(full, None) is full


def test_a_switched_off_employee_is_never_picked_and_its_own_tools_go():
    on = {e.id for e in em.TEAM} - {"marketing"}
    assert em.pick("Send everyone a promo for the fresh tuna", on) is None
    tools = [{"function": {"name": n}} for n in ("generate_marketing", "send_broadcast", "sales_summary",
                                                 "add_expense", "find_document")]
    left = {t["function"]["name"] for t in em.without_switched_off(tools, on)}
    assert left == {"sales_summary", "add_expense", "find_document"}     # sales_summary: others have it


def test_instructions_go_only_into_that_employees_job():
    block = em.job_block(em.BY_ID["sales"], "Always quote on 30-day terms.")
    assert "30-day terms" in block and em.BY_ID["sales"].title in block
    assert "30-day" not in em.job_block(em.BY_ID["bookkeeper"], "")


class _FakeTable:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.op, self.payload = db, name, {}, "select", None

    def select(self, *_a):
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row
        return self

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self.op == "upsert":
            for r in rows:
                if all(r.get(k) == self.payload.get(k) for k in ("tenant_id", "employee", "task")):
                    r.update(self.payload)
                    break
            else:
                rows.append(dict(self.payload))
        hit = [r for r in rows if all(r.get(k) == v for k, v in self.f.items())]
        return type("R", (), {"data": [dict(r) for r in hit]})()


@pytest.fixture()
def fake_db(monkeypatch):
    store = {}
    monkeypatch.setattr("vula.commerce.service._client",
                        lambda: type("D", (), {"table": lambda self, n: _FakeTable(store, n)})())
    return store


def test_settings_default_on_save_and_read_back_per_business(fake_db):
    assert all(v == {"enabled": True, "instructions": ""} for v in em.settings_for("tenant-a").values())
    em.save("tenant-a", "marketing", enabled=False, by="Owner")
    em.save("tenant-a", "sales", instructions="Always quote on   30-day terms.")
    s = em.settings_for("tenant-a")
    assert s["marketing"]["enabled"] is False and s["sales"]["instructions"] == "Always quote on 30-day terms."
    assert em.settings_for("off-the-hook")["marketing"]["enabled"] is True     # another business
    with pytest.raises(ValueError):
        em.save("tenant-a", "astronaut", enabled=False)
    with pytest.raises(ValueError):
        em.save("tenant-a", "sales", instructions="x" * 700)


def test_settings_before_the_migration_are_the_defaults(monkeypatch):
    class _Broken:
        def table(self, _n):
            raise RuntimeError("relation vula_employees does not exist")
    monkeypatch.setattr("vula.commerce.service._client", lambda: _Broken())
    assert em.settings_for("tenant-a")["bookkeeper"]["enabled"] is True


# ── in the admin agent ──────────────────────────────────────────────────────

def test_the_agent_narrows_tools_and_names_the_employee(all_modules, fake_db):
    full = ca._tools_for("eval-tenant", role="owner", message="I spent R450 on diesel")
    tools, emp, note = ca._pick_employee("eval-tenant", "I spent R450 on diesel", full)
    assert emp.id == "bookkeeper" and "YOUR JOB — AI bookkeeper" in note
    names = {t["function"]["name"] for t in tools}
    assert "add_expense" in names and "send_broadcast" not in names and "hand_over" in names


def test_an_unclear_message_keeps_todays_tools(all_modules, fake_db):
    full = ca._tools_for("eval-tenant", role="owner", message="yes")
    tools, emp, note = ca._pick_employee("eval-tenant", "yes", full)
    assert emp is None and note == "" and tools == full


def test_the_prompt_uses_the_employees_title():
    agent = ca.CommerceAdminSkill()
    with patch("vula.api.tenants.get_config", lambda t: {}):
        prompt = agent._system_prompt("eval-tenant", role="owner", role_label="AI operations assistant")
    assert "You are the owner's AI operations assistant" in prompt


@pytest.mark.asyncio
async def test_hand_over_reruns_with_the_whole_team(all_modules, fake_db, monkeypatch):
    from core.skills.base import SkillInput
    agent = ca.CommerceAdminSkill()
    seen = []

    async def fake_loop(system_msg, history, question, ctx, tools, sources=None):
        names = {t["function"]["name"] for t in tools}
        seen.append(names)
        if len(seen) == 1:
            raise ca.HandOver("they want the website changed")
        return "Done from the whole team."

    monkeypatch.setattr(agent, "_agent_loop", fake_loop)
    monkeypatch.setattr(ca, "_member_access", lambda tid, phone: None)
    out = await agent.run(SkillInput(question="I spent R450 on diesel", tenant_id="eval-tenant",
                                     metadata={"caller_role": "owner", "customer_phone": "27820000001"}))
    assert out.answer == "Done from the whole team."
    assert "hand_over" in seen[0] and "send_broadcast" not in seen[0]
    assert "send_broadcast" in seen[1] and "hand_over" not in seen[1]


def test_sales_reps_are_unchanged(all_modules):
    rep = ca._tools_for("eval-tenant", role="sales_rep", message="I spent R450 on diesel")
    assert {t["function"]["name"] for t in rep} == {t["function"]["name"] for t in ca._REP_TOOL_SPECS}


def test_team_roles_are_insiders_on_a_knowledge_line():
    from vula.api.whatsapp import _is_insider
    assert _is_insider("operations") and _is_insider("bookkeeper") and not _is_insider("customer")


def test_the_team_page_api_is_behind_the_admin_auth():
    import inspect
    from vula.api import commerce
    src = inspect.getsource(commerce)
    assert '@router.get("/{tenant_id}/admin/employees")' in src
    assert '@router.put("/{tenant_id}/admin/employees/{employee}")' in src


# ── the file tree and tasks (31 Oct: workflow → task → prompt / tools / data) ──

TASK_CASES = [(e.id, t.id, c) for e in em.TEAM for t in e.tasks
              for c in (yaml.safe_load((em.ROOT / e.id / "tasks" / t.id / "cases.yaml").read_text()) or [])]


def test_the_team_loads_from_the_file_tree():
    team, shared = em.load()
    assert [e.id for e in team] == ["bookkeeper", "sales", "office_admin", "marketing", "operations"]
    assert "hand_over" in shared and all(e.job and e.tools and e.keywords for e in team)
    assert {t.id for t in em.BY_ID["bookkeeper"].tasks} == {
        "project-bill", "payment-chase", "slips-and-expenses", "month-end", "bank-and-payments", "fix-a-record"}


def test_every_task_has_a_prompt_cases_and_only_its_employees_tools():
    for e in em.TEAM:
        for t in e.tasks:
            assert t.title and t.triggers and t.tools and len(t.prompt) > 80, (e.id, t.id)
            assert t.tools <= e.tools | em.SHARED_TOOLS, (e.id, t.id)
            assert (em.ROOT / e.id / "tasks" / t.id / "cases.yaml").exists()


@pytest.mark.parametrize("emp_id,task_id,case", TASK_CASES, ids=lambda x: x["prompt"][:40] if isinstance(x, dict) else x)
def test_every_task_case_reaches_its_task_with_the_tool_it_needs(emp_id, task_id, case, all_modules):
    emp = em.pick(case["prompt"])
    assert emp is not None and emp.id == emp_id, em.scores(case["prompt"])
    task = em.pick_task(emp, case["prompt"])
    assert task is not None and task.id == case["expect"]
    full = ca._tools_for("eval-tenant", role="owner", message=case["prompt"])
    names = {t["function"]["name"] for t in em.narrow(full, emp, task)}
    assert case["tool"] in names


def test_an_unclear_message_inside_the_job_keeps_the_whole_job():
    emp = em.BY_ID["bookkeeper"]
    assert em.pick_task(emp, "Cancel OTH-00077 and refund her") is None
    assert em.pick_task(emp, "yes") is None
    assert em.pick_task(emp, "Who owes us, and send me the P&L") is None        # two tasks
    assert em.pick_task(emp, "What does the client owe us on Oak Street, balance due") is None


def test_a_switched_off_task_is_never_picked():
    emp = em.BY_ID["bookkeeper"]
    assert em.pick_task(emp, "Send me this month's P&L").id == "month-end"
    assert em.pick_task(emp, "Send me this month's P&L", off={"month-end"}) is None


def test_the_task_prompt_and_its_instructions_go_into_the_job_block():
    emp = em.BY_ID["bookkeeper"]
    task = next(t for t in emp.tasks if t.id == "project-bill")
    block = em.job_block(emp, "", task, "No VAT line on our final accounts.")
    assert "THE TASK — the project bill" in block and "No VAT line" in block
    assert "THE TASK" not in em.job_block(emp, "")


def test_a_broken_task_file_is_refused_with_its_name(tmp_path):
    import shutil
    shutil.copytree(em.ROOT, tmp_path / "employees")
    bad = tmp_path / "employees" / "bookkeeper" / "tasks" / "month-end" / "task.yaml"
    bad.write_text(bad.read_text().replace("tools: [", "tools: [send_broadcast, "))
    with pytest.raises(ValueError, match="month-end"):
        em.load(tmp_path / "employees")


def test_task_settings_per_business(fake_db):
    em.save_task("tenant-a", "bookkeeper", "month-end", enabled=False, by="Owner")
    em.save_task("tenant-a", "bookkeeper", "project-bill", instructions="Our layout, no VAT line.")
    ts = em.task_settings("tenant-a")
    assert em.tasks_off(ts, "bookkeeper") == {"month-end"}
    assert ts[("bookkeeper", "project-bill")]["instructions"] == "Our layout, no VAT line."
    assert em.task_settings("off-the-hook") == {}
    view = {e["id"]: e for e in em.team_view("tenant-a")}
    pb = next(t for t in view["bookkeeper"]["tasks"] if t["id"] == "project-bill")
    assert pb["instructions"] == "Our layout, no VAT line." and pb["enabled"] is True
    with pytest.raises(ValueError):
        em.save_task("tenant-a", "bookkeeper", "fly-to-mars", enabled=False)


def test_the_agent_narrows_to_the_task(all_modules, fake_db):
    full = ca._tools_for("eval-tenant", role="owner", message="Send me this month's P&L")
    tools, emp, note = ca._pick_employee("eval-tenant", "Send me this month's P&L", full)
    names = {t["function"]["name"] for t in tools}
    assert emp.id == "bookkeeper" and "THE TASK — the reports and month-end" in note
    assert "financial_report" in names and "add_expense" not in names and "hand_over" in names



@pytest.mark.asyncio
async def test_a_task_setting_that_cannot_be_saved_is_a_clear_error_not_a_crash(monkeypatch):
    from fastapi import HTTPException
    from vula.api import commerce as api

    with pytest.raises(HTTPException) as e:          # an unknown task is the caller's mistake: 400
        await api.admin_employee_task_update("t1", "bookkeeper", "nope", api.EmployeeIn(enabled=False))
    assert e.value.status_code == 400

    def boom(*a, **k):
        raise RuntimeError("relation vula_employee_tasks does not exist")
    monkeypatch.setattr(em, "save_task", boom)
    with pytest.raises(HTTPException) as e:          # e.g. before migration 237: 503, no internals
        await api.admin_employee_task_update("t1", "bookkeeper", "month-end", api.EmployeeIn(enabled=False))
    assert e.value.status_code == 503 and "relation" not in e.value.detail

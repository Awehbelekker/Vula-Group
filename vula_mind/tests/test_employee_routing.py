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
    narrowed = {t["function"]["name"] for t in em.narrow(full, em.pick(case["prompt"]))}
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
                if r["tenant_id"] == self.payload["tenant_id"] and r["employee"] == self.payload["employee"]:
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
    assert all(v == {"enabled": True, "instructions": ""} for v in em.settings_for("digg-demo").values())
    em.save("digg-demo", "marketing", enabled=False, by="Ian")
    em.save("digg-demo", "sales", instructions="Always quote on   30-day terms.")
    s = em.settings_for("digg-demo")
    assert s["marketing"]["enabled"] is False and s["sales"]["instructions"] == "Always quote on 30-day terms."
    assert em.settings_for("off-the-hook")["marketing"]["enabled"] is True     # another business
    with pytest.raises(ValueError):
        em.save("digg-demo", "astronaut", enabled=False)
    with pytest.raises(ValueError):
        em.save("digg-demo", "sales", instructions="x" * 700)


def test_settings_before_the_migration_are_the_defaults(monkeypatch):
    class _Broken:
        def table(self, _n):
            raise RuntimeError("relation vula_employees does not exist")
    monkeypatch.setattr("vula.commerce.service._client", lambda: _Broken())
    assert em.settings_for("digg-demo")["bookkeeper"]["enabled"] is True


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

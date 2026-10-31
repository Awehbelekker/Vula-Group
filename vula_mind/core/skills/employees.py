"""
core/skills/employees.py — the business's team of AI employees, behind one WhatsApp number.

31 Oct 2026 (Ian): "a ready-made team every business gets", not a build-your-own-agent tool. The
owner's messages used to reach one admin agent holding up to ~95 tools (commerce_admin), introduced
by one job title (_ROLE_LABELS). Giving a model more tools than the job needs is the main cause of a
wrong first move, so each employee here has a job description and only the tools that job uses.

The front desk (pick) is deterministic — no model. It hands a message to an employee only when one
clearly fits; anything unclear (no hit, a tie, "yes"/"confirm"/"thanks", a bare photo) stays with the
whole team, which is exactly what every message got before this existed. Every employee also has a
hand_over tool: if it was given the wrong job it says so and the turn re-runs with the whole team,
so a wrong hand-over costs one step, never a wrong answer.

What the business can change (vula_employees, migration 236): switch an employee off, and give it
instructions in plain words. Everything is on by default, so nothing changes until an owner edits.

31 Oct 2026 (Ian's whiteboard: workflow → task → prompt / tools / data): the team lives in files,
vula_mind/employees/ — one folder per employee (employee.yaml, job.md) with its tasks
(tasks/<task>/task.yaml, prompt.md, cases.yaml, data/). After the front desk picks the employee,
pick_task picks a task the same way (clear winner on its trigger words, else the employee's whole
job); the task narrows the tools again and adds its own steps to the prompt. A business can switch a
task off or give it instructions (vula_employee_tasks, migration 237). The prompts are data, so a new
model runs the same files and the same cases check it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

TABLE = "vula_employees"
MAX_INSTRUCTIONS = 600

HAND_OVER_TOOL = {"type": "function", "function": {
    "name": "hand_over",
    "description": (
        "Use ONLY when this request is clearly outside your job and none of your tools can do it "
        "(e.g. you're the bookkeeper and they ask to change the website). The whole team then "
        "takes it. Never use it for a question you can answer or a tool you have."
    ),
    "parameters": {"type": "object", "properties": {
        "reason": {"type": "string", "description": "One line: what they asked for."}}}}}


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    summary: str
    triggers: Tuple[str, ...]
    tools: FrozenSet[str]
    prompt: str


@dataclass(frozen=True)
class Employee:
    id: str
    title: str
    emoji: str
    summary: str          # one line for the owner's "Your team" page
    job: str              # the job description, in the agent's prompt
    tools: FrozenSet[str]
    keywords: Tuple[str, ...] = field(default=())
    tasks: Tuple[Task, ...] = field(default=())


ROOT = Path(__file__).resolve().parents[2] / "employees"


def _lower(words) -> Tuple[str, ...]:
    return tuple(str(w).lower() for w in (words or []))


def _load_task(emp_id: str, d: Path) -> Task:
    spec = yaml.safe_load((d / "task.yaml").read_text()) or {}
    return Task(id=d.name, title=spec["title"], summary=spec.get("summary", ""),
                triggers=_lower(spec.get("triggers")), tools=frozenset(spec.get("tools") or []),
                prompt=(d / "prompt.md").read_text().strip())


def load(root: Path = ROOT) -> Tuple[Tuple[Employee, ...], FrozenSet[str]]:
    """The team and the shared tools, from the file tree. Raises on a broken tree (the caller
    decides what to do); a task's tools must be the employee's own or shared."""
    team = yaml.safe_load((root / "team.yaml").read_text())
    shared = frozenset(team["shared_tools"])
    out = []
    for emp_id in team["order"]:
        d = root / emp_id
        spec = yaml.safe_load((d / "employee.yaml").read_text())
        tools = frozenset(spec["tools"])
        tasks = tuple(_load_task(emp_id, t) for t in sorted((d / "tasks").glob("*")) if (t / "task.yaml").exists())
        for t in tasks:
            stray = t.tools - tools - shared
            if stray:
                raise ValueError(f"{emp_id}/{t.id}: tools not in the employee's job: {sorted(stray)}")
        out.append(Employee(id=spec["id"], title=spec["title"], emoji=spec["emoji"], summary=spec["summary"],
                            job=(d / "job.md").read_text().strip(), tools=tools,
                            keywords=_with_triggers(_lower(spec.get("keywords")), tasks), tasks=tasks))
    return tuple(out), shared


def _with_triggers(keywords: tuple, tasks: tuple) -> tuple:
    """The front-desk words plus every task's trigger words: a message that is clearly one of this
    employee's tasks reaches this employee, without keeping two lists in step by hand. A trigger
    that already contains a front-desk word ("remind me" with "remind") adds nothing — it would
    only count the same words twice and tip the front desk."""
    out = list(keywords)
    for k in (k for t in tasks for k in t.triggers):
        if k not in out and not any(w in k for w in out):
            out.append(k)
    return tuple(out)


try:
    TEAM, SHARED_TOOLS = load()
except Exception as exc:  # noqa: BLE001 — no team: the front desk sends everything to the whole team
    logger.error("AI employees not loaded from %s (%s): every message goes to the whole team", ROOT, exc)
    TEAM, SHARED_TOOLS = (), frozenset({"hand_over"})

BY_ID: Dict[str, Employee] = {e.id: e for e in TEAM}

# Short replies that continue the last turn ("yes", "confirm 2") must keep every tool the earlier
# step might need, so they never go to one employee.
_CONTINUATION = re.compile(
    r"^\s*(yes|yep|yeah|ja|no|nope|nee|ok|okay|sure|confirm|cancel|thanks|thank you|dankie|"
    r"go ahead|do it|send it|that one|the (first|second|third|last) one|\d{1,2})\b[\s.!]*\S{0,40}$",
    re.I)


def _hits(emp: Employee, text: str) -> int:
    return sum(1 for k in emp.keywords if re.search(r"(?<![a-z])" + re.escape(k), text))


def scores(message: str) -> Dict[str, int]:
    # "purchase order" is operations' word; written as one token so sales' "order" doesn't match it.
    text = " " + re.sub(r"purchase\s+orders?", "purchaseorder", (message or "").lower()) + " "
    return {e.id: _hits(e, text) for e in TEAM}


def pick(message: str, enabled: Optional[set] = None) -> Optional[Employee]:
    """The one employee this message clearly belongs to, or None (the whole team takes it).
    `enabled`: the employee ids the business has on (None = all)."""
    text = (message or "").strip()
    if not text or _CONTINUATION.match(text) or text.startswith("[What's in the photo"):
        return None
    s = {k: v for k, v in scores(text).items() if enabled is None or k in enabled}
    if not s:
        return None
    ranked = sorted(s.items(), key=lambda kv: -kv[1])
    top_id, top = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0
    if top == 0 or top == runner:
        return None
    return BY_ID[top_id]


def pick_task(emp: Optional[Employee], message: str, off: Optional[set] = None) -> Optional[Task]:
    """The one task inside this employee's job the message clearly is, or None (the employee's whole
    job). Stricter than the front desk: only one task's trigger words may appear (a message that
    touches two tasks keeps the whole job, so it never loses a tool), and never a continuation.
    `off`: task ids the business switched off."""
    text = (message or "").strip()
    if emp is None or not emp.tasks or not text or _CONTINUATION.match(text):
        return None
    low = " " + text.lower() + " "
    s = sorted(((sum(1 for k in t.triggers if re.search(r"(?<![a-z])" + re.escape(k), low)), t)
                for t in emp.tasks if not off or t.id not in off), key=lambda x: -x[0])
    if not s or s[0][0] == 0 or (len(s) > 1 and s[1][0] > 0):
        return None                # no task, or the message touches two: keep the whole job
    return s[0][1]


def narrow(tools: List[Dict], emp: Optional[Employee], task: Optional[Task] = None) -> List[Dict]:
    """The caller's tools cut down to this employee's job — or, with a task, to that task's tools —
    plus the shared ones and hand_over. The input is already gated by modules and the caller's
    access, so this only ever removes."""
    if emp is None:
        return tools
    keep = (task.tools if task else emp.tools) | SHARED_TOOLS
    out = [t for t in tools if t["function"]["name"] in keep]
    if not any(t["function"]["name"] == "hand_over" for t in out):
        out.append(HAND_OVER_TOOL)
    return out


def without_switched_off(tools: List[Dict], enabled: set) -> List[Dict]:
    """The whole team's tools minus those only a switched-off employee has."""
    off = [e for e in TEAM if e.id not in enabled]
    if not off:
        return tools
    still = set(SHARED_TOOLS).union(*(e.tools for e in TEAM if e.id in enabled))
    only_off = set().union(*(e.tools for e in off)) - still
    return [t for t in tools if t["function"]["name"] not in only_off]


def job_block(emp: Employee, instructions: str = "", task: Optional[Task] = None,
              task_instructions: str = "") -> str:
    """The employee's job description (and the owner's instructions for it), and the task's own
    instructions when one was picked, for the agent's prompt."""
    out = f"\n\nYOUR JOB — {emp.title}: {emp.job}"
    if (instructions or "").strip():
        out += ("\nTHE OWNER'S INSTRUCTIONS FOR YOU (follow them in this job): "
                + instructions.strip()[:MAX_INSTRUCTIONS])
    if task is not None:
        out += "\n\n" + task.prompt
        if (task_instructions or "").strip():
            out += ("\nTHE OWNER'S INSTRUCTIONS FOR THIS TASK (they win over the steps above): "
                    + task_instructions.strip()[:MAX_INSTRUCTIONS])
    out += ("\nIf the request is clearly another colleague's job and none of your tools fit, call "
            "hand_over rather than saying you can't.")
    return out


# ── the business's settings (migration 236) ─────────────────────────────────

def _client():
    from vula.commerce import service
    return service._client()


def settings_for(tenant_id: str) -> Dict[str, Dict]:
    """{employee_id: {"enabled": bool, "instructions": str}} for every employee; defaults (on, no
    instructions) when nothing is saved or before migration 236."""
    out = {e.id: {"enabled": True, "instructions": ""} for e in TEAM}
    try:
        rows = (_client().table(TABLE).select("employee,enabled,instructions")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("employee settings unreadable: %s", exc)
        return out
    for r in rows:
        if r.get("employee") in out:
            out[r["employee"]] = {"enabled": r.get("enabled") is not False,
                                  "instructions": (r.get("instructions") or "").strip()}
    return out


def save(tenant_id: str, employee: str, *, enabled: Optional[bool] = None,
         instructions: Optional[str] = None, by: Optional[str] = None) -> Dict:
    """Switch an employee on/off or set its instructions. Read back. ValueError on bad input."""
    if employee not in BY_ID:
        raise ValueError("There's no such employee.")
    text = None if instructions is None else re.sub(r"\s+", " ", instructions).strip()
    if text is not None and len(text) > MAX_INSTRUCTIONS:
        raise ValueError(f"Keep the instructions under {MAX_INSTRUCTIONS} characters.")
    current = settings_for(tenant_id)[employee]
    from datetime import datetime, timezone
    row = {"tenant_id": tenant_id, "employee": employee,
           "enabled": current["enabled"] if enabled is None else bool(enabled),
           "instructions": current["instructions"] if text is None else text,
           "updated_by": (by or "").strip() or None,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    db = _client()
    db.table(TABLE).upsert(row, on_conflict="tenant_id,employee").execute()
    back = (db.table(TABLE).select("employee,enabled,instructions").eq("tenant_id", tenant_id)
            .eq("employee", employee).limit(1).execute().data or [])
    if not back or back[0].get("enabled") != row["enabled"] or \
            (back[0].get("instructions") or "") != (row["instructions"] or ""):
        raise RuntimeError("the change was not saved")
    return {"employee": employee, "enabled": back[0]["enabled"],
            "instructions": back[0].get("instructions") or ""}


TASK_TABLE = "vula_employee_tasks"


def task_settings(tenant_id: str) -> Dict[Tuple[str, str], Dict]:
    """{(employee, task): {"enabled", "instructions"}} for what the business changed (migration 237);
    a task with no row is on, with no instructions. Empty before the migration."""
    try:
        rows = (_client().table(TASK_TABLE).select("employee,task,enabled,instructions")
                .eq("tenant_id", tenant_id).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("task settings unreadable: %s", exc)
        return {}
    return {(r["employee"], r["task"]): {"enabled": r.get("enabled") is not False,
                                         "instructions": (r.get("instructions") or "").strip()}
            for r in rows if r.get("employee") and r.get("task")}


def tasks_off(settings: Dict[Tuple[str, str], Dict], employee: str) -> set:
    return {t for (e, t), v in settings.items() if e == employee and not v["enabled"]}


def save_task(tenant_id: str, employee: str, task: str, *, enabled: Optional[bool] = None,
              instructions: Optional[str] = None, by: Optional[str] = None) -> Dict:
    """Switch one task off/on or give it instructions, for this business only. Read back."""
    emp = BY_ID.get(employee)
    if emp is None or task not in {t.id for t in emp.tasks}:
        raise ValueError("There's no such task.")
    text = None if instructions is None else re.sub(r"\s+", " ", instructions).strip()
    if text is not None and len(text) > MAX_INSTRUCTIONS:
        raise ValueError(f"Keep the instructions under {MAX_INSTRUCTIONS} characters.")
    current = task_settings(tenant_id).get((employee, task), {"enabled": True, "instructions": ""})
    from datetime import datetime, timezone
    row = {"tenant_id": tenant_id, "employee": employee, "task": task,
           "enabled": current["enabled"] if enabled is None else bool(enabled),
           "instructions": current["instructions"] if text is None else text,
           "updated_by": (by or "").strip() or None,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    db = _client()
    db.table(TASK_TABLE).upsert(row, on_conflict="tenant_id,employee,task").execute()
    back = (db.table(TASK_TABLE).select("enabled,instructions").eq("tenant_id", tenant_id)
            .eq("employee", employee).eq("task", task).limit(1).execute().data or [])
    if not back or back[0].get("enabled") != row["enabled"] or \
            (back[0].get("instructions") or "") != (row["instructions"] or ""):
        raise RuntimeError("the change was not saved")
    return {"employee": employee, "task": task, "enabled": back[0]["enabled"],
            "instructions": back[0].get("instructions") or ""}


def team_view(tenant_id: str) -> List[Dict]:
    """The "Your team" page: each employee, what it does, its tasks, and the business's settings."""
    s = settings_for(tenant_id)
    ts = task_settings(tenant_id)
    return [{"id": e.id, "title": e.title, "emoji": e.emoji, "summary": e.summary,
             "enabled": s[e.id]["enabled"], "instructions": s[e.id]["instructions"],
             "tasks": [{"id": t.id, "title": t.title, "summary": t.summary,
                        **ts.get((e.id, t.id), {"enabled": True, "instructions": ""})} for t in e.tasks]}
            for e in TEAM]

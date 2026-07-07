"""
core/skills/clickup_admin.py — manage ClickUp from WhatsApp.

Lets a tenant run their ClickUp over WhatsApp, conversationally:

    "add a task: site inspection Erf 214 Friday"  → create_task
    "what's due this week?"                        → list_tasks
    "mark the foundation task done"                → update_task_status
    "remind me to call the engineer tomorrow"      → set_reminder

Multi-tenant: every tool is scoped by inp.tenant_id and resolves that tenant's
own ClickUp token. If the tenant hasn't connected ClickUp, the skill returns a
low-confidence "connect first" message so the orchestrator can fall back.

Mirrors the tool-calling pattern of core/skills/commerce_admin.py.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from core.llm_router import resolve_generation_route
from core.skills.base import BaseSkill, SkillInput, SkillOutput
from vula.clickup import service
from vula.clickup.service import ClickUpNotConnected
from vula.clickup.credentials import get_tenant_clickup_creds

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 3

TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "create_task",
        "description": "Create a new ClickUp task. For 'set up/schedule/book a meeting' requests, "
                       "create a task titled 'Meeting: <subject>' with the parsed date/time — this "
                       "is a reminder task, NOT a calendar invite (Vula has no calendar integration).",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Task title"},
            "description": {"type": "string", "description": "Optional details"},
            "due_date": {"type": "string", "description": "Optional due date, ISO e.g. 2026-06-30"},
            "assignee": {"type": "string", "description": "Name of the person to assign this task to, e.g. 'Nolo'"}},
            "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "list_tasks",
        "description": "List the tenant's ClickUp tasks, optionally filtered by status and/or assignee.",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "description": "e.g. open, in progress, complete"},
            "assignee": {"type": "string", "description": "Name of the person to filter by, e.g. 'Nolo'"},
            "limit": {"type": "integer"}}},
    }},
    {"type": "function", "function": {
        "name": "update_task_status",
        "description": "Update a task's status by matching its title.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "A fragment of the task title to match"},
            "status": {"type": "string", "description": "New status, e.g. complete, in progress"}},
            "required": ["title", "status"]},
    }},
    {"type": "function", "function": {
        "name": "set_reminder",
        "description": "Create a due-dated reminder task.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "due_date": {"type": "string", "description": "ISO date/time the reminder is for"}},
            "required": ["title", "due_date"]},
    }},
]


class ClickUpAdminSkill(BaseSkill):
    name = "clickup_admin"
    description = "Create, list, and update ClickUp tasks and reminders over WhatsApp."

    async def run(self, inp: SkillInput) -> SkillOutput:
        # Defer to other skills if this tenant hasn't connected ClickUp.
        if not get_tenant_clickup_creds(inp.tenant_id):
            return SkillOutput(
                answer="ClickUp isn't connected for your account yet. Ask your Vula admin "
                       "to connect ClickUp, then I can manage tasks for you here.",
                skill_name=self.name, confidence=0.25,
            )
        ctx = {"tenant_id": inp.tenant_id}
        try:
            answer = await self._agent_loop(inp.conversation_history, inp.question, ctx)
            if not answer:
                raise RuntimeError("empty answer from clickup agent loop")
            return SkillOutput(answer=answer, skill_name=self.name, confidence=0.8)
        except Exception as exc:
            logger.warning("clickup_admin loop failed (%s)", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

    def _system_prompt(self) -> str:
        return (
            "You are Vula's task assistant, managing the user's ClickUp over WhatsApp. "
            "Use the tools to create, list, and update tasks and reminders — never invent "
            "task data. Parse natural dates (e.g. 'Friday', 'tomorrow') into ISO dates when "
            "calling tools. If the user names a person ('give Nolo a todo', 'what's on Nolo's "
            "plate'), pass their name as the assignee argument. If a create_task result comes "
            "back with assignee_not_found, tell the user plainly that no matching team member "
            "was found and that the task was created unassigned — never claim it was assigned. "
            "'Set up/schedule/book a meeting' has no calendar integration — create a task titled "
            "'Meeting: <subject>' with the parsed date and tell the user it's noted as a "
            "task/reminder, never say a calendar invite was sent. Keep replies short and "
            "WhatsApp-friendly, and confirm back what you created or changed."
        )

    # ── Agent loop (mirrors commerce_admin) ──────────────────────────────────
    async def _agent_loop(self, history: str, question: str, ctx: Dict[str, Any]) -> str:
        import litellm
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()

        messages: List[Dict[str, Any]] = [{"role": "system", "content": self._system_prompt()}]
        if history:
            messages.append({"role": "user", "content": f"(Earlier conversation)\n{history}"})
        messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await litellm.acompletion(
                model=model, messages=messages, tools=TOOL_SPECS, tool_choice="auto",
                temperature=0.2, max_tokens=900, api_key=api_key, api_base=api_base,
            )
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)

            if not tool_calls:
                inline = self._parse_inline_toolcall(msg.content or "")
                if inline:
                    name, args = inline
                    result = await self._dispatch_tool(name, args, ctx)
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content": (
                        f"[tool {name} returned]: {json.dumps(result, default=str)}\n"
                        "Reply to the user in plain, short WhatsApp language using this data. "
                        "Do not output JSON or tool calls."
                    )})
                    continue
                return (msg.content or "").strip()

            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                               for tc in tool_calls],
            })
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                result = await self._dispatch_tool(tc.function.name, args, ctx)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "name": tc.function.name, "content": json.dumps(result, default=str)})

        resp = await litellm.acompletion(
            model=model, messages=messages, temperature=0.2, max_tokens=500,
            api_key=api_key, api_base=api_base,
        )
        answer = (resp.choices[0].message.content or "").strip()

        inline = self._parse_inline_toolcall(answer)
        if inline:
            name, args = inline
            result = await self._dispatch_tool(name, args, ctx)
            resp = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": "Summarise this for the user in short, plain WhatsApp language. No JSON."},
                    {"role": "user", "content": json.dumps(result, default=str)},
                ],
                temperature=0.2, max_tokens=300, api_key=api_key, api_base=api_base,
            )
            answer = (resp.choices[0].message.content or "").strip()
        return answer

    _TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}

    def _parse_inline_toolcall(self, content: str):
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
        name = obj.get("function") or obj.get("name") or obj.get("tool")
        args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if name in self._TOOL_NAMES and isinstance(args, dict):
            return name, args
        return None

    # ── Tool dispatch ─────────────────────────────────────────────────────────
    async def _dispatch_tool(self, name: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
        tid = ctx["tenant_id"]
        try:
            if name == "create_task":
                assignee_name = args.get("assignee")
                assignees, assignee_not_found = None, None
                if assignee_name:
                    member = await service.resolve_assignee(tid, assignee_name)
                    if member:
                        assignees = [member["id"]]
                    else:
                        assignee_not_found = assignee_name
                result = await service.create_task(tid, title=args.get("title", ""),
                                                   description=args.get("description", ""),
                                                   due_date=args.get("due_date"),
                                                   assignees=assignees)
                if assignee_not_found and isinstance(result, dict):
                    result["assignee_not_found"] = assignee_not_found
                return result
            if name == "list_tasks":
                assignee_id = None
                if args.get("assignee"):
                    member = await service.resolve_assignee(tid, args["assignee"])
                    if not member:
                        return {"error": f"No team member found matching '{args['assignee']}'."}
                    assignee_id = member["id"]
                return await service.list_tasks(tid, status=args.get("status"),
                                               limit=args.get("limit", 15),
                                               assignee_id=assignee_id)
            if name == "update_task_status":
                return await service.update_task_status_by_name(tid, args.get("title", ""),
                                                               args.get("status", ""))
            if name == "set_reminder":
                return await service.set_reminder(tid, title=args.get("title", ""),
                                                 due_date=args.get("due_date", ""))
        except ClickUpNotConnected:
            return {"error": "ClickUp is not connected for this tenant."}
        except Exception as exc:
            logger.warning("clickup tool %s failed: %s", name, exc)
            return {"error": str(exc)}
        return {"error": f"unknown tool {name}"}

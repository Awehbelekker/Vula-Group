"""
core/skills/google_admin.py — Google Drive over WhatsApp/portal (email: see email_admin).

    "find the Bokaap fee proposal in my drive"      → drive_search
    "pull in that file"                              → drive_pull (downloads + files into KB)

Defers (low confidence) if the tenant hasn't connected Google. Email questions go to
email_admin (IMAP/SMTP connector); the Gmail tools were removed from this skill.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from core.llm_router import resolve_generation_route, reply_or_fallback, substitute_if_degenerate
from core.prompt_safety import fence
from core.skills.base import BaseSkill, SkillInput, SkillOutput, behaviour_preamble
from vula.google import service
from vula.google.service import GoogleNotConnected

logger = logging.getLogger(__name__)
MAX_TOOL_ITERATIONS = 5

TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "drive_search", "description": "Search the user's Google Drive by file name.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "drive_pull",
        "description": "Download a Drive file (by id from drive_search) and file it into the "
                       "knowledge base so the assistant can answer questions about it.",
        "parameters": {"type": "object", "properties": {
            "file_id": {"type": "string"}, "name": {"type": "string"}}, "required": ["file_id"]}}},
    # Gmail is handled by the IMAP/SMTP email connector (email_admin) — no Gmail OAuth
    # scopes are requested, so Google Drive verification stays out of the CASA tier.
]
_TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}


class GoogleAdminSkill(BaseSkill):
    name = "google_admin"
    description = "Search and file documents the user shares from Google Drive (via the Picker)."

    async def run(self, inp: SkillInput) -> SkillOutput:
        from vula.google.credentials import get_access_token
        if not await get_access_token(inp.tenant_id):
            return SkillOutput(
                answer="Google isn't connected yet. Connect Google Drive in Settings, "
                       "then I can find your files here.",
                skill_name=self.name, confidence=0.25)
        try:
            answer = await self._loop(inp.conversation_history, inp.question, inp.tenant_id)
            answer = substitute_if_degenerate(answer or "", skill=self.name, tenant_id=inp.tenant_id)
            if not (answer or "").strip():
                return SkillOutput(answer=reply_or_fallback(answer, skill=self.name),
                                   skill_name=self.name, confidence=0.2)
            return SkillOutput(answer=answer, skill_name=self.name, confidence=0.8)
        except Exception as exc:
            logger.warning("google_admin failed: %s", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

    def _system(self) -> str:
        # Gmail tools were removed (email goes through email_admin's IMAP/SMTP connector), so
        # the prompt no longer promises email — it made the model offer things it couldn't do.
        return ("You are Vula, managing the user's Google Drive.\n\n" + behaviour_preamble(agentic=True) +
                "\nUse the tools — never invent files. For email, tell the user to ask about "
                "their mailbox (it's handled separately). Keep replies short and WhatsApp-friendly.")

    async def _loop(self, history: str, question: str, tenant_id: str) -> str:
        import litellm
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()
        messages: List[Dict[str, Any]] = [{"role": "system", "content": self._system()}]
        if history:
            messages.append({"role": "user", "content": f"(Conversation so far)\n{history}"})
        messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await litellm.acompletion(model=model, messages=messages, tools=TOOL_SPECS,
                tool_choice="auto", temperature=0.2, max_tokens=700, api_key=api_key, api_base=api_base)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                inline = self._inline(msg.content or "")
                if inline:
                    name, args = inline
                    result = await self._dispatch(name, args, tenant_id)
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content":
                        f"[{name} returned]:{fence('TOOL_RESULT', json.dumps(result, default=str)[:1500])}\n"
                        "Reply to the user in short plain language. No JSON."})
                    continue
                return (msg.content or "").strip()
            messages.append({"role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls]})
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                result = await self._dispatch(tc.function.name, args, tenant_id)
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.function.name,
                                 "content": fence('TOOL_RESULT', json.dumps(result, default=str)[:1800])})

        # See commerce_admin.py's _agent_loop for why this nudge exists (2026-08-22 real
        # fabricated-success incident, a different skill but the same exhausted-budget shape).
        messages.append({"role": "user", "content": (
            "You were not able to complete this within the available attempts. Do NOT claim a "
            "file was found or an email was drafted unless a tool result above actually shows "
            "that. Tell the user plainly what's missing or what went wrong instead."
        )})
        resp = await litellm.acompletion(model=model, messages=messages, temperature=0.2,
            max_tokens=500, api_key=api_key, api_base=api_base)
        return (resp.choices[0].message.content or "").strip()

    def _inline(self, content: str):
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
        return (name, args) if name in _TOOL_NAMES and isinstance(args, dict) else None

    async def _dispatch(self, name: str, args: Dict[str, Any], tenant_id: str) -> Any:
        try:
            if name == "drive_search":
                return {"files": await service.drive_search(tenant_id, args.get("query", ""))}
            if name == "drive_pull":
                f = await service.drive_download(tenant_id, args.get("file_id", ""))
                from config import settings
                from vula.ingestion.pipeline import VulaIngestionPipeline
                d = settings.upload_dir / tenant_id
                d.mkdir(parents=True, exist_ok=True)
                from vula.uploads import safe_upload_path
                p: Path = safe_upload_path(d, f["name"])
                p.write_bytes(f["data"])
                res = await VulaIngestionPipeline(tenant_id=tenant_id).ingest_file(p, source_type="document")
                return {"filed": f["name"], "chunks": getattr(res, "chunks_stored", 0)}
        except GoogleNotConnected:
            return {"error": "Google not connected."}
        except Exception as exc:
            logger.warning("google tool %s failed: %s", name, exc)
            return {"error": str(exc)}
        return {"error": f"unknown tool {name}"}

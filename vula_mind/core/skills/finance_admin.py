"""
core/skills/finance_admin.py — answer money questions from the project finance ledger.

    "how much have we spent on HPC?"          → project_spend
    "what's left on the Bokaap budget?"        → budget_status
    "money in vs out this month?"              → money_in_out
    "who is account 805515?"                   → supplier_lookup

Reads vula_project_finances + vula_project_budgets (populated by filed invoices/payments).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from core.llm_router import resolve_generation_route
from core.skills.base import BaseSkill, SkillInput, SkillOutput, behaviour_preamble

logger = logging.getLogger(__name__)
MAX_TOOL_ITERATIONS = 3

TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "project_spend", "description": "Total money in/out (and recent items) for a project.",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}}, "required": ["project"]}}},
    {"type": "function", "function": {
        "name": "budget_status", "description": "Budget vs actual spend (remaining) for a project, or all projects.",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "money_in_out", "description": "Totals of money in vs out across projects, optionally for a period.",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "enum": ["this_month", "last_30", "all"]}}}}},
    {"type": "function", "function": {
        "name": "supplier_lookup",
        "description": "Find a supplier/beneficiary by bank account number or name; shows their payments + projects.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]
_TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}


class FinanceAdminSkill(BaseSkill):
    name = "finance_admin"
    description = "Answer money/budget/supplier questions from the project finance ledger."

    async def run(self, inp: SkillInput) -> SkillOutput:
        self._verified: List[float] = []  # every numeric value seen in a tool result this turn
        try:
            answer = await self._loop(inp.conversation_history, inp.question, inp.tenant_id)
            if not answer:
                return SkillOutput(answer="I couldn't find any financial records for that.",
                                   skill_name=self.name, confidence=0.8)
        except Exception as exc:
            logger.warning("finance_admin failed: %s", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

        # 2026-08 accuracy audit: unlike calculations.py (anchor check) or commerce_admin.py's
        # mutating tools (post-write readback), nothing previously verified that this skill's
        # prose reply actually matches the money figures its own tools returned — the model
        # could transpose/misreport a real number with nothing catching it. Same soft
        # anchor-and-caveat shape as calculations.py, not a hard block.
        anchored, unmatched = self._verify_answer(answer)
        confidence = 0.8
        if anchored is False:
            confidence = 0.45
            answer += ("\n\n⚠️ Please confirm these figures — some of the numbers above "
                       "couldn't be matched to what the ledger actually returned.")
        return SkillOutput(answer=answer, skill_name=self.name, confidence=confidence)

    def _system(self) -> str:
        return ("You are Vula, answering questions about the business's money from its project "
                "finance ledger (invoices and payments filed from email/WhatsApp).\n\n" + behaviour_preamble() +
                "\n- Use the tools to fetch real figures; never invent amounts.\n"
                "- Format money as South African Rand (e.g. R18,000). Keep answers short and WhatsApp-friendly.\n"
                "- If a project name is fuzzy, pass the user's wording; the tools match loosely.")

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
                    result = self._dispatch(name, args, tenant_id)
                    self._verified.extend(self._extract_candidates(result))
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content":
                        f"[{name} returned]: {json.dumps(result, default=str)[:1500]}\n"
                        "Answer the user in short plain language. No JSON."})
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
                result = self._dispatch(tc.function.name, args, tenant_id)
                self._verified.extend(self._extract_candidates(result))
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.function.name,
                                 "content": json.dumps(result, default=str)[:1800]})

        resp = await litellm.acompletion(model=model, messages=messages, temperature=0.2,
            max_tokens=400, api_key=api_key, api_base=api_base)
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

    # ── accuracy anchor (2026-08 audit) ──────────────────────────────────────────
    @staticmethod
    def _numbers(text: str) -> List[float]:
        return [float(m.replace(",", "")) for m in re.findall(r"-?\d[\d,]*\.?\d*", text or "")]

    def _extract_candidates(self, result: Any) -> List[float]:
        """Every numeric value in a tool result, plus its /100 form — vula_project_finances
        stores rand directly (not cents, unlike the commerce_* tables), but this stays
        defensive of either convention rather than assuming one."""
        raw = self._numbers(json.dumps(result, default=str))
        out = set()
        for v in raw:
            out.add(round(v, 2))
            out.add(round(v / 100, 2))
        return list(out)

    def _verify_answer(self, answer: str):
        """Every money-shaped number in the reply (>=10, to skip incidental small counts like
        '3 projects') must be anchored to a real tool-returned figure. Returns
        (all_anchored: True|False|None, unmatched_numbers)."""
        if not self._verified:
            return (None, [])
        ans_nums = [n for n in self._numbers(answer) if abs(n) >= 10]
        if not ans_nums:
            return (None, [])
        unmatched = []
        for a in ans_nums:
            tol = max(0.5, abs(a) * 0.01)
            if not any(abs(a - v) <= tol for v in self._verified):
                unmatched.append(a)
        return (len(unmatched) == 0, unmatched)

    # ── tools ──────────────────────────────────────────────────────────────────
    def _match_project(self, tenant_id: str, summary: dict, hint: str) -> str:
        hint_l = (hint or "").lower()
        for p in summary.get("projects", []):
            if p["project"] and p["project"].lower().replace("_", " ") in hint_l.replace("_", " "):
                return p["project"]
            toks = set(re.findall(r"[a-z0-9]{3,}", p["project"].lower()))
            if toks & set(re.findall(r"[a-z0-9]{3,}", hint_l)):
                return p["project"]
        return hint

    def _dispatch(self, name: str, args: Dict[str, Any], tenant_id: str) -> Any:
        from vula.integrations.finances import finance_summary, _client
        try:
            if name in ("project_spend", "budget_status"):
                full = finance_summary(tenant_id)
                proj = self._match_project(tenant_id, full, args.get("project", ""))
                one = finance_summary(tenant_id, proj)
                row = next((p for p in one["projects"]), None)
                if not row:
                    return {"project": proj, "found": False, "note": "no financial records for this project"}
                return {"project": row["project"], "money_in": row["in"], "money_out": row["out"],
                        "net": row["net"], "budget": row["budget"], "remaining": row["remaining"],
                        "transactions": row["count"]}
            if name == "money_in_out":
                period = args.get("period", "all")
                rows = (_client().table("vula_project_finances").select("amount,direction,occurred_at,created_at")
                        .eq("tenant_id", tenant_id).execute().data or [])
                if period in ("this_month", "last_30"):
                    if period == "last_30":
                        cut = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
                    else:
                        cut = datetime.now(timezone.utc).date().replace(day=1).isoformat()
                    rows = [r for r in rows if (r.get("occurred_at") or r.get("created_at", "")[:10]) >= cut]
                tin = round(sum(float(r["amount"]) for r in rows if r.get("direction") == "in"), 2)
                tout = round(sum(float(r["amount"]) for r in rows if r.get("direction") == "out"), 2)
                return {"period": period, "money_in": tin, "money_out": tout, "net": round(tin - tout, 2),
                        "transactions": len(rows)}
            if name == "supplier_lookup":
                q = (args.get("query") or "").strip()
                digits = re.sub(r"\D", "", q)
                rows = (_client().table("vula_project_finances").select("*")
                        .eq("tenant_id", tenant_id).execute().data or [])
                hits = [r for r in rows if (digits and digits in re.sub(r"\D", "", r.get("bank_account") or ""))
                        or (q.lower() in (r.get("counterparty") or "").lower())]
                if not hits:
                    return {"query": q, "found": False}
                total = round(sum(float(h["amount"]) for h in hits), 2)
                projects = sorted({h.get("project") for h in hits if h.get("project")})
                names = sorted({h.get("counterparty") for h in hits if h.get("counterparty")})
                return {"query": q, "found": True, "names": names, "account": next(
                    (h.get("bank_account") for h in hits if h.get("bank_account")), None),
                    "total": total, "payments": len(hits), "projects": projects}
        except Exception as exc:
            logger.warning("finance tool %s failed: %s", name, exc)
            return {"error": str(exc)}
        return {"error": f"unknown tool {name}"}

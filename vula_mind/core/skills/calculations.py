"""
core/skills/calculations.py — deterministic engineering/QS calculations.

LLMs are unreliable at arithmetic (this is what produced the 350-vs-850 errors).
This skill gives the model a `calculate` tool that evaluates arithmetic exactly,
so the NUMBERS are deterministic. The RULE/ratio must come from the user's stated
values or the cited standard in context — the skill never invents a code rule.

Mirrors the tool-loop pattern of core/skills/clickup_admin.py.
"""
from __future__ import annotations

import ast
import json
import logging
import math
import operator
import re
from typing import Any, Dict, List

from core.llm_router import resolve_generation_route
from core.prompt_safety import fence
from core.skills.base import BaseSkill, SkillInput, SkillOutput, behaviour_preamble

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

# ── Safe arithmetic evaluator (no eval) ───────────────────────────────────────
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCS = {
    "ceil": math.ceil, "floor": math.floor, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "abs": abs,
}


def safe_eval(expr: str) -> float:
    """Evaluate a plain arithmetic expression. Supports + - * / // % ** , parentheses,
    and ceil/floor/round/min/max/sqrt/abs. Raises ValueError on anything else."""
    def ev(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("non-numeric constant")
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError("unsupported expression")
    return ev(ast.parse(expr, mode="eval").body)


TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "calculate",
        "description": "Evaluate an arithmetic expression EXACTLY and return the number. "
                       "Use for every calculation. Supports + - * / // % ** (), and "
                       "ceil(), floor(), round(), min(), max(), sqrt(), abs().",
        "parameters": {"type": "object", "properties": {
            "expression": {"type": "string", "description": "e.g. 'ceil(850 / 190)' or '1500 / 5'"},
        }, "required": ["expression"]},
    }},
    {"type": "function", "function": {
        "name": "lookup_rate",
        "description": "Look up the tenant's OWN unit rate (ZAR) for a material/work item from "
                       "their QS rate library, for cost calculations. Returns matching rates with "
                       "unit. Use before computing any cost — never assume a market rate.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "e.g. 'brick wall' or 'acoustic panel'"},
        }, "required": ["description"]},
    }},
]


class CalculationsSkill(BaseSkill):
    name = "calculations"
    description = "Deterministic SA construction/QS calculations — occupancy, escape widths, areas, parking."
    verification_policy = "deterministic"   # self-verifies via safe_eval anchoring below

    async def run(self, inp: SkillInput) -> SkillOutput:
        # Retrieve authoritative context so any rule/ratio cited comes from real docs.
        context = ""
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            chunks = await VulaIngestionPipeline(tenant_id=inp.tenant_id).query(
                inp.question, top_k=inp.top_k, authoritative_only=True)
            if chunks:
                context = "\n\n".join(f"[{c.get('filename','doc')}]: {c.get('text','')[:1200]}" for c in chunks)
        except Exception as exc:
            logger.debug("calculations KB retrieval skipped: %s", exc)
        self._verified: List[Any] = []      # deterministic safe_eval results seen this run
        self._calc_errors = 0
        try:
            answer = await self._agent_loop(inp.conversation_history, inp.question, context, inp.tenant_id)
            if not answer:
                raise RuntimeError("empty answer")
        except Exception as exc:
            logger.warning("calculations loop failed: %s", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

        # Verified-reasoning check: the final answer must be anchored to a deterministic
        # (safe_eval) computation. If the model states a headline number that matches NO verified
        # calculation, don't present it as fact — flag it and drop confidence. This catches the
        # model computing correctly with the tool but then misreporting a different number.
        anchored, matched = self._verify_answer(answer, self._verified)
        confidence = 0.85
        if anchored is False:
            confidence = 0.45
            answer += ("\n\n⚠️ Please confirm these figures — the final number couldn't be matched "
                       "to a verified calculation.")
        logger.info("VRL calc-verify tenant=%s anchored=%s verified=%d errors=%d matched=%s",
                    inp.tenant_id, anchored, len(self._verified), self._calc_errors, matched)
        # The verify event is recorded here and emitted by the BaseSkill verification hook —
        # one emit path for all verifiers, identical envelope on the wire. anchored=False means
        # the headline number wasn't matched to a deterministic calc (the escalation-worthy case,
        # mirrors "caught a wrong proposal" in the harness).
        return SkillOutput(
            answer=answer, skill_name=self.name, confidence=confidence,
            verification={
                "verifier": "digg.calc",
                "task": "calc",   # keep the pre-hook telemetry label unchanged on the wire
                "outcome": "accepted" if anchored else "unanchored",
                "escalated": (anchored is False),
                "extra": {"verified_count": len(self._verified), "calc_errors": self._calc_errors,
                          "matched": matched, "anchored": anchored},
            })

    def _system_prompt(self) -> str:
        return (
            "You are Vula, doing South African construction/QS calculations.\n\n"
            + behaviour_preamble(agentic=True) +
            "\nCalculation rules:\n"
            "- Determine the governing rule or ratio ONLY from the user's stated values or "
            "the cited standard in the provided context. If the rule/ratio you need is not "
            "given and not in context, do NOT invent it — ask for it or say which clause is needed.\n"
            "- Do every arithmetic step with the calculate tool (never compute in your head).\n"
            "- Round occupants/exits/bays UP (ceil) unless the standard says otherwise.\n"
            "- Show the formula and the numbers used. Cite a clause number or standard "
            "value ONLY if it is in the provided context or the user gave it; otherwise say "
            "the exact clause/value must be confirmed against the official standard.\n"
            "- Universal QS/engineering formulas you MAY apply (these are standard, not code-"
            "specific rules): area=L×W; volume=L×W×D; perimeter=2(L+W); VAT=amount×0.15; "
            "retention/contingency = amount × percentage; gradient = rise/run. For material "
            "quantities or code ratios that vary (bricks/m², occupancy ratios, parking bays), "
            "use the rate from context or ask — do not assume a figure.\n"
            "- For COSTS: ALWAYS call lookup_rate first to get the tenant's own unit rate, then "
            "multiply by the quantity with calculate. The rate library is the authoritative rate "
            "— prefer it over any figure in a document, and if a document shows a different rate, "
            "note the discrepancy. If lookup_rate finds nothing, ask for the rate or say it isn't "
            "on file — never assume a market rate."
        )

    async def _agent_loop(self, history: str, question: str, context: str, tenant_id: str) -> str:
        import litellm
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()

        messages: List[Dict[str, Any]] = [{"role": "system", "content": self._system_prompt()}]
        if history:
            messages.append({"role": "user", "content": f"(Conversation so far)\n{history}"})
        if context:
            # Fenced (core/prompt_safety.py) — same gap as reasoning.py/standards_lookup.py:
            # behaviour_preamble's UNTRUSTED_CONTENT_RULE describes >>> <<< delimiters that
            # this reference context previously never actually had.
            messages.append({"role": "user", "content": f"(Reference context — cite if used){fence('REFERENCE_CONTEXT', context)}"})
        messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await litellm.acompletion(
                model=model, messages=messages, tools=TOOL_SPECS, tool_choice="auto",
                temperature=0.1, max_tokens=700, api_key=api_key, api_base=api_base)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                inline = self._parse_inline(msg.content or "")
                if inline:
                    result = self._calc(inline)
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content":
                        f"[calculate({inline.get('expression')}) = {result}] "
                        "Continue; give the final plain-language answer with the working."})
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
                messages.append({"role": "tool", "tool_call_id": tc.id,
                    "name": tc.function.name, "content": fence('TOOL_RESULT', json.dumps(result))})

        resp = await litellm.acompletion(
            model=model, messages=messages, temperature=0.1, max_tokens=500,
            api_key=api_key, api_base=api_base)
        return (resp.choices[0].message.content or "").strip()

    def _dispatch(self, name: str, args: Dict[str, Any], tenant_id: str) -> Any:
        if name == "lookup_rate":
            try:
                from vula.api.qs import search_rates
                rows = search_rates(tenant_id, str(args.get("description", "")), limit=10)
                return {"rates": rows} if rows else {"message": "No matching rate on file for this tenant."}
            except Exception as exc:
                return {"error": str(exc)}
        return self._calc(args)

    def _calc(self, args: Dict[str, Any]) -> Any:
        expr = str(args.get("expression", "")).strip()
        try:
            val = safe_eval(expr)
            if isinstance(val, float) and val.is_integer():
                val = int(val)
            v = getattr(self, "_verified", None)
            if isinstance(v, list):
                v.append(val)                      # record the deterministic ground truth
            return {"expression": expr, "result": val}
        except Exception as exc:
            if isinstance(getattr(self, "_verified", None), list):
                self._calc_errors = getattr(self, "_calc_errors", 0) + 1
            return {"expression": expr, "error": f"invalid expression: {exc}"}

    @staticmethod
    def _numbers(text: str) -> List[float]:
        return [float(m.replace(",", "")) for m in re.findall(r"-?\d[\d,]*\.?\d*", text or "")]

    def _verify_answer(self, answer: str, verified: List[Any]):
        """Is the answer's number anchored to a deterministic result?
        Returns (True | False | None-if-nothing-was-computed, matched_value)."""
        if not verified:
            return (None, None)
        ans_nums = self._numbers(answer)
        for val in verified:
            fv = float(val)
            tol = max(0.5, abs(fv) * 0.005)
            if any(abs(a - fv) <= tol for a in ans_nums):
                return (True, val)
        return (False, None)

    def _parse_inline(self, content: str):
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        m = re.search(r"\{[^{}]*\"expression\"[^{}]*\}", content)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if "expression" in obj else None
        except Exception:
            return None

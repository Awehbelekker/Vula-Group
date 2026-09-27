"""
evals/harness.py — Vula's test ground: replayable cases scored the same way every time.

Two layers:
  • routing (offline, deterministic) — which skill a knowledge-path message reaches. Runs in
    CI via tests/test_evals_routing.py; every production misroute becomes a case.
  • tool choice (live) — the FIRST move a skill's real system prompt + real toolset makes on a
    real model, with every tool stubbed (nothing is read from or written to any tenant). Used
    to compare models on Vula's own work before changing a default, not on reputation.

Run from vula_mind/:
    python -m evals.run routing
    python -m evals.run tools --model openrouter/anthropic/claude-haiku-4.5
    python -m evals.run tools --model ollama_chat/llama3.1:8b --skill commerce_admin
Reports land in evals/reports/<timestamp>-<model>.json.
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

CASES = Path(__file__).parent / "cases"
EVAL_TENANT = "eval-sandbox"   # never a real tenant: prompts are built for a tenant with no data


def load(name: str) -> List[Dict[str, Any]]:
    return yaml.safe_load((CASES / name).read_text()) or []


# ── Routing (offline) ─────────────────────────────────────────────────────────

def route(prompt: str, tenant_id: Optional[str] = None) -> tuple[str, str]:
    """The skill the knowledge path picks, keyword/rule layer only (no model call)."""
    from unittest.mock import patch
    from config import settings
    from core.hrm.orchestrator import HRMOrchestrator
    with patch.object(settings, "skill_llm_fallback_enabled", False), \
         patch.object(HRMOrchestrator, "_has_connected_mailbox", return_value=False):
        return HRMOrchestrator()._route_with_reason(prompt, tenant_id)


def run_routing() -> Dict[str, Any]:
    rows = []
    for c in load("routing.yaml"):
        got, how = route(c["prompt"], c.get("tenant"))
        rows.append({"prompt": c["prompt"], "expect": c["expect"], "got": got, "how": how,
                     "ok": got == c["expect"]})
    return {"layer": "routing", "passed": sum(r["ok"] for r in rows), "total": len(rows), "rows": rows}


# ── Tool choice (live) ────────────────────────────────────────────────────────

@dataclass
class ToolCase:
    skill: str
    prompt: str
    expect: Optional[str]
    role: str = "owner"
    forbid: List[str] = field(default_factory=list)
    note: str = ""


def _prompt_and_tools(case: ToolCase) -> tuple[str, list]:
    """The production system prompt and toolset for this skill/role."""
    if case.skill == "email_admin":
        from core.skills.email_admin import TOOL_SPECS, EmailAdminSkill
        return EmailAdminSkill()._system("draft"), TOOL_SPECS
    if case.skill == "commerce_admin":
        from unittest.mock import patch
        from core.skills import commerce_admin as ca
        with patch("vula.api.tenants.enabled_modules", return_value=[]):   # all modules on
            tools = ca._tools_for(EVAL_TENANT, role=case.role, message=case.prompt)
        return ca.CommerceAdminSkill()._system_prompt(EVAL_TENANT, role=case.role, name="Eval"), tools
    if case.skill == "commerce_assistant":
        from core.skills import commerce_assistant as cas
        # A shop with bookings on — the widest customer toolset production offers.
        return (cas.CommerceAssistantSkill()._system_prompt(EVAL_TENANT, ""),
                cas.TOOL_SPECS + cas.BOOKING_TOOL_SPECS)
    raise ValueError(f"unknown skill {case.skill}")


def _route_for(model: str):
    from config import settings
    from core.llm_router import OPENROUTER_BASE
    if model.startswith("openrouter/"):
        return model, settings.openrouter_api_key, OPENROUTER_BASE
    return model, None, settings.ollama_base


async def run_one(model: str, case: ToolCase) -> Dict[str, Any]:
    import litellm
    from core.llm_router import generation_kwargs
    litellm.drop_params = True
    system, tools = _prompt_and_tools(case)
    m, key, base = _route_for(model)
    t0 = time.monotonic()
    try:
        resp = await litellm.acompletion(
            model=m, api_key=key, api_base=base, tools=tools, tool_choice="auto",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": case.prompt}],
            temperature=0.2, max_tokens=800, **generation_kwargs(m))
    except Exception as exc:
        return {"prompt": case.prompt, "skill": case.skill, "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:120]}", "secs": round(time.monotonic() - t0, 2)}
    secs = round(time.monotonic() - t0, 2)
    msg = resp.choices[0].message
    calls = getattr(msg, "tool_calls", None) or []
    tool, args_ok = None, None
    if calls:
        tool = calls[0].function.name
        try:
            args_ok = isinstance(json.loads(calls[0].function.arguments or "{}"), dict)
        except (json.JSONDecodeError, TypeError):
            args_ok = False
    text = (msg.content or "").strip()
    json_leak = (not calls) and (text.startswith("{") or '"name"' in text[:200])
    offered = {t["function"]["name"] for t in tools}
    ok = (tool == case.expect and args_ok is not False and not json_leak
          and tool not in (case.forbid or []) and (tool is None or tool in offered))
    usage = getattr(resp, "usage", None)
    try:
        cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
    except Exception:
        cost = None
    return {"prompt": case.prompt, "skill": case.skill, "role": case.role, "expect": case.expect,
            "got": tool, "args_ok": args_ok, "json_leak": json_leak, "ok": ok, "secs": secs,
            "tokens": getattr(usage, "total_tokens", None) if usage else None, "cost_usd": cost}


async def run_tools(model: str, skill: Optional[str] = None) -> Dict[str, Any]:
    cases = [ToolCase(**{k: v for k, v in c.items() if k in ToolCase.__dataclass_fields__})
             for c in load("tool_choice.yaml") if not skill or c["skill"] == skill]
    rows = [await run_one(model, c) for c in cases]
    secs = [r["secs"] for r in rows if "error" not in r]
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    return {
        "layer": "tools", "model": model, "passed": sum(r["ok"] for r in rows), "total": len(rows),
        "errors": sum(1 for r in rows if "error" in r),
        "p50_secs": round(statistics.median(secs), 2) if secs else None,
        "p95_secs": round(sorted(secs)[max(0, int(len(secs) * 0.95) - 1)], 2) if secs else None,
        "cost_per_100_usd": round(100 * sum(costs) / len(costs), 4) if costs else None,
        "rows": rows,
    }

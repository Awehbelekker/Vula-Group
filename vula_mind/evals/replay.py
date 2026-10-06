"""
evals/replay.py — real WhatsApp messages replayed through the real question path.

Chat rework step 4 (6 Oct 2026). Each case in evals/cases/replay.yaml is a message someone really
sent, with where it must go: which branch of the question path (whatsapp._rag_reply) answers it
and which skill. The replay runs the production code itself — the same branch order, the same
keyword routing (HRMOrchestrator), the same "proceed" / follow-up memory — with everything that
would touch the database, a model or WhatsApp stubbed out, so it is deterministic and free and
runs in CI (tests/test_replay_corpus.py). A change that sends a known message somewhere else
fails the build before it ships.

What is checked is the route, which is decided in code. What the model then says is the job of
the supervisor (core/supervisor.py) and the live benchmark, not of this replay.

New cases come from the turn record (tools/export_turns.py) and are reviewed by hand before they
go in. The repository is public: a case carries the message and a role, never a phone number,
a customer's name or a tenant's figures.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import yaml

CORPUS = Path(__file__).parent / "cases" / "replay.yaml"
_PHONE = "27000000000"          # a placeholder number, never a real person's


def load_cases(path: Path = CORPUS) -> List[Dict[str, Any]]:
    return yaml.safe_load(path.read_text())["cases"]


class _FakeSkill:
    """What get_skill() hands back during a replay — answers "ok" and records its name."""

    def __init__(self, name: str):
        self.name = name

    async def __call__(self, inp):
        from core.skills.base import SkillOutput
        return SkillOutput(answer="ok", skill_name=self.name, confidence=1.0)


class _FakeRunner:
    """The agent runner, minus the model: the real orchestrator picks the skill."""

    async def run(self, question: str, tenant_id: str, **_kw):
        from core.hrm.orchestrator import HRMOrchestrator
        skill, _why = HRMOrchestrator()._route_with_reason(question, tenant_id=tenant_id)
        return SimpleNamespace(final_answer="ok", skill_used=skill, confidence=1.0, latency_ms=0)


async def _passthrough(_phone, _tenant_id, coro, *a, **kw):
    return await coro


async def _none(*_a, **_kw):
    return None


async def replay(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run one case through whatsapp._rag_reply; return the route it took, the skill, and the
    reply (meaningful only for replies built in code, e.g. a check-in or a refusal)."""
    from vula import turns
    from vula.api import whatsapp as wa

    tenant_id = case["tenant"]
    role = case.get("role", "owner")
    key = (tenant_id, _PHONE)
    wa._LAST_SKILL.pop(key, None)
    wa._LAST_ASK.pop(key, None)
    import time
    if case.get("last_skill"):
        wa._LAST_SKILL[key] = (case["last_skill"], time.time())
    if case.get("last_request"):
        wa._LAST_ASK[key] = (case["last_request"], time.time())

    turn = {"steps": [], "replies": [], "phone": _PHONE, "_t0": time.monotonic()}
    token = turns._CURRENT.set(turn)
    try:
        with patch("config.settings.skill_llm_fallback_enabled", False), \
                patch("vula.commerce.business_profile.handle_interview", new=_none), \
                patch.object(wa, "_maybe_learn_supplier_alias", new=_none), \
                patch("vula.commerce.project_admin.handle", new=_none), \
                patch("core.skills.commerce_admin._stock_sheet_answer", new=lambda *_a, **_k: None), \
                patch("vula.commerce.project_programme.programme_answer", new=_none), \
                patch("vula.commerce.project_programme.projects_answer", new=lambda *_a, **_k: None), \
                patch.object(wa, "_run_with_holding_message", new=_passthrough), \
                patch("core.skills.loader.get_skill", new=_FakeSkill), \
                patch("core.agent_runner.get_agent_runner", new=lambda: _FakeRunner()), \
                patch("vula.integrations.metering.set_request_tenant", new=lambda *_a: None), \
                patch("vula.commerce.service.known_supplier_names",
                      new=lambda _t: list(case.get("suppliers") or [])):
            reply = await wa._rag_reply(tenant_id, case["text"], "", _PHONE,
                                        caller_name=role.title(), caller_role=role)
    finally:
        turns._CURRENT.reset(token)
        wa._LAST_SKILL.pop(key, None)
        wa._LAST_ASK.pop(key, None)
    route = next((s for s in reversed(turn["steps"]) if s.get("step") == "route"), {})
    continued = next((s.get("continues") for s in turn["steps"]
                      if s.get("step") == "handler" and s.get("name") == "go_ahead"), None)
    return {"route": route.get("name"), "skill": route.get("skill"), "reply": reply,
            "continued": continued}


def mismatch(case: Dict[str, Any], got: Dict[str, Any]) -> Optional[str]:
    """Why a replay result doesn't match its case, or None."""
    want = case["expect"]
    if want.get("route") and got["route"] != want["route"]:
        return f"went to route {got['route']!r}, expected {want['route']!r}"
    if want.get("skill") and got["skill"] != want["skill"]:
        return f"went to skill {got['skill']!r}, expected {want['skill']!r}"
    if want.get("not_skill") and got["skill"] in want["not_skill"]:
        return f"went to skill {got['skill']!r}, which it must not"
    if want.get("continues") and got["continued"] != want["continues"]:
        return f"did not carry on with {want['continues']!r}"
    reply = got.get("reply") or ""
    for bad in want.get("reply_must_not", []):
        if bad.lower() in reply.lower():
            return f"reply contains {bad!r}"
    return None

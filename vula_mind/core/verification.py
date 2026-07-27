"""
core/verification.py — per-skill answer verification, separate from generation.

Generalises the pattern proven in core/skills/calculations.py: a skill produces an answer,
a DISTINCT check decides whether to trust it, and the outcome lands in the shared
verified-reasoning telemetry envelope (migration 045) so live catch rates are measurable.

Three policies, resolved per skill (env override beats the skill's class attribute):
  • none          — no check, no extra latency. The default for every skill.
  • deterministic — the skill runs its own exact check inside run() and records it on
                    SkillOutput.verification (calculations.py's safe_eval anchoring). The hook
                    only registers the outcome — one emit path for every verifier.
  • adversarial   — one extra checker-framed LLM pass ("find defects, don't answer"), routed
                    local-first like everything else. A found defect drops confidence and
                    appends a caveat; it never blocks the answer.

Design constraints (same as reasoning_telemetry):
  • Fail-open: a checker error/timeout must pass the answer through unchanged.
  • Never break the caller: apply() swallows everything.
  • POPIA: telemetry carries verdict/counts only — never answer or question text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, Dict

from config import settings

logger = logging.getLogger(__name__)

POLICY_NONE = "none"
POLICY_DETERMINISTIC = "deterministic"
POLICY_ADVERSARIAL = "adversarial"
_POLICIES = {POLICY_NONE, POLICY_DETERMINISTIC, POLICY_ADVERSARIAL}

# Cap what the checker sees — enough to judge, bounded for latency.
_CHECKER_INPUT_CAP = 3000

_CHECKER_SYSTEM = (
    "You are a verifier, not an answerer. Given a task and a proposed answer, find concrete "
    "defects: wrong numbers, invented facts or code clauses, contradictions with the task, "
    "unsupported claims. Do NOT rewrite or improve the answer. Respond ONLY with JSON: "
    '{"verdict": "pass" | "fail", "defects": ["..."]}. '
    'Use "fail" only for a concrete defect, not for style or brevity. '
    "If retrieved context is provided below, treat it as ground truth: fail the answer if it "
    "contradicts the context, or if it ignores information clearly relevant to the task that "
    "the context contains in favor of unrelated or unsupported claims."
)


def resolve_policy(skill_name: str, class_default: str = POLICY_NONE) -> str:
    """Env override map (VERIFICATION_POLICY_OVERRIDES) wins over the skill's class attribute.
    Anything unrecognised resolves to "none" — fail-open, zero behaviour change."""
    policy = settings.verification_policies.get(skill_name, class_default)
    return policy if policy in _POLICIES else POLICY_NONE


async def adversarial_check(question: str, answer: str, context: str = "") -> Dict[str, Any]:
    """One checker-framed LLM pass, local-first via the shared router. `context`, when given,
    is the retrieved KB text the answer should be grounded in — lets the checker catch a
    defect the generic (question, answer)-only check can't: an answer that ignores correctly-
    retrieved context in favor of unrelated/stale content (the 2026-07-27 DIGG bug). Returns
    {"verdict": "pass"|"fail"|"checker_error", "defects": [...], "checker_ms": int}."""
    started = time.monotonic()
    try:
        import litellm
        from core.llm_router import resolve_generation_route
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route(task_type="verification")
        context_block = (
            f"\n\nRetrieved context the answer should be grounded in:\n{context[:_CHECKER_INPUT_CAP]}"
            if context else ""
        )
        resp = await asyncio.wait_for(
            litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": _CHECKER_SYSTEM},
                    {"role": "user", "content":
                        f"Task:\n{question[:_CHECKER_INPUT_CAP]}\n\n"
                        f"Proposed answer:\n{answer[:_CHECKER_INPUT_CAP]}"
                        f"{context_block}"},
                ],
                temperature=0.0,
                max_tokens=settings.verification_checker_max_tokens,
                api_key=api_key, api_base=api_base),
            timeout=settings.verification_checker_timeout_s)
        text = resp.choices[0].message.content or ""
        verdict, defects = _parse_verdict(text)
        return {"verdict": verdict, "defects": defects,
                "checker_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        logger.debug("adversarial check failed open: %s", exc)
        return {"verdict": "checker_error", "defects": [],
                "checker_ms": int((time.monotonic() - started) * 1000)}


def _parse_verdict(text: str) -> tuple[str, list]:
    """Lenient parse — local 8B models don't always return clean JSON."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            verdict = str(data.get("verdict", "")).lower()
            if verdict in ("pass", "fail"):
                defects = data.get("defects") or []
                return verdict, defects if isinstance(defects, list) else []
        except Exception:
            pass
    # No parseable JSON — only a bare, unambiguous verdict word counts.
    lowered = text.strip().lower()
    if re.fullmatch(r'"?(pass|fail)"?\.?', lowered):
        return lowered.strip('".'), []
    return "checker_error", []


def register_outcome(skill_name: str, tenant_id: str, verification: Dict[str, Any]) -> None:
    """Single emit path for every verifier — deterministic or adversarial — so report.py sees
    one envelope shape. POPIA: only the whitelisted verification fields go to the sink; any
    defect text stays on the SkillOutput, never in telemetry."""
    try:
        from core.reasoning_telemetry import emit
        emit(system="verified-reasoning", run_id=uuid.uuid4().hex[:8],
             task=verification.get("task") or skill_name,
             outcome=verification.get("outcome"), escalated=bool(verification.get("escalated")),
             verifier=verification.get("verifier"), tenant_id=tenant_id,
             extra=verification.get("extra") or {})
    except Exception as exc:
        logger.debug("verification outcome emit skipped: %s", exc)


# Caveat mirrors calculations.py's unanchored warning — same voice, same confidence floor.
_CAVEAT = ("\n\n⚠️ Please double-check this answer — an automated review flagged possible "
           "issues with it.")
_DEFECT_CONFIDENCE = 0.45


async def apply(skill: Any, inp: Any, result: Any) -> None:
    """Verification hook called by BaseSkill.__call__ after a successful run().
    Mutates `result` in place (confidence/answer/verification). Must never raise."""
    try:
        if result.error or not result.answer:
            return
        policy = resolve_policy(skill.name, getattr(skill, "verification_policy", POLICY_NONE))

        # A skill that verified itself inside run() (deterministic, e.g. digg.calc) just gets
        # its outcome registered — also prevents double-checking under any policy.
        if result.verification:
            register_outcome(skill.name, inp.tenant_id, result.verification)
            return

        if policy == POLICY_NONE:
            return

        if policy == POLICY_DETERMINISTIC:
            # Claimed deterministic but registered nothing — visibility, no behaviour change.
            register_outcome(skill.name, inp.tenant_id, {
                "verifier": f"deterministic.{skill.name}", "outcome": "not_registered",
                "escalated": False, "extra": {}})
            return

        # POLICY_ADVERSARIAL
        context = "\n\n".join(
            s.get("text", "") for s in (result.sources or [])
            if s.get("type") == "kb" and s.get("text")
        )
        check = await adversarial_check(inp.question, result.answer, context=context)
        verdict = check["verdict"]
        outcome = {"pass": "accepted", "fail": "defect_found"}.get(verdict, "checker_error")
        if verdict == "fail":
            # "escalate" (forced cloud re-run) is reserved until the router grows a force-cloud
            # hook — until then every defect takes the caveat path, matching digg.calc.
            result.confidence = min(result.confidence, _DEFECT_CONFIDENCE)
            result.answer += _CAVEAT
        extra = {"verdict": verdict, "defect_count": len(check.get("defects", [])),
                 "checker_ms": check.get("checker_ms", 0)}
        if verdict in ("pass", "fail"):
            # `anchored` rides pull_prod.py's existing mapping (False → "caught a wrong
            # proposal"). A checker_error is neither — omit so it isn't counted as a catch.
            extra["anchored"] = verdict == "pass"
        result.verification = {
            "verifier": f"adversarial.{skill.name}", "outcome": outcome,
            "escalated": verdict == "fail",
            "defects": check.get("defects", []),   # stays on the output, never emitted
            "extra": extra,
        }
        register_outcome(skill.name, inp.tenant_id, result.verification)
    except Exception as exc:
        logger.debug("verification hook failed open: %s", exc)

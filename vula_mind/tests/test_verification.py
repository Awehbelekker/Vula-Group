"""Tests for the per-skill verification hook (core/verification.py + BaseSkill.__call__).

The load-bearing guarantee: policy "none" is a strict no-op (no LLM call, no emit, answer
untouched) so shipping the hook changes nothing until a policy is flipped per skill.
"""
import pytest

from config import settings
from core import verification
from core.skills.base import BaseSkill, SkillInput, SkillOutput


class DummySkill(BaseSkill):
    name = "dummy"
    description = "test skill"

    def __init__(self, answer="the answer is 42", confidence=0.9, verification_dict=None,
                 error=None):
        self._answer = answer
        self._confidence = confidence
        self._verification = verification_dict
        self._error = error

    async def run(self, inp: SkillInput) -> SkillOutput:
        if self._error:
            raise RuntimeError(self._error)
        return SkillOutput(answer=self._answer, skill_name=self.name,
                           confidence=self._confidence, verification=self._verification)


def _inp():
    return SkillInput(question="what is 6 * 7?", tenant_id="test-tenant")


@pytest.fixture
def emits(monkeypatch):
    """Capture telemetry envelopes synchronously."""
    captured = []
    import core.reasoning_telemetry as rt
    monkeypatch.setattr(rt, "emit", lambda **kw: captured.append(kw))
    return captured


@pytest.fixture
def no_llm(monkeypatch):
    """Fail the test if anything tries an LLM call."""
    import litellm

    async def _boom(*a, **kw):
        raise AssertionError("LLM was called on a no-op path")

    monkeypatch.setattr(litellm, "acompletion", _boom)


# ── Policy resolution ─────────────────────────────────────────────────────────

def test_policy_override_from_config(monkeypatch):
    monkeypatch.setattr(settings, "verification_policy_overrides", '{"dummy": "adversarial"}')
    assert verification.resolve_policy("dummy", "none") == "adversarial"
    # unknown skill falls back to the class default
    assert verification.resolve_policy("other", "deterministic") == "deterministic"


def test_policy_malformed_json_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "verification_policy_overrides", "{not json")
    assert verification.resolve_policy("dummy", "none") == "none"
    # unrecognised policy value resolves to none (fail-open)
    monkeypatch.setattr(settings, "verification_policy_overrides", '{"dummy": "yolo"}')
    assert verification.resolve_policy("dummy", "deterministic") == "none"


# ── none ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_policy_none_is_noop(emits, no_llm):
    skill = DummySkill()
    out = await skill(_inp())
    assert out.answer == "the answer is 42"
    assert out.confidence == 0.9
    assert out.verification is None
    assert emits == []


# ── deterministic ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deterministic_registers_outcome(emits, no_llm):
    v = {"verifier": "digg.test", "outcome": "accepted", "escalated": False,
         "extra": {"anchored": True}}
    skill = DummySkill(verification_dict=dict(v))
    skill.verification_policy = "deterministic"
    out = await skill(_inp())
    assert out.answer == "the answer is 42" and out.confidence == 0.9
    assert len(emits) == 1
    e = emits[0]
    assert e["system"] == "verified-reasoning"
    assert e["verifier"] == "digg.test" and e["outcome"] == "accepted"
    assert e["tenant_id"] == "test-tenant"


@pytest.mark.asyncio
async def test_deterministic_missing_registration(emits, no_llm):
    skill = DummySkill()
    skill.verification_policy = "deterministic"
    out = await skill(_inp())
    assert out.answer == "the answer is 42"
    assert len(emits) == 1
    assert emits[0]["outcome"] == "not_registered"


@pytest.mark.asyncio
async def test_calculations_single_emit(emits, monkeypatch):
    """Regression: the calc skill's verify event flows through the hook exactly once,
    with the pre-hook envelope shape (task='calc', verifier='digg.calc')."""
    import litellm
    from core.skills.calculations import CalculationsSkill
    import core.skills.calculations as calc_mod

    class _Msg:
        content = "6 * 7 = 42"
        tool_calls = None

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]

    async def _fake_completion(*a, **kw):
        return _Resp()

    async def _fake_route(*a, **kw):
        return "ollama/test", None, "http://localhost:11434"

    monkeypatch.setattr(litellm, "acompletion", _fake_completion)
    monkeypatch.setattr(calc_mod, "resolve_generation_route", _fake_route)
    import vula.ingestion.pipeline as pl

    def _no_kb(**kw):
        raise RuntimeError("no kb in tests")

    monkeypatch.setattr(pl, "VulaIngestionPipeline", _no_kb)

    out = await CalculationsSkill()(_inp())
    assert out.verification is not None
    assert out.verification["verifier"] == "digg.calc"
    assert len(emits) == 1
    assert emits[0]["task"] == "calc"
    assert emits[0]["verifier"] == "digg.calc"


# ── adversarial ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adversarial_pass(emits, monkeypatch):
    async def _pass(question, answer, context=""):
        return {"verdict": "pass", "defects": [], "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _pass)
    skill = DummySkill()
    skill.verification_policy = "adversarial"
    out = await skill(_inp())
    assert out.answer == "the answer is 42"
    assert out.confidence == 0.9
    assert out.verification["outcome"] == "accepted"
    assert len(emits) == 1
    assert emits[0]["escalated"] is False
    assert emits[0]["extra"]["anchored"] is True


@pytest.mark.asyncio
async def test_adversarial_defect_caveats(emits, monkeypatch):
    async def _fail(question, answer, context=""):
        return {"verdict": "fail", "defects": ["number is wrong"], "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _fail)
    skill = DummySkill()
    skill.verification_policy = "adversarial"
    out = await skill(_inp())
    assert out.confidence == 0.45
    assert "⚠️" in out.answer
    assert out.verification["defects"] == ["number is wrong"]
    assert len(emits) == 1
    assert emits[0]["outcome"] == "defect_found" and emits[0]["escalated"] is True
    assert emits[0]["extra"]["anchored"] is False
    # POPIA: defect text stays on the output, never in the envelope
    assert "defects" not in emits[0]["extra"]


@pytest.mark.asyncio
async def test_adversarial_checker_error_fails_open(emits, monkeypatch):
    """Checker blows up at the LLM layer → answer passes through unchanged, outcome recorded,
    and the error is NOT counted as a catch (no `anchored` key)."""
    import litellm

    async def _boom(*a, **kw):
        raise RuntimeError("model down")

    async def _fake_route(*a, **kw):
        return "ollama/test", None, "http://localhost:11434"

    monkeypatch.setattr(litellm, "acompletion", _boom)
    import core.llm_router as router
    monkeypatch.setattr(router, "resolve_generation_route", _fake_route)

    skill = DummySkill()
    skill.verification_policy = "adversarial"
    out = await skill(_inp())
    assert out.answer == "the answer is 42"
    assert out.confidence == 0.9
    assert len(emits) == 1
    assert emits[0]["outcome"] == "checker_error"
    assert "anchored" not in emits[0]["extra"]


@pytest.mark.asyncio
async def test_verification_never_breaks_skill(monkeypatch):
    async def _explode(skill, inp, result):
        raise RuntimeError("hook bug")

    monkeypatch.setattr(verification, "apply", _explode)
    out = await DummySkill()(_inp())
    assert out.answer == "the answer is 42"
    assert out.error is None


@pytest.mark.asyncio
async def test_skill_error_skips_adversarial(emits, monkeypatch):
    called = []

    async def _check(question, answer, context=""):
        called.append(1)
        return {"verdict": "pass", "defects": [], "checker_ms": 1}

    monkeypatch.setattr(verification, "adversarial_check", _check)
    skill = DummySkill(error="boom")
    skill.verification_policy = "adversarial"
    out = await skill(_inp())
    assert out.error == "boom"
    assert called == []
    assert emits == []


# ── context-aware checking (2026-07-27 DIGG bug: right doc retrieved, ignored anyway) ──

@pytest.mark.asyncio
async def test_adversarial_check_includes_context_when_provided(monkeypatch):
    import litellm

    captured = {}

    class _Msg:
        content = '{"verdict": "pass", "defects": []}'

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]

    async def _fake_completion(*a, **kw):
        captured["messages"] = kw["messages"]
        return _Resp()

    async def _fake_route(*a, **kw):
        return "ollama/test", None, "http://localhost:11434"

    monkeypatch.setattr(litellm, "acompletion", _fake_completion)
    import core.llm_router as router
    monkeypatch.setattr(router, "resolve_generation_route", _fake_route)

    await verification.adversarial_check("q", "a", context="Bathroom Bizarre quotation R1,234")
    user_content = captured["messages"][1]["content"]
    assert "Bathroom Bizarre quotation R1,234" in user_content
    assert "Retrieved context" in user_content


@pytest.mark.asyncio
async def test_adversarial_check_omits_context_block_when_empty(monkeypatch):
    import litellm

    captured = {}

    class _Msg:
        content = '{"verdict": "pass", "defects": []}'

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]

    async def _fake_completion(*a, **kw):
        captured["messages"] = kw["messages"]
        return _Resp()

    async def _fake_route(*a, **kw):
        return "ollama/test", None, "http://localhost:11434"

    monkeypatch.setattr(litellm, "acompletion", _fake_completion)
    import core.llm_router as router
    monkeypatch.setattr(router, "resolve_generation_route", _fake_route)

    await verification.adversarial_check("q", "a")
    assert "Retrieved context" not in captured["messages"][1]["content"]


@pytest.mark.asyncio
async def test_apply_builds_context_from_kb_sources(emits, monkeypatch):
    captured = {}

    async def _check(question, answer, context=""):
        captured["context"] = context
        return {"verdict": "pass", "defects": [], "checker_ms": 1}

    monkeypatch.setattr(verification, "adversarial_check", _check)

    class SourcedSkill(BaseSkill):
        name = "sourced"
        description = "test"
        verification_policy = "adversarial"

        async def run(self, inp: SkillInput) -> SkillOutput:
            return SkillOutput(
                answer="the quote is R1,234", skill_name=self.name, confidence=0.75,
                sources=[
                    {"type": "kb", "filename": "quote.pdf", "score": 0.46, "text": "R1,234 quotation"},
                    {"type": "kb", "filename": "other.pdf", "score": 0.3, "text": ""},
                    {"type": "conversation", "filename": "chat", "score": 0.9, "text": "irrelevant history"},
                ],
            )

    await SourcedSkill()(_inp())
    assert captured["context"] == "R1,234 quotation"


@pytest.mark.asyncio
async def test_apply_builds_context_from_tenant_and_training_kb_sources(monkeypatch):
    """architecture_planning.py's two-pipeline retrieval tags sources "tenant_kb"/
    "training_kb", not plain "kb" — apply() must still pick both up (substring match)."""
    captured = {}

    async def _check(question, answer, context=""):
        captured["context"] = context
        return {"verdict": "pass", "defects": [], "checker_ms": 1}

    monkeypatch.setattr(verification, "adversarial_check", _check)

    class ArchSourcedSkill(BaseSkill):
        name = "arch_sourced"
        description = "test"
        verification_policy = "adversarial"

        async def run(self, inp: SkillInput) -> SkillOutput:
            return SkillOutput(
                answer="answer", skill_name=self.name, confidence=0.75,
                sources=[
                    {"type": "tenant_kb", "filename": "past.pdf", "score": 0.5, "text": "practice fee schedule"},
                    {"type": "training_kb", "filename": "sans.pdf", "score": 0.4, "text": "SANS 10400 clause"},
                ],
            )

    await ArchSourcedSkill()(_inp())
    assert "practice fee schedule" in captured["context"]
    assert "SANS 10400 clause" in captured["context"]


@pytest.mark.asyncio
async def test_apply_context_empty_when_no_kb_sources(monkeypatch):
    captured = {}

    async def _check(question, answer, context=""):
        captured["context"] = context
        return {"verdict": "pass", "defects": [], "checker_ms": 1}

    monkeypatch.setattr(verification, "adversarial_check", _check)
    skill = DummySkill()
    skill.verification_policy = "adversarial"
    await skill(_inp())
    assert captured["context"] == ""


# ── verdict parsing (local 8B models return messy JSON) ───────────────────────

def test_parse_verdict_lenient():
    assert verification._parse_verdict('{"verdict": "pass", "defects": []}') == ("pass", [])
    assert verification._parse_verdict(
        'Sure! {"verdict": "fail", "defects": ["bad math"]} hope that helps'
    ) == ("fail", ["bad math"])
    assert verification._parse_verdict("<think>hmm</think>PASS") == ("pass", [])
    # 2026-09-01: an unreadable verdict is now reported as "unparseable" rather than
    # "checker_error", so it can be told apart from the checker actually throwing. Both still
    # fail open; 47 real events carried no usable cause because they were collapsed together.
    assert verification._parse_verdict("I think the answer might pass muster")[0] == "unparseable"
    assert verification._parse_verdict("")[0] == "unparseable"


# ── diagnosability of a failing checker (2026-09-01) ────────────────────────────
# Real audit: 47 checker_error events in 10 days, ALL for commerce_admin, all with
# checker_ms=0 and no reason recorded — verification was silently not running for roughly half
# of off-the-hook's traffic and there was no way to tell why. The failure was swallowed at
# logger.debug, and an exception was indistinguishable from the model returning junk. A safety
# net that fails invisibly is worse than none, because it looks fine.

@pytest.mark.asyncio
async def test_checker_exception_records_the_exception_type(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("no route")
    monkeypatch.setattr(verification, "resolve_generation_route", _boom, raising=False)
    import core.llm_router as router
    monkeypatch.setattr(router, "resolve_generation_route", _boom)
    out = await verification.adversarial_check("q", "a")
    assert out["verdict"] == "checker_error"
    assert out["reason"] == "exception:RuntimeError", "the cause must be recorded"


@pytest.mark.asyncio
async def test_unparseable_verdict_is_reported_separately_from_an_exception(monkeypatch):
    import litellm

    class _Msg:
        content = "I reckon it is probably fine honestly"

    async def _resp(*a, **kw):
        return type("R", (), {"choices": [type("C", (), {"message": _Msg()})()]})()

    async def _route(*a, **kw):
        return "ollama/test", None, "http://localhost:11434"

    monkeypatch.setattr(litellm, "acompletion", _resp)
    import core.llm_router as router
    monkeypatch.setattr(router, "resolve_generation_route", _route)
    out = await verification.adversarial_check("q", "a")
    assert out["verdict"] == "unparseable"
    assert out["reason"] == "unparseable_verdict"


@pytest.mark.asyncio
async def test_reason_reaches_telemetry_without_leaking_content(emits, monkeypatch):
    """The exception CLASS is diagnostic; its message can quote customer content (POPIA)."""
    async def _err(question, answer, context=""):
        return {"verdict": "checker_error", "defects": [], "checker_ms": 0,
                "reason": "exception:TimeoutError"}
    monkeypatch.setattr(verification, "adversarial_check", _err)
    skill = DummySkill()
    skill.verification_policy = "adversarial"
    await skill(_inp())
    assert emits[0]["outcome"] == "checker_error"
    assert emits[0]["extra"]["reason"] == "exception:TimeoutError"


@pytest.mark.asyncio
async def test_unparseable_gets_its_own_outcome(emits, monkeypatch):
    async def _unp(question, answer, context=""):
        return {"verdict": "unparseable", "defects": [], "checker_ms": 300,
                "reason": "unparseable_verdict"}
    monkeypatch.setattr(verification, "adversarial_check", _unp)
    skill = DummySkill()
    skill.verification_policy = "adversarial"
    out = await skill(_inp())
    assert emits[0]["outcome"] == "checker_unparseable"
    assert "anchored" not in emits[0]["extra"], "neither a pass nor a catch"
    assert out.answer == "the answer is 42", "must still fail open"

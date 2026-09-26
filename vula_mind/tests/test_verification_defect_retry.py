"""Tests for the defect-retry feature (core/verification.py, 2026-09-21) — the "#2" follow-up
from the same-day accuracy work: a confirmed adversarial defect fires one backgrounded corrective
retry on the cloud route instead of only ever appending a caveat, gated behind
settings.verification_adversarial_action == "escalate" (default stays "caveat" — every
adversarial skill's behaviour is unchanged until this is explicitly flipped on).

Design, per the discussion this session: the first reply (caveated, as before) ships immediately
and unchanged — the retry never adds latency to it. A detached background task then retries with
the specific defects + grounding context, re-checks the correction, and only sends a WhatsApp
follow-up if THAT clears verification too. A retry that doesn't clear it sends nothing further.
"""
from unittest.mock import AsyncMock, patch

import pytest

from config import settings
from core import verification
from core.skills.base import BaseSkill, SkillInput, SkillOutput


class DummySkill(BaseSkill):
    name = "dummy"
    description = "test skill"

    def __init__(self, answer="the answer is 42", confidence=0.9):
        self._answer = answer
        self._confidence = confidence

    async def run(self, inp: SkillInput) -> SkillOutput:
        return SkillOutput(answer=self._answer, skill_name=self.name, confidence=self._confidence)


def _inp():
    return SkillInput(question="what is 6 * 7?", tenant_id="test-tenant")


@pytest.fixture
def emits(monkeypatch):
    """Capture telemetry envelopes synchronously — same fixture shape as test_verification.py."""
    captured = []
    import core.reasoning_telemetry as rt
    monkeypatch.setattr(rt, "emit", lambda **kw: captured.append(kw))
    return captured


# ── apply(): the gate ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_default_caveat_action_never_fires_a_retry(monkeypatch):
    """The load-bearing no-op guarantee for this feature, same shape as policy 'none': shipping
    it must change nothing until verification_adversarial_action is explicitly flipped."""
    async def _fail(question, answer, context=""):
        return {"verdict": "fail", "defects": ["wrong number"], "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _fail)
    calls = []
    monkeypatch.setattr(verification, "_run_bg", lambda coro, **kw: (calls.append(kw), coro.close()))
    assert settings.verification_adversarial_action == "caveat"  # the actual default, not a stub

    skill = DummySkill()
    skill.verification_policy = "adversarial"
    await skill(_inp())

    assert calls == []


@pytest.mark.asyncio
async def test_escalate_action_fires_a_retry_on_defect(monkeypatch):
    async def _fail(question, answer, context=""):
        return {"verdict": "fail", "defects": ["wrong number"], "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _fail)
    monkeypatch.setattr(settings, "verification_adversarial_action", "escalate")
    calls = []
    monkeypatch.setattr(verification, "_run_bg", lambda coro, **kw: (calls.append(kw), coro.close()))

    skill = DummySkill(answer="the answer is 41")
    skill.verification_policy = "adversarial"
    await skill(_inp())

    assert len(calls) == 1
    assert calls[0]["label"] == "verification_retry.dummy"


@pytest.mark.asyncio
async def test_escalate_action_does_not_fire_on_a_pass(monkeypatch):
    async def _pass(question, answer, context=""):
        return {"verdict": "pass", "defects": [], "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _pass)
    monkeypatch.setattr(settings, "verification_adversarial_action", "escalate")
    calls = []
    monkeypatch.setattr(verification, "_run_bg", lambda coro, **kw: (calls.append(kw), coro.close()))

    skill = DummySkill()
    skill.verification_policy = "adversarial"
    await skill(_inp())

    assert calls == []


@pytest.mark.asyncio
async def test_first_reply_is_unaffected_by_the_retry_being_queued(monkeypatch):
    """The caveated answer must ship exactly as before — the retry is purely additive/backgrounded,
    never changes what the immediate SkillOutput carries."""
    async def _fail(question, answer, context=""):
        return {"verdict": "fail", "defects": ["wrong number"], "checker_ms": 5}

    monkeypatch.setattr(verification, "adversarial_check", _fail)
    monkeypatch.setattr(settings, "verification_adversarial_action", "escalate")
    monkeypatch.setattr(verification, "_run_bg", lambda coro, **kw: coro.close())

    skill = DummySkill(answer="the answer is 41", confidence=0.9)
    skill.verification_policy = "adversarial"
    out = await skill(_inp())

    assert out.confidence == 0.45
    assert out.answer.startswith("the answer is 41")
    assert "⚠️" in out.answer


# ── _retry_with_cloud ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_with_cloud_returns_none_without_a_cloud_key(monkeypatch):
    monkeypatch.setattr("core.llm_router.escalate_to_cloud", lambda *a, **kw: None)
    out = await verification._retry_with_cloud("q", "flawed", ["bad number"], "", "run1")
    assert out is None


@pytest.mark.asyncio
async def test_retry_with_cloud_returns_corrected_text(monkeypatch):
    monkeypatch.setattr("core.llm_router.escalate_to_cloud",
                        lambda *a, **kw: ("openrouter/model", "key", "base"))

    class _Msg:
        content = "the corrected answer is 42"

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]

    async def _fake_completion(*a, **kw):
        return _Resp()

    with patch("litellm.acompletion", new=AsyncMock(side_effect=_fake_completion)):
        out = await verification._retry_with_cloud("q", "flawed", ["bad number"], "ctx", "run1")
    assert out == "the corrected answer is 42"


@pytest.mark.asyncio
async def test_retry_with_cloud_fails_open_on_exception(monkeypatch):
    monkeypatch.setattr("core.llm_router.escalate_to_cloud",
                        lambda *a, **kw: ("openrouter/model", "key", "base"))
    with patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("boom"))):
        out = await verification._retry_with_cloud("q", "flawed", [], "", "run1")
    assert out is None


# ── _background_defect_retry ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_background_retry_no_correction_registers_failure_and_sends_nothing(emits, monkeypatch):
    monkeypatch.setattr(verification, "_retry_with_cloud", AsyncMock(return_value=None))
    send = AsyncMock()
    with patch("vula.api.whatsapp._send_reply", new=send):
        await verification._background_defect_retry(
            "dummy", "test-tenant", "27821234567", "q", "flawed", ["bad"], "", "run1")

    send.assert_not_called()
    assert len(emits) == 1
    assert emits[0]["outcome"] == "defect_retry_failed"
    assert emits[0]["extra"]["stage"] == "retry_generation"


@pytest.mark.asyncio
async def test_background_retry_correction_still_fails_recheck_sends_nothing(emits, monkeypatch):
    monkeypatch.setattr(verification, "_retry_with_cloud", AsyncMock(return_value="still wrong"))
    monkeypatch.setattr(verification, "adversarial_check",
                        AsyncMock(return_value={"verdict": "fail", "defects": ["still bad"]}))
    send = AsyncMock()
    with patch("vula.api.whatsapp._send_reply", new=send):
        await verification._background_defect_retry(
            "dummy", "test-tenant", "27821234567", "q", "flawed", ["bad"], "", "run1")

    send.assert_not_called()
    assert len(emits) == 1
    assert emits[0]["outcome"] == "defect_retry_failed"
    assert emits[0]["extra"]["stage"] == "retry_recheck"


@pytest.mark.asyncio
async def test_background_retry_success_sends_whatsapp_follow_up(emits, monkeypatch):
    monkeypatch.setattr(verification, "_retry_with_cloud",
                        AsyncMock(return_value="the corrected answer is 42"))
    monkeypatch.setattr(verification, "adversarial_check",
                        AsyncMock(return_value={"verdict": "pass", "defects": []}))
    send = AsyncMock(return_value=True)
    with patch("vula.api.whatsapp._send_reply", new=send):
        await verification._background_defect_retry(
            "dummy", "test-tenant", "27821234567", "q", "flawed", ["bad"], "", "run1")

    send.assert_awaited_once()
    args = send.call_args.args
    assert args[0] == "27821234567"
    assert "the corrected answer is 42" in args[1]
    assert args[2] == "test-tenant"
    assert len(emits) == 1
    assert emits[0]["outcome"] == "corrected_on_retry"


@pytest.mark.asyncio
async def test_background_retry_success_without_a_phone_skips_the_send(emits, monkeypatch):
    """No customer_phone in metadata (shouldn't happen on a real WhatsApp-originated request,
    but fail-open rather than crash) — still registers the outcome, just never calls send."""
    monkeypatch.setattr(verification, "_retry_with_cloud",
                        AsyncMock(return_value="the corrected answer is 42"))
    monkeypatch.setattr(verification, "adversarial_check",
                        AsyncMock(return_value={"verdict": "pass", "defects": []}))
    send = AsyncMock()
    with patch("vula.api.whatsapp._send_reply", new=send):
        await verification._background_defect_retry(
            "dummy", "test-tenant", "", "q", "flawed", ["bad"], "", "run1")

    send.assert_not_called()
    assert emits[0]["outcome"] == "corrected_on_retry"


@pytest.mark.asyncio
async def test_background_retry_send_failure_fails_open(emits, monkeypatch):
    """A broken WhatsApp send must not raise out of the background task — it's already detached
    and error-swallowed by _run_bg in production, but the function itself should be robust too."""
    monkeypatch.setattr(verification, "_retry_with_cloud",
                        AsyncMock(return_value="the corrected answer is 42"))
    monkeypatch.setattr(verification, "adversarial_check",
                        AsyncMock(return_value={"verdict": "pass", "defects": []}))
    send = AsyncMock(side_effect=RuntimeError("whatsapp down"))
    with patch("vula.api.whatsapp._send_reply", new=send):
        await verification._background_defect_retry(
            "dummy", "test-tenant", "27821234567", "q", "flawed", ["bad"], "", "run1")

    assert emits[0]["outcome"] == "corrected_on_retry"  # registered before the send attempt

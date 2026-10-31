"""2026-09-30: with the local box down, every keyword miss silently became `reasoning`. The
one-word skill classification now falls back to a cheap cloud model, logged as local_unreachable."""
import httpx

from config import settings
from core.hrm.orchestrator import HRMOrchestrator


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=None, response=None)

    def json(self):
        return self._p


def test_local_down_uses_cloud(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        if "openrouter" in url:
            assert kw["json"]["provider"]["zdr"] is True
            return _Resp({"choices": [{"message": {"content": "finance_admin"}}]})
        raise httpx.ConnectError("tunnel down")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "openrouter_zdr", True)
    logged = []
    monkeypatch.setattr("core.llm_router._log_decision", lambda **kw: logged.append(kw))
    assert HRMOrchestrator()._llm_classify_skill("how much came in from Atlantis") == "finance_admin"
    assert any("openrouter" in c for c in calls)
    assert logged and logged[0]["reason"] == "local_unreachable" and logged[0]["task"] == "skill_classifier"


def test_local_up_never_calls_cloud(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        return _Resp({"response": "calculations"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    assert HRMOrchestrator()._llm_classify_skill("12 x 14") == "calculations"
    assert not any("openrouter" in c for c in calls)


def test_no_key_no_cloud(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert HRMOrchestrator()._llm_classify_skill("anything") is None


# ── 31 Oct 2026: the cloud fallback answered nothing on every benchmark case ─────────────────

def _cloud_only(monkeypatch, answers):
    """Local box down; each OpenRouter call returns the next entry (a content string, or an
    exception to raise). Returns the list of models asked, in order."""
    asked = []

    def fake_post(url, **kw):
        if "openrouter" not in url:
            raise httpx.ConnectError("tunnel down")
        asked.append(kw["json"]["model"])
        nxt = answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _Resp({"choices": [{"message": {"content": nxt}}],
                      "usage": {"prompt_tokens": 90, "completion_tokens": 2}})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "skill_classifier_cloud_model", "first/model")
    monkeypatch.setattr(settings, "cloud_model_fallbacks", "first/model,second/model,third/model")
    monkeypatch.setattr("core.llm_router._log_decision", lambda **kw: None)
    return asked


def test_empty_answer_tries_the_next_model(monkeypatch):
    asked = _cloud_only(monkeypatch, ["", "finance_admin"])
    assert HRMOrchestrator()._llm_classify_skill("what did we spend at the hardware store") == "finance_admin"
    assert asked == ["first/model", "second/model"]   # one other model, never a third


def test_error_tries_the_next_model(monkeypatch):
    asked = _cloud_only(monkeypatch, [httpx.ReadTimeout("slow"), "calculations"])
    assert HRMOrchestrator()._llm_classify_skill("12 x 14") == "calculations"
    assert asked == ["first/model", "second/model"]


def test_a_real_none_is_the_answer(monkeypatch):
    asked = _cloud_only(monkeypatch, ["none"])
    assert HRMOrchestrator()._llm_classify_skill("hello there") is None
    assert asked == ["first/model"]                   # an answer, so no second call


def test_both_empty_warns_and_falls_back(monkeypatch, caplog):
    _cloud_only(monkeypatch, ["", ""])
    with caplog.at_level("WARNING", logger="core.hrm.orchestrator"):
        assert HRMOrchestrator()._llm_classify_skill("the request text") is None
    assert "gave no answer" in caplog.text and "empty answer" in caplog.text
    assert "the request text" not in caplog.text      # never the message itself


def test_reply_has_room_for_a_thinking_model(monkeypatch):
    bodies = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: (
        bodies.append(kw["json"]) or _Resp({"choices": [{"message": {"content": "web_search"}}]}))
        if "openrouter" in url else (_ for _ in ()).throw(httpx.ConnectError("down")))
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr("core.llm_router._log_decision", lambda **kw: None)
    assert HRMOrchestrator()._llm_classify_skill("latest news on load shedding") == "web_search"
    assert bodies[0]["max_tokens"] >= 128


def test_every_attempt_is_measured_without_text(monkeypatch):
    rows = []
    monkeypatch.setattr("core.reasoning_telemetry.emit", lambda **kw: rows.append(kw))
    _cloud_only(monkeypatch, ["", "finance_admin"])
    HRMOrchestrator()._llm_classify_skill("secret customer words")
    calls = [r for r in rows if r.get("system") == "vula-llm-call"]
    assert [(r["extra"]["backend"], r["outcome"]) for r in calls] == [
        ("local", "error"), ("cloud", "error"), ("cloud", "ok")]
    assert all(r["task"] == "skill_classifier" for r in calls)
    assert calls[-1]["extra"]["model"] == "second/model" and calls[-1]["extra"]["prompt_tokens"] == 90
    assert "secret customer words" not in repr(rows)


def test_no_cloud_model_configured_means_no_cloud(monkeypatch):
    monkeypatch.setattr(settings, "skill_classifier_cloud_model", "")
    assert HRMOrchestrator._cloud_classifier_models() == []


def test_default_cloud_classifier_is_not_a_thinking_model():
    from config import Settings
    assert "gemini-2.5" not in Settings.model_fields["skill_classifier_cloud_model"].default

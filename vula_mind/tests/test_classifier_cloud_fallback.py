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

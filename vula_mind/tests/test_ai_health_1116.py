"""16 Nov 2026, Ian: "we need to be accurate". A dead Gemini (no endpoint under zero retention) and
an empty DuckDuckGo ran for days with nobody told. vula/ai_health.py makes them loud; a failed tool
no longer becomes an answer from general knowledge."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import litellm
import pytest

from config import settings
from core import llm_router
from core.skills import web_search
from vula import ai_health

SONNET, LLAMA, GEMINI = "anthropic/claude-sonnet-5", "meta-llama/llama-3.3-70b-instruct", "google/gemini-2.5-flash"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    ai_health._state.clear()
    monkeypatch.setattr(ai_health, "_restored", True)
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(settings, "model_vision", SONNET)
    monkeypatch.setattr(settings, "model_worker_cheap", LLAMA)
    monkeypatch.setattr(settings, "model_worker_cloud", LLAMA)
    monkeypatch.setattr(settings, "cloud_model_fallbacks", f"{LLAMA},{SONNET}")
    monkeypatch.setattr(settings, "cloud_model_by_task", "")
    web_search.take_stats()
    yield
    ai_health._state.clear()


def _not_found(model):
    return litellm.NotFoundError(message="No endpoints found", model=model, llm_provider="openrouter")


def test_every_configured_cloud_model_is_listed_once_with_its_uses(monkeypatch):
    monkeypatch.setattr(settings, "cloud_model_by_task", '{"commerce_assistant": "%s"}' % GEMINI)
    models = ai_health.cloud_models()
    assert set(models) == {SONNET, LLAMA, GEMINI}
    assert "scanning" in models[SONNET] and "buildup research" in models[SONNET]
    assert "document analysis" in models[LLAMA] and "fallback" in models[LLAMA]
    assert models[GEMINI] == ["commerce assistant"]


def test_a_dead_model_is_reported_even_though_a_fallback_would_answer():
    """The probe must reach the model it names — the fallback chain hid Gemini for 3 days."""
    seen = []

    async def fake_call(**kw):
        seen.append(kw["model"])
        if "gemini" in kw["model"]:
            raise _not_found(kw["model"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])

    async def wrapped(**kw):
        return await llm_router._with_cloud_fallback(fake_call, kw)

    with patch.object(litellm, "acompletion", wrapped):
        ok, why = asyncio.run(ai_health.probe_model(GEMINI))
    assert not ok and "zero retention" in why
    assert seen == [f"openrouter/{GEMINI}"]          # no fallback tried


def test_the_vision_model_is_sent_an_image():
    sent = {}

    async def fake(**kw):
        sent.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace()])

    with patch.object(litellm, "acompletion", fake):
        assert asyncio.run(ai_health.probe_model(SONNET, vision=True)) == (True, "")
    parts = sent["messages"][0]["content"]
    assert any(p.get("type") == "image_url" for p in parts)
    assert sent["max_tokens"] == 5


def test_search_fails_when_it_finds_nothing_or_real_searches_came_back_empty(monkeypatch):
    monkeypatch.setattr(web_search, "_ddg_search", AsyncMock(return_value=[]))
    ok, why = asyncio.run(ai_health.probe_search())
    assert not ok and "no results" in why

    monkeypatch.setattr(web_search, "_ddg_search", AsyncMock(return_value=[{"title": "t", "url": "u"}]))
    web_search._stats.update(searches=10, empty=9)
    ok, why = asyncio.run(ai_health.probe_search())
    assert not ok and "9 of 10" in why
    assert web_search._stats == {"searches": 0, "empty": 0}     # read and reset
    assert asyncio.run(ai_health.probe_search()) == (True, "")


def test_search_counts_empty_results(monkeypatch):
    monkeypatch.setattr(settings, "serper_api_key", "")
    monkeypatch.setattr(web_search, "_ddg_html", AsyncMock(return_value=[]))
    asyncio.run(web_search._ddg_search("anything"))
    assert web_search.take_stats() == {"searches": 1, "empty": 1}


def _run(checks, monkeypatch):
    alerts, rows = [], []
    monkeypatch.setattr(ai_health, "run_checks", AsyncMock(return_value=checks))
    monkeypatch.setattr(ai_health, "_record", lambda c, since: rows.append((c["name"], c["ok"])))

    async def fake_alert(text):
        alerts.append(text)
        return {"whatsapp": False, "email": False}

    monkeypatch.setattr(ai_health, "_alert", fake_alert)
    changed = asyncio.run(ai_health.tick())
    return changed, alerts, rows


def test_failing_alerts_once_then_recovery_is_told(monkeypatch):
    down = [{"name": f"model:{GEMINI}", "ok": False, "why": "no endpoint", "uses": ["scanning"]},
            {"name": "web_search", "ok": True, "why": "", "uses": []}]
    changed, alerts, rows = _run(down, monkeypatch)
    assert [c["name"] for c in changed] == [f"model:{GEMINI}"]
    assert len(alerts) == 1 and "FAILING" in alerts[0] and GEMINI in alerts[0] and "scanning" in alerts[0]
    assert len(rows) == 2                                     # every check recorded every run

    changed, alerts, _ = _run(down, monkeypatch)            # still down: no new alert
    assert changed == [] and alerts == []

    up = [{**down[0], "ok": True, "why": ""}, down[1]]
    changed, alerts, _ = _run(up, monkeypatch)
    assert len(alerts) == 1 and "working again" in alerts[0]


def test_status_reads_the_latest_result_per_check_and_says_what_isnt_set_up(monkeypatch):
    rows = [{"detail": {"name": f"model:{GEMINI}", "ok": False, "why": "no endpoint", "uses": ["scanning"],
                        "since": "2026-11-13T05:00:00Z"}, "created_at": "2026-11-16T09:00:00Z"},
            {"detail": {"name": f"model:{GEMINI}", "ok": True, "why": ""}, "created_at": "2026-11-16T03:00:00Z"},
            {"detail": {"name": "web_search", "ok": True, "why": ""}, "created_at": "2026-11-16T09:00:00Z"}]

    class Q:
        def __getattr__(self, _):
            return lambda *a, **k: self

        def execute(self):
            return SimpleNamespace(data=rows)

    monkeypatch.setattr("core.mass_mind.health._client", lambda: SimpleNamespace(table=lambda _t: Q()))
    monkeypatch.setattr(settings, "platform_support_phone", "")
    st = ai_health.status()
    assert [c["name"] for c in st["failing"]] == [f"model:{GEMINI}"]
    assert st["failing"][0]["label"].startswith(f"Cloud model {GEMINI}")
    assert len(st["checks"]) == 2 and st["last_run"] == "2026-11-16T09:00:00Z"
    assert "PLATFORM_SUPPORT_PHONE" in st["alerts_not_set_up"]


def test_a_failed_tool_is_said_plainly_and_never_passes_on_provider_text():
    from core.skills.commerce_admin import TOOL_FAILED_NOTE, _tool_failed
    out = _tool_failed(litellm.BadRequestError(message='OpenrouterException - {"user_id":"user_x"}',
                                               model="m", llm_provider="openrouter"))
    assert out["error"] == "a technical problem on Vula's side" and out["failed"]
    assert "user_x" not in str(out) and out["note"] == TOOL_FAILED_NOTE
    assert "general knowledge" in TOOL_FAILED_NOTE
    # a reason meant for the person (our own validation) still reaches them
    assert _tool_failed(ValueError("That project isn't registered."))["error"] == "That project isn't registered."


def test_build_up_research_failing_gets_a_fixed_reply(monkeypatch):
    from core.skills import commerce_admin as ca
    from vula.commerce import buildups
    monkeypatch.setattr(buildups, "find", lambda *a, **k: None)
    monkeypatch.setattr(buildups, "research", AsyncMock(side_effect=litellm.BadRequestError(
        message="context_length_exceeded", model="m", llm_provider="openrouter")))
    skill = ca.CommerceAdminSkill.__new__(ca.CommerceAdminSkill)
    out = asyncio.run(skill._buildup("trade_buildup", "digg-demo",
                                     {"item": "one-brick wall", "spec": {"thickness_mm": 220}, "research": True},
                                     {"phone": "27820000000"}))
    assert out["reply_verbatim"] == ca.BUILDUP_FAILED and "nothing was guessed" in out["reply_verbatim"]
    assert ca.checked_reply(out) == ca.BUILDUP_FAILED

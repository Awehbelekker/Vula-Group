"""Gap audit 1.3: every message answered even when AI is down.

- an OpenRouter model that errors falls through to the next model in CLOUD_MODEL_FALLBACKS;
- an error in the request itself (bad input, auth) is not retried on other models;
- when every model failed and nothing was sent, WhatsApp sends a holding reply;
- a blocked event loop (the 26 Oct freeze) is noticed from another thread."""
import asyncio
import logging

import litellm
import pytest

from core import llm_router
from vula import loop_watchdog, turns


def _down(model):
    return litellm.ServiceUnavailableError(message="provider down", llm_provider="openrouter", model=model)


def _fake(fail: set, calls: list, exc=_down):
    async def call(**kw):
        calls.append(kw["model"])
        if kw["model"] in fail:
            raise exc(kw["model"])
        return {"model": kw["model"]}
    return call


@pytest.fixture
def chain(monkeypatch):
    monkeypatch.setattr(llm_router.settings, "cloud_model_fallbacks", "meta-llama/llama-3.3-70b-instruct, google/gemini-2.5-flash")
    monkeypatch.setattr(llm_router.settings, "model_vision", "google/gemini-2.5-flash")


def test_chain_starts_with_the_route_and_skips_repeats(chain):
    assert llm_router.cloud_fallback_chain("openrouter/google/gemini-2.5-flash") == [
        "openrouter/google/gemini-2.5-flash", "openrouter/meta-llama/llama-3.3-70b-instruct"]


def test_image_calls_only_fall_back_to_the_vision_model(chain):
    msgs = [{"role": "user", "content": [{"type": "text", "text": "read"},
                                         {"type": "image_url", "image_url": {"url": "data:x"}}]}]
    assert llm_router.cloud_fallback_chain("openrouter/x/vision-a", msgs) == [
        "openrouter/x/vision-a", "openrouter/google/gemini-2.5-flash"]


def test_next_model_answers_when_the_first_is_down(chain):
    calls = []
    out = asyncio.run(llm_router._with_cloud_fallback(
        _fake({"openrouter/a/one"}, calls), {"model": "openrouter/a/one", "messages": []}))
    assert out == {"model": "openrouter/meta-llama/llama-3.3-70b-instruct"}
    assert calls == ["openrouter/a/one", "openrouter/meta-llama/llama-3.3-70b-instruct"]


def test_a_bad_request_is_not_retried(chain):
    calls = []

    def bad(model):
        return litellm.BadRequestError(message="bad", model=model, llm_provider="openrouter")

    with pytest.raises(litellm.BadRequestError):
        asyncio.run(llm_router._with_cloud_fallback(
            _fake({"openrouter/a/one"}, calls, bad), {"model": "openrouter/a/one", "messages": []}))
    assert calls == ["openrouter/a/one"]


def test_timeouts_and_rate_limits_move_on():
    assert llm_router._worth_another_model(litellm.Timeout(message="t", model="m", llm_provider="p"))
    assert llm_router._worth_another_model(asyncio.TimeoutError())
    assert not llm_router._worth_another_model(
        litellm.AuthenticationError(message="key", llm_provider="p", model="m"))
    assert not llm_router._worth_another_model(ValueError("ours"))


def test_all_down_marks_the_turn_and_alerts_once(chain, monkeypatch):
    alerts = []

    async def fake_alert(text):
        alerts.append(text)
        return True

    monkeypatch.setattr("vula.integrations.platform_support.alert_operator", fake_alert)
    monkeypatch.setattr(turns, "_save", lambda turn: None)
    from core.mass_mind import health
    health._last_alerted.pop("cloud_models_down", None)
    seen = {}

    async def turn_body():
        calls = []
        everything = {"openrouter/a/one", "openrouter/meta-llama/llama-3.3-70b-instruct",
                      "openrouter/google/gemini-2.5-flash"}
        for _ in range(2):
            with pytest.raises(litellm.ServiceUnavailableError):
                await llm_router._with_cloud_fallback(_fake(everything, calls),
                                                      {"model": "openrouter/a/one", "messages": []})
        seen.update(turns.current())
        await asyncio.sleep(0)

    asyncio.run(turns.run(turn_body(), tenant_id="digg-demo", phone="27820000000", kind="text"))
    assert seen["_ai_down"] is True
    assert any(s["step"] == "ai_unavailable" for s in seen["steps"])
    assert len(alerts) == 1 and "ServiceUnavailableError" in alerts[0]
    assert "provider down" not in alerts[0]


def test_whatsapp_sends_the_holding_reply_when_ai_is_down(monkeypatch):
    from vula.api import whatsapp as wa
    sent = []

    async def fake_send(phone, text, tenant_id="", **k):
        sent.append(text)
        turns.reply(text)
        return True

    monkeypatch.setattr(wa, "_send_reply", fake_send)
    monkeypatch.setattr(turns, "_save", lambda turn: None)

    async def silent_failure():
        turns.current()["_ai_down"] = True

    asyncio.run(turns.run(wa._answer_even_if_ai_down(silent_failure()),
                          tenant_id="digg-demo", phone="27820000000", kind="text"))
    assert sent == [wa.AI_DOWN_REPLY]


def test_no_holding_reply_when_something_was_answered(monkeypatch):
    from vula.api import whatsapp as wa
    sent = []

    async def fake_send(phone, text, tenant_id="", **k):
        sent.append(text)
        turns.reply(text)
        return True

    monkeypatch.setattr(wa, "_send_reply", fake_send)
    monkeypatch.setattr(turns, "_save", lambda turn: None)

    async def answered_anyway():
        turns.current()["_ai_down"] = True
        await wa._send_reply("27820000000", "Here are your invoices…")

    async def only_the_quick_holding_message():
        turns.current()["_ai_down"] = True
        await wa._send_reply("27820000000", wa.HOLDING_REPLY)

    asyncio.run(turns.run(wa._answer_even_if_ai_down(answered_anyway()),
                          tenant_id="t", phone="27820000000", kind="text"))
    assert sent == ["Here are your invoices…"]
    sent.clear()
    # "Just checking that for you…" is not an answer: the person still gets told
    asyncio.run(turns.run(wa._answer_even_if_ai_down(only_the_quick_holding_message()),
                          tenant_id="t", phone="27820000000", kind="text"))
    assert sent == [wa.HOLDING_REPLY, wa.AI_DOWN_REPLY]


def test_watchdog_alerts_once_per_stall_and_logs_recovery(caplog):
    alerts = []
    dog = loop_watchdog.Watchdog(60, alerts.append)
    dog.beat(now=1000)
    assert dog.check(now=1030) is None
    with caplog.at_level(logging.WARNING, logger="vula.loop_watchdog"):
        assert dog.check(now=1061) == "stalled"
        assert dog.check(now=1200) is None          # still stalled: no second alert
        dog.beat(now=1205)
        assert dog.check(now=1206) == "recovered"
    assert len(alerts) == 1 and "61s" in alerts[0]
    assert any("stalled for 61s" in r.message for r in caplog.records if r.levelno == logging.ERROR)


def test_watchdog_off_when_zero():
    assert loop_watchdog.start(0) is None


def test_watchdog_sees_a_blocked_loop():
    """End to end: a blocking call on the loop is caught from the watchdog thread."""
    import threading
    import time
    alerts = []

    async def main():
        dog = loop_watchdog.Watchdog(0.3, alerts.append, threading.get_ident())

        async def beats():
            while True:
                dog.beat()
                await asyncio.sleep(0.05)

        task = asyncio.get_running_loop().create_task(beats())
        await asyncio.sleep(0.1)
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                time.sleep(0.05)
                dog.check()

        t = threading.Thread(target=watch, daemon=True)
        t.start()
        time.sleep(0.6)          # blocks the loop, like the 26 Oct freeze
        await asyncio.sleep(0.1)
        stop.set()
        task.cancel()
        return dog

    dog = asyncio.run(main())
    assert len(alerts) == 1 and not dog.stalled

"""vula/local_llm_watch.py — alert after N minutes down, remind every few hours, all-clear once."""
import pytest

from config import settings
from vula import local_llm_watch as w

T0 = 1_760_000_000.0  # a fixed epoch second


@pytest.fixture
def watch(monkeypatch):
    w._reset()
    w._state["restored"] = True          # no DB in tests
    status = {"up": False, "why": "HTTP 530"}
    sent, recorded = [], []

    async def fake_probe():
        return status["up"], ("" if status["up"] else status["why"])

    async def fake_send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(w, "probe", fake_probe)
    monkeypatch.setattr(w, "_send", fake_send)
    monkeypatch.setattr(w, "_record", lambda *a: recorded.append(a[0]))
    monkeypatch.setattr(settings, "local_llm_alert_after_min", 15)
    monkeypatch.setattr(settings, "local_llm_alert_repeat_h", 6.0)
    yield status, sent, recorded
    w._reset()


async def test_no_alert_before_threshold_then_one_alert(watch):
    status, sent, recorded = watch
    assert await w.tick(T0) is None
    assert await w.tick(T0 + 10 * 60) is None
    assert await w.tick(T0 + 15 * 60) == "down"
    assert len(sent) == 1 and "unreachable for 15 min" in sent[0] and "cloudflared" in sent[0]
    assert "\n" not in sent[0]          # template parameters can't hold newlines
    assert await w.tick(T0 + 20 * 60) is None   # no repeat within the reminder window
    assert recorded == ["down"]


async def test_reminder_every_repeat_window_and_all_clear_once(watch):
    status, sent, recorded = watch
    await w.tick(T0)
    await w.tick(T0 + 15 * 60)
    assert await w.tick(T0 + 15 * 60 + 6 * 3600) == "reminder"
    status["up"] = True
    assert await w.tick(T0 + 7 * 3600) == "up"
    assert "back after 7h 00m" in sent[-1]
    assert await w.tick(T0 + 7 * 3600 + 300) is None
    assert recorded == ["down", "reminder", "up"]


async def test_short_blip_sends_nothing(watch):
    status, sent, _ = watch
    await w.tick(T0)
    status["up"] = True
    assert await w.tick(T0 + 5 * 60) is None
    status["up"] = False
    assert await w.tick(T0 + 16 * 60) is None   # the clock restarted with the new outage
    assert sent == []


def test_watch_skips_localhost(monkeypatch):
    monkeypatch.setattr(settings, "ollama_base", "http://localhost:11434")
    assert not w.enabled()
    monkeypatch.setattr(settings, "ollama_base", "https://ollama.example.com")
    assert w.enabled()


def test_platform_support_phone_is_a_setting():
    assert hasattr(settings, "platform_support_phone")

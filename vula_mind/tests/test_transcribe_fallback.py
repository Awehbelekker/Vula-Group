"""Voice-note transcription must survive the SA GPU being down.

Real incident (confirmed from vula_reasoning_telemetry, 2026-09-01): every off-the-hook voice
note on 2026-07-16 — and several digg-demo ones since — failed with a bare 530 from
whisper.vula-ai.com, and the customer was told to type instead. The module docstring promised
a cloud fallback but `_provider()` only ever returned the FIRST configured provider, so no
fallback could ever run. With OTH about to take real WhatsApp orders that is a silent
order-loss path. These tests lock in the real fallback chain.
"""
import pytest

import core.transcribe as tr


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"Server error '{self.status_code} <none>'")

    def json(self):
        return self._payload


class _Client:
    """Async-context httpx stand-in that replays a scripted list of outcomes per POST."""

    def __init__(self, script, calls):
        self._script = script
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, files=None, data=None):
        self._calls.append({"url": url, "headers": dict(headers or {}), "model": (data or {}).get("model")})
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(tr._telemetry, "emit", lambda **kw: seen.append(("telemetry", kw)))
    return seen


def _wire(monkeypatch, script, calls):
    monkeypatch.setattr(tr.httpx, "AsyncClient", lambda **kw: _Client(script, calls))


# ── provider chain ordering ─────────────────────────────────────────────────────

def test_providers_lists_local_first_then_cloud_fallbacks(monkeypatch):
    monkeypatch.setattr(tr.settings, "transcribe_base", "https://whisper.vula-ai.com/v1")
    monkeypatch.setattr(tr.settings, "transcribe_api_key", "local-key")
    monkeypatch.setattr(tr.settings, "transcribe_model", "Systran/faster-whisper-large-v3")
    monkeypatch.setattr(tr.settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(tr.settings, "openai_api_key", "oai-key")

    provs = tr._providers()
    assert [p[0] for p in provs] == [
        "https://whisper.vula-ai.com/v1",
        "https://api.groq.com/openai/v1",
        "https://api.openai.com/v1",
    ]


def test_providers_empty_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(tr.settings, "transcribe_base", "")
    monkeypatch.setattr(tr.settings, "groq_api_key", "")
    monkeypatch.setattr(tr.settings, "openai_api_key", "")
    assert tr._providers() == []


# ── real fallback behaviour ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_primary_530_falls_through_to_cloud(monkeypatch, calls):
    """The exact real failure: SA tunnel returns 530, cloud fallback saves the order."""
    monkeypatch.setattr(tr.settings, "transcribe_base", "https://whisper.vula-ai.com/v1")
    monkeypatch.setattr(tr.settings, "transcribe_api_key", "local-key")
    monkeypatch.setattr(tr.settings, "transcribe_model", "faster-whisper-large-v3")
    monkeypatch.setattr(tr.settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(tr.settings, "openai_api_key", "")
    _wire(monkeypatch, [_Resp({}, status=530), _Resp({"text": "twee hake asseblief", "language": "af"})], calls)

    text, lang = await tr.transcribe_audio(b"audio-bytes", tenant_id="off-the-hook")

    assert text == "twee hake asseblief"
    assert lang == "af"
    posts = [c for c in calls if isinstance(c, dict)]
    assert len(posts) == 2, "should have retried on the fallback provider"
    assert posts[0]["url"].startswith("https://whisper.vula-ai.com")
    assert posts[1]["url"].startswith("https://api.groq.com")


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback(monkeypatch, calls):
    monkeypatch.setattr(tr.settings, "transcribe_base", "https://whisper.vula-ai.com/v1")
    monkeypatch.setattr(tr.settings, "transcribe_api_key", "local-key")
    monkeypatch.setattr(tr.settings, "transcribe_model", "faster-whisper-large-v3")
    monkeypatch.setattr(tr.settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(tr.settings, "openai_api_key", "")
    _wire(monkeypatch, [_Resp({"text": "two hake please", "language": "en"})], calls)

    text, lang = await tr.transcribe_audio(b"audio", tenant_id="off-the-hook")

    assert (text, lang) == ("two hake please", "en")
    assert len([c for c in calls if isinstance(c, dict)]) == 1


@pytest.mark.asyncio
async def test_all_providers_failing_still_degrades_gracefully(monkeypatch, calls):
    """Never fabricate a transcript — the caller asks the customer to type."""
    monkeypatch.setattr(tr.settings, "transcribe_base", "https://whisper.vula-ai.com/v1")
    monkeypatch.setattr(tr.settings, "transcribe_api_key", "k")
    monkeypatch.setattr(tr.settings, "transcribe_model", "m")
    monkeypatch.setattr(tr.settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(tr.settings, "openai_api_key", "")
    _wire(monkeypatch, [_Resp({}, status=530), _Resp({}, status=500)], calls)

    assert await tr.transcribe_audio(b"audio", tenant_id="off-the-hook") == (None, None)


@pytest.mark.asyncio
async def test_cloudflare_access_token_not_leaked_to_cloud_fallback(monkeypatch, calls):
    """The Access service token authenticates the private SA tunnel only — sending it to a
    third-party cloud endpoint would hand out a credential to an unrelated service."""
    monkeypatch.setattr(tr.settings, "transcribe_base", "https://whisper.vula-ai.com/v1")
    monkeypatch.setattr(tr.settings, "transcribe_api_key", "local-key")
    monkeypatch.setattr(tr.settings, "transcribe_model", "m")
    monkeypatch.setattr(tr.settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(tr.settings, "openai_api_key", "")
    monkeypatch.setattr(tr, "_cf_access_headers",
                        lambda: {"CF-Access-Client-Id": "cid", "CF-Access-Client-Secret": "csec"})
    _wire(monkeypatch, [_Resp({}, status=530), _Resp({"text": "hi", "language": "en"})], calls)

    await tr.transcribe_audio(b"audio", tenant_id="off-the-hook")

    posts = [c for c in calls if isinstance(c, dict)]
    assert "CF-Access-Client-Id" in posts[0]["headers"]
    assert "CF-Access-Client-Id" not in posts[1]["headers"]
    assert "CF-Access-Client-Secret" not in posts[1]["headers"]


@pytest.mark.asyncio
async def test_no_provider_configured_returns_none(monkeypatch, calls):
    monkeypatch.setattr(tr.settings, "transcribe_base", "")
    monkeypatch.setattr(tr.settings, "groq_api_key", "")
    monkeypatch.setattr(tr.settings, "openai_api_key", "")
    assert await tr.transcribe_audio(b"audio", tenant_id="off-the-hook") == (None, None)

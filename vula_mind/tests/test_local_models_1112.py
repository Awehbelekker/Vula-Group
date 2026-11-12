"""Missing local models are downloaded through the tunnel after boot (12 Nov 2026).

LOCAL_MODELS_ENSURE lists the models the SA box should hold. Each one /api/tags doesn't show is
pulled with Ollama's own API, one at a time; the outcome is recorded (model, outcome, seconds,
Ollama's error) and nothing is ever deleted.
"""
import json

import httpx
import pytest

from vula import local_models as lm


def _box(installed, pull_lines=None, calls=None):
    """A fake Ollama: /api/tags lists `installed`; /api/pull streams `pull_lines` (per model)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, request.url.path, request.content.decode() or None))
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in installed]})
        if request.url.path == "/api/pull":
            model = json.loads(request.content)["model"]
            lines = (pull_lines or {}).get(model, [{"status": "success"}])
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        if request.url.path == "/api/delete":
            raise AssertionError("never delete a model")
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(lm.settings, "ollama_base", "http://box:11434")
    monkeypatch.setattr(lm.settings, "local_models_ensure", "qwen3:8b, glm-ocr, bge-m3")
    seen = []
    monkeypatch.setattr(lm, "_record", lambda *a, **k: seen.append(a))
    return seen


@pytest.mark.asyncio
async def test_only_missing_models_are_pulled(_settings):
    calls = []
    progress = [{"status": "pulling manifest"},
                {"status": "downloading", "total": 100, "completed": 50},
                {"status": "downloading", "total": 100, "completed": 100},
                {"status": "verifying sha256 digest"}, {"status": "success"}]
    async with _box(["bge-m3:latest", "llama3.1:8b"], {"qwen3:8b": progress}, calls) as c:
        out = await lm.ensure_once(c)
    assert out == {"qwen3:8b": "pulled", "glm-ocr": "pulled", "bge-m3": "present"}
    pulled = [json.loads(b)["model"] for m, p, b in calls if p == "/api/pull"]
    assert pulled == ["qwen3:8b", "glm-ocr"]            # one at a time, in order, bge-m3 left alone
    assert [r[:2] for r in _settings] == [("qwen3:8b", "pulled"), ("glm-ocr", "pulled")]


@pytest.mark.asyncio
async def test_ollamas_error_is_recorded_and_retried_later(_settings):
    lines = {"glm-ocr": [{"status": "pulling manifest"},
                         {"error": "pull model manifest: 412: requires a newer version of Ollama"}]}
    async with _box(["qwen3:8b", "bge-m3:latest"], lines) as c:
        out = await lm.ensure_once(c)
    assert out == {"qwen3:8b": "present", "glm-ocr": "failed", "bge-m3": "present"}
    model, outcome, error, _ = _settings[0]
    assert (model, outcome) == ("glm-ocr", "failed") and "newer version of Ollama" in error


@pytest.mark.asyncio
async def test_a_stream_that_ends_without_success_is_a_failure(_settings):
    lines = {"qwen3:8b": [{"status": "downloading", "total": 100, "completed": 40}]}
    async with _box(["glm-ocr:latest", "bge-m3:latest"], lines) as c:
        out = await lm.ensure_once(c)
    assert out["qwen3:8b"] == "failed"


@pytest.mark.asyncio
async def test_box_down_pulls_nothing():
    def handler(request):
        raise httpx.ConnectError("tunnel down")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await lm.ensure_once(c)
    assert set(out.values()) == {"unreachable"}


def test_only_ollama_names_are_used(monkeypatch):
    monkeypatch.setattr(lm.settings, "local_models_ensure", "qwen3:8b,,glm-ocr ; rm -rf,library/x:1, glm-ocr")
    assert lm.wanted() == ["qwen3:8b", "library/x:1", "glm-ocr"]
    monkeypatch.setattr(lm.settings, "local_models_ensure", "")
    assert lm.wanted() == []


@pytest.mark.asyncio
async def test_blank_setting_never_starts(monkeypatch):
    monkeypatch.setattr(lm.settings, "local_models_ensure", "")
    await lm.run_forever(first_delay_s=0)          # returns at once, no network


def test_names_without_a_tag_mean_latest():
    assert lm._full("glm-ocr") == "glm-ocr:latest" and lm._full("qwen3:8b") == "qwen3:8b"

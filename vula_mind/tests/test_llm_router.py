"""Tests for the local-first generation router (core/llm_router)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import llm_router
from core.llm_router import (
    resolve_generation_route,
    resolve_vision_route,
    reset_health_cache,
    assess_complexity,
    looks_unreliable,
    compute_confidence,
    escalate_to_cloud,
    _task_label,
    _log_decision,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_health_cache()
    yield
    reset_health_cache()


# ── resolve_generation_route ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_route_prefers_local_even_when_openrouter_key_set():
    """Local-first: Ollama up → use Ollama, even if a cloud key is configured.

    This is the default, non-negotiable behaviour (prefer_cloud_llm is False).
    """
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=True)),
        patch("core.llm_router.settings") as s,
    ):
        s.prefer_cloud_llm = False
        s.model_worker = "qwen2.5"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = "sk-or-test"
        model, api_key, api_base = await resolve_generation_route()

    assert model == "ollama/qwen2.5"
    assert api_key is None
    assert api_base == "http://localhost:11434"


@pytest.mark.asyncio
async def test_prefer_cloud_llm_is_an_explicit_opt_in_override():
    """prefer_cloud_llm=True is the only way local-first is bypassed; it must be
    an explicit accuracy-first opt-in (default False), never the default."""
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=True)),
        patch("core.llm_router.settings") as s,
    ):
        s.prefer_cloud_llm = True
        s.model_worker = "qwen2.5"
        s.model_worker_cloud = "meta-llama/llama-3.3-70b-instruct"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = "sk-or-test"
        model, api_key, api_base = await resolve_generation_route()

    assert model == "openrouter/meta-llama/llama-3.3-70b-instruct"
    assert api_key == "sk-or-test"
    assert api_base == llm_router.OPENROUTER_BASE


@pytest.mark.asyncio
async def test_prefer_cloud_llm_without_key_still_falls_back_to_local():
    """Even with the cloud override on, no key means we stay local-first."""
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=True)),
        patch("core.llm_router.settings") as s,
    ):
        s.prefer_cloud_llm = True
        s.model_worker = "qwen2.5"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = ""
        model, api_key, api_base = await resolve_generation_route()

    assert model == "ollama/qwen2.5"
    assert api_key is None


@pytest.mark.asyncio
async def test_route_falls_back_to_openrouter_when_ollama_down():
    """Hybrid: Ollama unreachable + key set → fall back to OpenRouter."""
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=False)),
        patch("core.llm_router.settings") as s,
    ):
        s.prefer_cloud_llm = False
        s.model_worker = "deepseek-r1:8b"
        s.model_worker_cloud = "deepseek-r1:8b"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = "sk-or-test"
        model, api_key, api_base = await resolve_generation_route()

    assert model == "openrouter/deepseek-r1:8b"
    assert api_key == "sk-or-test"
    assert api_base == llm_router.OPENROUTER_BASE


@pytest.mark.asyncio
async def test_route_stays_local_when_down_and_no_cloud_key():
    """Ollama down + no key → return local route so the caller errors loudly."""
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=False)),
        patch("core.llm_router.settings") as s,
    ):
        s.prefer_cloud_llm = False
        s.model_worker = "deepseek-r1:8b"
        s.model_worker_cloud = "deepseek-r1:8b"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = ""
        model, api_key, api_base = await resolve_generation_route()

    assert model == "ollama/deepseek-r1:8b"
    assert api_key is None
    assert api_base == "http://localhost:11434"


@pytest.mark.asyncio
async def test_route_honours_explicit_model_override():
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=True)),
        patch("core.llm_router.settings") as s,
    ):
        s.prefer_cloud_llm = False
        s.model_worker = "deepseek-r1:8b"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = ""
        model, _, _ = await resolve_generation_route(model="llava:7b")

    assert model == "ollama/llava:7b"


# ── resolve_vision_route ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vision_route_prefers_local_llava_when_ollama_up():
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=True)),
        patch("core.llm_router.settings") as s,
    ):
        s.model_ocr = "llava:7b"
        s.model_vision = "anthropic/claude-3.5-sonnet"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = "sk-or-test"
        model, api_key, api_base = await resolve_vision_route()

    assert model == "ollama/llava:7b"
    assert api_key is None
    assert api_base == "http://localhost:11434"


@pytest.mark.asyncio
async def test_vision_route_falls_back_to_openrouter_vision_model():
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=False)),
        patch("core.llm_router.settings") as s,
    ):
        s.model_ocr = "llava:7b"
        s.model_vision = "anthropic/claude-3.5-sonnet"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = "sk-or-test"
        model, api_key, api_base = await resolve_vision_route()

    assert model == "openrouter/anthropic/claude-3.5-sonnet"
    assert api_key == "sk-or-test"
    assert api_base == llm_router.OPENROUTER_BASE


@pytest.mark.asyncio
async def test_vision_route_stays_local_when_down_and_no_cloud_key():
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=False)),
        patch("core.llm_router.settings") as s,
    ):
        s.model_ocr = "llava:7b"
        s.model_vision = "anthropic/claude-3.5-sonnet"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = ""
        model, api_key, api_base = await resolve_vision_route()

    assert model == "ollama/llava:7b"
    assert api_key is None
    assert api_base == "http://localhost:11434"


# ── ollama_available probe + cache ────────────────────────────────────────────

def _http_client(get_mock):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = get_mock
    return client


@pytest.mark.asyncio
async def test_probe_returns_true_on_200():
    resp = MagicMock()
    resp.status_code = 200
    client = _http_client(AsyncMock(return_value=resp))
    with patch("core.llm_router.httpx.AsyncClient", return_value=client):
        assert await llm_router.ollama_available("http://localhost:11434") is True


@pytest.mark.asyncio
async def test_probe_returns_false_on_connection_error():
    client = _http_client(AsyncMock(side_effect=Exception("connection refused")))
    with patch("core.llm_router.httpx.AsyncClient", return_value=client):
        assert await llm_router.ollama_available("http://localhost:11434") is False


@pytest.mark.asyncio
async def test_probe_result_is_cached():
    """Second call within TTL must not hit the network again."""
    resp = MagicMock()
    resp.status_code = 200
    get_mock = AsyncMock(return_value=resp)
    client = _http_client(get_mock)
    with patch("core.llm_router.httpx.AsyncClient", return_value=client):
        await llm_router.ollama_available("http://localhost:11434")
        await llm_router.ollama_available("http://localhost:11434")

    assert get_mock.await_count == 1


@pytest.mark.asyncio
async def test_probe_requires_the_specific_model_when_given():
    """Model-presence probe: /api/tags up but the requested model absent → not available.

    Guards the real prod mismatch (tunnel serves llama3.2:3b, MODEL_WORKER=llama3.1:8b)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"models": [{"name": "llama3.2:3b"}]})
    client = _http_client(AsyncMock(return_value=resp))
    with patch("core.llm_router.httpx.AsyncClient", return_value=client):
        assert await llm_router.ollama_available("http://x:11434", model="llama3.2:3b") is True
        assert await llm_router.ollama_available("http://x:11434", model="llama3.1:8b") is False


# ── requirement (c): complexity threshold ─────────────────────────────────────

def test_assess_complexity_frontier_type_and_token_cap():
    with patch("core.llm_router.settings") as s:
        s.local_complexity_token_cap = 100
        assert assess_complexity(task_type="architecture_planning") == "complexity:architecture_planning"
        assert assess_complexity(task_type="commerce_chat") is None
        assert assess_complexity(messages=[{"role": "user", "content": "x" * 5000}]) == "complexity:tokens>=100"
        assert assess_complexity(messages=[{"role": "user", "content": "hi"}]) is None


@pytest.mark.asyncio
async def test_complexity_routes_to_cloud_with_logged_reason():
    logged = {}
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=True)),
        patch("core.llm_router._log_decision", side_effect=lambda **k: logged.update(k)),
        patch("core.llm_router.settings") as s,
    ):
        s.prefer_cloud_llm = False
        s.model_worker = "llama3.2:3b"
        s.model_worker_cloud = "meta-llama/llama-3.3-70b-instruct"
        s.ollama_base = "http://x:11434"
        s.openrouter_api_key = "sk-or-test"
        model, key, _ = await resolve_generation_route(task_type="architecture_planning")

    assert model == "openrouter/meta-llama/llama-3.3-70b-instruct"
    assert logged["outcome"] == "cloud" and logged["escalated"] is True
    assert logged["reason"] == "complexity:architecture_planning"


@pytest.mark.asyncio
async def test_local_first_decision_is_logged():
    logged = {}
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=True)),
        patch("core.llm_router._log_decision", side_effect=lambda **k: logged.update(k)),
        patch("core.llm_router.settings") as s,
    ):
        s.prefer_cloud_llm = False
        s.model_worker = "llama3.2:3b"
        s.model_worker_cloud = "x"
        s.ollama_base = "http://x:11434"
        s.openrouter_api_key = "sk-or-test"
        model, key, _ = await resolve_generation_route(task_type="commerce_chat")

    assert model == "ollama/llama3.2:3b" and key is None
    assert logged["outcome"] == "local" and logged["escalated"] is False
    assert logged["reason"] == "local_first"


# ── requirement (b): post-response reliability + escalation ───────────────────

def test_looks_unreliable():
    assert looks_unreliable("") is True
    assert looks_unreliable("   ") is True
    assert looks_unreliable("I cannot help with that") is True
    assert looks_unreliable("As an AI language model, I can't") is True
    assert looks_unreliable("R185.00 for 2kg hake") is False
    # confidence only counts when a threshold is supplied
    assert looks_unreliable("okay", confidence=0.2, confidence_threshold=0.4) is True
    assert looks_unreliable("okay", confidence=0.9, confidence_threshold=0.4) is False
    assert looks_unreliable("okay", confidence=0.2) is False


# ── compute_confidence: the other half of the previously-dormant confidence path ─────

def _resp_with_logprobs(token_logprobs):
    """Builds a fake litellm response with the shape compute_confidence expects:
    resp.choices[0].logprobs.content[i].logprob"""
    content = [MagicMock(logprob=v) for v in token_logprobs]
    logprobs = MagicMock(content=content)
    choice = MagicMock(logprobs=logprobs)
    return MagicMock(choices=[choice])


def test_compute_confidence_converts_mean_logprob_to_probability():
    import math
    # Two tokens at logprob -0.1 and -0.3 → mean -0.2 → exp(-0.2)
    resp = _resp_with_logprobs([-0.1, -0.3])
    conf = compute_confidence(resp)
    assert conf == pytest.approx(math.exp(-0.2), rel=1e-6)
    assert 0.0 < conf <= 1.0


def test_compute_confidence_returns_none_when_no_logprobs_present():
    """The pre-fix state for every real caller: no logprobs requested/returned at all."""
    choice = MagicMock(logprobs=None)
    resp = MagicMock(choices=[choice])
    assert compute_confidence(resp) is None


def test_compute_confidence_returns_none_when_logprobs_content_empty():
    resp = _resp_with_logprobs([])
    assert compute_confidence(resp) is None


def test_compute_confidence_handles_dict_shaped_logprobs():
    """Some providers return logprobs as a plain dict rather than an object with
    attributes — compute_confidence must handle both shapes."""
    resp = MagicMock(choices=[MagicMock(
        logprobs={"content": [{"logprob": -0.5}, {"logprob": -0.7}]}
    )])
    import math
    conf = compute_confidence(resp)
    assert conf == pytest.approx(math.exp(-0.6), rel=1e-6)


def test_compute_confidence_never_raises_on_malformed_response():
    assert compute_confidence(MagicMock(choices=[])) is None
    assert compute_confidence(object()) is None


def test_escalate_to_cloud_returns_route_or_none():
    with patch("core.llm_router.settings") as s:
        s.openrouter_api_key = "sk-or-test"
        s.model_worker_cloud = "meta-llama/llama-3.3-70b-instruct"
        route = escalate_to_cloud("local_unreliable", run_id="r1", task_type="reasoning")
    assert route[0] == "openrouter/meta-llama/llama-3.3-70b-instruct"
    assert route[1] == "sk-or-test"

    with patch("core.llm_router.settings") as s2:
        s2.openrouter_api_key = ""
        assert escalate_to_cloud("local_unreliable") is None


# ── requirement 4 + POPIA: shared telemetry envelope, no raw prompt ───────────

def test_task_label_never_leaks_prompt_content():
    msgs = [{"role": "user", "content": "SECRET tenant medical record 42"}]
    label = _task_label(None, msgs)
    assert label.startswith("hash:")
    assert "SECRET" not in label and "medical" not in label
    assert _task_label("reasoning", msgs) == "reasoning"


def test_log_decision_emits_shared_envelope(tmp_path, monkeypatch):
    logf = tmp_path / "router.jsonl"
    monkeypatch.setenv("VULA_ROUTER_LOG", str(logf))
    _log_decision(run_id="r1", task="reasoning", outcome="local", escalated=False,
                  backend="ollama/llama3.2:3b", reason="local_first")
    import json as _json
    entry = _json.loads(logf.read_text(encoding="utf-8").strip())
    assert entry["schema"] == 1 and entry["system"] == "vula-llm-router"
    assert {"run_id", "task", "timestamp", "outcome", "escalated"} <= set(entry)
    assert entry["reason"] == "local_first" and entry["backend"] == "ollama/llama3.2:3b"

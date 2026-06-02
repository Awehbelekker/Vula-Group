"""Tests for the local-first generation router (core/llm_router)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import llm_router
from core.llm_router import (
    resolve_generation_route,
    resolve_vision_route,
    reset_health_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_health_cache()
    yield
    reset_health_cache()


# ── resolve_generation_route ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_route_prefers_local_even_when_openrouter_key_set():
    """Local-first: Ollama up → use Ollama, even if a cloud key is configured."""
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=True)),
        patch("core.llm_router.settings") as s,
    ):
        s.model_worker = "qwen2.5"
        s.ollama_base = "http://localhost:11434"
        s.openrouter_api_key = "sk-or-test"
        model, api_key, api_base = await resolve_generation_route()

    assert model == "ollama/qwen2.5"
    assert api_key is None
    assert api_base == "http://localhost:11434"


@pytest.mark.asyncio
async def test_route_falls_back_to_openrouter_when_ollama_down():
    """Hybrid: Ollama unreachable + key set → fall back to OpenRouter."""
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=False)),
        patch("core.llm_router.settings") as s,
    ):
        s.model_worker = "deepseek-r1:8b"
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
        s.model_worker = "deepseek-r1:8b"
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

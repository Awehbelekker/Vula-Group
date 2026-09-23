"""Local generation over litellm's native "ollama_chat/" provider, plus the fallbacks and cost
metering around it (2026-09-23).

Root cause found while tracing DIGG retests: Vula routed local calls as "ollama/<model>". For
that provider litellm 1.102 does NOT use Ollama's native tool calling — it drops `tools`, forces
`format: json`, pastes the tool list into the system prompt under "Produce JSON OUTPUT ONLY!",
and flattens the conversation into one "### System / ### User" text block for /api/generate. The
local model never saw its own chat/tool template, could not answer in plain text, and made at
most one tool call per reply (the local_json_leak / local_toolcall_text telemetry). The
"ollama_chat/" provider uses /api/chat with real roles and native tools.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import llm_router
from core.llm_router import (
    cloud_generation_kwargs, complete_local_first, generation_kwargs, is_local_model,
    local_generation_kwargs, resolve_generation_route,
)

_TOOLS = [{"type": "function", "function": {"name": "find_document", "description": "d",
           "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}]


# ── why: what litellm actually does with each provider ──────────────────────────

def _litellm_params(provider):
    import litellm
    from litellm.utils import get_optional_params
    litellm.drop_params = True
    return get_optional_params(model="llama3.1:8b", custom_llm_provider=provider,
                               temperature=0.2, tools=_TOOLS, tool_choice="auto")


def test_ollama_chat_provider_passes_tools_natively():
    params = _litellm_params("ollama_chat")
    assert params.get("tools") and params.get("format") != "json"


def test_legacy_ollama_provider_emulates_tools_with_forced_json():
    # Documents the old behaviour this change moves away from.
    params = _litellm_params("ollama")
    assert "tools" not in params and params.get("format") == "json"


# ── routing ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("native,prefix", [(True, "ollama_chat/"), (False, "ollama/")])
async def test_generation_route_prefix_follows_the_flag(native, prefix):
    with (
        patch("core.llm_router.ollama_available", new=AsyncMock(return_value=True)),
        patch("core.llm_router.settings") as s,
    ):
        s.prefer_cloud_llm = False
        s.model_worker = "llama3.1:8b"
        s.ollama_base = "http://x:11434"
        s.openrouter_api_key = ""
        s.ollama_native_chat = native
        model, _, _ = await resolve_generation_route()
    assert model == f"{prefix}llama3.1:8b"


@pytest.mark.parametrize("model,expected", [
    ("ollama/llama3.1:8b", True), ("ollama_chat/qwen3:8b", True),
    ("openrouter/meta-llama/llama-3.3-70b-instruct", False), ("", False), (None, False),
])
def test_is_local_model(model, expected):
    assert is_local_model(model) is expected


# ── per-call kwargs ─────────────────────────────────────────────────────────────

def test_local_kwargs_carry_context_window_and_timeout():
    with patch("core.llm_router.settings") as s:
        s.ollama_num_ctx, s.local_call_timeout_s = 8192, 60
        kw = local_generation_kwargs("ollama_chat/llama3.1:8b")
        assert kw == {"num_ctx": 8192, "timeout": 60}
        assert local_generation_kwargs("openrouter/x") == {}


def test_qwen_models_run_with_thinking_off():
    with patch("core.llm_router.settings") as s:
        s.ollama_num_ctx, s.local_call_timeout_s = 8192, 60
        assert local_generation_kwargs("ollama_chat/qwen3:8b")["think"] is False
        assert "think" not in local_generation_kwargs("ollama_chat/llama3.1:8b")


def test_cloud_kwargs_lock_openrouter_to_no_retention_providers():
    with patch("core.llm_router.settings") as s:
        s.openrouter_zdr = True
        kw = cloud_generation_kwargs("openrouter/meta-llama/llama-3.3-70b-instruct")
        assert kw == {"extra_body": {"provider": {"data_collection": "deny", "zdr": True}}}
        assert cloud_generation_kwargs("ollama_chat/llama3.1:8b") == {}
        s.openrouter_zdr = False
        assert cloud_generation_kwargs("openrouter/x") == {}


def test_generation_kwargs_merges_both():
    with patch("core.llm_router.settings") as s:
        s.ollama_num_ctx, s.local_call_timeout_s, s.openrouter_zdr = 8192, 60, True
        assert "num_ctx" in generation_kwargs("ollama_chat/llama3.1:8b")
        assert "extra_body" in generation_kwargs("openrouter/x")


def test_prompts_that_would_not_fit_the_local_window_go_to_cloud():
    with patch("core.llm_router.settings") as s:
        s.local_complexity_token_cap, s.ollama_num_ctx = 8000, 8192
        # 6,500 tokens: under the 8k cap, but over num_ctx minus 2k of headroom.
        msgs = [{"role": "user", "content": "x" * (6500 * 4)}]
        assert llm_router.assess_complexity(messages=msgs) == "complexity:tokens>=6144"


# ── local failure → one cloud retry ─────────────────────────────────────────────

def _resp(content="ok"):
    r = MagicMock()
    r.choices = [SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
    return r


LOCAL = ("ollama_chat/llama3.1:8b", None, "http://x:11434")
CLOUD = ("openrouter/meta-llama/llama-3.3-70b-instruct", "k", "https://openrouter.ai/api/v1")


@pytest.mark.asyncio
async def test_local_error_retries_once_on_cloud_and_keeps_the_cloud_route():
    calls = []

    async def _fake(**kw):
        calls.append(kw["model"])
        if kw["model"].startswith("ollama"):
            raise RuntimeError("OllamaException - 524: A timeout occurred")
        return _resp("from cloud")

    with patch("litellm.acompletion", new=_fake), \
         patch("core.llm_router.escalate_to_cloud", return_value=CLOUD) as esc:
        resp, route = await complete_local_first(LOCAL, task_type="email_admin",
                                                 messages=[{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "from cloud"
    assert route == CLOUD
    assert calls == [LOCAL[0], CLOUD[0]]
    esc.assert_called_once_with("local_error", task_type="email_admin")


@pytest.mark.asyncio
async def test_cloud_errors_are_not_retried():
    async def _fake(**kw):
        raise RuntimeError("openrouter down")
    with patch("litellm.acompletion", new=_fake), \
         patch("core.llm_router.escalate_to_cloud") as esc:
        with pytest.raises(RuntimeError):
            await complete_local_first(CLOUD, task_type="t", messages=[])
    esc.assert_not_called()


@pytest.mark.asyncio
async def test_local_error_without_a_cloud_key_propagates():
    async def _fake(**kw):
        raise RuntimeError("timeout")
    with patch("litellm.acompletion", new=_fake), \
         patch("core.llm_router.escalate_to_cloud", return_value=None):
        with pytest.raises(RuntimeError):
            await complete_local_first(LOCAL, task_type="t", messages=[])


@pytest.mark.asyncio
async def test_email_admin_answers_via_cloud_when_the_local_call_times_out():
    from core.skills.base import SkillInput
    from core.skills.email_admin import EmailAdminSkill

    async def _fake(**kw):
        if kw["model"].startswith("ollama"):
            raise RuntimeError("OllamaException - 524")
        return _resp("Here's what I found.")

    with (
        patch("core.skills.email_admin.get_email_creds", return_value={"email": "a@b.c"}),
        patch("core.skills.email_admin.resolve_generation_route", new=AsyncMock(return_value=LOCAL)),
        patch("core.llm_router.escalate_to_cloud", return_value=CLOUD),
        patch("litellm.acompletion", new=_fake),
    ):
        out = await EmailAdminSkill().run(SkillInput(question="check my email", tenant_id="t"))
    assert out.answer == "Here's what I found."


# ── metering: local calls are free under either provider ────────────────────────

def test_metering_prices_local_calls_at_zero():
    from vula.integrations.metering import _price
    assert _price("ollama/llama3.1:8b") == (0.0, 0.0)
    assert _price("ollama_chat/llama3.1:8b") == (0.0, 0.0)
    assert _price("openrouter/meta-llama/llama-3.3-70b-instruct") == (0.10, 0.32)


@pytest.mark.parametrize("kwargs,expected", [
    ({"model": "llama3.1:8b", "custom_llm_provider": "ollama_chat"}, "ollama_chat/llama3.1:8b"),
    ({"model": "llama3.1:8b", "litellm_params": {"custom_llm_provider": "ollama"}},
     "ollama/llama3.1:8b"),
    ({"model": "meta-llama/llama-3.3-70b-instruct", "custom_llm_provider": "openrouter"},
     "openrouter/meta-llama/llama-3.3-70b-instruct"),
    ({"model": "ollama_chat/llama3.1:8b", "custom_llm_provider": "ollama_chat"},
     "ollama_chat/llama3.1:8b"),
    ({"model": "x"}, "x"),
])
def test_callback_model_restores_the_provider_prefix(kwargs, expected):
    from vula.integrations.metering import _callback_model
    assert _callback_model(kwargs) == expected

"""16 Nov 2026, Ian: "how do we keep the costs down".

- the cheap tier goes to Llama 3.3 70B, with Sonnet only as its fallback;
- a weak cheap-tier read escalates to a different, stronger model (not Llama again);
- the business assistant's system prompt is marked for Anthropic's prompt cache on a Claude route
  only, and nothing changes for any other model."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from config import Settings, settings
from core import llm_router
from core.llm_router import cached_prompt_tokens, with_prompt_cache

LLAMA = "meta-llama/llama-3.3-70b-instruct"
SONNET = "anthropic/claude-sonnet-5"
LONG = "rules " * 1000


def test_cheap_tier_defaults_to_llama_with_sonnet_next():
    assert Settings.model_fields["model_worker_cheap"].default == LLAMA
    with patch.object(settings, "cloud_model_fallbacks", Settings.model_fields["cloud_model_fallbacks"].default):
        assert llm_router.cloud_fallback_chain(f"openrouter/{LLAMA}") == [f"openrouter/{LLAMA}", f"openrouter/{SONNET}"]


def test_cheap_route_uses_the_cheap_model():
    with patch.object(settings, "openrouter_api_key", "k"), patch.object(settings, "model_worker_cheap", LLAMA):
        model, _k, _b = asyncio.run(llm_router.resolve_cheap_route())
    assert model == f"openrouter/{LLAMA}"


def test_escalating_a_weak_cheap_read_goes_to_a_different_model():
    with patch.object(settings, "openrouter_api_key", "k"), \
         patch.object(settings, "model_worker_cheap", LLAMA), \
         patch.object(settings, "model_worker_cloud", LLAMA), \
         patch.object(settings, "cloud_model_fallbacks", f"{LLAMA},{SONNET}"):
        assert llm_router.resolve_cloud_route()[0] == f"openrouter/{SONNET}"
    # a different cheap model leaves the strong model as it is
    with patch.object(settings, "openrouter_api_key", "k"), \
         patch.object(settings, "model_worker_cheap", "x/cheap"), \
         patch.object(settings, "model_worker_cloud", LLAMA):
        assert llm_router.resolve_cloud_route()[0] == f"openrouter/{LLAMA}"


def test_system_prompt_is_cached_on_a_claude_route_only():
    msgs = [{"role": "system", "content": LONG}, {"role": "user", "content": "hi"}]
    out = with_prompt_cache(msgs, f"openrouter/{SONNET}")
    assert out[0]["content"] == [{"type": "text", "text": LONG, "cache_control": {"type": "ephemeral"}}]
    assert out[1] is msgs[1]
    assert msgs[0]["content"] == LONG            # the caller's list is not changed
    assert with_prompt_cache(msgs, f"openrouter/{LLAMA}") is msgs
    assert with_prompt_cache(msgs, "ollama_chat/llama3.1:8b") is msgs


def test_short_or_non_text_system_prompts_are_left_alone():
    short = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert with_prompt_cache(short, f"openrouter/{SONNET}") is short
    parts = [{"role": "system", "content": [{"type": "text", "text": LONG}]}]
    assert with_prompt_cache(parts, f"openrouter/{SONNET}") is parts
    no_system = [{"role": "user", "content": LONG}]
    assert with_prompt_cache(no_system, f"openrouter/{SONNET}") is no_system


def test_cached_tokens_are_read_from_the_usage():
    resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=8800)))
    assert cached_prompt_tokens(resp) == 8800
    assert cached_prompt_tokens(SimpleNamespace(usage=SimpleNamespace(prompt_tokens_details=None))) == 0
    assert cached_prompt_tokens(object()) == 0

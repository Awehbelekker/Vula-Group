"""Local Qwen3 answers without reasoning first (12 Nov 2026).

Vula's local work is short classification, extraction and summaries. Qwen3 reasons before it
answers unless told not to: the skill classifier asks for 10 tokens and would get
"<think>\\nOkay, the user…", find no skill name and send every keyword miss to `reasoning`.
Every local path now sends think=false for the qwen3 family, and nothing for other models.
"""
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import llm_router
from core.llm_router import install_ollama_auth, think_off


@pytest.mark.parametrize("model,expected", [
    ("ollama_chat/qwen3:8b", {"think": False}),
    ("qwen3:14b", {"think": False}),
    ("qwen3", {"think": False}),
    ("ollama_chat/llama3.1:8b", {}),
    ("qwen3-vl:8b", {}),            # vision model: no hybrid reasoning switch
    ("glm-ocr", {}),
    ("deepseek-r1:8b", {}),         # ThinkMesh reads its reasoning on purpose
    ("", {}),
])
def test_think_off_only_for_qwen3(model, expected):
    assert think_off(model) == expected


@pytest.fixture()
def _fake_litellm(monkeypatch):
    fake = types.ModuleType("litellm")
    orig = AsyncMock(return_value="ok")
    fake.acompletion = orig
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr(llm_router, "_ollama_auth_installed", False)
    monkeypatch.setattr(llm_router.settings, "ollama_base", "http://localhost:11434")
    install_ollama_auth()
    return orig


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["ollama_chat/qwen3:8b", "ollama/qwen3:8b"])
async def test_wrapper_turns_reasoning_off_for_local_qwen3(_fake_litellm, model):
    import litellm
    await litellm.acompletion(model=model, api_base="http://localhost:11434",
                              messages=[{"role": "user", "content": "hi"}])
    assert _fake_litellm.call_args.kwargs["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_wrapper_leaves_other_models_and_callers_alone(_fake_litellm):
    import litellm
    await litellm.acompletion(model="ollama_chat/llama3.1:8b", api_base="http://localhost:11434",
                              messages=[])
    assert "reasoning_effort" not in _fake_litellm.call_args.kwargs
    await litellm.acompletion(model="ollama_chat/qwen3:8b", api_base="http://localhost:11434",
                              messages=[], reasoning_effort="high")
    assert _fake_litellm.call_args.kwargs["reasoning_effort"] == "high"
    # A cloud model named like it is not local: untouched.
    await litellm.acompletion(model="ollama_chat/qwen3:8b", api_base="https://elsewhere.example",
                              messages=[])
    assert "reasoning_effort" not in _fake_litellm.call_args.kwargs


def test_litellm_sends_think_false_to_ollama():
    """The pinned litellm (1.105.1) maps reasoning_effort="none" to Ollama's think=false."""
    from litellm.llms.ollama.chat.transformation import OllamaChatConfig
    from litellm.llms.ollama.completion.transformation import OllamaConfig
    for cfg in (OllamaChatConfig(), OllamaConfig()):
        params = cfg.map_openai_params({"reasoning_effort": "none"}, {}, "qwen3:8b", drop_params=False)
        assert params.get("think") is False


def test_skill_classifier_asks_qwen3_not_to_reason():
    from core.hrm.orchestrator import HRMOrchestrator
    orch = HRMOrchestrator(model="qwen3:8b")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": "calculations"}
    with patch("core.hrm.orchestrator.httpx.post", return_value=resp) as post:
        assert orch._llm_classify_skill("how much is 12% of R4 000") == "calculations"
    body = post.call_args.kwargs["json"]
    assert body["think"] is False and body["model"] == "qwen3:8b"


def test_skill_classifier_body_unchanged_for_llama():
    from core.hrm.orchestrator import HRMOrchestrator
    orch = HRMOrchestrator(model="llama3.1:8b")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": "calculations"}
    with patch("core.hrm.orchestrator.httpx.post", return_value=resp) as post:
        orch._llm_classify_skill("how much is 12% of R4 000")
    assert "think" not in post.call_args.kwargs["json"]

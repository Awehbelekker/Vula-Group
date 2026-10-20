"""A tool-calling completion must reach the network, not fail inside litellm first.

2026-10-20: a fresh Railway build picked up litellm 1.106.0, whose tool-calling path imports
the `mcp` package. Every agent reply (owner, staff and customer) then failed with
"No module named 'mcp'" before any request was sent, while all mocked tests stayed green.
This calls the real litellm against a closed local port: the only acceptable failure is
the connection itself."""
import pytest

TOOLS = [{"type": "function", "function": {"name": "find_document", "description": "d",
                                           "parameters": {"type": "object", "properties": {}}}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["openrouter/google/gemini-2.5-flash", "ollama_chat/llama3.1:8b"])
async def test_tool_calls_fail_only_on_the_connection(model):
    import litellm
    acompletion = getattr(litellm.acompletion, "__wrapped__", litellm.acompletion)
    with pytest.raises(Exception) as err:
        await acompletion(model=model, messages=[{"role": "user", "content": "hi"}], tools=TOOLS,
                          api_key="x", api_base="http://127.0.0.1:9", timeout=5, num_retries=0)
    assert "No module named" not in str(err.value), str(err.value)

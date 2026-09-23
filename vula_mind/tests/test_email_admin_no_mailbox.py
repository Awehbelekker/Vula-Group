"""email_admin without a connected mailbox, its tool-result budget, and the local context window
(2026-09-23, DIGG live retests).

- Supplier spend/materials questions are routed to email_admin for every knowledge-mode owner
  (the only skill on that path with find_document), but it used to refuse outright without a
  mailbox. It now runs with find_document only.
- A flat 1,800-char cap on tool results cut a 16-invoice find_document result mid-list, so the
  model never saw the server-computed total/notes/materials. find_document gets a larger budget.
- Local Ollama calls carry a timeout below Cloudflare's 100 s limit, but NOT a per-call context
  size: that is set once on the box (OLLAMA_CONTEXT_LENGTH), because callers requesting
  different sizes make Ollama reload the model (the 524s after #66).
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.base import SkillInput
from core.skills.email_admin import EmailAdminSkill, _fenced_result, _tools_for, TOOL_SPECS

TENANT = "digg-demo"


def _resp(content="", tool_calls=None):
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    return resp


def _tool_call(call_id, name, args_json):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args_json))


def test_tools_without_a_mailbox_are_find_document_only():
    assert [t["function"]["name"] for t in _tools_for(None)] == ["find_document"]
    assert _tools_for({"email": "x@y.z"}) is TOOL_SPECS


@pytest.mark.asyncio
async def test_non_document_question_without_mailbox_still_explains():
    with patch("core.skills.email_admin.get_email_creds", return_value=None):
        out = await EmailAdminSkill().run(SkillInput(question="check my email", tenant_id=TENANT))
    assert "No email account is connected" in out.answer


@pytest.mark.asyncio
async def test_supplier_question_without_mailbox_runs_find_document():
    seen = {}
    calls = {"n": 0}
    found = {"status": "found", "match_type": "filed_document", "total_matches": 16,
             "total_amount": "R21,256.00", "note": "n", "matches": []}

    async def _fake_completion(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            seen["tools"] = [t["function"]["name"] for t in kw["tools"]]
            seen["system"] = kw["messages"][0]["content"]
            return _resp(tool_calls=[_tool_call("c1", "find_document", '{"query": "Jack Hammer"}')])
        seen["tool_msg"] = next(m for m in kw["messages"] if m.get("role") == "tool")["content"]
        return _resp(content="16 invoices, R21,256.00 in total.")

    with (
        patch("core.skills.email_admin.get_email_creds", return_value=None),
        patch("vula.commerce.service.find_filed_document", new=AsyncMock(return_value=found)),
        patch("core.skills.email_admin.resolve_generation_route",
              new=AsyncMock(return_value=("openrouter/test", "k", None))),
        patch("litellm.acompletion", new=_fake_completion),
    ):
        out = await EmailAdminSkill().run(SkillInput(
            question="What materials did we buy from Jack Hammer?", tenant_id=TENANT))

    assert out.answer == "16 invoices, R21,256.00 in total."
    assert seen["tools"] == ["find_document"]
    assert "NO MAILBOX IS CONNECTED" in seen["system"]
    assert "R21,256.00" in seen["tool_msg"]


def test_find_document_results_get_a_larger_budget():
    big = {"total_amount": "R21,256.00", "matches": [{"summary": "x" * 200}] * 30}
    fd, other = _fenced_result("find_document", big), _fenced_result("email_read", big)
    assert len(fd) > len(other) + 3000
    assert ">>> BEGIN EMAIL_TOOL_RESULT" in fd and "R21,256.00" in fd


@pytest.mark.asyncio
async def test_local_model_calls_use_the_shared_context_and_a_timeout():
    kwargs_seen = []

    async def _fake_completion(*a, **kw):
        kwargs_seen.append(kw)
        return _resp(content="done")

    with (
        patch("core.skills.email_admin.get_email_creds", return_value={"email": "a@b.c"}),
        patch("core.skills.email_admin.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/llama3.1:8b", None, "http://localhost:11434"))),
        patch("litellm.acompletion", new=_fake_completion),
    ):
        await EmailAdminSkill().run(SkillInput(question="check my email", tenant_id=TENANT))
    from config import settings
    assert "num_ctx" not in kwargs_seen[0]
    assert kwargs_seen[0]["timeout"] == settings.local_call_timeout_s


def test_filed_rows_result_puts_the_summary_before_the_list():
    from vula.commerce.service import _filed_rows_result
    out = _filed_rows_result([{"id": "a", "fields": {"total_cents": 100}}])
    keys = list(out)
    assert keys[-1] == "matches"
    assert keys.index("total_amount") < keys.index("matches")
    assert json.dumps(out).index('"total_amount"') < json.dumps(out).index('"matches"')

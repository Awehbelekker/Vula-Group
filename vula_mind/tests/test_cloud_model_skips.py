"""1 Nov 2026 capability benchmark: the admin agent's cloud model answered NotFoundError on every
tool-calling call, the fallback then returned ~900 '!' characters on several turns, and two
decision benchmark runs overlapped on different workers. Made-up data only."""
import asyncio
import time

import pytest

from config import settings
from core import llm_router


class NotFoundError(Exception):
    """Same class name litellm/openai use, so the router's MRO check sees it."""


class APIError(Exception):
    pass


class NotFoundAPIError(NotFoundError, APIError):
    pass


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    llm_router._unavailable.clear()
    monkeypatch.setattr(settings, "cloud_model_fallbacks", "model/b,model/c")
    yield
    llm_router._unavailable.clear()


def _caller(fail_with: dict):
    asked = []

    async def call(**kw):
        asked.append(kw["model"])
        exc = fail_with.get(kw["model"])
        if exc:
            raise exc
        return {"model": kw["model"]}
    return call, asked


def test_not_found_is_skipped_next_time_and_logged_once(caplog):
    call, asked = _caller({"openrouter/model/a": NotFoundAPIError("no endpoint")})
    kw = {"model": "openrouter/model/a", "messages": [{"role": "user", "content": "x"}],
          "tools": [{"type": "function"}]}
    with caplog.at_level("WARNING", logger="core.llm_router"):
        assert asyncio.run(llm_router._with_cloud_fallback(call, dict(kw)))["model"] == "openrouter/model/b"
        assert asyncio.run(llm_router._with_cloud_fallback(call, dict(kw)))["model"] == "openrouter/model/b"
    assert asked == ["openrouter/model/a", "openrouter/model/b", "openrouter/model/b"]  # no second miss
    assert caplog.text.count("has no endpoint for this request") == 1
    assert "with tool calls" in caplog.text
    assert llm_router.unavailable_models() == ["model/a (tool calls)"]


def test_the_skip_is_per_kind_of_request():
    call, _ = _caller({"openrouter/model/a": NotFoundAPIError("no endpoint")})
    tools = {"model": "openrouter/model/a", "messages": [], "tools": [{"type": "function"}]}
    asyncio.run(llm_router._with_cloud_fallback(call, tools))   # model/a skipped for tool calls
    plain_call, plain_asked = _caller({})
    asyncio.run(llm_router._with_cloud_fallback(plain_call, {"model": "openrouter/model/a", "messages": []}))
    assert plain_asked == ["openrouter/model/a"]   # plain chat still uses it


def test_the_skip_expires(monkeypatch):
    call, asked = _caller({"openrouter/model/a": NotFoundAPIError("no endpoint")})
    asyncio.run(llm_router._with_cloud_fallback(call, {"model": "openrouter/model/a", "messages": []}))
    later = time.monotonic() + llm_router._UNAVAILABLE_FOR_S + 1
    monkeypatch.setattr(llm_router.time, "monotonic", lambda: later)
    ok_call, ok_asked = _caller({})
    asyncio.run(llm_router._with_cloud_fallback(ok_call, {"model": "openrouter/model/a", "messages": []}))
    assert ok_asked == ["openrouter/model/a"]
    assert llm_router.unavailable_models() == []


def test_everything_skipped_still_tries():
    for m in ("openrouter/model/a", "openrouter/model/b", "openrouter/model/c"):
        llm_router._unavailable[(m, False)] = time.monotonic() + 600
    call, asked = _caller({})
    asyncio.run(llm_router._with_cloud_fallback(call, {"model": "openrouter/model/a", "messages": []}))
    assert asked == ["openrouter/model/a"]


def test_next_cloud_model_after_the_one_that_answered():
    assert llm_router.next_cloud_model("openrouter/model/a", "model/b") == "openrouter/model/c"
    assert llm_router.next_cloud_model("openrouter/model/a", None) == "openrouter/model/b"
    assert llm_router.next_cloud_model("openrouter/model/a", "model/c") is None
    llm_router._unavailable[("openrouter/model/a", True)] = time.monotonic() + 600
    # model/a skipped for tools: the chain is b, c — an answer from b goes to c
    assert llm_router.next_cloud_model("openrouter/model/a", "model/b", with_tools=True) == "openrouter/model/c"


def test_readiness_names_a_skipped_model(monkeypatch):
    from vula import readiness
    llm_router._unavailable[("openrouter/model/a", True)] = time.monotonic() + 600
    gaps = readiness.platform_gaps()
    hit = [g for g in gaps if "model/a (tool calls)" in str(g)]
    assert hit and "CLOUD_MODEL_BY_TASK" in str(hit[0])


# ── the admin agent retries a garbled reply once on the next model ──────────────────────────

class _Msg:
    def __init__(self, content):
        self.content, self.tool_calls = content, None


class _Resp:
    def __init__(self, content, model):
        self.choices = [type("C", (), {"message": _Msg(content)})()]
        self.model = model


def _admin_loop(monkeypatch, replies):
    import litellm
    from core.skills.commerce_admin import CommerceAdminSkill
    asked = []

    async def fake(**kw):
        asked.append(kw["model"])
        content, answered_by = replies.pop(0)
        return _Resp(content, answered_by)

    async def route(**kw):
        return "openrouter/model/a", "k", "https://example.invalid"
    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setattr("core.skills.commerce_admin.resolve_generation_route", route)
    monkeypatch.setattr("core.skills.commerce_admin.escalate_to_cloud",
                        lambda *a, **k: ("openrouter/model/a", "k", "https://example.invalid"))
    skill = CommerceAdminSkill()
    ctx = {"tenant_id": "t-seaview", "phone": "27000000000", "caller_role": "owner", "caller_name": "Sam"}
    answer = asyncio.run(skill._agent_loop("system", "", "how were sales", ctx, tools=[
        {"type": "function", "function": {"name": "sales_summary", "parameters": {"type": "object"}}}]))
    return answer, asked


def test_garbled_reply_is_tried_again_on_the_next_model(monkeypatch):
    answer, asked = _admin_loop(monkeypatch, [("!" * 900, "model/b"), ("Sales were R1,200.00 this week.", "model/c")])
    assert answer == "Sales were R1,200.00 this week."
    assert asked == ["openrouter/model/a", "openrouter/model/c"]   # after b, which garbled


def test_only_one_retry_then_the_guard_handles_it(monkeypatch):
    answer, asked = _admin_loop(monkeypatch, [("!" * 900, "model/a"), ("!" * 900, "model/b")])
    assert len(asked) == 2 and llm_router.looks_degenerate(answer)   # run() swaps in the fallback text


def test_a_normal_reply_is_not_retried(monkeypatch):
    answer, asked = _admin_loop(monkeypatch, [("Which order do you mean?", "model/a")])
    assert answer == "Which order do you mean?" and asked == ["openrouter/model/a"]


# ── one decision benchmark at a time, across workers ────────────────────────────────────────

def test_a_run_on_another_worker_blocks_a_new_one(monkeypatch):
    from fastapi import HTTPException
    from vula import decision_bench
    from vula.api import master
    master._BENCH_TASKS.clear()
    monkeypatch.setattr(decision_bench, "run_in_progress", lambda: "bench-elsewhere")
    with pytest.raises(HTTPException) as e:
        asyncio.run(master.decision_bench_start({}))
    assert e.value.status_code == 409 and "bench-elsewhere" in e.value.detail
    monkeypatch.setattr(decision_bench, "latest", lambda run_id=None: None)
    assert asyncio.run(master.decision_bench_status())["running"] is True


def test_run_in_progress_reads_the_newest_run(monkeypatch):
    from vula import decision_bench
    from vula.commerce import service

    class Q:
        def __init__(self, rows):
            self.rows = rows

        def select(self, *a):
            return self

        def eq(self, k, v):
            if k == "outcome":
                self.rows = [r for r in self.rows if r.get("outcome") == v]
            return self

        gte = order = limit = lambda self, *a, **k: self

        def execute(self):
            return type("R", (), {"data": self.rows})()

    rows = [{"run_id": "bench-1", "outcome": "jev"}]
    monkeypatch.setattr(service, "_client", lambda: type("C", (), {"table": lambda s, n: Q(list(rows))})())
    assert decision_bench.run_in_progress() == "bench-1"
    rows.append({"run_id": "bench-1", "outcome": "summary"})
    assert decision_bench.run_in_progress() is None
    monkeypatch.setattr(service, "_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert decision_bench.run_in_progress() is None   # fail-open

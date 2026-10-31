"""Measuring every model call, the Jev decision client and the decision benchmark (31 Oct 2026)."""
import asyncio
from datetime import datetime, timedelta

import pytest
import yaml

from config import settings
from core import decision_client as dc
from vula import decision_bench as bench
from vula.integrations import metering

# ── measuring ────────────────────────────────────────────────────────────────


class _Usage:
    prompt_tokens, completion_tokens = 1200, 30


class _Resp:
    usage = _Usage()


def test_each_call_is_measured_under_its_task_without_any_text():
    metering.set_current_task("escalation_classify")
    t0 = datetime(2026, 10, 31, 10, 0, 0)
    rec = metering.call_record({"model": "google/gemini-2.5-flash", "custom_llm_provider": "openrouter",
                                "messages": [{"role": "user", "content": "secret customer text"}]},
                               _Resp(), t0, t0 + timedelta(milliseconds=840))
    assert rec["task"] == "escalation_classify" and rec["latency_ms"] == 840 and rec["backend"] == "cloud"
    assert rec["prompt_tokens"] == 1200 and rec["completion_tokens"] == 30
    assert rec["est_cost_usd"] == pytest.approx(1200 / 1e6 * 0.075 + 30 / 1e6 * 0.30)
    assert "secret" not in str(rec)
    local = metering.call_record({"model": "llama3.1:8b", "custom_llm_provider": "ollama"}, _Resp(), t0, t0)
    assert local["backend"] == "local" and local["est_cost_usd"] == 0
    failed = metering.call_record({"model": "x"}, None, t0, t0 + timedelta(seconds=2), ok=False)
    assert failed["ok"] is False and failed["latency_ms"] == 2000 and failed["prompt_tokens"] == 0


def test_the_measured_call_goes_to_the_telemetry_table(monkeypatch):
    from core import reasoning_telemetry
    seen = []
    monkeypatch.setattr(reasoning_telemetry, "emit", lambda **k: seen.append(k))
    metering.log_call({"task": "rag_answer", "latency_ms": 900, "ok": True, "prompt_tokens": 1}, "tenant-a")
    assert seen[0]["system"] == "vula-llm-call" and seen[0]["task"] == "rag_answer"
    assert seen[0]["tenant_id"] == "tenant-a" and "task" not in seen[0]["extra"]
    metering.log_call({"task": "x", "ok": False}, "_unattributed")
    assert seen[1]["tenant_id"] is None and seen[1]["outcome"] == "error"


@pytest.mark.asyncio
async def test_the_router_labels_the_call_that_follows(monkeypatch):
    from core import llm_router
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    await llm_router.resolve_generation_route(task_type="query_expansion")
    assert metering._current_task.get() == "query_expansion"


def test_no_router_call_is_left_unlabelled():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    bare = [str(p) for p in list((root / "vula").rglob("*.py")) + list((root / "core").rglob("*.py"))
            if re.search(r"await resolve_generation_route\(\)", p.read_text(errors="ignore"))]
    assert bare == []


# ── personal details stay on the server ──────────────────────────────────────

@pytest.mark.parametrize("text,gone,kept", [
    ("call me on 082 555 0101 please", "082 555 0101", "please"),
    ("my number is +27 82 555 0101", "555 0101", "my number"),
    ("WhatsApp 27825550101", "27825550101", "WhatsApp"),
    ("ID 8001015009087 for the account", "8001015009087", "for the account"),
    ("pay into 62812345678, FNB", "62812345678", "FNB"),
    ("mail me at thandi@example.co.za", "thandi@example", "mail me"),
])
def test_phone_id_bank_and_email_are_removed(text, gone, kept):
    out = dc.redact(text)
    assert gone not in out and kept in out


def test_amounts_and_long_text_are_kept_sensible():
    assert "R1 250.00" in dc.redact("total R1 250.00") and "R12345678.50" in dc.redact("R12345678.50")
    assert len(dc.redact("x" * 5000)) == dc.MAX_TEXT


# ── the Jev client ───────────────────────────────────────────────────────────

def test_the_request_is_typed_redacted_and_asks_for_no_retention(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_zdr", True)
    req = dc.body(dc.escalation_decision(), "Call me on 082 555 0101, I'm fed up")
    assert req["model"] == settings.decision_model and "[phone]" in req["state"]
    assert set(req["questions"]) == {"wants_person", "upset"}
    assert req["questions"]["upset"]["type"] == "choice" and set(req["questions"]["upset"]["criteria"]) == {"yes", "no"}
    assert req["provider"] == {"data_collection": "deny", "zdr": True}


def test_answers_are_read_carefully():
    d = dc.escalation_decision()
    good = dc.parse(d, {"answers": {"wants_person": {"choice": "yes", "confidence": 0.91},
                                    "upset": {"choice": "no", "probabilities": {"no": 0.7, "yes": 0.3}}},
                        "usage": {"cost": 0.00012}})
    assert good.ok and good.answers == {"wants_person": "yes", "upset": "no"}
    assert good.confidence == {"wants_person": 0.91, "upset": 0.7} and good.min_confidence == 0.7
    assert good.cost_usd == 0.00012
    assert not dc.parse(d, {"answers": {"wants_person": {"choice": "maybe"}, "upset": {"choice": "no"}}}).ok
    assert not dc.parse(d, {"answers": {"wants_person": {"choice": "yes"}}}).ok
    assert not dc.parse(d, {"choices": []}).ok


def test_the_three_decisions_are_well_formed():
    from core.hrm.orchestrator import SKILL_KEYWORDS
    skill = dc.skill_decision().questions[0]
    assert "none" in skill.options and "reasoning" not in skill.options
    assert set(skill.options) - {"none"} == set(SKILL_KEYWORDS) - {"reasoning"}
    assert set(dc.expense_decision().questions[0].options) == {"petrol", "clients", "accommodation", "other", "uncertain"}
    with pytest.raises(Exception):
        dc.Question(name="x", instructions="Pick one of these please", options={"Only": "one"})


class _HTTP:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def factory(self):
        outer = self

        class _C:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None):
                outer.calls += 1
                outer.last = (url, json, headers)
                r = outer.replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r
        return _C


class _R:
    def __init__(self, status, data=None):
        self.status_code, self._d = status, data

    def json(self):
        return self._d


@pytest.mark.asyncio
async def test_decide_retries_once_and_never_raises(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    http = _HTTP([_R(503), _R(200, {"answers": {"category": {"choice": "petrol", "confidence": 0.95}}})])
    monkeypatch.setattr("httpx.AsyncClient", http.factory())
    res = await dc.decide(dc.expense_decision(), "Vendor: Engen. Amount: R450.00")
    assert res.ok and res.answers == {"category": "petrol"} and http.calls == 2
    assert http.last[0] == settings.decision_api_url and http.last[2]["Authorization"] == "Bearer test-key"
    http = _HTTP([asyncio.TimeoutError(), asyncio.TimeoutError()])
    monkeypatch.setattr("httpx.AsyncClient", http.factory())
    res = await dc.decide(dc.expense_decision(), "x")
    assert not res.ok and res.error == "TimeoutError" and http.calls == 2
    http = _HTTP([_R(400)])
    monkeypatch.setattr("httpx.AsyncClient", http.factory())
    assert (await dc.decide(dc.expense_decision(), "x")).error == "HTTP 400" and http.calls == 1
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert (await dc.decide(dc.expense_decision(), "x")).error == "no OpenRouter key"


# ── the benchmark ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("decision,minimum", [("skill", 200), ("escalation", 180), ("expense_purpose", 200)])
def test_the_case_files_are_labelled_with_valid_answers(decision, minimum):
    cases = bench.load_cases(decision)
    assert len(cases) >= minimum and len({c["id"] for c in cases}) == len(cases)
    d = dc.DECISIONS[decision]()
    opts = {q.name: set(q.options) for q in d.questions}
    for c in cases:
        for q, v in bench.expected(decision, c).items():
            assert v in opts[q], (c["id"], q, v)
    if decision == "escalation":
        assert {c["lang"] for c in cases} >= {"en", "af", "zu", "xh", "st", "nso", "tn", "mixed"}


def test_the_case_files_hold_no_personal_details():
    for d in ("skill", "escalation", "expense_purpose"):
        for c in bench.load_cases(d):
            assert dc.redact(c["text"]) == c["text"][:dc.MAX_TEXT], c["id"]


def _rows(provider, decision, outcomes):
    out = []
    for i, (correct, conf, ms, lang) in enumerate(outcomes):
        out.append({"decision": decision, "provider": provider, "case_id": f"{decision}-{i}", "lang": lang,
                    "correct": correct, "ok": True, "error": None, "latency_ms": ms, "confidence": conf,
                    "cost_usd": 0.0001 if provider == "jev" else None})
    return out


def test_the_verdict_follows_the_two_point_and_twenty_percent_bar(monkeypatch):
    monkeypatch.setattr(settings, "decision_min_confidence", 0.75)
    monkeypatch.setattr(settings, "decision_timeout_s", 1.0)
    cur = _rows("current", "skill", [(True, None, 900, "en")] * 9 + [(False, None, 900, "en")])
    good = _rows("jev", "skill", [(True, 0.9, 300, "en")] * 9 + [(False, 0.6, 300, "en")])
    s = bench.summarise(cur + good)["skill"]
    assert s["current"]["accuracy"] == 90.0 and s["jev"]["accuracy"] == 90.0
    assert s["jev"]["fallback_rate"] == 0.1 and s["jev"]["cost_per_1k_usd"] == 0.1 and s["verdict"] == "flip"
    slow = _rows("jev", "skill", [(True, 0.9, 1500, "en")] * 10)          # right, but too slow for live
    assert bench.summarise(cur + slow)["skill"]["verdict"] == "keep current"
    worse = _rows("jev", "skill", [(True, 0.9, 300, "en")] * 8 + [(False, 0.9, 300, "en")] * 2)
    assert bench.summarise(cur + worse)["skill"]["verdict"] == "keep current"


def test_a_language_where_jev_lags_is_named():
    cur = _rows("current", "escalation", [(True, None, 500, "en")] * 5 + [(True, None, 500, "zu")] * 5)
    jev = _rows("jev", "escalation", [(True, 0.9, 200, "en")] * 5 + [(False, 0.9, 200, "zu")] * 2 + [(True, 0.9, 200, "zu")] * 3)
    s = bench.summarise(cur + jev)["escalation"]
    assert s["jev"]["by_lang"] == {"en": 100.0, "zu": 60.0} and s["jev"]["lagging_languages"] == ["zu"]


@pytest.mark.asyncio
async def test_a_run_writes_each_answer_without_the_text_and_a_summary(monkeypatch):
    from core import reasoning_telemetry
    seen = []
    monkeypatch.setattr(reasoning_telemetry, "emit", lambda **k: seen.append(k))

    async def jev(decision, case):
        return {"answers": bench.expected(decision, case), "ok": True, "error": None, "confidence": 0.9,
                "cost_usd": 0.0001, "latency_ms": 200}

    async def current(decision, case):
        return {"answers": {}, "ok": False, "error": "no answer", "confidence": None, "cost_usd": None, "latency_ms": 900}
    monkeypatch.setattr(bench, "SIDES", {"jev": jev, "current": current})
    out = await bench.run("bench-test", decisions=["escalation"], limit=6)
    assert out["results"]["escalation"]["jev"]["accuracy"] == 100.0
    assert out["results"]["escalation"]["current"]["failed"] == 6
    texts = {c["text"] for c in bench.load_cases("escalation")[:6]}
    answers = [k for k in seen if k["outcome"] != "summary"]
    assert len(answers) == 12 and all(k["system"] == bench.SYSTEM and k["run_id"] == "bench-test" for k in seen)
    assert not any(t in str(k) for k in answers for t in texts)
    assert seen[-1]["outcome"] == "summary"


@pytest.mark.asyncio
async def test_only_one_run_at_a_time_and_its_status(monkeypatch):
    from fastapi import HTTPException

    from vula.api import master
    gate = asyncio.Event()

    async def slow(run_id, **k):
        await gate.wait()
        return {}
    monkeypatch.setattr(bench, "run", slow)
    monkeypatch.setattr(bench, "latest", lambda run_id=None: {"run_id": "bench-old"})
    master._BENCH_TASKS.clear()
    started = await master.decision_bench_start({})
    assert started["started"] and started["run_id"].startswith("bench-")
    with pytest.raises(HTTPException) as e:
        await master.decision_bench_start({})
    assert e.value.status_code == 409
    st = await master.decision_bench_status()
    assert st["running"] is True and st["summary"] == {"run_id": "bench-old"}
    gate.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert (await master.decision_bench_status())["running"] is False
    master._BENCH_TASKS.clear()


def test_the_benchmark_routes_are_master_only():
    from vula.api import master
    paths = {r.path for r in master.router.routes}
    assert {"/decision-bench"} <= paths
    assert any("require_master" in str(getattr(d, "dependency", "")) for d in master.router.dependencies)


def test_case_files_are_valid_yaml_lists():
    for d in ("skill", "escalation", "expense_purpose"):
        assert isinstance(yaml.safe_load((bench.CASES / f"{d}.yaml").read_text()), list)

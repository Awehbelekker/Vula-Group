"""
vula/decision_bench.py — Jev against today's classifiers, on the same labelled cases.

31 Oct 2026 (Ian: "can we not run benchmark and test to compare jev"). Run on Vula's own server
(it holds the OpenRouter key — nothing here handles keys), started from the master panel. Three
decisions, each with ~200 made-up labelled cases in evals/decisions/ (no real messages):

  skill            which knowledge skill answers a request (today: HRM keyword-miss classifier,
                   local model first, then the cloud classifier)
  escalation       does the customer want a person / are they upset (today: escalation.py's
                   local-first classifier), in 7 South African languages and mixed text
  expense_purpose  what a rep's receipt was for (today: expenses.py — vendor rules, then a model)

Every case goes to "jev" (core/decision_client.py) and "current" (the real function in use today).
Each answer is written to vula_reasoning_telemetry (system vula-decision-bench) WITHOUT the
message text — only the case id, language, expected and given answer, time, confidence and
cost — and a summary row closes the run:
  accuracy (overall and per language), p50 / p95 time, cost per 1,000, how often the answer
  failed or was below the confidence bar, and how often Jev would have fallen back live (failed,
  below the bar, or slower than the live timeout). A decision is marked "flip" only when Jev is
  within 2 points of today's accuracy and would fall back on fewer than 20% of cases.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from config import settings

log = logging.getLogger(__name__)

CASES = Path(__file__).resolve().parents[1] / "evals" / "decisions"
SYSTEM = "vula-decision-bench"
PROVIDERS = ("jev", "current")
BENCH_TENANT = "_decision_bench"
BENCH_TIMEOUT_S = 10.0          # generous, so the slow tail is measured rather than cut off
FLIP_POINTS, MAX_FALLBACK = 2.0, 0.20


def load_cases(decision: str) -> List[Dict[str, Any]]:
    return yaml.safe_load((CASES / f"{decision}.yaml").read_text()) or []


def expected(decision: str, case: Dict[str, Any]) -> Dict[str, str]:
    """The case's label in the decision's own answer form (question -> option)."""
    e = case["expect"]
    if decision == "escalation":
        return {"wants_person": "yes" if e["wants_person"] else "no", "upset": "yes" if e["upset"] else "no"}
    if decision == "skill":
        return {"skill": str(e)}
    return {"category": str(e)}


# ── the two sides ────────────────────────────────────────────────────────────

async def _current(decision: str, case: Dict[str, Any]) -> Dict[str, Any]:
    """Today's function for the decision, exactly as the live path calls it."""
    t0 = time.perf_counter()
    answers: Dict[str, str] = {}
    ok, error = True, None
    try:
        if decision == "skill":
            from core.hrm.orchestrator import HRMOrchestrator
            got = await asyncio.to_thread(HRMOrchestrator()._llm_classify_skill, case["text"])
            answers = {"skill": got or "none"}
        elif decision == "escalation":
            from vula.escalation import classify_customer_message
            got = await classify_customer_message(case["text"], timeout=BENCH_TIMEOUT_S)
            if got is None:
                ok, error = False, "no answer"
            else:
                answers = {"wants_person": "yes" if got["wants_person"] else "no",
                           "upset": "yes" if got["upset"] else "no"}
        else:
            from vula.commerce.expenses import classify_purpose_category
            answers = {"category": await classify_purpose_category(BENCH_TENANT, case["vendor"], case["amount_cents"])}
    except Exception as exc:  # noqa: BLE001
        ok, error = False, type(exc).__name__
    return {"answers": answers, "ok": ok, "error": error, "confidence": None, "cost_usd": None,
            "latency_ms": int((time.perf_counter() - t0) * 1000)}


async def _jev(decision: str, case: Dict[str, Any]) -> Dict[str, Any]:
    from core import decision_client as dc
    res = await dc.decide(dc.DECISIONS[decision](), case["text"], timeout_s=BENCH_TIMEOUT_S, retries=0)
    return {"answers": res.answers, "ok": res.ok, "error": res.error, "confidence": res.min_confidence,
            "cost_usd": res.cost_usd, "latency_ms": res.latency_ms}


SIDES = {"jev": _jev, "current": _current}


# ── running ──────────────────────────────────────────────────────────────────

def _row(decision: str, provider: str, case: Dict[str, Any], got: Dict[str, Any]) -> Dict[str, Any]:
    want = expected(decision, case)
    per_q = {q: got["answers"].get(q) == v for q, v in want.items()}
    return {"decision": decision, "provider": provider, "case_id": case["id"], "lang": case.get("lang") or "en",
            "expected": want, "answers": got["answers"], "correct": bool(got["ok"] and all(per_q.values())),
            "per_question": per_q, "ok": got["ok"], "error": got["error"], "latency_ms": got["latency_ms"],
            "confidence": got["confidence"], "cost_usd": got["cost_usd"]}


async def run(run_id: Optional[str] = None, decisions: Optional[List[str]] = None,
              providers: Optional[List[str]] = None, limit: Optional[int] = None,
              concurrency: int = 4) -> Dict[str, Any]:
    """Run every case through each side; write each answer and the summary; return the summary."""
    from core import reasoning_telemetry
    from vula.integrations.metering import set_request_tenant
    set_request_tenant(BENCH_TENANT)          # the model calls' spend is filed under the benchmark
    run_id = run_id or "bench-" + uuid.uuid4().hex[:10]
    decisions = [d for d in (decisions or ["skill", "escalation", "expense_purpose"]) if (CASES / f"{d}.yaml").exists()]
    providers = [p for p in (providers or list(PROVIDERS)) if p in SIDES]
    sem = asyncio.Semaphore(max(1, concurrency))
    rows: List[Dict[str, Any]] = []

    async def one(decision: str, provider: str, case: Dict[str, Any]) -> None:
        async with sem:
            got = await SIDES[provider](decision, case)
        row = _row(decision, provider, case, got)
        rows.append(row)
        reasoning_telemetry.emit(system=SYSTEM, run_id=run_id, task=decision, outcome=provider,
                                 extra={k: v for k, v in row.items() if k not in ("decision", "provider")})

    jobs = [one(d, p, c) for d in decisions for c in load_cases(d)[: limit or None] for p in providers]
    started = time.time()
    await asyncio.gather(*jobs)
    summary = {"run_id": run_id, "seconds": round(time.time() - started, 1),
               "decision_model": settings.decision_model, "results": summarise(rows)}
    reasoning_telemetry.emit(system=SYSTEM, run_id=run_id, task="all", outcome="summary", extra=summary)
    log.info("decision benchmark %s done: %s", run_id, {d: {p: v.get("accuracy") for p, v in r.items() if p != "verdict"}
                                                         for d, r in summary["results"].items()})
    return summary


# ── the numbers ──────────────────────────────────────────────────────────────

def _pct(xs: List[float], q: float) -> Optional[int]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return int(xs[k])


def _side(rows: List[Dict[str, Any]], provider: str) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0}
    acc = lambda rs: round(100 * sum(r["correct"] for r in rs) / len(rs), 1) if rs else None  # noqa: E731
    langs = sorted({r["lang"] for r in rows})
    lat = [r["latency_ms"] for r in rows]
    bar = settings.decision_min_confidence
    low = [r for r in rows if r["ok"] and r["confidence"] is not None and r["confidence"] < bar]
    no_conf = [r for r in rows if r["ok"] and r["confidence"] is None]
    live_ms = settings.decision_timeout_s * 1000
    fallback = [r for r in rows if not r["ok"] or (r["confidence"] is not None and r["confidence"] < bar)
                or (r["latency_ms"] or 0) > live_ms] if provider == "jev" else [r for r in rows if not r["ok"]]
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    out = {"n": n, "accuracy": acc(rows), "by_lang": {lg: acc([r for r in rows if r["lang"] == lg]) for lg in langs},
           "failed": sum(1 for r in rows if not r["ok"]), "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95),
           "fallback_rate": round(len(fallback) / n, 3),
           "cost_per_1k_usd": round(1000 * sum(costs) / len(costs), 4) if costs else None}
    if provider == "jev":
        out.update(low_confidence_rate=round(len(low) / n, 3), no_confidence_given=len(no_conf),
                   slower_than_live_timeout=sum(1 for r in rows if (r["latency_ms"] or 0) > live_ms),
                   errors=sorted({r["error"] for r in rows if r["error"]})[:5])
    return out


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for d in sorted({r["decision"] for r in rows}):
        sides = {p: _side([r for r in rows if r["decision"] == d and r["provider"] == p], p) for p in PROVIDERS}
        j, c = sides.get("jev") or {}, sides.get("current") or {}
        verdict = "not enough data"
        if j.get("n") and c.get("n") and j.get("accuracy") is not None and c.get("accuracy") is not None:
            close = j["accuracy"] >= c["accuracy"] - FLIP_POINTS
            verdict = "flip" if close and j["fallback_rate"] < MAX_FALLBACK else "keep current"
            lagging = [lg for lg, a in (j.get("by_lang") or {}).items()
                       if a is not None and (c.get("by_lang") or {}).get(lg) is not None
                       and a < c["by_lang"][lg] - FLIP_POINTS]
            if lagging:
                sides["jev"]["lagging_languages"] = lagging
        out[d] = {**sides, "verdict": verdict}
    return out


def run_in_progress(max_age_min: int = 20) -> Optional[str]:
    """A run another server worker is still on: its newest answer is under `max_age_min` old and
    it has no summary yet. The in-process task list only sees this worker's runs (two overlapped on
    31 Oct). Fail-open: None when the table can't be read."""
    try:
        from datetime import datetime, timedelta, timezone
        from vula.commerce import service
        since = (datetime.now(timezone.utc) - timedelta(minutes=max_age_min)).isoformat()
        rows = (service._client().table("vula_reasoning_telemetry").select("run_id")
                .eq("system", SYSTEM).gte("created_at", since)
                .order("created_at", desc=True).limit(1).execute().data or [])
        if not rows:
            return None
        rid = rows[0]["run_id"]
        done = (service._client().table("vula_reasoning_telemetry").select("run_id")
                .eq("system", SYSTEM).eq("run_id", rid).eq("outcome", "summary").limit(1).execute().data or [])
        return None if done else rid
    except Exception:  # noqa: BLE001
        return None


def latest(run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The newest benchmark summary (or a given run's), from the telemetry table."""
    from vula.commerce import service
    q = (service._client().table("vula_reasoning_telemetry").select("run_id,extra,created_at")
         .eq("system", SYSTEM).eq("outcome", "summary"))
    if run_id:
        q = q.eq("run_id", run_id)
    rows = q.order("created_at", desc=True).limit(1).execute().data or []
    return {**rows[0]["extra"], "finished_at": rows[0]["created_at"]} if rows else None

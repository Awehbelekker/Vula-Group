"""vula/weekly_benchmark.py — the capability benchmark runs itself every Sunday night.

1 Nov 2026 (Ian: "set up the weekly benchmark"). The benchmark (evals/benchmark.py) only ran when
someone pressed the button, so the admin agent's cloud model failing on every tool call
(31 Oct) was found by luck. Now it runs on Sunday between 22:00 and 23:59 SAST, at most once a
week, read-only like every run (core/dry_run.py). Its comparison with the previous finished run
— what got worse, what got better, the top failures — goes into Monday's team email
(vula/master_digest.py) and shows in Master › Benchmark like any other run.

Once a week: a run with created_by "weekly" in the last 3 days means this week's has started, so
a restart or a second worker never starts another. A run cut off by a restart is not retried the
same night; the next week's runs as normal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SAST = timezone(timedelta(hours=2))
BY = "weekly"
_BENCH = "benchmark"


def _client():
    from vula.commerce import service
    return service._client()


def due(now: datetime) -> bool:
    """Sunday, 22:00–23:59 SAST."""
    now = now.astimezone(SAST)
    return now.weekday() == 6 and now.hour >= 22


def already_started(now: datetime) -> bool:
    try:
        since = (now - timedelta(days=3)).astimezone(timezone.utc).isoformat()
        rows = (_client().table("vula_eval_reports").select("id").eq("model", _BENCH)
                .eq("created_by", BY).gte("created_at", since).limit(1).execute().data or [])
        return bool(rows)
    except Exception as exc:  # noqa: BLE001
        # Can't tell → don't start: a skipped week is better than a run every 15 minutes.
        log.warning("weekly benchmark: started-marker check failed (%s) — skipping", exc)
        return True


async def start(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Start this week's run if it is due and hasn't started. Returns what happened."""
    from config import settings
    now = now or datetime.now(SAST)
    if not getattr(settings, "weekly_benchmark", True):
        return {"started": False, "reason": "WEEKLY_BENCHMARK is off"}
    if not due(now):
        return {"started": False, "reason": "not due"}
    if already_started(now):
        return {"started": False, "reason": "already started this week"}
    row = (_client().table("vula_eval_reports")
           .insert({"model": _BENCH, "skill": None, "status": "running", "created_by": BY})
           .execute().data or [{}])[0]
    if not row.get("id"):
        return {"started": False, "reason": "run not recorded"}
    from vula.api.master import _run_benchmark
    await _run_benchmark(row["id"], settings.benchmark_judge_model or None, None)
    return {"started": True, "id": row["id"]}


# ── comparing two runs ───────────────────────────────────────────────────────

def _results(report: Dict[str, Any]) -> Dict[str, bool]:
    """case id -> passed. From the per-case rows; failing ids from `gaps` if rows are absent."""
    rows = report.get("rows") or []
    if rows:
        return {str(r.get("id")): bool(r.get("ok")) for r in rows if r.get("id") is not None}
    return {str(g.get("id")): False for g in report.get("gaps") or [] if g.get("id") is not None}


def compare(cur: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What changed from `prev` to `cur` (two stored benchmark reports)."""
    cur_c = {c["component"]: c for c in cur.get("components") or []}
    prev_c = {c["component"]: c for c in (prev or {}).get("components") or []}
    worse, better = [], []
    for name, c in cur_c.items():
        p = prev_c.get(name)
        if not p:
            continue
        if c["passed"] < p["passed"]:
            worse.append({"component": name, "now": f"{c['passed']}/{c['total']}",
                          "before": f"{p['passed']}/{p['total']}"})
        elif c["passed"] > p["passed"]:
            better.append({"component": name, "now": f"{c['passed']}/{c['total']}",
                           "before": f"{p['passed']}/{p['total']}"})
    now_res, before_res = _results(cur), _results(prev or {})
    newly_failing = sorted(i for i, ok in now_res.items() if not ok and before_res.get(i) is True)
    prompts = {str(g.get("id")): g.get("prompt") for g in cur.get("gaps") or []}
    top = sorted((c for c in cur_c.values() if c["passed"] < c["total"]),
                 key=lambda c: (c["passed"] - c["total"], c["component"]))[:3]
    return {
        "passed": cur.get("passed"), "total": cur.get("total"),
        "before": ({"passed": prev.get("passed"), "total": prev.get("total")} if prev else None),
        "worse": worse, "better": better,
        "newly_failing": [{"id": i, "prompt": (prompts.get(i) or "")[:120]} for i in newly_failing][:10],
        "top_failures": [{"component": c["component"], "failing": c["total"] - c["passed"],
                          "total": c["total"]} for c in top],
    }


def latest_comparison() -> Optional[Dict[str, Any]]:
    """The newest finished run against the one before it (any trigger), for the digest."""
    try:
        rows = (_client().table("vula_eval_reports").select("id,report,created_by,created_at")
                .eq("model", _BENCH).eq("status", "done").order("created_at", desc=True)
                .limit(2).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("weekly benchmark comparison unavailable: %s", exc)
        return None
    if not rows:
        return None
    out = compare(rows[0].get("report") or {}, (rows[1].get("report") or {}) if len(rows) > 1 else None)
    out.update(run_at=rows[0].get("created_at"), weekly=rows[0].get("created_by") == BY)
    return out


def render_lines(c: Dict[str, Any]) -> List[str]:
    """The digest section, as text lines."""
    head = f"Capability benchmark: {c['passed']}/{c['total']} passed"
    if c.get("before"):
        b = c["before"]
        head += f" (previous run {b['passed']}/{b['total']})"
    if not c.get("weekly"):
        head += " — last run was started by hand; this week's automatic run didn't finish"
    lines = [head]
    if c["worse"]:
        lines.append("Got worse: " + "; ".join(f"{w['component']} {w['before']} → {w['now']}" for w in c["worse"]))
    if c["better"]:
        lines.append("Got better: " + "; ".join(f"{w['component']} {w['before']} → {w['now']}" for w in c["better"]))
    if c["newly_failing"]:
        lines.append("Newly failing: " + "; ".join(f"“{n['prompt']}”" for n in c["newly_failing"][:5]))
    if c["top_failures"]:
        lines.append("Most failures: " + "; ".join(f"{t['component']} ({t['failing']} of {t['total']})"
                                                  for t in c["top_failures"]))
    return lines

"""Answer checks — does Vula still give each business the right answer from its own records?
(16 Nov 2026, Ian: "we need to be accurate".)

The weekly benchmark grades capability on a few real questions with an AI judge. This is
narrower and stricter: real questions to real businesses (evals/cases/answer_checks.yaml) whose
right answer is in that business's own records, read live at run time — DIGG's rate for one-brick
walls, Off the Hook's prawn price — checked in code (the figure must be stated, no AI judge), run
through the same entry the WhatsApp path uses inside core.dry_run (nothing is changed or sent).

When it runs (scheduler leader, `run_forever`): ~10 minutes after a new deploy (keyed on
RAILWAY_DEPLOYMENT_ID) and at least weekly. Each run is a vula_eval_reports row (model
'answer_checks'). A case that newly fails alerts like vula/ai_health.py (operator WhatsApp and team
email where set up) and shows in Master › Health. Model spend is Vula's ("benchmark"), never a
tenant's. Only case ids, figures and pass/fail leave this module — answers stay in the report row.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

CASES = Path(__file__).parent / "cases" / "answer_checks.yaml"
MODEL = "answer_checks"
WEEK = timedelta(days=7)
AFTER_BOOT_S = 600
CHECK_EVERY_S = 3600


# ── figures read live from the business's records ───────────────────────────

async def _amount_qs_rate(tenant_id: str, arg: str) -> Optional[int]:
    """The business's own rate for the item, in cents: "item | unit"."""
    from vula.api.qs import search_rates
    item, _, unit = (arg or "").partition("|")
    item, unit = item.strip(), unit.strip().lower()
    for r in await asyncio.to_thread(search_rates, tenant_id, item, 10):
        if r.get("rate") and (not unit or str(r.get("unit") or "").lower() == unit) \
                and str(r.get("description") or "").strip().lower() == item.lower():
            return int(round(float(r["rate"]) * 100))
    return None


async def _amount_product_price(tenant_id: str, name: str) -> Optional[int]:
    from vula.commerce import service
    rows = await asyncio.to_thread(lambda: (
        service._client().table("commerce_products").select("name,price_cents").eq("tenant_id", tenant_id)
        .eq("name", name).limit(1).execute().data or []))
    return int(rows[0]["price_cents"]) if rows and rows[0].get("price_cents") else None


AMOUNTS: Dict[str, Callable[[str, str], Awaitable[Optional[int]]]] = {
    "qs_rate": _amount_qs_rate,
    "product_price": _amount_product_price,
}


def _rands(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


async def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """One case: the benchmark's real-entry dry run, its code rules, plus every expected figure."""
    from evals import benchmark
    want: List[int] = []
    missing_truth: List[str] = []
    for spec in case.get("amounts") or []:
        fn = AMOUNTS.get(spec.get("kind"))
        cents = None
        if fn:
            try:
                cents = await fn(case["tenant"], str(spec.get("arg") or ""))
            except Exception as exc:  # noqa: BLE001
                log.debug("answer check figure unavailable: %s", exc)
        if cents:
            want.append(cents)
        else:
            missing_truth.append(f"{spec.get('kind')} {spec.get('arg')}")
    row = await benchmark.run_agent_case(case, judge_model=None)
    checks = row.setdefault("checks", {})
    if want:
        got = set(benchmark._amounts(row.get("answer") or ""))
        checks["figures_stated"] = all(c in got for c in want)
        row["expected"] = [_rands(c) for c in want]
        # A figure that is the expected one, read from the business's own records, is backed by
        # those records even when no tool returned it (16 Nov 2026: the shop assistant's
        # "Yes, we have it: Braai Box: R350.00" is built from the catalogue in code).
        unbacked = [f for f in checks.get("_ungrounded") or [] if f not in row["expected"]]
        if checks.get("figures_grounded") is False and not unbacked:
            checks["figures_grounded"] = True
    if missing_truth:
        checks["expected_figure_on_file"] = False
        row["expected_missing"] = missing_truth
    row["why"] = [k for k, v in checks.items() if not k.startswith("_") and not v]
    if "figures_stated" in row["why"]:
        row["why"][row["why"].index("figures_stated")] = "didn't state " + ", ".join(row["expected"])
    row["ok"] = not row["why"]
    return row


async def run() -> Dict[str, Any]:
    from evals.benchmark import load_cases
    rows = []
    for case in load_cases(CASES):
        try:
            rows.append(await run_case(case))
        except Exception as exc:  # noqa: BLE001
            rows.append({"id": case["id"], "component": case["component"], "tenant": case["tenant"],
                         "prompt": case["prompt"], "ok": False,
                         "why": [f"case crashed: {type(exc).__name__}"]})
    return {"kind": MODEL, "passed": sum(1 for r in rows if r["ok"]), "total": len(rows),
            "failing": [r["id"] for r in rows if not r["ok"]], "rows": rows,
            "deploy_id": deploy_id()}


# ── when to run, storing, alerting ───────────────────────────────────────────

def deploy_id() -> str:
    return os.environ.get("RAILWAY_DEPLOYMENT_ID", "") or ""


def _client():
    from vula.commerce import service
    return service._client()


def latest(status: Optional[str] = "done") -> Optional[Dict[str, Any]]:
    try:
        q = (_client().table("vula_eval_reports").select("id,status,passed,total,report,created_by,created_at,finished_at")
             .eq("model", MODEL))
        if status:
            q = q.eq("status", status)
        rows = q.order("created_at", desc=True).limit(1).execute().data or []
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        log.debug("answer checks: couldn't read the last run: %s", exc)
        return None


def due(now: Optional[datetime] = None) -> Optional[str]:
    """'deploy' when this deploy hasn't been checked, 'weekly' when the last check is a week old,
    else None. A run started in the last 2 hours (any status) holds it, so two boots don't both run."""
    now = now or datetime.now(timezone.utc)
    last_any = latest(status=None)
    if last_any and _age(last_any.get("created_at"), now) < timedelta(hours=2) and last_any.get("status") == "running":
        return None
    last = latest()
    if last is None:
        return "deploy" if deploy_id() else "weekly"
    if deploy_id() and ((last.get("report") or {}).get("deploy_id") != deploy_id()):
        return "deploy"
    if _age(last.get("created_at"), now) >= WEEK:
        return "weekly"
    return None


def _age(ts: Any, now: datetime) -> timedelta:
    try:
        return now - datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return WEEK


def newly_failing(report: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    before = set(((previous or {}).get("report") or {}).get("failing") or [])
    return [r for r in report["rows"] if not r["ok"] and r["id"] not in before]


async def run_and_store(trigger: str) -> Dict[str, Any]:
    previous = latest()
    started = None
    try:
        got = (_client().table("vula_eval_reports").insert({
            "model": MODEL, "skill": "all", "status": "running", "created_by": trigger}).execute().data or [])
        started = got[0]["id"] if got else None
    except Exception as exc:  # noqa: BLE001
        log.warning("answer checks: couldn't record the run: %s", exc)
    report = await run()
    if started:
        try:
            _client().table("vula_eval_reports").update({
                "status": "done", "passed": report["passed"], "total": report["total"], "report": report,
                "finished_at": datetime.now(timezone.utc).isoformat()}).eq("id", started).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("answer checks: couldn't save the result: %s", exc)
    new = newly_failing(report, previous)
    log.info("answer checks (%s): %d of %d right, %d newly wrong", trigger, report["passed"],
             report["total"], len(new))
    if new:
        from vula.ai_health import _alert
        text = ("Vula answer checks: " + "; ".join(
            f"{r['id']} ({r.get('tenant')}): {', '.join(r.get('why') or ['wrong'])}" for r in new[:6])
            + f". {report['passed']} of {report['total']} right after this deploy.")
        await _alert(text)
    return report


def status() -> Optional[Dict[str, Any]]:
    """For Master › Health: the latest finished run."""
    last = latest()
    if not last:
        return None
    rep = last.get("report") or {}
    return {"passed": last.get("passed"), "total": last.get("total"), "at": last.get("finished_at") or last.get("created_at"),
            "trigger": last.get("created_by"),
            "failing": [{"id": r["id"], "tenant": r.get("tenant"), "prompt": r.get("prompt"), "why": r.get("why")}
                        for r in rep.get("rows") or [] if not r.get("ok")]}


async def run_forever() -> None:
    await asyncio.sleep(AFTER_BOOT_S)
    while True:
        try:
            trigger = await asyncio.to_thread(due)
            if trigger:
                await run_and_store(trigger)
        except Exception as exc:  # noqa: BLE001
            log.warning("answer checks tick failed: %s", exc)
        await asyncio.sleep(CHECK_EVERY_S)

"""KB maintenance jobs that need production's own keys (KB retrieval 1b, 2026-10-17).

The audience backfill, the collection health report, the past-exposure check and the live
retrieval/customer-view evals all read (and the backfill writes) the production Qdrant and
Supabase, whose keys live only on the server. They run here, on the server, two ways:

  * on boot, once, for the jobs named in KB_MAINTENANCE (comma-separated) — so a job can be
    started by setting a Railway variable, and its one-line summary read back from the logs;
  * POST /v1/master/kb-maintenance {"jobs": [...]} from a master login.

Jobs: stats, exposure, backfill_preview, backfill_apply, customer_view, retrieval_live.
Each full report goes to vula_eval_reports (model='kb_maintenance', skill=<job>), which only the
service role behind require_master can read. The log line carries counts only — never chunk
text, questions, replies or phone numbers (POPIA).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

log = logging.getLogger(__name__)

JOBS = ("stats", "exposure", "backfill_preview", "backfill_apply", "customer_view", "retrieval_live")


async def _stats() -> Dict[str, Any]:
    import httpx
    from tools.kb_stats import _scroll_all, analyse
    from vula.ingestion.pipeline import QdrantStore
    store = QdrantStore()
    out: Dict[str, Any] = {"collections": {}}
    async with httpx.AsyncClient(timeout=60.0, headers=store._headers()) as client:
        try:
            out["qdrant_version"] = (await client.get(f"{store.base}/")).json().get("version")
        except Exception as exc:  # noqa: BLE001
            out["qdrant_version"] = f"unknown ({type(exc).__name__})"
        r = await client.get(f"{store.base}/collections")
        r.raise_for_status()
        for c in sorted(x["name"] for x in r.json()["result"]["collections"] if x["name"].startswith("vula_")):
            try:
                out["collections"][c] = analyse(await _scroll_all(client, store.base, c))
            except Exception as exc:  # noqa: BLE001
                out["collections"][c] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    cols = [v for v in out["collections"].values() if "error" not in v]
    out["summary"] = {
        "qdrant_version": out["qdrant_version"], "collections": len(out["collections"]),
        "points": sum(v["points"] for v in cols),
        "legacy_int_ids": sum(v["legacy_int_ids"] for v in cols),
        "missing_chunks": sum(v["missing_chunks"] for v in cols),
        "confirmed_collisions": sum(v["confirmed_collisions"] for v in cols),
        "untagged_points": sum(v["audience"].get("(none)", 0) for v in cols),
        "chars_p50_by_collection": {k: v["chars"]["p50"] for k, v in out["collections"].items() if "chars" in v},
        "over_900_pct_by_collection": {k: v["over_900_chars_pct"] for k, v in out["collections"].items()
                                       if "over_900_chars_pct" in v},
    }
    return out


async def _exposure() -> Dict[str, Any]:
    from tools.kb_audience_backfill import all_tenant_ids
    from tools.kb_exposure_check import check, customer_turns
    from vula.ingestion.pipeline import is_shared_collection
    out: Dict[str, Any] = {"tenants": {}}
    for t in await all_tenant_ids():
        if is_shared_collection(t):
            continue
        try:
            turns = await asyncio.to_thread(customer_turns, t, "2026-01-01", 400)
            rep = await check(t, turns)
            # The stored report keeps ids, documents and figures; no question or reply text.
            for row in rep["turns"]:
                row.pop("question", None)
                row.pop("reply", None)
            out["tenants"][t] = rep
        except Exception as exc:  # noqa: BLE001
            out["tenants"][t] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    ok = [v for v in out["tenants"].values() if "error" not in v]
    out["summary"] = {
        "customer_questions": sum(v["questions"] for v in ok),
        "reached_private": {k: v["reached_private"] for k, v in out["tenants"].items() if "error" not in v and v["reached_private"]},
        "figure_overlap": {k: v["figure_overlap"] for k, v in out["tenants"].items() if "error" not in v and v["figure_overlap"]},
        "errors": [k for k, v in out["tenants"].items() if "error" in v],
    }
    return out


async def _backfill(apply: bool) -> Dict[str, Any]:
    from tools.kb_audience_backfill import all_tenant_ids, run
    out: Dict[str, Any] = {"tenants": {}}
    for t in await all_tenant_ids():
        try:
            out["tenants"][t] = await run(t, apply=apply)
        except Exception as exc:  # noqa: BLE001
            out["tenants"][t] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    ok = [v for v in out["tenants"].values() if "error" not in v]
    by: Dict[str, int] = {}
    for v in ok:
        for a, n in v["by_audience"].items():
            by[a] = by.get(a, 0) + n
    out["summary"] = {"mode": "apply" if apply else "preview", "collections": len(out["tenants"]),
                      "documents": sum(v["documents"] for v in ok), "already_tagged": sum(v["already_tagged"] for v in ok),
                      "by_audience": by, "needs_review": sum(len(v["review"]) for v in ok),
                      "written": sum(v["written"] for v in ok), "verified": sum(v["verified"] for v in ok),
                      "mismatched": sum(v["mismatched"] for v in ok),
                      "errors": [k for k, v in out["tenants"].items() if "error" in v]}
    return out


async def _customer_view() -> Dict[str, Any]:
    from evals import retrieval
    from tools.kb_audience_backfill import all_tenant_ids
    from vula.ingestion.pipeline import is_shared_collection
    tenants = [t for t in await all_tenant_ids() if not is_shared_collection(t)]
    rep = await retrieval.run_customer_view(tenants)
    rep["summary"] = {r["tenant"]: (r.get("error") or {"ok": r["ok"], "violations": len(r["violations"]),
                                                       "untagged_visible": r["legacy_untagged_visible"],
                                                       "by_audience": r["by_audience"]})
                      for r in rep["rows"]}
    return rep


async def _retrieval_live() -> Dict[str, Any]:
    from evals import retrieval
    rep = await retrieval.run_live()
    rep["summary_by_tenant"] = rep["by_tenant"]
    return rep


async def run_job(job: str, by: str = "boot") -> Dict[str, Any]:
    fn = {"stats": _stats, "exposure": _exposure, "backfill_preview": lambda: _backfill(False),
          "backfill_apply": lambda: _backfill(True), "customer_view": _customer_view,
          "retrieval_live": _retrieval_live}[job]
    from vula.commerce import service
    db = service._client()
    row_id = None
    try:
        row_id = (db.table("vula_eval_reports").insert({"model": "kb_maintenance", "skill": job,
                                                       "status": "running", "created_by": by})
                  .execute().data or [{}])[0].get("id")
    except Exception as exc:  # noqa: BLE001
        log.warning("kb maintenance %s: report row not created: %s", job, exc)
    try:
        rep = await fn()
        log.info("KB maintenance %s: %s", job, json.dumps(rep.get("summary") or rep.get("summary_by_tenant"),
                                                         default=str)[:3000])
        upd = {"status": "done", "report": json.loads(json.dumps(rep, default=str)),
               "finished_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:  # noqa: BLE001
        log.exception("KB maintenance %s failed", job)
        rep = {"error": f"{type(exc).__name__}: {exc}"}
        upd = {"status": "failed", "error": rep["error"][:300], "finished_at": datetime.now(timezone.utc).isoformat()}
    if row_id:
        try:
            db.table("vula_eval_reports").update(upd).eq("id", row_id).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("kb maintenance %s: report not saved: %s", job, exc)
    return rep


async def run_jobs(jobs: List[str], by: str = "boot") -> None:
    for job in jobs:
        await run_job(job, by=by)


async def on_boot() -> None:
    """Run the jobs named in KB_MAINTENANCE once, a minute after boot (in that order)."""
    from config import settings
    jobs = [j.strip() for j in (settings.kb_maintenance or "").split(",") if j.strip()]
    bad = [j for j in jobs if j not in JOBS]
    if bad:
        log.warning("KB_MAINTENANCE: unknown job(s) %s — known: %s", bad, ", ".join(JOBS))
    jobs = [j for j in jobs if j in JOBS]
    if not jobs:
        return
    await asyncio.sleep(60)
    try:
        from core.llm_router import install_ollama_auth
        install_ollama_auth()
    except Exception:
        pass
    log.info("KB maintenance starting: %s", ", ".join(jobs))
    await run_jobs(jobs)
    log.info("KB maintenance finished: %s", ", ".join(jobs))

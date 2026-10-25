"""KB maintenance jobs that need production's own keys (KB retrieval 1b, 2026-10-17).

The audience backfill, the collection health report, the past-exposure check and the live
retrieval/customer-view evals all read (and the backfill writes) the production Qdrant and
Supabase, whose keys live only on the server. They run here, on the server, two ways:

  * on boot, once, for the jobs named in KB_MAINTENANCE (comma-separated) — so a job can be
    started by setting a Railway variable, and its one-line summary read back from the logs;
  * POST /v1/master/kb-maintenance {"jobs": [...]} from a master login.

Jobs: stats, exposure, backfill_preview, backfill_apply, mark_public, customer_view, retrieval_live,
retrieval_diag.

mark_public (2026-10-17) opens up a REVIEWED list of documents to customers: KB_MARK_PUBLIC is
JSON {tenant_id: [filename, ...]} (spaces and underscores match each other, case ignored). Each
match is set public with audience_by=review, which the backfill never overrides. The list lives in
the environment, not the repository, because filenames are tenant data.
Each full report goes to vula_eval_reports (model='kb_maintenance', skill=<job>), which only the
service role behind require_master can read. The log line carries counts only — never chunk
text, questions, replies or phone numbers (POPIA).
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# The vula_eval_reports row of the job now running, for jobs that save progress as they go.
_CURRENT_ROW: ContextVar = ContextVar("kb_maintenance_row", default=None)

JOBS = ("stats", "exposure", "backfill_preview", "backfill_apply", "mark_public", "customer_view", "kb_learning",
        "contextualize_preview", "contextualize", "contextualize_revert", "retrieval_diag_ctx",
        "enrich_documents", "supplier_details_preview", "supplier_details",
        "retrieval_live", "retrieval_diag")


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


def _norm(name: str) -> str:
    import re
    return re.sub(r"[\s_]+", " ", (name or "").strip().lower())


async def _mark_public() -> Dict[str, Any]:
    from config import settings
    from vula.ingestion.pipeline import QdrantStore
    wanted = json.loads(settings.kb_mark_public or "{}")
    store = QdrantStore()
    out: Dict[str, Any] = {"tenants": {}}
    for tenant, names in wanted.items():
        want = {_norm(n) for n in names}
        docs = await store.list_documents(tenant)
        hit, verified = set(), 0
        for d in docs:
            if _norm(d.get("filename")) in want:
                res = await store.set_document_audience(tenant, d["doc_id"], "public", by="review")
                verified += res["chunks"] if res["verified"] else 0
                hit.add(_norm(d.get("filename")))
        out["tenants"][tenant] = {"asked": len(want), "matched": len(hit), "chunks_verified": verified,
                                  "not_found": sorted(want - hit)}
    out["summary"] = {t: {k: v[k] for k in ("asked", "matched", "chunks_verified")}
                      | {"not_found": len(v["not_found"])} for t, v in out["tenants"].items()}
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


def guard_problems(summary: Dict[str, Any]) -> List[str]:
    """Tenants whose customer view isn't clean, one short line each (counts only)."""
    out = []
    for t, v in sorted(summary.items()):
        if not isinstance(v, dict):
            out.append(f"{t}: check failed")
        elif v.get("violations") or v.get("untagged_visible"):
            out.append(f"{t}: {v.get('violations', 0)} private and {v.get('untagged_visible', 0)} "
                       "untagged document chunks visible to customers")
    return out


def _last_done(job: str) -> Optional[datetime]:
    from vula.commerce import service
    rows = (service._client().table("vula_eval_reports").select("created_at")
            .eq("model", "kb_maintenance").eq("skill", job).eq("status", "done")
            .order("created_at", desc=True).limit(1).execute().data or [])
    return datetime.fromisoformat(str(rows[0]["created_at"]).replace("Z", "+00:00")) if rows else None


GUARD_EVERY_DAYS = 7


async def weekly_guard(now: Optional[datetime] = None) -> Optional[List[str]]:
    """The customer-view check, at most once a week (2026-10-20): customers must see only
    documents marked public. Runs when the last completed check is a week old, so redeploys don't
    re-run it. A tenant whose check errored is retried once before it counts. Any problem left is
    WhatsApped to the platform operator. Returns the problems (empty = clean), or None if not due."""
    now = now or datetime.now(timezone.utc)
    try:
        last = await asyncio.to_thread(_last_done, "customer_view")
    except Exception as exc:  # noqa: BLE001
        log.info("weekly customer-view guard: last run unknown (%s) — skipping this tick", exc)
        return None
    if last and (now - last).total_seconds() < GUARD_EVERY_DAYS * 86400:
        return None
    rep = await run_job("customer_view", by="weekly")
    if "error" in rep:
        problems = [f"check failed: {str(rep['error'])[:120]}"]
    else:
        summary = dict(rep.get("summary") or {})
        failed = [t for t, v in summary.items() if not isinstance(v, dict)]
        if failed:
            from evals import retrieval
            await asyncio.sleep(60)
            try:
                retry = await retrieval.run_customer_view(failed)
                for r in retry["rows"]:
                    summary[r["tenant"]] = r.get("error") or {
                        "violations": len(r["violations"]), "untagged_visible": r["legacy_untagged_visible"]}
            except Exception as exc:  # noqa: BLE001
                log.info("weekly customer-view guard: retry failed: %s", exc)
        problems = guard_problems(summary)
    if problems:
        from vula.integrations.platform_support import alert_operator
        sent = await alert_operator(
            "Vula weekly customer-view check found a problem: " + "; ".join(problems)[:600]
            + ". Customers may see documents they shouldn't: open Documents for that business and "
              "set those documents to staff, or ask Claude to run the customer_view check again.")
        log.warning("KB weekly customer-view guard: %d problem(s), alert sent=%s", len(problems), sent)
    else:
        log.info("KB weekly customer-view guard: clean")
    return problems


async def _kb_learning() -> Dict[str, Any]:
    """The week's staff knowledge questions (vula/kb_learning.py): answered, asked which document,
    picked, nothing found, called wrong / right — and the questions worth a look."""
    from vula import kb_learning
    rep = await asyncio.to_thread(kb_learning.weekly_report)
    rep["summary"] = rep["tenants"]
    return rep


async def learning_weekly(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The knowledge-question report, at most once a week (2026-10-25), WhatsApped to the platform
    operator as one line. Returns the report, or None if not due."""
    now = now or datetime.now(timezone.utc)
    try:
        last = await asyncio.to_thread(_last_done, "kb_learning")
    except Exception as exc:  # noqa: BLE001
        log.info("weekly knowledge report: last run unknown (%s) — skipping this tick", exc)
        return None
    if last and (now - last).total_seconds() < GUARD_EVERY_DAYS * 86400:
        return None
    rep = await run_job("kb_learning", by="weekly")
    from vula import kb_learning
    line = kb_learning.weekly_line(rep) if "error" not in rep else ""
    if line:
        from vula.integrations.platform_support import alert_operator
        await alert_operator(line[:900])
    return rep


async def _retrieval_live() -> Dict[str, Any]:
    from evals import retrieval
    rep = await retrieval.run_live()
    rep["summary_by_tenant"] = rep["by_tenant"]
    return rep


def _ctx_baseline() -> str:
    from config import settings
    return settings.kb_ctx_baseline or ""


def _ctx_tenants() -> List[str]:
    from config import settings
    return [t.strip() for t in (settings.kb_ctx_tenants or "").split(",") if t.strip()]


async def _contextualize(revert: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    """Each stored piece re-embedded with its document's label (vula/kb_context.py)."""
    from vula import kb_context
    return await kb_context.run(_ctx_tenants(), revert=revert, dry_run=dry_run)


async def _enrich_documents() -> Dict[str, Any]:
    """Filed invoices and quotes read once more for the details the read didn't ask for before
    (document number, customer, order number, VAT numbers, bank details) — vula/commerce/reread.py."""
    from config import settings
    from vula.commerce import reread
    by: Dict[str, Any] = {}
    for t in _ctx_tenants():
        try:
            by[t] = await reread.enrich_details(t, limit=int(settings.kb_enrich_per_night or 0))
        except Exception as exc:  # noqa: BLE001
            by[t] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return {"by_tenant": by, "summary": by}


async def _supplier_details(apply: bool = True) -> Dict[str, Any]:
    """Suppliers' blank VAT number / phone / email / bank details filled from their own filed
    invoices (vula/commerce/supplier_details.py). No alerts; nothing on a record is overwritten."""
    from vula.commerce import supplier_details
    by: Dict[str, Any] = {}
    for t in _ctx_tenants():
        try:
            by[t] = await asyncio.to_thread(supplier_details.backfill, t, apply)
        except Exception as exc:  # noqa: BLE001
            by[t] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return {"by_tenant": by, "summary": by}


async def context_nightly(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Once a night (01:00–05:00 SAST), label any pieces not yet labelled — the first night is
    the whole knowledge base, later nights only new uploads — then, when anything changed and a
    baseline report is set (KB_CTX_BASELINE), re-run that report's questions to measure it."""
    from config import settings
    now = now or datetime.now(timezone.utc)
    if not settings.kb_context_nightly or now.hour not in (23, 0, 1, 2):
        return None
    try:
        last = await asyncio.to_thread(_last_done, "contextualize")
    except Exception as exc:  # noqa: BLE001
        log.info("nightly labelling: last run unknown (%s) — skipping this tick", exc)
        return None
    if last and (now - last).total_seconds() < 20 * 3600:
        return None
    if settings.kb_enrich_per_night:
        await run_job("enrich_documents", by="nightly")   # first, so tonight's labels carry the numbers
        await run_job("supplier_details", by="nightly")   # suppliers' blanks from what was just read
    rep = await run_job("contextualize", by="nightly")
    changed = sum((v or {}).get("done", 0) for v in (rep.get("by_tenant") or {}).values()
                  if isinstance(v, dict))
    if changed and settings.kb_ctx_baseline:
        await run_job("retrieval_diag_ctx", by="nightly")
    return rep


async def _retrieval_diag(replay_id: Optional[str] = None) -> Dict[str, Any]:
    """Where retrieval loses the right document, per stage (evals/retrieval_diag.py). Tenants from
    KB_DIAG_TENANTS. The stored report has the questions; the log line has counts only.
    Saves the report after each tenant, and picks up the tenants an earlier run (cut off by a
    restart within the last day) already finished."""
    from datetime import timedelta
    from config import settings
    from evals import retrieval_diag
    from vula.commerce import service
    tenants = [t.strip() for t in (settings.kb_diag_tenants or "").split(",") if t.strip()]
    replay_id = replay_id if replay_id is not None else (settings.kb_diag_replay or None)
    db = service._client()
    done_rows: Dict[str, List[Dict[str, Any]]] = {}
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        prev = (db.table("vula_eval_reports").select("id,report").eq("model", "kb_maintenance")
                .eq("skill", "retrieval_diag").neq("status", "done").gte("created_at", since)
                .order("created_at", desc=True).limit(5).execute().data or [])
        # Only a cut-off run of the same kind: a replay resumes a replay of the same report.
        same = [p.get("report") for p in prev
                if ((p.get("report") or {}).get("replay_of") or "") == (replay_id or "")]
        done_rows = retrieval_diag.finished_rows(same, tenants)
    except Exception as exc:  # noqa: BLE001
        log.info("retrieval_diag: no earlier partial run read (%s)", exc)
    row_id = _CURRENT_ROW.get(None)

    async def save(partial: Dict[str, Any]) -> None:
        if not row_id:
            return
        try:
            partial["replay_of"] = replay_id or None
            await asyncio.to_thread(lambda: db.table("vula_eval_reports").update(
                {"report": json.loads(json.dumps(partial, default=str))}).eq("id", row_id).execute())
        except Exception as exc:  # noqa: BLE001
            log.info("retrieval_diag: partial report not saved: %s", exc)

    replay = None
    if replay_id:
        try:
            got = (db.table("vula_eval_reports").select("report").eq("id", replay_id)
                   .limit(1).execute().data or [])
            replay = ((got[0].get("report") or {}).get("rows") or []) if got else None
        except Exception as exc:  # noqa: BLE001
            log.warning("retrieval_diag: replay report %s not read: %s", replay_id, exc)
    real = None
    if replay is None:
        from vula import kb_learning
        real = await asyncio.to_thread(kb_learning.confirmed_cases, tenants)
    rep = await retrieval_diag.run(tenants, done_rows=done_rows, on_tenant=save, replay=replay,
                                   real=real)
    rep["replay_of"] = replay_id or None
    rep["summary_by_tenant"] = {t: {k: v[k] for k in ("cases", "causes", "recall@4", "seeded_recall@4")}
                                for t, v in rep["by_tenant"].items()}
    return rep


async def run_job(job: str, by: str = "boot") -> Dict[str, Any]:
    fn = {"stats": _stats, "exposure": _exposure, "backfill_preview": lambda: _backfill(False),
          "backfill_apply": lambda: _backfill(True), "mark_public": _mark_public,
          "customer_view": _customer_view,
          "retrieval_live": _retrieval_live, "retrieval_diag": _retrieval_diag,
          "kb_learning": _kb_learning,
          "contextualize_preview": lambda: _contextualize(dry_run=True),
          "contextualize": _contextualize,
          "contextualize_revert": lambda: _contextualize(revert=True),
          "retrieval_diag_ctx": lambda: _retrieval_diag(replay_id=_ctx_baseline()),
          "enrich_documents": _enrich_documents,
          "supplier_details_preview": lambda: _supplier_details(apply=False),
          "supplier_details": _supplier_details}[job]
    from vula.commerce import service
    db = service._client()
    row_id = None
    try:
        row_id = (db.table("vula_eval_reports").insert({"model": "kb_maintenance", "skill": job,
                                                       "status": "running", "created_by": by})
                  .execute().data or [{}])[0].get("id")
    except Exception as exc:  # noqa: BLE001
        log.warning("kb maintenance %s: report row not created: %s", job, exc)
    token = _CURRENT_ROW.set(row_id)
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
    finally:
        _CURRENT_ROW.reset(token)
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

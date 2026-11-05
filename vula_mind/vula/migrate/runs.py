"""vula/migrate/runs.py — a move run (vula_imports, migration 247): read the source, build the preview.

Step 1 (5 Nov 2026) writes nothing to the books: only the run row, its status and its preview.
Reading can take minutes for a busy organisation (Xero allows 60 calls a minute), so it runs as a
background task and the page polls the run. A run left 'reading' for 30 minutes (the server
restarted) shows as failed with "start it again".
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TABLE = "vula_imports"
STALE = timedelta(minutes=30)
_TASKS: set = set()


def _client():
    from vula.commerce.service import _client as c
    return c()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_cutover(value: str) -> date:
    try:
        d = date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        raise ValueError("Pick the cut-over date (the day Vula takes over the books).")
    if d > date.today():
        raise ValueError("The cut-over date can't be in the future — pick today or an earlier month-end.")
    if d < date(2000, 1, 1):
        raise ValueError("That cut-over date is too far back.")
    return d


def get(tenant_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", run_id)
            .limit(1).execute().data or [])
    if not rows:
        return None
    run = rows[0]
    try:
        started = datetime.fromisoformat(str(run.get("updated_at") or run.get("created_at")).replace("Z", "+00:00"))
    except ValueError:
        started = datetime.now(timezone.utc)
    if run.get("status") == "reading" and started < datetime.now(timezone.utc) - STALE:
        run = {**run, "status": "failed", "error": "Reading stopped part-way (Vula restarted). Start it again."}
    return run


def recent(tenant_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    try:
        rows = (_client().table(TABLE).select("id,source,cutover_date,status,error,started_by,created_at,updated_at")
                .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(limit).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — no migration 247 yet
        log.debug("imports read skipped: %s", exc)
        return []
    return rows


def _set(tenant_id: str, run_id: str, patch: Dict[str, Any]) -> None:
    _client().table(TABLE).update({**patch, "updated_at": _now()}).eq("tenant_id", tenant_id).eq("id", run_id).execute()


def start_preview(tenant_id: str, cutover: date, by: str = "") -> Dict[str, Any]:
    """Record the run and read Xero in the background; returns the run (status 'reading')."""
    busy = [r for r in recent(tenant_id, 5) if r.get("status") in ("reading", "importing")]
    if busy and (get(tenant_id, busy[0]["id"]) or {}).get("status") in ("reading", "importing"):
        raise ValueError("A move is already being read — wait for it to finish.")
    run = (_client().table(TABLE).insert({"tenant_id": tenant_id, "source": "xero", "cutover_date": cutover.isoformat(),
                                          "status": "reading", "started_by": (by or "")[:80] or None})
           .execute().data or [{}])[0]
    task = asyncio.get_event_loop().create_task(run_preview(tenant_id, run["id"], cutover))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return run


async def _vula_side(tenant_id: str) -> Dict[str, List[Dict[str, Any]]]:
    from vula.commerce import service
    suppliers = await service.list_suppliers(tenant_id)
    customers = await service.list_invoice_clients(tenant_id)
    try:
        products = (_client().table("commerce_products").select("name,sku").eq("tenant_id", tenant_id)
                    .execute().data or [])
    except Exception:  # noqa: BLE001
        products = []
    return {"vula_suppliers": suppliers, "vula_customers": customers, "vula_products": products}


async def read_xero(tenant_id: str, cutover: date, client=None) -> Dict[str, Any]:
    from vula.xero.client import XeroClient
    x = client or XeroClient(tenant_id)
    got: Dict[str, Any] = {"org": await x.organisation(), "accounts": await x.accounts(),
                           "tb_report": await x.trial_balance(cutover), "contacts": await x.contacts(),
                           "items": await x.items(), "invoices": await x.invoices(),
                           "credit_notes": await x.credit_notes(), "payments": await x.payments(),
                           "bank_transactions": await x.bank_transactions(),
                           "manual_journals": await x.manual_journals()}
    got["calls"] = getattr(x, "calls", 0)
    return got


async def run_preview(tenant_id: str, run_id: str, cutover: date, client=None) -> Dict[str, Any]:
    from vula.migrate import plan
    try:
        src = await read_xero(tenant_id, cutover, client)
        calls = src.pop("calls", 0)
        preview = plan.build(cutover=cutover, **src, **(await _vula_side(tenant_id)))
        preview["xero_calls"] = calls
        _set(tenant_id, run_id, {"status": "previewed", "preview": preview, "error": None})
        return preview
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        _set(tenant_id, run_id, {"status": "failed", "error": str(exc)[:300]})
    except Exception as exc:  # noqa: BLE001 — never leave a run 'reading' forever
        log.warning("xero preview %s for %s failed: %s", run_id[:8], tenant_id, exc)
        _set(tenant_id, run_id, {"status": "failed", "error": "Something went wrong reading Xero — try again."})
    return {}

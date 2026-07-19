"""
vula/integrations/procurement.py — procurement/stock-ordering completion hook.

Nolo (DIGG's procurement owner, ClickUp member "Lehlogonolo Chuene") works ClickUp
tasks directly; this is the follow-up half. A task tagged "procurement" or "stock"
that reaches a complete-equivalent status: (1) logs a dated line item to that
project's vula_project_finances ledger if a Rand amount can be read off the task,
(2) WhatsApps the project owner. Tag-based (not a dedicated list per project) so it
works across every existing/future project folder without restructuring ClickUp.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

_DONE_STATUSES = {"complete", "closed", "done"}
_TAGS = {"procurement", "stock", "stock ordering", "stock-ordering"}
_AMOUNT_RE = re.compile(r"R\s?([\d][\d,]*(?:\.\d{1,2})?)", re.IGNORECASE)


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def _extract_amount(text: str) -> float:
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return 0.0


def _project_for_list(tenant_id: str, list_id: Optional[str]) -> Optional[str]:
    if not list_id:
        return None
    from vula.integrations.doc_filing import _clickup_candidates, _project_label
    for lid, lname in _clickup_candidates(tenant_id):
        if lid == list_id:
            return _project_label(lname)
    return None


def is_procurement_task(task: dict) -> bool:
    """A task counts if it's tagged procurement/stock, regardless of which list
    it lives in — tags travel with the task, so no per-project ClickUp setup needed."""
    tags = {t.lower() for t in (task.get("tags") or [])}
    return bool(tags & _TAGS)


async def handle_task_status_change(tenant_id: str, task: dict) -> Optional[dict]:
    """If `task` just reached a done status and carries a procurement/stock tag,
    post a dated finance line (when a cost is readable off the task) and notify
    the project owner. Returns the finance row if one was posted, else None."""
    if (task.get("status") or "").lower() not in _DONE_STATUSES:
        return None
    if not is_procurement_task(task):
        return None

    project = _project_for_list(tenant_id, task.get("list_id"))
    amount = _extract_amount(f"{task.get('title', '')} {task.get('description', '')}")

    posted = None
    if amount > 0:
        row = {
            "tenant_id": tenant_id, "project": project,
            "doc_id": f"clickup_task_{task['id']}", "filename": task.get("title") or "",
            "direction": "out", "amount": amount, "counterparty": None,
            "category": "procurement", "kind": "expense", "description": task.get("title"),
            "occurred_at": date.today().isoformat(), "source": "clickup", "reconciled": False,
        }
        try:
            res = _client().table("vula_project_finances").upsert(
                row, on_conflict="tenant_id,doc_id").execute()
            posted = res.data[0] if res.data else row
        except Exception as exc:
            logger.debug("procurement finance post skipped (run migration 027?): %s", exc)

    try:
        from vula.integrations.notify import notify_team
        proj_bit = f" on {project}" if project else ""
        cost_bit = f" — R{amount:,.0f}" if amount > 0 else ""
        msg = f"✅ Procurement done{proj_bit}: {task.get('title')}{cost_bit}"
        await notify_team(tenant_id, "procurement_done", msg)
    except Exception as exc:
        logger.debug("procurement notify skipped: %s", exc)

    return posted

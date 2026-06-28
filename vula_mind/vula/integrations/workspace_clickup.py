"""
vula/integrations/workspace_clickup.py — mirror project to-dos into ClickUp.

Best-effort: if the tenant has ClickUp connected and the project maps to a list, a Vula
to-do is created there too (and status changes mirrored). Never blocks the Vula flow.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def create_task(tenant_id: str, project: str, title: str) -> Optional[str]:
    """Create a ClickUp task in the project's list. Returns the ClickUp task id or None."""
    try:
        from vula.clickup.credentials import get_tenant_clickup_creds
        if not get_tenant_clickup_creds(tenant_id):
            return None
        from vula.integrations.doc_filing import _canonical_list_for_project
        list_id = _canonical_list_for_project(tenant_id, project)
        from vula.clickup import service as clickup
        res = await clickup.create_task(tenant_id, title=title, list_id=list_id,
                                        description=f"From Vula · project {project}")
        return res.get("id")
    except Exception as exc:
        logger.debug("workspace clickup create_task failed: %s", exc)
        return None


async def set_status(tenant_id: str, clickup_task_id: str, done: bool) -> None:
    try:
        from vula.clickup import service as clickup
        await clickup.update_task(tenant_id, clickup_task_id,
                                  status="complete" if done else "to do")
    except Exception as exc:
        logger.debug("workspace clickup set_status failed: %s", exc)

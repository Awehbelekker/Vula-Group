"""
vula/api/field_ops.py

Field operations REST API — outbound WhatsApp task management.

Endpoints:
  POST /v1/field/contractors                — register/update a contractor
  GET  /v1/field/contractors/{tenant_id}    — list tenant contractors
  POST /v1/field/project/assign             — assign contractor to project
  GET  /v1/field/project/{project_id}/team  — list project team
  GET  /v1/field/project/{project_id}/status — task summary

  POST /v1/field/task                       — create a task
  POST /v1/field/task/assign                — assign task + WhatsApp briefing
  POST /v1/field/task/{task_id}/complete-request — ask contractor to confirm done
  GET  /v1/field/task/{task_id}             — get task details + evidence

  POST /v1/field/walkthrough/start          — trigger walkthrough checklist via WA
  POST /v1/field/walkthrough/{id}/approve   — architect sign-off via dashboard

  GET  /v1/field/daily-tasks/{tenant_id}    — tasks due today (for n8n cron)
  POST /v1/field/daily-tasks/{tenant_id}/dispatch — send morning WhatsApp briefings
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from vula.api.whatsapp import _send_reply, _send_wa_template
from vula.models.field_ops import get_field_ops_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["field-ops"])


# ─── Request / Response Models ────────────────────────────────────────────────

class ContractorIn(BaseModel):
    tenant_id: str
    name: str
    phone: str
    trade: str


class ProjectAssignIn(BaseModel):
    tenant_id: str
    project_id: str
    contractor_id: str
    role: str = "contractor"


class TaskIn(BaseModel):
    tenant_id: str
    project_id: str
    title: str
    trade: str
    assigned_to: str = ""
    due_date: str = ""
    notes: str = ""


class TaskAssignIn(BaseModel):
    task_id: str
    contractor_id: str
    send_whatsapp: bool = True


class CompleteRequestIn(BaseModel):
    task_id: str
    custom_message: str = ""


class WalkthroughStartIn(BaseModel):
    tenant_id: str
    project_id: str
    title: str
    items: List[str]
    contractor_id: str  # the person doing the walkthrough


class WalkthroughApproveIn(BaseModel):
    approver_phone: str
    decision: str       # "approved" | "rejected"
    notes: str = ""


# ─── Contractors ──────────────────────────────────────────────────────────────

@router.post("/contractors")
async def register_contractor(body: ContractorIn) -> dict:
    """Register or update a contractor. Returns contractor record."""
    db = get_field_ops_db()
    c = db.upsert_contractor(body.tenant_id, body.name, body.phone, body.trade)
    return {"id": c.id, "name": c.name, "phone": c.phone, "trade": c.trade, "tenant_id": c.tenant_id}


@router.get("/contractors/{tenant_id}")
async def list_contractors(tenant_id: str) -> dict:
    db = get_field_ops_db()
    contractors = db.list_contractors(tenant_id)
    return {
        "tenant_id": tenant_id,
        "contractors": [
            {"id": c.id, "name": c.name, "phone": c.phone, "trade": c.trade}
            for c in contractors
        ],
    }


# ─── Project assignment ───────────────────────────────────────────────────────

@router.post("/project/assign")
async def assign_to_project(body: ProjectAssignIn) -> dict:
    """Assign a contractor to a project with a given role."""
    db = get_field_ops_db()
    pa = db.assign_to_project(body.tenant_id, body.project_id, body.contractor_id, body.role)
    return {"id": pa.id, "project_id": pa.project_id, "contractor_id": pa.contractor_id, "role": pa.role}


@router.get("/project/{project_id}/team")
async def get_project_team(project_id: str) -> dict:
    db = get_field_ops_db()
    team = db.get_project_team(project_id)
    return {"project_id": project_id, "team": team}


@router.get("/project/{project_id}/status")
async def get_project_status(project_id: str) -> dict:
    db = get_field_ops_db()
    summary = db.project_status_summary(project_id)
    tasks = db.get_tasks_for_project(project_id)
    return {
        **summary,
        "tasks": [
            {
                "id": t.id, "title": t.title, "trade": t.trade,
                "status": t.status, "assigned_to": t.assigned_to,
                "due_date": t.due_date,
                "evidence_count": db.count_evidence(t.id),
            }
            for t in tasks
        ],
    }


# ─── Tasks ────────────────────────────────────────────────────────────────────

@router.post("/task")
async def create_task(body: TaskIn) -> dict:
    """Create a task (without assigning yet)."""
    db = get_field_ops_db()
    task = db.create_task(
        body.tenant_id, body.project_id, body.title, body.trade,
        body.assigned_to, body.due_date, body.notes,
    )
    # Mirror into ClickUp if the tenant has it connected (best-effort).
    try:
        from vula.integrations.clickup_sync import mirror_task_create
        await mirror_task_create(body.tenant_id, task.id, task.title,
                                 description=task.notes, due_date=task.due_date)
    except Exception:
        pass
    return {"id": task.id, "title": task.title, "status": task.status, "project_id": task.project_id}


@router.post("/task/assign")
async def assign_task(body: TaskAssignIn) -> dict:
    """Assign a task to a contractor and optionally send a WhatsApp briefing."""
    db = get_field_ops_db()
    task = db.get_task(body.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    contractor = db.get_contractor(body.contractor_id)
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    db.update_task_status(task.id, "in_progress")
    db.set_task_assignee(task.id, body.contractor_id)

    wa_sent = False
    if body.send_whatsapp:
        # Task assignment is a proactive/business-initiated send — must use an approved template
        # (free text fails with 'Re-engagement message' unless the contractor messaged in the last
        # 24h). Due date/notes go in the log; the contractor opens Vula or asks once they reply.
        logger.info("Task assigned: %s -> %s (project %s, due=%s, notes=%s)",
                    task.title, contractor.name, task.project_id, task.due_date, task.notes)
        wa_sent = await _send_wa_template(contractor.tenant_id, contractor.phone, "digg_task_assigned",
                                          contractor.name, task.project_id, task.title)

    return {
        "task_id": task.id,
        "contractor_id": contractor.id,
        "contractor_name": contractor.name,
        "whatsapp_sent": wa_sent,
    }


@router.post("/task/{task_id}/complete-request")
async def request_completion(task_id: str, body: CompleteRequestIn) -> dict:
    """Ask the assigned contractor to confirm their task is complete."""
    db = get_field_ops_db()
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not task.assigned_to:
        raise HTTPException(status_code=400, detail="Task is not assigned to anyone")

    contractor = db.get_contractor(task.assigned_to)
    if not contractor:
        raise HTTPException(status_code=404, detail="Assigned contractor not found")

    if body.custom_message:
        # A custom message can't ride the fixed-text template — best-effort free text (works if
        # the contractor has messaged Vula in the last 24h, same as any normal reply).
        sent = await _send_reply(contractor.phone, body.custom_message, contractor.tenant_id)
    else:
        sent = await _send_wa_template(contractor.tenant_id, contractor.phone,
                                       "digg_task_chase", contractor.name, task.title)
    return {"task_id": task_id, "contractor_name": contractor.name, "whatsapp_sent": sent}


@router.get("/task/{task_id}")
async def get_task(task_id: str) -> dict:
    db = get_field_ops_db()
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    evidence = db.get_evidence(task_id)
    sign_off = db.get_sign_off(task_id)

    return {
        "id": task.id,
        "title": task.title,
        "trade": task.trade,
        "status": task.status,
        "project_id": task.project_id,
        "assigned_to": task.assigned_to,
        "due_date": task.due_date,
        "notes": task.notes,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "evidence": [
            {"id": e.id, "photo_url": e.photo_url, "caption": e.caption, "submitted_at": e.submitted_at}
            for e in evidence
        ],
        "sign_off": {
            "status": sign_off.status,
            "approver": sign_off.approver_phone,
            "notes": sign_off.notes,
            "approved_at": sign_off.approved_at,
        } if sign_off else None,
    }


# ─── Walkthrough ──────────────────────────────────────────────────────────────

@router.post("/walkthrough/start")
async def start_walkthrough(body: WalkthroughStartIn) -> dict:
    """Create a walkthrough checklist and send it to the contractor via WhatsApp."""
    db = get_field_ops_db()

    contractor = db.get_contractor(body.contractor_id)
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    wt = db.create_walkthrough(body.tenant_id, body.project_id, body.title, body.items)
    db.update_walkthrough_status(wt.id, "in_progress")

    # Checklist is unbounded length (can't fit a template) — filed in the log; the approved
    # template just kicks off the walkthrough, contractor opens Vula for the item list.
    numbered = "\n".join(f"{i+1}. {item}" for i, item in enumerate(body.items))
    logger.info("Walkthrough started: %s (%s) — checklist:\n%s", body.title, contractor.name, numbered)
    sent = await _send_wa_template(contractor.tenant_id, contractor.phone,
                                   "digg_walkthrough_start", contractor.name, body.title)

    return {
        "walkthrough_id": wt.id,
        "project_id": body.project_id,
        "items": body.items,
        "contractor_name": contractor.name,
        "whatsapp_sent": sent,
    }


@router.post("/walkthrough/{walkthrough_id}/approve")
async def approve_walkthrough(walkthrough_id: str, body: WalkthroughApproveIn) -> dict:
    """Architect approves or rejects a walkthrough from the dashboard."""
    db = get_field_ops_db()
    wt = db.get_walkthrough(walkthrough_id)
    if not wt:
        raise HTTPException(status_code=404, detail="Walkthrough not found")

    new_status = "complete" if body.decision == "approved" else "pending"
    db.update_walkthrough_status(walkthrough_id, new_status)

    # Notify team
    team = db.get_project_team(wt.project_id)
    contractors = [m for m in team if m["role"] == "contractor"]
    for c in contractors:
        if body.decision == "approved":
            await _send_wa_template(wt.tenant_id, c["phone"], "digg_walkthrough_approved", wt.title)
        else:
            reason = body.notes or "no reason given"
            await _send_wa_template(wt.tenant_id, c["phone"], "digg_walkthrough_rejected", wt.title, reason)

    return {
        "walkthrough_id": walkthrough_id,
        "decision": body.decision,
        "notes": body.notes,
        "status": new_status,
    }


# ─── Daily task dispatch (called by n8n cron at 06:30) ────────────────────────

@router.get("/daily-tasks/{tenant_id}")
async def get_daily_tasks(tenant_id: str) -> dict:
    """Return all tasks due today for a tenant. Used by n8n to build briefings."""
    db = get_field_ops_db()
    tasks = db.get_tasks_due_today(tenant_id)
    result = []
    for task in tasks:
        contractor = db.get_contractor(task.assigned_to) if task.assigned_to else None
        result.append({
            "task_id": task.id,
            "title": task.title,
            "trade": task.trade,
            "project_id": task.project_id,
            "due_date": task.due_date,
            "contractor_id": task.assigned_to,
            "contractor_name": contractor.name if contractor else "",
            "contractor_phone": contractor.phone if contractor else "",
        })
    return {"tenant_id": tenant_id, "date": __import__("datetime").datetime.utcnow().date().isoformat(), "tasks": result, "count": len(result)}


@router.post("/daily-tasks/{tenant_id}/dispatch")
async def dispatch_daily_briefings(tenant_id: str) -> dict:
    """Send morning WhatsApp briefings to all contractors with tasks due today."""
    db = get_field_ops_db()
    tasks = db.get_tasks_due_today(tenant_id)

    # Group tasks by contractor
    by_contractor: dict[str, list] = {}
    for task in tasks:
        if task.assigned_to:
            by_contractor.setdefault(task.assigned_to, []).append(task)

    sent_count = 0
    results = []
    for contractor_id, contractor_tasks in by_contractor.items():
        contractor = db.get_contractor(contractor_id)
        if not contractor or not contractor.phone:
            continue

        # Itemized task list is unbounded (can't fit a template) — filed in the log; the approved
        # template carries a count only, contractor opens Vula for the full list.
        task_lines = "\n".join(
            f"  • {t.title} (project {t.project_id})"
            for t in contractor_tasks
        )
        logger.info("Daily briefing for %s: %d task(s)\n%s", contractor.name, len(contractor_tasks), task_lines)
        ok = await _send_wa_template(contractor.tenant_id, contractor.phone, "digg_daily_tasks",
                                     contractor.name, str(len(contractor_tasks)))
        if ok:
            sent_count += 1
        results.append({
            "contractor": contractor.name,
            "phone": contractor.phone,
            "tasks": len(contractor_tasks),
            "sent": ok,
        })

    return {
        "tenant_id": tenant_id,
        "contractors_briefed": sent_count,
        "results": results,
    }

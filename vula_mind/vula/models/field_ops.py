"""
vula/models/field_ops.py

Field operations data layer — contractors, task assignments, evidence photos,
and sign-off records. Backed by Supabase (durable + reportable alongside the
rest of Vula). Public methods and dataclasses are unchanged so callers
(whatsapp.py, api/field_ops.py) work as before.
"""
from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from config import settings

logger = logging.getLogger(__name__)


# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class Contractor:
    id: str
    tenant_id: str
    name: str
    phone: str          # E.164 without leading +, e.g. 27821234567
    trade: str          # plumber, electrician, bricklayer, etc.
    active: bool = True
    created_at: str = ""


@dataclass
class ProjectAssignment:
    id: str
    tenant_id: str
    project_id: str
    contractor_id: str
    role: str           # contractor | site_manager | architect | quantity_surveyor
    assigned_at: str = ""


@dataclass
class Task:
    id: str
    tenant_id: str
    project_id: str
    title: str
    trade: str
    status: str         # pending | in_progress | awaiting_sign_off | complete | rejected
    assigned_to: str    # contractor_id
    due_date: str = ""
    created_at: str = ""
    updated_at: str = ""
    notes: str = ""


@dataclass
class Evidence:
    id: str
    task_id: str
    contractor_id: str
    photo_url: str      # Supabase Storage URL (or local path fallback)
    caption: str = ""
    submitted_at: str = ""


@dataclass
class SignOff:
    id: str
    task_id: str
    approver_phone: str
    status: str         # approved | rejected
    notes: str = ""
    approved_at: str = ""


@dataclass
class WalkthroughChecklist:
    id: str
    tenant_id: str
    project_id: str
    title: str
    items: List[str] = field(default_factory=list)
    status: str = "pending"
    created_at: str = ""


# ─── Supabase client ─────────────────────────────────────────────────────────

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from supabase import create_client
        _CLIENT = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key or settings.supabase_service_key,
        )
    return _CLIENT


# Table names
_T_CONTRACTORS = "vula_field_contractors"
_T_ASSIGN = "vula_field_assignments"
_T_TASKS = "vula_field_tasks"
_T_EVIDENCE = "vula_field_evidence"
_T_SIGNOFFS = "vula_field_sign_offs"
_T_WALK = "vula_field_walkthroughs"


# ─── Row → dataclass mappers ─────────────────────────────────────────────────

def _to_contractor(r: dict) -> Contractor:
    return Contractor(id=r["id"], tenant_id=r["tenant_id"], name=r["name"],
                      phone=r["phone"], trade=r.get("trade", "") or "",
                      active=bool(r.get("active", True)), created_at=r.get("created_at", "") or "")


def _to_task(r: dict) -> Task:
    return Task(id=r["id"], tenant_id=r["tenant_id"], project_id=r["project_id"],
                title=r["title"], trade=r.get("trade", "") or "", status=r.get("status", "pending"),
                assigned_to=r.get("assigned_to", "") or "", due_date=r.get("due_date", "") or "",
                notes=r.get("notes", "") or "", created_at=r.get("created_at", "") or "",
                updated_at=r.get("updated_at", "") or "")


def _to_evidence(r: dict) -> Evidence:
    return Evidence(id=r["id"], task_id=r["task_id"], contractor_id=r["contractor_id"],
                    photo_url=r["photo_url"], caption=r.get("caption", "") or "",
                    submitted_at=r.get("submitted_at", "") or "")


def _to_signoff(r: dict) -> SignOff:
    return SignOff(id=r["id"], task_id=r["task_id"], approver_phone=r["approver_phone"],
                   status=r["status"], notes=r.get("notes", "") or "",
                   approved_at=r.get("approved_at", "") or "")


# ─── Database ────────────────────────────────────────────────────────────────

class FieldOpsDB:
    """Supabase-backed field operations store."""

    def __init__(self):
        # Local scratch dir for downloading media before uploading to Storage.
        self._evidence_dir = settings.data_dir / "evidence"
        try:
            self._evidence_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # ── Contractors ───────────────────────────────────────────────────────────

    def upsert_contractor(self, tenant_id: str, name: str, phone: str, trade: str) -> Contractor:
        normalised = _normalise_phone(phone)
        existing = self.get_contractor_by_phone(normalised)
        if existing:
            _client().table(_T_CONTRACTORS).update(
                {"name": name, "trade": trade, "active": True}
            ).eq("id", existing.id).execute()
            existing.name = name
            existing.trade = trade
            return existing

        c = Contractor(id=_new_id(), tenant_id=tenant_id, name=name,
                       phone=normalised, trade=trade, created_at=_now())
        _client().table(_T_CONTRACTORS).insert({
            "id": c.id, "tenant_id": c.tenant_id, "name": c.name, "phone": c.phone,
            "trade": c.trade, "active": True, "created_at": c.created_at,
        }).execute()
        return c

    def get_contractor_by_phone(self, phone: str) -> Optional[Contractor]:
        normalised = _normalise_phone(phone)
        res = _client().table(_T_CONTRACTORS).select("*").eq("phone", normalised).limit(1).execute()
        rows = res.data or []
        return _to_contractor(rows[0]) if rows else None

    def get_contractor(self, contractor_id: str) -> Optional[Contractor]:
        res = _client().table(_T_CONTRACTORS).select("*").eq("id", contractor_id).limit(1).execute()
        rows = res.data or []
        return _to_contractor(rows[0]) if rows else None

    def list_contractors(self, tenant_id: str) -> List[Contractor]:
        res = (_client().table(_T_CONTRACTORS).select("*")
               .eq("tenant_id", tenant_id).eq("active", True).order("name").execute())
        return [_to_contractor(r) for r in (res.data or [])]

    # ── Project Assignments ───────────────────────────────────────────────────

    def assign_to_project(self, tenant_id: str, project_id: str, contractor_id: str,
                          role: str = "contractor") -> ProjectAssignment:
        existing = (_client().table(_T_ASSIGN).select("id")
                    .eq("project_id", project_id).eq("contractor_id", contractor_id)
                    .eq("role", role).limit(1).execute())
        rows = existing.data or []
        if rows:
            return ProjectAssignment(id=rows[0]["id"], tenant_id=tenant_id, project_id=project_id,
                                     contractor_id=contractor_id, role=role)
        pa = ProjectAssignment(id=_new_id(), tenant_id=tenant_id, project_id=project_id,
                               contractor_id=contractor_id, role=role, assigned_at=_now())
        _client().table(_T_ASSIGN).insert({
            "id": pa.id, "tenant_id": pa.tenant_id, "project_id": pa.project_id,
            "contractor_id": pa.contractor_id, "role": pa.role, "assigned_at": pa.assigned_at,
        }).execute()
        return pa

    def get_project_team(self, project_id: str) -> List[dict]:
        assigns = (_client().table(_T_ASSIGN).select("contractor_id,role,assigned_at")
                   .eq("project_id", project_id).execute()).data or []
        if not assigns:
            return []
        ids = list({a["contractor_id"] for a in assigns})
        crows = (_client().table(_T_CONTRACTORS).select("id,name,phone,trade")
                 .in_("id", ids).execute()).data or []
        cmap = {c["id"]: c for c in crows}
        team = []
        for a in assigns:
            c = cmap.get(a["contractor_id"])
            if not c:
                continue
            team.append({"id": c["id"], "name": c["name"], "phone": c["phone"],
                         "trade": c.get("trade", ""), "role": a["role"],
                         "assigned_at": a.get("assigned_at", "")})
        team.sort(key=lambda x: (x["role"], x["name"]))
        return team

    def get_projects_for_contractor(self, contractor_id: str) -> List[str]:
        res = (_client().table(_T_ASSIGN).select("project_id")
               .eq("contractor_id", contractor_id).execute())
        return list({r["project_id"] for r in (res.data or [])})

    # ── Tasks ─────────────────────────────────────────────────────────────────

    def create_task(self, tenant_id: str, project_id: str, title: str, trade: str,
                    assigned_to: str = "", due_date: str = "", notes: str = "") -> Task:
        t = Task(id=_new_id(), tenant_id=tenant_id, project_id=project_id, title=title,
                 trade=trade, status="pending", assigned_to=assigned_to, due_date=due_date,
                 notes=notes, created_at=_now(), updated_at=_now())
        _client().table(_T_TASKS).insert({
            "id": t.id, "tenant_id": t.tenant_id, "project_id": t.project_id, "title": t.title,
            "trade": t.trade, "status": t.status, "assigned_to": t.assigned_to,
            "due_date": t.due_date, "notes": t.notes, "created_at": t.created_at,
            "updated_at": t.updated_at,
        }).execute()
        return t

    def set_task_assignee(self, task_id: str, contractor_id: str) -> bool:
        res = _client().table(_T_TASKS).update(
            {"assigned_to": contractor_id, "updated_at": _now()}
        ).eq("id", task_id).execute()
        return bool(res.data)

    def update_task_status(self, task_id: str, status: str, notes: str = "") -> bool:
        payload = {"status": status, "updated_at": _now()}
        if notes:
            payload["notes"] = notes
        res = _client().table(_T_TASKS).update(payload).eq("id", task_id).execute()
        return bool(res.data)

    def get_task(self, task_id: str) -> Optional[Task]:
        res = _client().table(_T_TASKS).select("*").eq("id", task_id).limit(1).execute()
        rows = res.data or []
        return _to_task(rows[0]) if rows else None

    def get_tasks_for_contractor(self, contractor_id: str, status: Optional[str] = None) -> List[Task]:
        q = _client().table(_T_TASKS).select("*").eq("assigned_to", contractor_id)
        if status:
            q = q.eq("status", status)
        res = q.order("due_date").order("created_at").execute()
        return [_to_task(r) for r in (res.data or [])]

    def get_tasks_for_project(self, project_id: str) -> List[Task]:
        res = (_client().table(_T_TASKS).select("*").eq("project_id", project_id)
               .order("due_date").order("trade").order("created_at").execute())
        return [_to_task(r) for r in (res.data or [])]

    def get_tasks_due_today(self, tenant_id: str) -> List[Task]:
        today = datetime.utcnow().date().isoformat()
        res = (_client().table(_T_TASKS).select("*").eq("tenant_id", tenant_id)
               .eq("due_date", today).not_.in_("status", ["complete", "rejected"])
               .order("trade").execute())
        return [_to_task(r) for r in (res.data or [])]

    # ── Evidence (photos) ─────────────────────────────────────────────────────

    def save_evidence(self, task_id: str, contractor_id: str, photo_url: str, caption: str = "") -> Evidence:
        e = Evidence(id=_new_id(), task_id=task_id, contractor_id=contractor_id,
                     photo_url=photo_url, caption=caption, submitted_at=_now())
        _client().table(_T_EVIDENCE).insert({
            "id": e.id, "task_id": e.task_id, "contractor_id": e.contractor_id,
            "photo_url": e.photo_url, "caption": e.caption, "submitted_at": e.submitted_at,
        }).execute()
        return e

    def get_evidence(self, task_id: str) -> List[Evidence]:
        res = (_client().table(_T_EVIDENCE).select("*").eq("task_id", task_id)
               .order("submitted_at").execute())
        return [_to_evidence(r) for r in (res.data or [])]

    def count_evidence(self, task_id: str) -> int:
        res = _client().table(_T_EVIDENCE).select("id", count="exact").eq("task_id", task_id).execute()
        return res.count or len(res.data or [])

    # ── Sign-offs ─────────────────────────────────────────────────────────────

    def record_sign_off(self, task_id: str, approver_phone: str, status: str, notes: str = "") -> SignOff:
        s = SignOff(id=_new_id(), task_id=task_id, approver_phone=_normalise_phone(approver_phone),
                    status=status, notes=notes, approved_at=_now())
        _client().table(_T_SIGNOFFS).insert({
            "id": s.id, "task_id": s.task_id, "approver_phone": s.approver_phone,
            "status": s.status, "notes": s.notes, "approved_at": s.approved_at,
        }).execute()
        self.update_task_status(task_id, "complete" if status == "approved" else "rejected", notes)
        return s

    def get_sign_off(self, task_id: str) -> Optional[SignOff]:
        res = (_client().table(_T_SIGNOFFS).select("*").eq("task_id", task_id)
               .order("approved_at", desc=True).limit(1).execute())
        rows = res.data or []
        return _to_signoff(rows[0]) if rows else None

    # ── Walkthrough Checklists ─────────────────────────────────────────────────

    def create_walkthrough(self, tenant_id: str, project_id: str, title: str,
                           items: List[str]) -> WalkthroughChecklist:
        wt = WalkthroughChecklist(id=_new_id(), tenant_id=tenant_id, project_id=project_id,
                                  title=title, items=items, status="pending", created_at=_now())
        _client().table(_T_WALK).insert({
            "id": wt.id, "tenant_id": wt.tenant_id, "project_id": wt.project_id,
            "title": wt.title, "items": items, "status": wt.status, "created_at": wt.created_at,
        }).execute()
        return wt

    def get_walkthrough(self, walkthrough_id: str) -> Optional[WalkthroughChecklist]:
        res = _client().table(_T_WALK).select("*").eq("id", walkthrough_id).limit(1).execute()
        rows = res.data or []
        if not rows:
            return None
        r = rows[0]
        items = r.get("items") or []
        return WalkthroughChecklist(id=r["id"], tenant_id=r["tenant_id"], project_id=r["project_id"],
                                    title=r["title"], items=items, status=r.get("status", "pending"),
                                    created_at=r.get("created_at", ""))

    def update_walkthrough_status(self, walkthrough_id: str, status: str) -> bool:
        res = _client().table(_T_WALK).update({"status": status}).eq("id", walkthrough_id).execute()
        return bool(res.data)

    # ── Manager queries / reporting ────────────────────────────────────────────

    def get_manager_roles_for_phone(self, phone: str) -> list[dict]:
        c = self.get_contractor_by_phone(phone)
        if not c:
            return []
        res = (_client().table(_T_ASSIGN).select("project_id,role")
               .eq("contractor_id", c.id)
               .in_("role", ["architect", "site_manager", "quantity_surveyor"]).execute())
        return [{"project_id": r["project_id"], "role": r["role"],
                 "contractor_id": c.id, "name": c.name} for r in (res.data or [])]

    def get_tasks_awaiting_signoff_for_manager(self, phone: str) -> list[dict]:
        roles = self.get_manager_roles_for_phone(phone)
        if not roles:
            return []
        projects = list({r["project_id"] for r in roles})
        tasks = (_client().table(_T_TASKS).select("*")
                 .in_("project_id", projects).eq("status", "awaiting_sign_off")
                 .order("updated_at", desc=True).limit(1).execute()).data or []
        if not tasks:
            return []
        out = []
        for t in tasks:
            cphone, cname = "", "Unknown"
            if t.get("assigned_to"):
                c = self.get_contractor(t["assigned_to"])
                if c:
                    cphone, cname = c.phone, c.name
            out.append({"task_id": t["id"], "title": t["title"], "project_id": t["project_id"],
                        "contractor_phone": cphone, "contractor_name": cname})
        return out

    def project_status_summary(self, project_id: str) -> dict:
        tasks = (_client().table(_T_TASKS).select("status").eq("project_id", project_id).execute()).data or []
        team = (_client().table(_T_ASSIGN).select("id", count="exact")
                .eq("project_id", project_id).execute())
        team_count = team.count if team.count is not None else len(team.data or [])
        status_counts: dict = {}
        for t in tasks:
            status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
        total = sum(status_counts.values())
        done = status_counts.get("complete", 0)
        return {
            "project_id": project_id,
            "team_size": team_count,
            "tasks_total": total,
            "tasks_complete": done,
            "tasks_pending": status_counts.get("pending", 0),
            "tasks_in_progress": status_counts.get("in_progress", 0),
            "tasks_awaiting_sign_off": status_counts.get("awaiting_sign_off", 0),
            "tasks_rejected": status_counts.get("rejected", 0),
            "completion_pct": round(100 * done / total) if total else 0,
        }

    @property
    def evidence_dir(self) -> Path:
        return self._evidence_dir


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.utcnow().isoformat()


def _normalise_phone(phone: str) -> str:
    """Normalise to digits only, E.164-style without leading +."""
    digits = (phone or "").lstrip("+").replace(" ", "").replace("-", "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "27" + digits[1:]
    return digits


# ─── Singleton ───────────────────────────────────────────────────────────────

_DB: Optional[FieldOpsDB] = None


def get_field_ops_db() -> FieldOpsDB:
    global _DB
    if _DB is None:
        _DB = FieldOpsDB()
    return _DB

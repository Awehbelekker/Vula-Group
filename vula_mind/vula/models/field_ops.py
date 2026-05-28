"""
vula/models/field_ops.py

Field operations data layer — contractors, task assignments, evidence photos,
and sign-off records.  All stored in SQLite (same pattern as chat/history.py).
"""
from __future__ import annotations

import sqlite3
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from config import settings

logger = logging.getLogger(__name__)

DB_PATH = settings.data_dir / "field_ops.db"


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
    photo_url: str      # path in ~/.vula/evidence/ or remote URL
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
    items: List[str] = field(default_factory=list)   # checklist line items
    status: str = "pending"   # pending | in_progress | complete
    created_at: str = ""


# ─── Database ────────────────────────────────────────────────────────────────

class FieldOpsDB:
    """SQLite-backed store for all field operations state."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._evidence_dir = settings.data_dir / "evidence"
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS contractors (
                    id          TEXT PRIMARY KEY,
                    tenant_id   TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    phone       TEXT NOT NULL,
                    trade       TEXT NOT NULL,
                    active      INTEGER NOT NULL DEFAULT 1,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_contractors_tenant ON contractors(tenant_id);
                CREATE INDEX IF NOT EXISTS idx_contractors_phone  ON contractors(phone);

                CREATE TABLE IF NOT EXISTS project_assignments (
                    id              TEXT PRIMARY KEY,
                    tenant_id       TEXT NOT NULL,
                    project_id      TEXT NOT NULL,
                    contractor_id   TEXT NOT NULL,
                    role            TEXT NOT NULL DEFAULT 'contractor',
                    assigned_at     TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_pa_project ON project_assignments(project_id);
                CREATE INDEX IF NOT EXISTS idx_pa_contractor ON project_assignments(contractor_id);

                CREATE TABLE IF NOT EXISTS tasks (
                    id              TEXT PRIMARY KEY,
                    tenant_id       TEXT NOT NULL,
                    project_id      TEXT NOT NULL,
                    title           TEXT NOT NULL,
                    trade           TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    assigned_to     TEXT NOT NULL DEFAULT '',
                    due_date        TEXT NOT NULL DEFAULT '',
                    notes           TEXT NOT NULL DEFAULT '',
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
                CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);

                CREATE TABLE IF NOT EXISTS evidence (
                    id              TEXT PRIMARY KEY,
                    task_id         TEXT NOT NULL,
                    contractor_id   TEXT NOT NULL,
                    photo_url       TEXT NOT NULL,
                    caption         TEXT NOT NULL DEFAULT '',
                    submitted_at    TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_task ON evidence(task_id);

                CREATE TABLE IF NOT EXISTS sign_offs (
                    id              TEXT PRIMARY KEY,
                    task_id         TEXT NOT NULL,
                    approver_phone  TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    notes           TEXT NOT NULL DEFAULT '',
                    approved_at     TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_signoffs_task ON sign_offs(task_id);

                CREATE TABLE IF NOT EXISTS walkthrough_checklists (
                    id          TEXT PRIMARY KEY,
                    tenant_id   TEXT NOT NULL,
                    project_id  TEXT NOT NULL,
                    title       TEXT NOT NULL,
                    items_json  TEXT NOT NULL DEFAULT '[]',
                    status      TEXT NOT NULL DEFAULT 'pending',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_wt_project ON walkthrough_checklists(project_id);
            """)
            conn.commit()

    # ── Contractors ───────────────────────────────────────────────────────────

    def upsert_contractor(self, tenant_id: str, name: str, phone: str, trade: str) -> Contractor:
        normalised = _normalise_phone(phone)
        existing = self.get_contractor_by_phone(normalised)
        if existing:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE contractors SET name=?, trade=?, active=1 WHERE id=?",
                    (name, trade, existing.id),
                )
                conn.commit()
            existing.name = name
            existing.trade = trade
            return existing

        c = Contractor(
            id=_new_id(),
            tenant_id=tenant_id,
            name=name,
            phone=normalised,
            trade=trade,
            created_at=_now(),
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO contractors (id, tenant_id, name, phone, trade, active, created_at) VALUES (?,?,?,?,?,?,?)",
                (c.id, c.tenant_id, c.name, c.phone, c.trade, 1, c.created_at),
            )
            conn.commit()
        return c

    def get_contractor_by_phone(self, phone: str) -> Optional[Contractor]:
        normalised = _normalise_phone(phone)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id,tenant_id,name,phone,trade,active,created_at FROM contractors WHERE phone=? LIMIT 1",
                (normalised,),
            ).fetchone()
        if row:
            return Contractor(id=row[0], tenant_id=row[1], name=row[2], phone=row[3],
                              trade=row[4], active=bool(row[5]), created_at=row[6])
        return None

    def get_contractor(self, contractor_id: str) -> Optional[Contractor]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id,tenant_id,name,phone,trade,active,created_at FROM contractors WHERE id=? LIMIT 1",
                (contractor_id,),
            ).fetchone()
        if row:
            return Contractor(id=row[0], tenant_id=row[1], name=row[2], phone=row[3],
                              trade=row[4], active=bool(row[5]), created_at=row[6])
        return None

    def list_contractors(self, tenant_id: str) -> List[Contractor]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id,tenant_id,name,phone,trade,active,created_at FROM contractors WHERE tenant_id=? AND active=1 ORDER BY name",
                (tenant_id,),
            ).fetchall()
        return [Contractor(id=r[0], tenant_id=r[1], name=r[2], phone=r[3],
                           trade=r[4], active=bool(r[5]), created_at=r[6]) for r in rows]

    # ── Project Assignments ───────────────────────────────────────────────────

    def assign_to_project(self, tenant_id: str, project_id: str, contractor_id: str, role: str = "contractor") -> ProjectAssignment:
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM project_assignments WHERE project_id=? AND contractor_id=? LIMIT 1",
                (project_id, contractor_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE project_assignments SET role=? WHERE id=?",
                    (role, existing[0]),
                )
                conn.commit()
                return ProjectAssignment(id=existing[0], tenant_id=tenant_id,
                                         project_id=project_id, contractor_id=contractor_id,
                                         role=role)
            pa = ProjectAssignment(
                id=_new_id(), tenant_id=tenant_id, project_id=project_id,
                contractor_id=contractor_id, role=role, assigned_at=_now(),
            )
            conn.execute(
                "INSERT INTO project_assignments (id,tenant_id,project_id,contractor_id,role,assigned_at) VALUES (?,?,?,?,?,?)",
                (pa.id, pa.tenant_id, pa.project_id, pa.contractor_id, pa.role, pa.assigned_at),
            )
            conn.commit()
        return pa

    def get_project_team(self, project_id: str) -> List[dict]:
        """Return contractors + their roles for a project."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT c.id, c.name, c.phone, c.trade, pa.role, pa.assigned_at
                   FROM project_assignments pa
                   JOIN contractors c ON c.id = pa.contractor_id
                   WHERE pa.project_id = ?
                   ORDER BY pa.role, c.name""",
                (project_id,),
            ).fetchall()
        return [{"id": r[0], "name": r[1], "phone": r[2], "trade": r[3],
                 "role": r[4], "assigned_at": r[5]} for r in rows]

    def get_projects_for_contractor(self, contractor_id: str) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT project_id FROM project_assignments WHERE contractor_id=?",
                (contractor_id,),
            ).fetchall()
        return [r[0] for r in rows]

    # ── Tasks ─────────────────────────────────────────────────────────────────

    def create_task(self, tenant_id: str, project_id: str, title: str, trade: str,
                    assigned_to: str = "", due_date: str = "", notes: str = "") -> Task:
        t = Task(
            id=_new_id(), tenant_id=tenant_id, project_id=project_id,
            title=title, trade=trade, status="pending",
            assigned_to=assigned_to, due_date=due_date,
            notes=notes, created_at=_now(), updated_at=_now(),
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO tasks (id,tenant_id,project_id,title,trade,status,assigned_to,due_date,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (t.id, t.tenant_id, t.project_id, t.title, t.trade, t.status,
                 t.assigned_to, t.due_date, t.notes, t.created_at, t.updated_at),
            )
            conn.commit()
        return t

    def update_task_status(self, task_id: str, status: str, notes: str = "") -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE tasks SET status=?, notes=CASE WHEN ? != '' THEN ? ELSE notes END, updated_at=? WHERE id=?",
                (status, notes, notes, _now(), task_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def get_task(self, task_id: str) -> Optional[Task]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id,tenant_id,project_id,title,trade,status,assigned_to,due_date,notes,created_at,updated_at FROM tasks WHERE id=? LIMIT 1",
                (task_id,),
            ).fetchone()
        if row:
            return Task(id=row[0], tenant_id=row[1], project_id=row[2], title=row[3],
                        trade=row[4], status=row[5], assigned_to=row[6], due_date=row[7],
                        notes=row[8], created_at=row[9], updated_at=row[10])
        return None

    def get_tasks_for_contractor(self, contractor_id: str, status: Optional[str] = None) -> List[Task]:
        with sqlite3.connect(self.db_path) as conn:
            if status:
                rows = conn.execute(
                    "SELECT id,tenant_id,project_id,title,trade,status,assigned_to,due_date,notes,created_at,updated_at FROM tasks WHERE assigned_to=? AND status=? ORDER BY due_date, created_at",
                    (contractor_id, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,tenant_id,project_id,title,trade,status,assigned_to,due_date,notes,created_at,updated_at FROM tasks WHERE assigned_to=? ORDER BY due_date, created_at",
                    (contractor_id,),
                ).fetchall()
        return [Task(id=r[0], tenant_id=r[1], project_id=r[2], title=r[3], trade=r[4],
                     status=r[5], assigned_to=r[6], due_date=r[7], notes=r[8],
                     created_at=r[9], updated_at=r[10]) for r in rows]

    def get_tasks_for_project(self, project_id: str) -> List[Task]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id,tenant_id,project_id,title,trade,status,assigned_to,due_date,notes,created_at,updated_at FROM tasks WHERE project_id=? ORDER BY due_date, trade, created_at",
                (project_id,),
            ).fetchall()
        return [Task(id=r[0], tenant_id=r[1], project_id=r[2], title=r[3], trade=r[4],
                     status=r[5], assigned_to=r[6], due_date=r[7], notes=r[8],
                     created_at=r[9], updated_at=r[10]) for r in rows]

    def get_tasks_due_today(self, tenant_id: str) -> List[Task]:
        today = datetime.utcnow().date().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id,tenant_id,project_id,title,trade,status,assigned_to,due_date,notes,created_at,updated_at FROM tasks WHERE tenant_id=? AND due_date=? AND status NOT IN ('complete','rejected') ORDER BY trade",
                (tenant_id, today),
            ).fetchall()
        return [Task(id=r[0], tenant_id=r[1], project_id=r[2], title=r[3], trade=r[4],
                     status=r[5], assigned_to=r[6], due_date=r[7], notes=r[8],
                     created_at=r[9], updated_at=r[10]) for r in rows]

    # ── Evidence (photos) ─────────────────────────────────────────────────────

    def save_evidence(self, task_id: str, contractor_id: str, photo_url: str, caption: str = "") -> Evidence:
        e = Evidence(
            id=_new_id(), task_id=task_id, contractor_id=contractor_id,
            photo_url=photo_url, caption=caption, submitted_at=_now(),
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO evidence (id,task_id,contractor_id,photo_url,caption,submitted_at) VALUES (?,?,?,?,?,?)",
                (e.id, e.task_id, e.contractor_id, e.photo_url, e.caption, e.submitted_at),
            )
            conn.commit()
        return e

    def get_evidence(self, task_id: str) -> List[Evidence]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id,task_id,contractor_id,photo_url,caption,submitted_at FROM evidence WHERE task_id=? ORDER BY submitted_at",
                (task_id,),
            ).fetchall()
        return [Evidence(id=r[0], task_id=r[1], contractor_id=r[2], photo_url=r[3],
                         caption=r[4], submitted_at=r[5]) for r in rows]

    def count_evidence(self, task_id: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM evidence WHERE task_id=?", (task_id,)).fetchone()[0]

    # ── Sign-offs ─────────────────────────────────────────────────────────────

    def record_sign_off(self, task_id: str, approver_phone: str, status: str, notes: str = "") -> SignOff:
        s = SignOff(
            id=_new_id(), task_id=task_id, approver_phone=_normalise_phone(approver_phone),
            status=status, notes=notes, approved_at=_now(),
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sign_offs (id,task_id,approver_phone,status,notes,approved_at) VALUES (?,?,?,?,?,?)",
                (s.id, s.task_id, s.approver_phone, s.status, s.notes, s.approved_at),
            )
            conn.commit()
        task_status = "complete" if status == "approved" else "rejected"
        self.update_task_status(task_id, task_status, notes)
        return s

    def get_sign_off(self, task_id: str) -> Optional[SignOff]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id,task_id,approver_phone,status,notes,approved_at FROM sign_offs WHERE task_id=? ORDER BY approved_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row:
            return SignOff(id=row[0], task_id=row[1], approver_phone=row[2],
                           status=row[3], notes=row[4], approved_at=row[5])
        return None

    # ── Walkthrough Checklists ─────────────────────────────────────────────────

    def create_walkthrough(self, tenant_id: str, project_id: str, title: str,
                           items: List[str]) -> WalkthroughChecklist:
        import json
        wt = WalkthroughChecklist(
            id=_new_id(), tenant_id=tenant_id, project_id=project_id,
            title=title, items=items, status="pending", created_at=_now(),
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO walkthrough_checklists (id,tenant_id,project_id,title,items_json,status,created_at) VALUES (?,?,?,?,?,?,?)",
                (wt.id, wt.tenant_id, wt.project_id, wt.title,
                 json.dumps(items), wt.status, wt.created_at),
            )
            conn.commit()
        return wt

    def get_walkthrough(self, walkthrough_id: str) -> Optional[WalkthroughChecklist]:
        import json
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id,tenant_id,project_id,title,items_json,status,created_at FROM walkthrough_checklists WHERE id=? LIMIT 1",
                (walkthrough_id,),
            ).fetchone()
        if row:
            return WalkthroughChecklist(id=row[0], tenant_id=row[1], project_id=row[2],
                                        title=row[3], items=json.loads(row[4]),
                                        status=row[5], created_at=row[6])
        return None

    def update_walkthrough_status(self, walkthrough_id: str, status: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE walkthrough_checklists SET status=? WHERE id=?",
                (status, walkthrough_id),
            )
            conn.commit()
        return cur.rowcount > 0

    # ── Project summary ───────────────────────────────────────────────────────

    def get_manager_roles_for_phone(self, phone: str) -> list[dict]:
        """Return all project assignments where this phone is a manager-level role."""
        normalised = _normalise_phone(phone)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT pa.project_id, pa.role, c.id as contractor_id, c.name
                   FROM project_assignments pa
                   JOIN contractors c ON c.id = pa.contractor_id
                   WHERE c.phone = ? AND pa.role IN ('architect', 'site_manager', 'quantity_surveyor')""",
                (normalised,),
            ).fetchall()
        return [{"project_id": r[0], "role": r[1], "contractor_id": r[2], "name": r[3]} for r in rows]

    def get_tasks_awaiting_signoff_for_manager(self, phone: str) -> list[dict]:
        """Return tasks awaiting sign-off in projects where this phone has a manager role."""
        normalised = _normalise_phone(phone)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT t.id, t.title, t.project_id,
                          assigned_c.phone  AS contractor_phone,
                          assigned_c.name   AS contractor_name
                   FROM tasks t
                   JOIN project_assignments manager_pa ON manager_pa.project_id = t.project_id
                   JOIN contractors manager_c ON manager_c.id = manager_pa.contractor_id
                   LEFT JOIN contractors assigned_c ON assigned_c.id = t.assigned_to
                   WHERE manager_c.phone = ?
                     AND manager_pa.role IN ('architect', 'site_manager', 'quantity_surveyor')
                     AND t.status = 'awaiting_sign_off'
                   ORDER BY t.updated_at DESC
                   LIMIT 1""",
                (normalised,),
            ).fetchall()
        return [
            {
                "task_id": r[0], "title": r[1], "project_id": r[2],
                "contractor_phone": r[3] or "", "contractor_name": r[4] or "Unknown",
            }
            for r in rows
        ]

    def project_status_summary(self, project_id: str) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            tasks = conn.execute(
                "SELECT status, COUNT(*) FROM tasks WHERE project_id=? GROUP BY status",
                (project_id,),
            ).fetchall()
            team_count = conn.execute(
                "SELECT COUNT(*) FROM project_assignments WHERE project_id=?",
                (project_id,),
            ).fetchone()[0]
        status_counts = {r[0]: r[1] for r in tasks}
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
    digits = phone.lstrip("+").replace(" ", "").replace("-", "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "27" + digits[1:]
    return digits


# ─── Singleton ───────────────────────────────────────────────────────────────

_db: Optional[FieldOpsDB] = None


def get_field_ops_db() -> FieldOpsDB:
    global _db
    if _db is None:
        _db = FieldOpsDB()
    return _db

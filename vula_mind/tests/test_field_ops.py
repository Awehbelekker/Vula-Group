"""Tests for field ops data layer and API."""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_db():
    from vula.models.field_ops import FieldOpsDB
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = FieldOpsDB(db_path=Path(tmp.name))
    # patch evidence dir to tmp
    import tempfile as tf
    db._evidence_dir = Path(tf.mkdtemp())
    return db


@pytest.fixture()
def db():
    return make_db()


@pytest.fixture()
def client():
    from vula.api.field_ops import router
    app = FastAPI()
    app.include_router(router, prefix="/v1/field")
    with patch("vula.api.field_ops.get_field_ops_db", return_value=make_db()):
        yield TestClient(app)


# ─── FieldOpsDB — contractors ─────────────────────────────────────────────────

def test_upsert_contractor_create(db):
    c = db.upsert_contractor("t1", "John Bricklayer", "0821112222", "bricklayer")
    assert c.id
    assert c.name == "John Bricklayer"
    assert c.phone == "27821112222"  # normalised
    assert c.trade == "bricklayer"


def test_upsert_contractor_update_existing(db):
    db.upsert_contractor("t1", "John", "0821112222", "bricklayer")
    c2 = db.upsert_contractor("t1", "John Updated", "0821112222", "tiler")
    assert c2.name == "John Updated"
    assert c2.trade == "tiler"
    # Only one record
    contractors = db.list_contractors("t1")
    assert len(contractors) == 1


def test_get_contractor_by_phone_normalises(db):
    db.upsert_contractor("t1", "Jane", "+27831234567", "electrician")
    found = db.get_contractor_by_phone("0831234567")
    assert found is not None
    assert found.name == "Jane"


def test_list_contractors_filters_by_tenant(db):
    db.upsert_contractor("t1", "Alice", "0821000001", "plumber")
    db.upsert_contractor("t2", "Bob", "0821000002", "painter")
    t1_list = db.list_contractors("t1")
    assert len(t1_list) == 1
    assert t1_list[0].name == "Alice"


# ─── FieldOpsDB — tasks ───────────────────────────────────────────────────────

def test_create_task(db):
    task = db.create_task("t1", "proj1", "Lay foundations", "concrete",
                          due_date="2026-06-01")
    assert task.id
    assert task.status == "pending"
    assert task.due_date == "2026-06-01"


def test_update_task_status(db):
    task = db.create_task("t1", "proj1", "Paint walls", "painting")
    ok = db.update_task_status(task.id, "in_progress")
    assert ok
    updated = db.get_task(task.id)
    assert updated.status == "in_progress"


def test_get_tasks_for_contractor(db):
    c = db.upsert_contractor("t1", "Pete", "0821000003", "plumber")
    t1 = db.create_task("t1", "proj1", "Install pipes", "plumbing", assigned_to=c.id)
    t2 = db.create_task("t1", "proj1", "Test pressure", "plumbing", assigned_to=c.id)
    db.create_task("t1", "proj1", "Other task", "electrical")  # unassigned

    tasks = db.get_tasks_for_contractor(c.id)
    assert len(tasks) == 2


def test_get_tasks_for_contractor_status_filter(db):
    c = db.upsert_contractor("t1", "Pete", "0821000003", "plumber")
    t = db.create_task("t1", "proj1", "Install pipes", "plumbing", assigned_to=c.id)
    db.update_task_status(t.id, "in_progress")

    in_progress = db.get_tasks_for_contractor(c.id, status="in_progress")
    assert len(in_progress) == 1

    pending = db.get_tasks_for_contractor(c.id, status="pending")
    assert len(pending) == 0


# ─── FieldOpsDB — evidence & sign-offs ───────────────────────────────────────

def test_save_and_get_evidence(db):
    task = db.create_task("t1", "proj1", "Lay tile", "tiling")
    e = db.save_evidence(task.id, "contractor1", "/tmp/photo.jpg", "before shot")
    assert e.id
    assert db.count_evidence(task.id) == 1
    evidence = db.get_evidence(task.id)
    assert len(evidence) == 1
    assert evidence[0].caption == "before shot"


def test_record_sign_off_updates_task_status(db):
    task = db.create_task("t1", "proj1", "Install tiles", "tiling")
    db.update_task_status(task.id, "awaiting_sign_off")
    db.record_sign_off(task.id, "+27821000099", "approved", "looks great")
    updated = db.get_task(task.id)
    assert updated.status == "complete"


def test_record_sign_off_rejected_sets_rejected(db):
    task = db.create_task("t1", "proj1", "Paint wall", "painting")
    db.update_task_status(task.id, "awaiting_sign_off")
    db.record_sign_off(task.id, "27821000099", "rejected", "missed a section")
    updated = db.get_task(task.id)
    assert updated.status == "rejected"


# ─── FieldOpsDB — project assignment & summary ───────────────────────────────

def test_project_assignment(db):
    c = db.upsert_contractor("t1", "Tom", "0821111222", "foreman")
    pa = db.assign_to_project("t1", "proj42", c.id, "site_manager")
    assert pa.project_id == "proj42"
    assert pa.role == "site_manager"
    team = db.get_project_team("proj42")
    assert len(team) == 1
    assert team[0]["name"] == "Tom"


def test_project_status_summary(db):
    for title, status in [("T1", "pending"), ("T2", "in_progress"), ("T3", "complete")]:
        t = db.create_task("t1", "proj5", title, "electrical")
        if status != "pending":
            db.update_task_status(t.id, status)

    summary = db.project_status_summary("proj5")
    assert summary["tasks_total"] == 3
    assert summary["tasks_complete"] == 1
    assert summary["completion_pct"] == 33


# ─── FieldOpsDB — walkthrough ─────────────────────────────────────────────────

def test_create_and_get_walkthrough(db):
    wt = db.create_walkthrough("t1", "proj1", "Final inspection",
                               ["Ceilings", "Floors", "Doors"])
    assert wt.id
    assert wt.status == "pending"
    fetched = db.get_walkthrough(wt.id)
    assert fetched.items == ["Ceilings", "Floors", "Doors"]


def test_update_walkthrough_status(db):
    wt = db.create_walkthrough("t1", "proj1", "Inspection", ["Item 1"])
    ok = db.update_walkthrough_status(wt.id, "complete")
    assert ok
    fetched = db.get_walkthrough(wt.id)
    assert fetched.status == "complete"


# ─── API endpoints ────────────────────────────────────────────────────────────

def test_register_contractor_endpoint(client):
    resp = client.post("/v1/field/contractors", json={
        "tenant_id": "t1", "name": "Jan Builder",
        "phone": "0821234567", "trade": "bricklayer",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Jan Builder"
    assert data["phone"] == "27821234567"


def test_list_contractors_endpoint(client):
    client.post("/v1/field/contractors", json={
        "tenant_id": "t1", "name": "Alice", "phone": "0821000001", "trade": "plumber",
    })
    resp = client.get("/v1/field/contractors/t1")
    assert resp.status_code == 200
    assert resp.json()["contractors"][0]["name"] == "Alice"


def test_create_task_endpoint(client):
    resp = client.post("/v1/field/task", json={
        "tenant_id": "t1", "project_id": "proj1",
        "title": "Install geysers", "trade": "plumbing",
        "due_date": "2026-07-01",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_project_status_endpoint(client):
    # Create a task first
    client.post("/v1/field/task", json={
        "tenant_id": "t1", "project_id": "projX", "title": "T1", "trade": "electrical",
    })
    resp = client.get("/v1/field/project/projX/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks_total"] == 1
    assert "tasks" in data


def test_assign_task_sends_whatsapp(client):
    # Register contractor and create task
    c_resp = client.post("/v1/field/contractors", json={
        "tenant_id": "t1", "name": "Bob", "phone": "0821000099", "trade": "plumber",
    })
    contractor_id = c_resp.json()["id"]

    t_resp = client.post("/v1/field/task", json={
        "tenant_id": "t1", "project_id": "proj1", "title": "Fix leak", "trade": "plumbing",
    })
    task_id = t_resp.json()["id"]

    with patch("vula.api.field_ops._send_reply", new=AsyncMock(return_value=True)):
        resp = client.post("/v1/field/task/assign", json={
            "task_id": task_id, "contractor_id": contractor_id, "send_whatsapp": True,
        })
    assert resp.status_code == 200
    assert resp.json()["contractor_name"] == "Bob"


def test_get_task_with_evidence(client):
    t_resp = client.post("/v1/field/task", json={
        "tenant_id": "t1", "project_id": "proj1", "title": "Inspect slab", "trade": "concrete",
    })
    task_id = t_resp.json()["id"]
    resp = client.get(f"/v1/field/task/{task_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Inspect slab"
    assert data["evidence"] == []
    assert data["sign_off"] is None


def test_daily_tasks_endpoint(client):
    from datetime import datetime
    today = datetime.utcnow().date().isoformat()
    client.post("/v1/field/task", json={
        "tenant_id": "t1", "project_id": "proj1",
        "title": "Due today", "trade": "painting", "due_date": today,
    })
    resp = client.get("/v1/field/daily-tasks/t1")
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1

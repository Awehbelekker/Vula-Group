"""POPIA for uploaded plans (Phase 4g): a retention period, a delete-now route, safe file names."""
from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from config import settings
from vula.api.server import app
from vula.takeoff import api

client = TestClient(app, headers={"X-API-Key": settings.api_key or ""})


def test_plans_older_than_the_retention_period_are_deleted(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path)
    old, new = tmp_path / "a_old.pdf", tmp_path / "b_new.pdf"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    now = time.time()
    os.utime(old, (now - 91 * 86400, now - 91 * 86400))
    api._jobs["ret_old"] = {"status": "complete", "uploaded_at": now - 91 * 86400}
    api._jobs["ret_new"] = {"status": "complete", "uploaded_at": now - 5 * 86400}
    res = api.purge_expired(days=90, now=now)
    assert res["files"] == 1 and not old.exists() and new.exists()
    assert "ret_old" not in api._jobs and "ret_new" in api._jobs


def test_retention_zero_keeps_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"x")
    os.utime(f, (0, 0))
    assert api.purge_expired(days=0) == {"files": 0, "jobs": 0} and f.exists()


def test_delete_now_removes_the_plan_and_the_job(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path)
    plan = tmp_path / "del1_plan.pdf"
    plan.write_bytes(b"x")
    api._jobs["del1"] = {"status": "complete", "tenant_id": "t1", "file_path": str(plan)}
    r = client.delete("/takeoff/del1?tenant_id=t1")
    assert r.status_code == 200 and r.json()["plan_file_removed"] is True
    assert not plan.exists() and "del1" not in api._jobs


def test_delete_is_tenant_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path)
    plan = tmp_path / "del2_plan.pdf"
    plan.write_bytes(b"x")
    api._jobs["del2"] = {"status": "complete", "tenant_id": "tenant-a", "file_path": str(plan)}
    assert client.delete("/takeoff/del2?tenant_id=tenant-b").status_code == 404
    assert plan.exists()


def test_delete_never_touches_a_file_outside_the_upload_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir()
    outside = tmp_path / "keep.txt"
    outside.write_text("x")
    api._delete_job("del3", {"file_path": str(outside)})
    assert outside.exists()


def test_upload_names_are_one_safe_path_component():
    assert api._safe_name("../../etc/passwd") == "passwd"
    assert api._safe_name("Plans – Rev B (final).PDF") == "Plans_Rev_B_final.pdf"
    assert api._safe_name(None) == "plan"

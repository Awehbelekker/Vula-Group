"""Stores that lived only in local SQLite survive a deploy (2026-09-27).

The Railway service has no volume attached, so drafts.db, tenants.db and the Takeoff rate and
supplier databases were wiped on every deploy, and Takeoff jobs lived only in memory. Each store
now writes through to Supabase (vula/durable.py) and reads back from it when the local copy is
empty — simulated here with a fresh temp directory standing in for a fresh container.
"""
from unittest.mock import patch

import pytest

from vula import durable


class FakeCloud:
    """In-memory stand-in for the Supabase tables durable.py talks to."""

    def __init__(self):
        self.tables = {}

    def upsert(self, table, row, on_conflict=None):
        keys = (on_conflict or "id").split(",")
        rows = self.tables.setdefault(table, [])
        rows[:] = [r for r in rows if any(r.get(k) != row.get(k) for k in keys)]
        rows.append(dict(row))
        return True

    def delete(self, table, **eq):
        rows = self.tables.setdefault(table, [])
        rows[:] = [r for r in rows if any(r.get(k) != v for k, v in eq.items())]
        return True

    def select(self, table, columns="*", order=None, desc=False, limit=None, **eq):
        rows = [r for r in self.tables.get(table, []) if all(r.get(k) == v for k, v in eq.items())]
        return rows[:limit] if limit else rows


@pytest.fixture()
def cloud(monkeypatch):
    fc = FakeCloud()
    monkeypatch.setattr(durable, "upsert", fc.upsert)
    monkeypatch.setattr(durable, "delete", fc.delete)
    monkeypatch.setattr(durable, "select", fc.select)
    return fc


def test_durable_is_a_no_op_without_supabase():
    """Tests/local dev: no Supabase key → nothing raises and callers fall back to SQLite."""
    assert durable.select("vula_drafts") is None
    assert durable.upsert("vula_drafts", {"draft_id": "d"}) is False
    assert durable.delete("vula_drafts", draft_id="d") is False


def test_drafts_survive_a_new_container(cloud, tmp_path, monkeypatch):
    from vula.api import draft
    monkeypatch.setattr(draft.settings, "data_dir", tmp_path / "a")
    draft.DraftStore().save("digg-demo", "d1", "letter", "Fee letter for Echium", "Dear Sir…", 3, "m", 2)
    monkeypatch.setattr(draft.settings, "data_dir", tmp_path / "b")   # fresh, empty disk
    fresh = draft.DraftStore()
    assert fresh.get("d1")["content"] == "Dear Sir…"
    assert [d["draft_id"] for d in fresh.list_tenant("digg-demo")] == ["d1"]
    assert fresh.list_tenant("off-the-hook") == []


def test_tenant_phones_survive_a_new_container(cloud, tmp_path):
    from vula.models.tenants import LocalTenantDB
    db = LocalTenantDB(tmp_path / "a.db")
    db.upsert("digg-demo", "DIGG Architecture")
    db.add_phone("digg-demo", "082 707 7080", label="judy", role="admin")
    assert cloud.tables["vula_tenant_phones"][0]["phone"] == "27827077080"
    fresh = LocalTenantDB(tmp_path / "b.db")
    assert fresh.phones("digg-demo") == [{"phone": "27827077080", "label": "judy", "role": "admin"}]
    db.remove_phone("0827077080")
    assert cloud.tables["vula_tenant_phones"] == []


def test_rates_reload_from_supabase_when_the_local_file_is_empty(cloud, tmp_path):
    from vula.takeoff.construction_rates_scraper import MaterialRate, RatesDatabase
    db = RatesDatabase(tmp_path / "a.db")
    db.upsert(MaterialRate(key="cement_50kg", label="Cement 50kg", unit="bag", low=95, high=120,
                           mid=107.5, source="AECOM", scraped_at="2026-09-20"))
    fresh = RatesDatabase(tmp_path / "b.db")
    assert [r["key"] for r in fresh.get_all()] == ["cement_50kg"]
    assert fresh.get_all()[0]["mid"] == 107.5


def test_supplier_edits_survive_a_new_container(cloud, tmp_path):
    from vula.takeoff.order_manager import SupplierDatabase
    db = SupplierDatabase(tmp_path / "a.db")
    s = next(x for x in db.get_all() if x.id == "sup_hva")
    s.email = "quotes@extraair.co.za"
    db.upsert_supplier(s, tenant_id="digg-demo")
    fresh = SupplierDatabase(tmp_path / "b.db")
    got = next(x for x in fresh.get_all("digg-demo") if x.id == "sup_hva")
    assert got.email == "quotes@extraair.co.za"


def test_takeoff_job_is_reloaded_after_a_restart(cloud):
    from vula.takeoff import api
    api._jobs["j1"] = {"status": "complete", "tenant_id": "digg-demo", "boq": {"items": [1]}}
    api._save_job("j1")
    api._jobs.clear()                                   # the restart
    assert api._job_for("j1", "digg-demo")["boq"] == {"items": [1]}
    assert api._job_for("j1", "off-the-hook") is None   # still tenant-scoped


def test_a_job_cut_off_mid_run_reports_the_restart(cloud):
    from vula.takeoff import api
    api._jobs["j2"] = {"status": "generating_boq", "tenant_id": "digg-demo"}
    api._save_job("j2")
    api._jobs.clear()
    job = api._job_for("j2", "digg-demo")
    assert job["status"] == "failed" and "restart" in job["error"]

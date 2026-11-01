"""A priced takeoff becomes the project's one working BOQ (vula_boq_drafts), never a second bill."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from config import settings
from vula.api.server import app
from vula.commerce import boq_online
from vula.takeoff import api

client = TestClient(app, headers={"X-API-Key": settings.api_key or ""})

_BOQ = {"items": [
    {"id": "FLO_001", "trade": "flooring", "description": "Commercial carpet (laid)", "unit": "m²",
     "qty": 40.0, "rate_cents": 35000},
    {"id": "JOI_002", "trade": "joinery", "description": "Skirting", "unit": "m", "qty": 30.5, "rate_cents": 0},
]}


def test_takeoff_lines_are_worked_out_again_in_cents():
    lines = boq_online.from_takeoff(_BOQ)
    carpet, skirting = lines
    assert carpet["amount_cents"] == 1400000 and carpet["kind"] == "takeoff"
    assert skirting["rate_cents"] is None and skirting["amount_cents"] is None   # priced in the online BOQ
    assert all(li["section"] for li in lines)


def _job(jid, status="complete"):
    api._jobs[jid] = {"status": status, "tenant_id": "t1", "boq": _BOQ}


def test_a_priced_takeoff_becomes_the_project_bill():
    _job("tp1")
    with patch.object(boq_online, "load", return_value={"lines": []}), \
         patch.object(boq_online, "save", return_value={"project": "Site A", "total_cents": 1400000}) as save:
        r = client.post("/takeoff/tp1/to-project?tenant_id=t1", json={"project": "Site A"})
    assert r.status_code == 200 and r.json()["lines"] == 2
    tenant, project, lines, by = save.call_args.args
    assert (tenant, project, len(lines)) == ("t1", "Site A", 2) and by.startswith("takeoff:")


def test_an_existing_bill_is_never_overwritten_unasked():
    _job("tp2")
    with patch.object(boq_online, "load", return_value={"lines": [{"description": "QS line"}]}), \
         patch.object(boq_online, "save") as save:
        assert client.post("/takeoff/tp2/to-project?tenant_id=t1", json={"project": "Site A"}).status_code == 409
        save.assert_not_called()


def test_only_a_priced_takeoff_moves():
    _job("tp3", status="needs_review")
    assert client.post("/takeoff/tp3/to-project?tenant_id=t1", json={"project": "Site A"}).status_code == 409


def test_another_business_can_t_move_it():
    _job("tp4")
    assert client.post("/takeoff/tp4/to-project?tenant_id=t2", json={"project": "X"}).status_code == 404

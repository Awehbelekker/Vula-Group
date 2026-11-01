"""A plan sent on WhatsApp: read, preview, Price it (vula/takeoff/whatsapp_flow.py)."""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

from vula.takeoff import api, whatsapp_flow as wf
from vula.takeoff.plan_reader import ExtractedProject, Room, assess


def _pdf(path: Path, size, lines):
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=size[0], height=size[1])
    for i, t in enumerate(lines):
        page.insert_text((40, 60 + 16 * i), t, fontsize=10)
    doc.save(path)
    return path


def test_what_counts_as_a_plan(tmp_path):
    dxf = tmp_path / "x.dxf"
    dxf.write_text("0\nEOF\n")
    invoice = _pdf(tmp_path / "inv.pdf", (595, 842), ["TAX INVOICE 1:100 widgets", "A101 item"])
    drawing = _pdf(tmp_path / "plan.pdf", (1191, 842), ["A101 GROUND FLOOR PLAN", "SCALE 1:100"])
    assert wf.is_plan(dxf)
    assert not wf.is_plan(invoice)                     # A4: never a drawing on its own
    assert wf.is_plan(invoice, caption="takeoff please")
    assert wf.is_plan(drawing)
    assert not wf.is_plan(tmp_path / "x.xlsx")


def _held_job(job_id, rooms=(("Office", 40.0),)):
    api._jobs[job_id] = {"status": "needs_review", "tenant_id": "t1", "markup": 15.0, "filename": "plan.pdf",
                         "project": {"title": "Site", "room_list": [{"name": n, "area": a, "floor": "carpet"}
                                                                   for n, a in rooms], "gfa": 0},
                         "review": {"status": "pending", "reasons": ["Only one reading."]},
                         "extracted": api._project_to_state(ExtractedProject(
                             rooms=[Room(name=n, area=a, floor_finish="carpet") for n, a in rooms]))}


def test_only_the_team_of_a_business_with_estimating(tmp_path):
    dxf = tmp_path / "x.dxf"
    dxf.write_text("0\nEOF\n")
    with patch.object(wf, "_estimating_on", return_value=False):
        assert asyncio.run(wf.plan_first("t1", "27820000001", dxf, "x.dxf")) is False
    with patch.object(wf, "_estimating_on", return_value=True):
        assert asyncio.run(wf.plan_first("t1", "27820000001", dxf, "x.dxf", is_staff=False)) is False


def test_a_plan_is_read_previewed_and_waits_for_price_it(tmp_path):
    dxf = tmp_path / "x.dxf"
    dxf.write_text("0\nEOF\n")

    async def fake_process(job_id, path, tenant_id, markup):
        source = api._jobs[job_id]["source"]
        _held_job(job_id)
        api._jobs[job_id]["source"] = source
    with patch.object(wf, "_estimating_on", return_value=True), \
         patch.object(api, "_process_plans", fake_process), \
         patch("vula.api.whatsapp._send_reply", AsyncMock()), \
         patch("vula.api.whatsapp._dashboard_link", return_value="https://dash/takeoff"), \
         patch("vula.api.whatsapp._ask_admin_confirm", AsyncMock()) as ask:
        assert asyncio.run(wf.plan_first("t1", "27820000001", dxf, "x.dxf")) is True
    phone, tenant, tool, args = ask.await_args.args[:4]
    assert (tool, ask.await_args.kwargs["labels"]) == ("takeoff_approve", ("Price it", "Not now"))
    summary = ask.await_args.kwargs["summary"]
    assert "Office: 40.0 m²" in summary and "https://dash/takeoff" in summary and "Only one reading" in summary
    assert api._jobs[args["job_id"]]["source"] == "whatsapp"


def test_price_it_approves_and_prices(tmp_path):
    _held_job("wa1")

    async def fake_price(job_id, project, tenant_id, markup):
        api._jobs[job_id].update(status="complete", boq={"grand_total_cents": 1234500, "markup_pct": 15,
                                                         "items": [{"rate_cents": 100}, {"rate_cents": 0}],
                                                         "excluded": ["Electrical — not measured"]})
    with patch.object(api, "_price", fake_price), \
         patch("vula.api.whatsapp._dashboard_link", return_value="https://dash/takeoff"):
        text = asyncio.run(wf.approve("t1", "wa1", "27820000001"))
    assert "R12,345.00" in text and "1 line(s) have no rate" in text and "Electrical" in text
    assert api._jobs["wa1"]["review"]["status"] == "approved"
    assert api._jobs["wa1"]["review"]["decided_by"] == "27820000001"


def test_price_it_twice_does_nothing_the_second_time():
    _held_job("wa2")
    api._jobs["wa2"]["status"] = "complete"
    assert "isn't waiting" in asyncio.run(wf.approve("t1", "wa2", "x"))


def test_the_confirm_tap_reaches_the_flow():
    from core.skills.commerce_admin import CommerceAdminSkill
    with patch.object(wf, "approve", AsyncMock(return_value="priced")) as ap:
        out = asyncio.run(CommerceAdminSkill()._dispatch_tool(
            "takeoff_approve", {"job_id": "j9", "confirm": True}, {"tenant_id": "t1", "phone": "27820000001"}))
    assert out["reply_verbatim"] == "priced" and ap.await_args.args == ("t1", "j9", "27820000001")


def test_every_takeoff_waits_for_the_owner_even_when_readings_agree(tmp_path):
    p = assess(ExtractedProject(rooms=[Room(name="A", area=50, floor_finish="carpet")],
                                ai_rooms=[Room(name="A", area=50)], deterministic_rooms=[("A", 50.0)]),
               use_history=False)
    assert p.review["status"] == "not_required"          # the checks pass…
    api._jobs["wa3"] = {"status": "queued", "tenant_id": "t1"}
    with patch.object(api, "PlanReader") as PR, patch.object(api, "BOQGenerator") as BG:
        PR.return_value.read = AsyncMock(return_value=p)
        asyncio.run(api._process_plans("wa3", tmp_path / "p.pdf", "t1", 15))
    assert api._jobs["wa3"]["status"] == "needs_review"   # …and it still waits for the owner
    assert api._jobs["wa3"]["review"] == {"status": "pending", "reasons": [], "checked": True}
    BG.assert_not_called()


def test_the_document_path_offers_plans_to_takeoff_before_filing():
    from vula.api import whatsapp
    src = inspect.getsource(whatsapp._handle_document_ingest)
    assert src.index("_returned_form_first") < src.index("plan_first") < src.index("content_hash")

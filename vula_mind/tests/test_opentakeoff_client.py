"""OpenTakeoff sidecar client: units, matching measured rooms into ExtractedProject, fail-open."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.takeoff import opentakeoff_client as ot
from vula.takeoff.plan_reader import ExtractedProject, Room


def _raw(rooms, scale=None, page=1, sheet_number="A-101", warning=None):
    sc = scale if scale is not None else {"label": "1:100", "source": "detected", "confirmed": False,
                                          "upp_ft": 0.1, "warning": warning}
    return {"engine": {"tag": "apache-2.0-final", "commit": "84c7f48e458f6cd74f694e3a970dd011c73a8f2e"},
            "units": {"area": "sf", "length": "lf", "upp": "ft_per_px"},
            "sheets": [{"page": page, "sheet": "plan.pdf", "sheet_number": sheet_number,
                        "detected_scale": "1:100", "scale": sc, "rooms": rooms,
                        "withheld": {"total": 0}, "error": None}]}


def test_parse_converts_feet_to_metres():
    plan = ot.parse_response(_raw([{"label": "101", "area_sf": 1000.0, "perimeter_lf": 100.0,
                                    "confidence": 0.93}]))
    r = plan.rooms[0]
    assert r.area_m2 == pytest.approx(92.903, abs=1e-3)       # 1000 SF
    assert r.perimeter_m == pytest.approx(30.48, abs=1e-3)    # 100 LF
    assert r.trace_confidence == 0.93
    c = plan.sheets[0]
    assert c.scale_label == "1:100" and c.method == "detected" and c.confirmed is False
    assert c.m_per_px == pytest.approx(0.03048)


def test_parse_drops_unmeasured_and_rejects_unknown_units():
    plan = ot.parse_response(_raw([{"label": "x", "area_sf": 0}, {"label": "y"}]))
    assert plan.rooms == []
    bad = _raw([]); bad["units"] = {"area": "m2", "length": "m"}
    with pytest.raises(ValueError):
        ot.parse_response(bad)


def test_measured_area_replaces_ai_read_and_reports_agreement():
    p = ExtractedProject(rooms=[Room(name="Office 101", area=100.0, floor_finish="lvt"),
                                Room(name="Boardroom", area=40.0, floor_finish="carpet")])
    plan = ot.parse_response(_raw([{"label": "OFFICE 101", "area_sf": 1000.0, "perimeter_lf": 130.0},
                                   {"label": "BOARDROOM", "area_sf": 430.0}]))
    res = ot.apply_measurements(p, plan)
    office, board = p.rooms
    assert office.name == "Office 101" and office.floor_finish == "lvt"   # schedule data kept
    assert office.area == pytest.approx(92.903, abs=1e-3)
    assert office.perimeter == pytest.approx(39.624, abs=1e-3)
    assert board.perimeter == 0.0                                       # not measured → untouched
    a = {x.room: x for x in res.agreements}
    assert a["Office 101"].ai_area_m2 == 100.0
    assert a["Office 101"].delta_pct == pytest.approx(7.64, abs=0.01)
    assert a["Office 101"].scale_label == "1:100" and a["Office 101"].scale_confirmed is False
    assert any("not yet confirmed" in n for n in p.extraction_notes)


def test_room_number_decides_when_both_are_numbered():
    p = ExtractedProject(rooms=[Room(name="Office 101", area=10), Room(name="Office 102", area=10)])
    res = ot.apply_measurements(p, ot.parse_response(_raw([{"label": "102", "area_sf": 200.0}])))
    assert [x.room for x in res.agreements] == ["Office 102"]
    assert p.rooms[0].area == 10


def test_ambiguous_matches_are_never_guessed_into_the_bill():
    # "OFFICE" names both rooms; two measured "104"s claim one room. Neither is applied.
    p = ExtractedProject(rooms=[Room(name="Office A", area=10), Room(name="Office B", area=10),
                                Room(name="Store 104", area=5)])
    res = ot.apply_measurements(p, ot.parse_response(_raw([
        {"label": "OFFICE", "area_sf": 100.0},
        {"label": "104", "area_sf": 50.0}, {"label": "104", "area_sf": 60.0}])))
    assert res.agreements == []
    assert len(res.unmatched) == 3
    assert [r.area for r in p.rooms] == [10, 10, 5]
    assert any("didn't match the room schedule" in n for n in p.extraction_notes)


def test_no_room_schedule_takes_the_measured_rooms():
    p = ExtractedProject()
    ot.apply_measurements(p, ot.parse_response(_raw([{"label": "101", "area_sf": 1000.0,
                                                      "perimeter_lf": 100.0}])))
    assert len(p.rooms) == 1 and p.rooms[0].name == "101"
    assert p.rooms[0].area == pytest.approx(92.903, abs=1e-3)


def test_unscaled_sheet_is_reported_not_measured():
    raw = _raw([], scale={})
    raw["sheets"][0]["scale"] = None
    p = ExtractedProject(rooms=[Room(name="Office", area=10)])
    res = ot.apply_measurements(p, ot.parse_response(raw))
    assert res.calibrations[0].scale_label is None and res.calibrations[0].method is None
    assert any("No scale on sheet(s) A-101" in n for n in p.extraction_notes)


def test_none_plan_changes_nothing():
    p = ExtractedProject(rooms=[Room(name="Office", area=10)])
    res = ot.apply_measurements(p, None)
    assert res.agreements == [] and p.extraction_notes == [] and p.rooms[0].area == 10


@pytest.mark.asyncio
async def test_measure_plan_unconfigured_or_not_pdf_returns_none(tmp_path, monkeypatch):
    f = tmp_path / "plan.pdf"; f.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(ot.settings, "opentakeoff_url", "")
    assert await ot.measure_plan(f) is None
    monkeypatch.setattr(ot.settings, "opentakeoff_url", "http://sidecar:8790")
    assert await ot.measure_plan(tmp_path / "plan.dxf") is None


def _client_returning(resp=None, exc=None):
    client = MagicMock()
    client.post = AsyncMock(return_value=resp, side_effect=exc)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


@pytest.mark.asyncio
async def test_measure_plan_posts_pdf_with_key_and_scale_override(tmp_path, monkeypatch):
    f = tmp_path / "plan.pdf"; f.write_bytes(b"%PDF-1.4 body")
    monkeypatch.setattr(ot.settings, "opentakeoff_url", "http://sidecar:8790/")
    monkeypatch.setattr(ot.settings, "opentakeoff_api_key", "k")
    resp = MagicMock(); resp.json.return_value = _raw([{"label": "1", "area_sf": 10.0}])
    cm, client = _client_returning(resp)
    with patch.object(ot.httpx, "AsyncClient", return_value=cm):
        plan = await ot.measure_plan(f, scales={2: "1:50"})
    assert plan and plan.rooms[0].area_m2 == pytest.approx(0.929, abs=1e-3)
    url = client.post.call_args.args[0]
    kw = client.post.call_args.kwargs
    assert url == "http://sidecar:8790/measure"
    assert kw["content"] == b"%PDF-1.4 body" and kw["headers"]["x-api-key"] == "k"
    assert kw["params"] == {"scales": '{"2": "1:50"}'}


@pytest.mark.asyncio
async def test_measure_plan_fails_open_on_sidecar_error(tmp_path, monkeypatch):
    f = tmp_path / "plan.pdf"; f.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(ot.settings, "opentakeoff_url", "http://sidecar:8790")
    cm, _ = _client_returning(exc=ot.httpx.ConnectError("down"))
    with patch.object(ot.httpx, "AsyncClient", return_value=cm):
        assert await ot.measure_plan(f) is None

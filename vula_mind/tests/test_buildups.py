"""Trade costing, phase 2 (14 Nov 2026): build-ups researched once, confirmed by an owner or
manager, then looked up — for every trade. Quantities only ever come from a cited page or a
person; a component without one is listed as unknown, never guessed."""
import json
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula.commerce import buildups, service, trade_checklists
from vula.commerce.boq_template import TRADES

T, OTHER = "digg-demo", "other-builder"
WALL = {"thickness": "220 mm"}
PAGES = [
    {"title": "Brickwork quantities guide", "url": "https://bricks.example/guide",
     "text": "A one-brick (220 mm) wall needs 104 bricks per square metre with 10 mm joints. "
             "Allow 5% for breakages. Mortar: 0.06 m3 per m2, about 1.5 bags of cement."},
    {"title": "Mortar mixes", "url": "https://mortar.example/mix",
     "text": "For class II mortar use 1 bag cement to 0,15 m3 building sand. Brickforce every 4th course."},
]


@pytest.fixture()
def db(monkeypatch):
    d = FakeSupabase({buildups.TABLE: [], buildups.LINES: []})
    monkeypatch.setattr(service, "_client", lambda: d)
    return d


def _answer(*rows):
    return json.dumps([dict(component=c, qty_per_unit=q, unit=u, waste_pct=w, source=s)
                       for c, q, u, w, s in rows])


def _patch_web(monkeypatch, answer, pages=PAGES):
    seen = {}

    async def plan(question, history=""):
        seen["question"] = question
        return ["220 mm brick wall quantities per m2"]
    monkeypatch.setattr("vula.research.plan_queries", plan)
    monkeypatch.setattr("vula.research._search", AsyncMock(return_value=[{"title": "x", "url": "u"}]))
    monkeypatch.setattr("vula.research.read_pages", AsyncMock(return_value=pages))

    async def complete(task, messages, **kw):
        seen["task"], seen["accept"] = task, kw.get("accept")
        return answer
    monkeypatch.setattr("vula.research._complete", complete)
    return seen


# ── the checklists ───────────────────────────────────────────────────────────

def test_every_trade_has_a_checklist_and_no_quantities():
    assert set(trade_checklists.trades()) == set(TRADES)
    for trade in TRADES:
        comps = trade_checklists.checklist_for(trade)
        assert comps and all(c["kind"] in ("material", "labour", "plant") for c in comps)
    masonry = {c["key"] for c in trade_checklists.checklist_for("Masonry")}
    assert {"units", "mortar_cement", "mortar_sand", "brickforce", "labour"} <= masonry
    plaster = {c["key"] for c in trade_checklists.checklist_for("Plastering")}
    assert {"cement", "plaster_sand", "labour"} <= plaster


def test_items_find_their_trade():
    assert trade_checklists.trade_of_item("one brick wall 220 mm") == "Masonry"
    assert trade_checklists.trade_of_item("plaster both sides") == "Plastering"


def test_the_same_item_however_worded_has_one_key():
    assert buildups.item_key("One-brick wall", {"Thickness": "220 mm"}) == \
        buildups.item_key("one brick  WALL", {"thickness": "220mm "}.copy() | {"thickness": "220 mm"})


def test_numbers_as_printed():
    nums = buildups.numbers_in("104 bricks, 0,017 m3, 1 250 kg, 0.5 h")
    assert {Decimal("104"), Decimal("0.017"), Decimal("1250"), Decimal("0.5")} <= nums


# ── grounding ────────────────────────────────────────────────────────────────

def test_only_quantities_printed_on_the_cited_page_are_kept():
    checklist = trade_checklists.checklist_for("Masonry")
    items = [dict(component="units", qty_per_unit=104, unit="each", waste_pct=5, source=1),
             dict(component="mortar_cement", qty_per_unit=1.5, unit="bag", waste_pct=12, source=1),  # 12% not printed
             dict(component="mortar_sand", qty_per_unit=0.15, unit="m3", source=2),
             dict(component="brickforce", qty_per_unit=4.2, unit="m", source=2),        # not printed
             dict(component="wall_ties", qty_per_unit=2, unit="each", source=9),       # no such source
             dict(component="paint", qty_per_unit=1, unit="l", source=1)]              # not in the checklist
    lines, unknown, used = buildups.grounded_lines(items, PAGES, checklist)
    got = {ln["component"]: ln for ln in lines}
    assert set(got) == {"units", "mortar_cement", "mortar_sand"}
    assert got["units"]["waste_pct"] == Decimal("5")
    assert got["mortar_cement"]["waste_pct"] == 0 and "waste not stated" in got["mortar_cement"]["note"]
    assert {"brickforce", "wall_ties", "labour"} <= {u["key"] for u in unknown}
    assert used == {1, 2}


# ── research ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_saves_a_proposed_build_up_with_sources_and_unknowns(db, monkeypatch):
    seen = _patch_web(monkeypatch, _answer(("units", 104, "each", 5, 1), ("mortar_sand", 0.15, "m3", None, 2),
                                           ("brickforce", 3.3, "m", None, 2)))
    b = await buildups.research(T, "One-brick wall", WALL, created_by="27820000000")
    assert seen["task"] == "buildup_research" and "Judy" not in seen["question"]
    assert "per m2 for One-brick wall (220 mm)" in seen["question"]
    assert b["status"] == "proposed" and b["origin"] == "proposed" and b["trade"] == "Masonry"
    assert [ln["component"] for ln in b["lines"]] == ["units", "mortar_sand"]
    assert {"brickforce", "mortar_cement", "labour"} <= {u["key"] for u in b["unknown"]}
    assert [s["n"] for s in b["sources"]] == [1, 2]
    text = buildups.describe(b)
    assert "Bricks or blocks: 104 each + 5% waste [Brickwork quantities guide]" in text
    assert "needs a figure" in text and "waiting for an owner or manager" in text
    # the local model's answer is only accepted when something in it checks out
    assert seen["accept"](_answer(("units", 999, "each", 0, 1))) is False


@pytest.mark.asyncio
async def test_research_that_finds_nothing_checkable_saves_nothing(db, monkeypatch):
    _patch_web(monkeypatch, _answer(("units", 99, "each", 0, 1)))
    with pytest.raises(buildups.BuildupError, match="couldn't find quantities"):
        await buildups.research(T, "One-brick wall", WALL)
    assert db.rows[buildups.TABLE] == []


@pytest.mark.asyncio
async def test_unknown_work_asks_what_it_is(db, monkeypatch):
    _patch_web(monkeypatch, "[]")
    with pytest.raises(buildups.BuildupError, match="which trade"):
        await buildups.research(T, "a nice thing", {})


# ── confirm, versions, sharing ───────────────────────────────────────────────

def _proposed(tenant=T):
    return buildups.save_proposed(tenant, trade="Masonry", item="One-brick wall", spec=WALL, unit="m2",
                                  lines=[{"component": "units", "qty_per_unit": 104, "unit": "each", "waste_pct": 5},
                                         {"component": "mortar_sand", "qty_per_unit": "0.15", "unit": "m3"}],
                                  sources=[{"n": 1, "title": "Guide", "url": "https://x"}],
                                  unknown=[{"key": "labour", "label": "Labour"}])


def test_only_an_owner_or_manager_confirms(db):
    b = _proposed()
    with pytest.raises(buildups.BuildupError, match="owner or manager"):
        buildups.confirm(T, b["id"], person="staff", full_access=False)
    assert buildups.get(T, b["id"])["status"] == "proposed"


def test_confirm_then_find_is_a_lookup(db):
    b = _proposed()
    c = buildups.confirm(T, b["id"], person="judy", full_access=True)
    assert c["id"] == b["id"] and c["status"] == "confirmed" and c["confirmed_by"] == "judy"
    found = buildups.find(T, "one brick wall", {"thickness": "220 mm"})
    assert found["id"] == b["id"] and found["origin"] == "own"


def test_a_change_makes_a_new_version_and_retires_the_old(db):
    b = buildups.confirm(T, _proposed()["id"], person="judy", full_access=True)
    v2 = buildups.confirm(T, b["id"], person="judy", full_access=True,
                          changes=[{"component": "units", "waste_pct": 7},
                                   {"component": "labour", "qty_per_unit": 1.2, "unit": "hour"},
                                   {"component": "mortar_sand", "remove": True}])
    assert v2["id"] != b["id"] and v2["version"] == 2 and v2["based_on"] == b["id"]
    lines = {ln["component"]: ln for ln in v2["lines"]}
    assert set(lines) == {"units", "labour"} and Decimal(lines["units"]["waste_pct"]) == 7
    assert lines["labour"]["kind"] == "labour" and lines["labour"]["source_ref"] == "confirmed by owner"
    assert v2["unknown"] == []                                    # labour now has a figure
    assert buildups.get(T, b["id"])["status"] == "retired"
    assert buildups.find(T, "One-brick wall", WALL)["id"] == v2["id"]


def test_bad_lines_are_refused_in_plain_words(db):
    b = buildups.confirm(T, _proposed()["id"], person="judy", full_access=True)
    with pytest.raises(buildups.BuildupError, match="more than 0"):
        buildups.confirm(T, b["id"], person="judy", full_access=True,
                         changes=[{"component": "units", "qty_per_unit": 0}])
    with pytest.raises(buildups.BuildupError, match="0 to 99"):
        buildups.confirm(T, b["id"], person="judy", full_access=True,
                         changes=[{"component": "units", "waste_pct": 150}])


def test_shared_quantities_never_name_the_business_and_can_be_adopted(db):
    theirs = buildups.confirm(OTHER, _proposed(OTHER)["id"], person="their-owner", full_access=True)
    found = buildups.find(T, "One-brick wall", WALL)
    assert found["origin"] == "shared" and "tenant_id" not in found and "confirmed_by" not in found
    assert all("tenant_id" not in ln for ln in found["lines"])
    assert "confirmed by another Vula business" in buildups.describe(found)
    mine = buildups.confirm(T, theirs["id"], person="judy", full_access=True)
    assert mine["origin"] == "own" and mine["based_on"] == theirs["id"]
    assert buildups.find(T, "One-brick wall", WALL)["id"] == mine["id"]
    assert buildups.get(OTHER, theirs["id"])["status"] == "confirmed"      # theirs untouched


def test_another_business_proposed_build_up_stays_private(db):
    theirs = _proposed(OTHER)
    assert buildups.find(T, "One-brick wall", WALL) is None
    assert buildups.get(T, theirs["id"]) is None


def test_retire(db):
    b = buildups.confirm(T, _proposed()["id"], person="judy", full_access=True)
    with pytest.raises(buildups.BuildupError):
        buildups.retire(T, b["id"], full_access=False)
    assert buildups.retire(T, b["id"], full_access=True) is True
    assert buildups.find(T, "One-brick wall", WALL) is None


# ── the business assistant's tools ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_tools_only_with_the_switch_and_confirm_needs_full_access(db, monkeypatch):
    from core.skills import commerce_admin as ca
    monkeypatch.setattr("vula.features.on", lambda t, name: name == "trade_costing")
    names = {t["function"]["name"] for t in ca._tools_for(T)}
    assert {"trade_buildup", "confirm_buildup"} <= names
    assert ca._TOOL_SCOPE["confirm_buildup"] == "owner_only"
    monkeypatch.setattr("vula.features.on", lambda t, name: False)
    assert "trade_buildup" not in {t["function"]["name"] for t in ca._tools_for(T)}

    skill = ca.CommerceAdminSkill()
    b = _proposed()
    shown = await skill._buildup("trade_buildup", T, {"item": "One-brick wall", "spec": WALL}, {"phone": "1"})
    assert shown["found"] and shown["status"] == "proposed" and "confirm" in shown["next"]
    preview = await skill._buildup("confirm_buildup", T, {"buildup_id": b["id"]}, {"phone": "1"})
    assert preview["preview"] is True and buildups.get(T, b["id"])["status"] == "proposed"
    refused = await skill._buildup("confirm_buildup", T, {"buildup_id": b["id"], "confirm": True},
                                   {"phone": "1", "access": ["projects"]})
    assert "owner or manager" in refused["error"]
    done = await skill._buildup("confirm_buildup", T, {"buildup_id": b["id"], "confirm": True},
                                {"phone": "1", "access": None})
    assert done["confirmed"] and done["verified"] and buildups.get(T, b["id"])["status"] == "confirmed"


# ── the dashboard routes ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routes_are_switched_per_business_and_confirm_is_for_leads(db, monkeypatch):
    from vula.api import trade_costing as api
    monkeypatch.setattr("vula.features.on", lambda t, name: False)
    with pytest.raises(HTTPException) as off:
        await api.list_buildups(T, request=None)
    assert off.value.status_code == 403
    monkeypatch.setattr("vula.features.on", lambda t, name: True)
    b = _proposed()
    monkeypatch.setattr(api, "_caller", AsyncMock(return_value=("staff", False)))
    with pytest.raises(HTTPException) as no:
        await api.confirm_buildup(T, b["id"], api.ConfirmIn(), request=None)
    assert no.value.status_code == 403
    monkeypatch.setattr(api, "_caller", AsyncMock(return_value=("judy", True)))
    ok = await api.confirm_buildup(T, b["id"], api.ConfirmIn(changes=[api.LineChange(component="units", waste_pct=6)]),
                                   request=None)
    assert ok["buildup"]["status"] == "confirmed" and ok["buildup"]["version"] == 2
    listed = await api.list_buildups(T, request=None)
    assert [x["version"] for x in listed["buildups"]] == [2] and listed["can_confirm"] is True
    trades = await api.trades(T)
    assert len(trades["trades"]) == 23

"""Trade costing, phase 4 (15 Nov 2026): Judy's 6 x 4 m wall, plastered both sides, compared with
what was paid on the project — and what Vula learns from it once the work is finished. All in
code; nothing changes until an owner or manager accepts."""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from tests.fake_supabase import FakeSupabase
from vula.commerce import actuals, buildups, estimate, service

T = "digg-demo"
P = "HPC Bokaap"


@pytest.fixture()
def db(monkeypatch):
    d = FakeSupabase({buildups.TABLE: [], buildups.LINES: [], estimate.TABLE: [], actuals.TABLE: [],
                      "vula_price_observations": [],
                      "vula_projects": [{"id": "p1", "tenant_id": T, "name": P, "number": "D-104",
                                         "aliases": ["HPC_Bokaap"], "status": "active"}]})
    monkeypatch.setattr(service, "_client", lambda: d)
    # prices don't matter here — each line gets R1 a unit so it's priced
    monkeypatch.setattr(estimate, "own_price", lambda *a, **k: {"cents_per_unit": Decimal(100), "basis": "paid",
                                                                "label": "x", "date": "2026-11-01", "where": "x"})
    monkeypatch.setattr(estimate, "_compare", lambda *a, **k: None)
    return d


def _confirmed(trade, item, spec, lines, tenant=T):
    b = buildups.save_proposed(tenant, trade=trade, item=item, spec=spec, unit="m2", lines=lines,
                               sources=[{"n": 1, "title": "Guide", "url": "https://guide"}], unknown=[])
    return buildups.confirm(tenant, b["id"], person="judy", full_access=True)


WALL = [{"component": "units", "qty_per_unit": 104, "unit": "each", "waste_pct": 5},
        {"component": "mortar_cement", "qty_per_unit": "0.5", "unit": "bag"},
        {"component": "mortar_sand", "qty_per_unit": "0.06", "unit": "m3"},
        {"component": "labour", "qty_per_unit": "1.5", "unit": "hour"}]
PLASTER = [{"component": "cement", "qty_per_unit": "0.12", "unit": "bag"},
           {"component": "plaster_sand", "qty_per_unit": "0.02", "unit": "m3"},
           {"component": "labour", "qty_per_unit": "0.5", "unit": "hour"}]


@pytest.fixture()
def wall(db):
    return (_confirmed("Masonry", "one-brick wall", {"thickness": "220 mm"}, WALL),
            _confirmed("Plastering", "plaster to brick walls", {}, PLASTER))


def _paid(db, desc, qty, unit, cents, kind="material", project=P, tenant=T, doc="inv-1", src="invoice"):
    from vula.commerce.price_book import norm_key
    db.rows["vula_price_observations"].append({
        "tenant_id": tenant, "doc_id": doc, "source_kind": src, "supplier": "Cashbuild", "project": project,
        "description": desc, "norm_key": norm_key(desc), "unit": unit, "kind": kind, "quantity": qty,
        "unit_price_cents": cents, "observed_on": "2026-11-10"})


async def _estimate(items, project=P):
    return await estimate.estimate(T, items, project=project, refresh=False)


WALL_ONLY = [{"item": "one-brick wall", "spec": {"thickness": "220 mm"}, "quantity_expr": "6 x 4", "unit": "m2"}]
BOTH = WALL_ONLY + [{"item": "plaster to brick walls", "quantity_expr": "6 x 4 x 2", "unit": "m2"}]


def _group(cmp, label):
    return next(g for g in cmp["groups"] if g["label"] == label)


async def test_an_estimate_belongs_to_a_registered_project_only(wall, db):
    est = await _estimate(WALL_ONLY, project="hpc_bokaap")
    assert est["project"] == P and db.rows[estimate.TABLE][0]["project"] == P
    with pytest.raises(estimate.EstimateError, match="can't find a project called \"Nowhere\".*HPC Bokaap"):
        await _estimate(WALL_ONLY, project="Nowhere")


async def test_bricks_bought_far_over_the_build_up_suggest_more_waste(wall, db):
    est = await _estimate(WALL_ONLY)
    # 24 m2 x 104 = 2 496 bricks laid; + 5% waste = 2 620.8 estimated. Bought 3 100:
    # 3 100 / 2 620.8 = +18.3%;  waste = 3 100 / 2 496 - 1 = 24.2% -> 24%
    _paid(db, "Clay stock brick", 3000, "each", 299)
    _paid(db, "Clay stock brick", 100, "each", 299, doc="inv-2")
    cmp = actuals.compare(T, est)
    g = _group(cmp, "Bricks or blocks")
    assert (g["estimated_qty"], g["bought_qty"], g["gap_pct"]) == ("2620.8", "3100", "18.3")
    assert g["paid_cents"] == 3100 * 299 and len(g["lines"]) == 2
    p = next(p for p in actuals.proposals(cmp) if p["label"] == "Bricks or blocks")
    assert (p["verdict"], p["field"], p["current_value"], p["suggested_value"]) == \
        ("suggest", "waste_pct", Decimal("5.00"), Decimal(24))


async def test_a_small_gap_suggests_nothing(wall, db):
    est = await _estimate(WALL_ONLY)
    _paid(db, "Clay stock brick", 2880, "each", 299)          # +9.9%
    p = next(p for p in actuals.proposals(actuals.compare(T, est)) if p["label"] == "Bricks or blocks")
    assert p["verdict"] == "close"


async def test_cement_for_walls_and_plaster_is_a_total_only(wall, db):
    est = await _estimate(BOTH)
    _paid(db, "PPC cement 50kg", 40, "bag", 9999)
    cmp = actuals.compare(T, est)
    g = next(g for g in cmp["groups"] if g["key"] == "material:cement")
    # walls 24 x 0.5 = 12 bags, plaster 48 x 0.12 = 5.76 bags
    assert g["shared"] and g["estimated_qty"] == "17.76" and g["bought_qty"] == "40"
    assert set(g["items"]) == {"one-brick wall", "plaster to brick walls"}
    assert not any(p["group"] == "material:cement" for p in actuals.proposals(cmp))
    assert "compared as a total only" in actuals.summary(cmp, actuals.proposals(cmp), finished=True)


async def test_a_sand_line_that_could_be_either_is_not_counted(wall, db):
    est = await _estimate(BOTH)
    _paid(db, "River sand", 2, "m3", 65000)
    _paid(db, "Plaster sand", 1, "m3", 70000, doc="inv-2")
    cmp = actuals.compare(T, est)
    assert cmp["unclear"][0]["description"] == "River sand"
    assert set(cmp["unclear"][0]["could_be"]) == {"Mortar (building) sand", "Plaster sand"}
    assert _group(cmp, "Plaster sand")["bought_qty"] == "1"
    assert _group(cmp, "Mortar (building) sand")["bought_qty"] is None


def test_a_bag_with_its_size_counts_in_kg_and_days_in_hours():
    assert actuals._bought_qty({"quantity": 10, "unit": "each", "description": "PPC CEMENT 50KG"}, "kg") == \
        (Decimal(500), "10 × 50 kg")
    assert actuals._bought_qty({"quantity": 6, "unit": "day", "description": "General worker"}, "hour")[0] == 48
    qty, why = actuals._bought_qty({"quantity": 3, "unit": "pallet", "description": "Bricks"}, "each")
    assert qty is None and "per pallet" in why


async def test_labour_paid_by_the_day_suggests_hours_and_a_lump_sum_counts_cost_only(wall, db):
    est = await _estimate(WALL_ONLY)
    _paid(db, "General worker labour", 6, "day", 40000, kind="labour")      # 48 h for 36 h estimated
    _paid(db, "Labour brickwork", 1, "sum", 500000, kind="labour", doc="inv-2")
    cmp = actuals.compare(T, est)
    g = _group(cmp, "Labour")
    assert (g["estimated_qty"], g["bought_qty"]) == ("36", "48")
    assert g["paid_cents"] == 6 * 40000 + 500000
    assert "per sum" in g["not_comparable"][0]["why"]
    p = next(p for p in actuals.proposals(cmp) if p["label"] == "Labour")
    assert (p["field"], p["current_value"], p["suggested_value"]) == ("qty_per_unit", Decimal("1.5000"), Decimal("2.00"))


async def test_a_result_that_cant_be_right_is_flagged_not_suggested(wall, db):
    est = await _estimate(WALL_ONLY)
    _paid(db, "Clay stock brick", 4000, "each", 299)          # 60% waste
    p = next(p for p in actuals.proposals(actuals.compare(T, est)) if p["label"] == "Bricks or blocks")
    assert p["verdict"] == "check" and p["implied_waste_pct"] == "60"


async def test_other_work_on_the_project_is_listed_not_counted(wall, db):
    est = await _estimate(WALL_ONLY)
    _paid(db, "Y12 rebar 6m", 20, "each", 15000)
    cmp = actuals.compare(T, est)
    assert cmp["not_in_estimate"][0]["description"] == "Y12 rebar 6m" and cmp["not_in_estimate_cents"] == 300000


async def test_another_business_and_another_project_never_count(wall, db):
    est = await _estimate(WALL_ONLY)
    _paid(db, "Clay stock brick", 9000, "each", 299, tenant="someone-else")
    _paid(db, "Clay stock brick", 9000, "each", 299, project="Porterfield")
    _paid(db, "Clay stock brick", 3100, "each", 299, project="HPC_Bokaap")     # an alias is the same project
    assert _group(actuals.compare(T, est), "Bricks or blocks")["bought_qty"] == "3100"


async def test_a_running_job_shows_progress_and_suggests_nothing(wall, db):
    est = await _estimate(WALL_ONLY)
    _paid(db, "Clay stock brick", 3100, "each", 299)
    cmp = actuals.compare(T, est)
    text = actuals.summary(cmp, actuals.proposals(cmp), finished=False)
    assert "progress only" in text and "suggest waste" not in text
    assert db.rows[actuals.TABLE] == []


async def test_completing_the_project_learns_and_tells_the_owner_once(wall, db, monkeypatch):
    monkeypatch.setattr("vula.features.on", lambda t, n: n == "trade_costing")
    est = await _estimate(WALL_ONLY)
    _paid(db, "Clay stock brick", 3100, "each", 299)
    told = AsyncMock(return_value=1)
    with patch("vula.integrations.notify.notify_team", told):
        assert await actuals.learn_project(T, P) == 1
        assert await actuals.learn_project(T, P) == 0               # nothing new the second time
    assert told.await_count == 1
    assert "waste 5% → 24%" in told.await_args.args[2]
    rows = db.rows[actuals.TABLE]
    assert len(rows) == 1 and rows[0]["status"] == "pending" and rows[0]["estimate_id"] == est["id"]


async def test_staff_can_neither_finish_nor_accept(wall, db):
    est = await _estimate(WALL_ONLY)
    _paid(db, "Clay stock brick", 3100, "each", 299)
    with pytest.raises(actuals.ActualsError, match="owner or manager"):
        await actuals.learn(T, est["id"], person="sipho", full_access=False)
    res = await actuals.learn(T, est["id"], person="judy", full_access=True, notify=False)
    sid = res["suggestions"][0]["id"]
    with pytest.raises(actuals.ActualsError, match="owner or manager"):
        actuals.accept(T, sid, person="sipho", full_access=False)


async def test_accepting_makes_a_new_version_with_that_one_change(wall, db):
    old = wall[0]
    est = await _estimate(WALL_ONLY)
    _paid(db, "Clay stock brick", 3100, "each", 299)
    res = await actuals.learn(T, est["id"], person="judy", full_access=True, notify=False)
    new = actuals.accept(T, res["suggestions"][0]["id"], person="judy", full_access=True)
    assert new["id"] != old["id"] and new["based_on"] == old["id"] and new["origin"] == "own"
    by = {ln["component"]: ln for ln in new["lines"]}
    assert Decimal(by["units"]["waste_pct"]) == 24 and Decimal(by["units"]["qty_per_unit"]) == 104
    assert Decimal(by["mortar_cement"]["qty_per_unit"]) == Decimal("0.5")
    assert buildups.get(T, old["id"])["status"] == "retired"
    assert buildups.find(T, "one-brick wall", {"thickness": "220 mm"})["id"] == new["id"]
    assert db.rows[actuals.TABLE][0]["status"] == "accepted"
    with pytest.raises(actuals.ActualsError, match="already accepted"):
        actuals.accept(T, res["suggestions"][0]["id"], person="judy", full_access=True)


async def test_dismissed_is_never_suggested_again(wall, db):
    est = await _estimate(WALL_ONLY)
    _paid(db, "Clay stock brick", 3100, "each", 299)
    res = await actuals.learn(T, est["id"], person="judy", full_access=True, notify=False)
    assert actuals.dismiss(T, res["suggestions"][0]["id"], person="judy", full_access=True)
    again = await actuals.learn(T, est["id"], person="judy", full_access=True, notify=False)
    assert again["new"] == 0 and again["suggestions"] == []
    assert len(db.rows[actuals.TABLE]) == 1


async def test_a_shared_build_up_is_never_changed(db):
    theirs = _confirmed("Masonry", "one-brick wall", {"thickness": "220 mm"}, WALL, tenant="other-builder")
    est = await _estimate(WALL_ONLY)
    assert est["items"][0]["buildup_origin"] == "shared"
    _paid(db, "Clay stock brick", 3100, "each", 299)
    res = await actuals.learn(T, est["id"], person="judy", full_access=True, notify=False)
    new = actuals.accept(T, res["suggestions"][0]["id"], person="judy", full_access=True)
    assert new["tenant_id"] == T and new["origin"] == "own"
    back = buildups.get("other-builder", theirs["id"])
    assert back["status"] == "confirmed"
    assert Decimal(next(ln for ln in back["lines"] if ln["component"] == "units")["waste_pct"]) == 5


async def test_an_estimate_without_a_project_says_so(wall, db):
    est = await _estimate(WALL_ONLY, project=None)
    with pytest.raises(actuals.ActualsError, match="isn't tied to a project"):
        actuals.compare(T, est)


# ── chat, routes, and the project being completed ────────────────────────────

async def test_chat_tools_compare_then_decide_owner_only(wall, db, monkeypatch):
    from core.skills import commerce_admin as ca
    monkeypatch.setattr("vula.features.on", lambda t, name: name == "trade_costing")
    names = {t["function"]["name"] for t in ca._tools_for(T)}
    assert {"compare_estimate", "buildup_suggestion"} <= names
    assert ca._TOOL_SCOPE["compare_estimate"] == ca._TOOL_SCOPE["buildup_suggestion"] == "owner_only"
    await _estimate(WALL_ONLY)
    _paid(db, "Clay stock brick", 3100, "each", 299)
    skill = ca.CommerceAdminSkill()
    progress = await skill._actuals("compare_estimate", T, {"project": "hpc bokaap"}, {"phone": "1", "access": None})
    assert "progress only" in progress["reply_verbatim"] and db.rows[actuals.TABLE] == []
    staff = await skill._actuals("compare_estimate", T, {"project": P, "finished": True},
                                 {"phone": "2", "access": ["projects"]})
    assert "progress only" in staff["reply_verbatim"] and db.rows[actuals.TABLE] == []
    done = await skill._actuals("compare_estimate", T, {"project": P, "finished": True}, {"phone": "1", "access": None})
    assert "waste 5% → 24%" in done["reply_verbatim"] and "nothing changes until you do" in done["reply_verbatim"]
    sid = done["suggestions"][0]["id"]
    preview = await skill._actuals("buildup_suggestion", T, {"action": "accept", "suggestion_id": sid},
                                   {"phone": "1", "access": None})
    assert preview["preview"] and db.rows[actuals.TABLE][0]["status"] == "pending"
    refused = await skill._actuals("buildup_suggestion", T, {"action": "accept", "suggestion_id": sid, "confirm": True},
                                   {"phone": "2", "access": ["projects"]})
    assert "owner or manager" in refused["error"]
    ok = await skill._actuals("buildup_suggestion", T, {"action": "accept", "suggestion_id": sid, "confirm": True},
                              {"phone": "1", "access": None})
    assert ok["accepted"] and ok["verified"] and "24% waste" in ok["reply_verbatim"]


async def test_routes(wall, db, monkeypatch):
    from fastapi import HTTPException

    from vula.api import trade_costing as api
    monkeypatch.setattr("vula.features.on", lambda t, name: True)
    monkeypatch.setattr(api, "_caller", AsyncMock(return_value=("judy", True)))
    made = await api.make_estimate(T, api.EstimateIn(items=[api.EstimateItem(item="one-brick wall",
                                   spec={"thickness": "220 mm"}, quantity_expr="6 x 4")], project="D-104",
                                   research=False), request=None)
    eid = made["estimate"]["id"]
    assert made["estimate"]["project"] == P
    assert (await api.list_estimates(T))["estimates"][0]["project"] == P
    _paid(db, "Clay stock brick", 3100, "each", 299)
    got = await api.estimate_actuals(T, eid)
    assert got["finished"] is False and got["suggestions"] == [] and "progress only" in got["text"]
    monkeypatch.setattr(api, "_caller", AsyncMock(return_value=("sipho", False)))
    with pytest.raises(HTTPException) as no:
        await api.estimate_learn(T, eid, request=None)
    assert no.value.status_code == 403
    monkeypatch.setattr(api, "_caller", AsyncMock(return_value=("judy", True)))
    learned = await api.estimate_learn(T, eid, request=None)
    sid = learned["suggestions"][0]["id"]
    assert [s["id"] for s in (await api.buildup_suggestions(T))["suggestions"]] == [sid]
    ok = await api.decide_suggestion(T, sid, "accept", request=None)
    assert ok["buildup"]["version"] == 2
    assert (await api.buildup_suggestions(T))["suggestions"] == []
    with pytest.raises(HTTPException) as again:
        await api.decide_suggestion(T, sid, "dismiss", request=None)
    assert again.value.status_code == 400 and "already accepted" in again.value.detail


async def test_marking_the_project_complete_starts_the_learning(db, monkeypatch):
    from vula.api import projects
    started = []
    monkeypatch.setattr("vula.commerce.background_tasks.run_background",
                        lambda t, label, coro: (started.append((t, label)), coro.close()))
    await projects.update_project(T, "p1", projects.ProjectPatch(status="complete"))
    assert started == [(T, "estimate_learn")]
    await projects.update_project(T, "p1", projects.ProjectPatch(status="complete"))   # already complete
    assert len(started) == 1

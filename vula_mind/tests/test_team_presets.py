"""Preset teams: the tabs each job needs, teams as groups, and never a notification switched on."""
import asyncio

import pytest
from fastapi import HTTPException

from vula import team_presets as tp


@pytest.mark.parametrize("words,team", [
    ("sales", "sales"), ("the Sales team", "sales"), ("reps", "sales"), ("bookkeeper", "bookkeeping"),
    ("accounts", "bookkeeping"), ("stock", "operations"), ("Front desk", "admin"), ("site", "field"),
    ("marketing", "marketing"), ("manager", None), ("", None),
])
def test_find(words, team):
    assert tp.find(words) == team


def test_presets_give_tabs_but_never_notifications():
    for t in tp.presets():
        assert t["access"] and "notify" not in t        # suggested_notify is a hint only
    assert tp.access_for("bookkeeping", "staff", ["inbox"]) == tp.TEAMS["bookkeeping"]["access"]


def test_a_sales_rep_keeps_their_my_work_tabs():
    out = tp.access_for("sales", "sales_rep", ["rep-contacts", "rep-callsheet", "inbox"])
    assert out[:2] == ["rep-contacts", "rep-callsheet"] and "customers" in out and out.count("inbox") == 1


# ── the team API ──────────────────────────────────────────────────────────────────────────

class _T:
    def __init__(self, store):
        self.store, self.op, self.f, self.payload = store, "select", [], None

    def select(self, *_): return self
    def limit(self, *_): return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def upsert(self, row, **k): self.op, self.payload = "upsert", row; return self
    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self

    def execute(self):
        if self.op == "upsert":
            self.store.append({"id": "new", **self.payload})
            return type("R", (), {"data": [self.store[-1]]})()
        rows = [r for r in self.store if all(f(r) for f in self.f)]
        if self.op == "update":
            for r in rows:
                r.update(self.payload)
        return type("R", (), {"data": rows})()


@pytest.fixture
def team_api(monkeypatch):
    from vula.api import team as api
    store = [{"id": "m1", "tenant_id": "t1", "name": "Sipho", "role": "staff", "access": ["inbox"], "notify": []}]
    monkeypatch.setattr(api, "_client", lambda: type("C", (), {"table": lambda s, n: _T(store)})())
    monkeypatch.setattr(api.merchant_audit, "audit", lambda *a, **k: None)
    monkeypatch.setattr(api._team_index, "invalidate", lambda *a: None)

    async def no_birthday(*a): return None
    monkeypatch.setattr(api, "_ask_new_member_birthday", no_birthday)
    return api, store


def test_putting_someone_in_a_team_gives_its_tabs_when_asked(team_api):
    api, store = team_api
    asyncio.run(api.update_member("t1", "m1", api.MemberPatch(team="bookkeeping", apply_team_access=True), identity={}))
    assert store[0]["team"] == "bookkeeping" and store[0]["access"] == tp.TEAMS["bookkeeping"]["access"]
    assert store[0]["notify"] == []                                     # notifications untouched


def test_changing_team_without_tabs_keeps_their_tabs(team_api):
    api, store = team_api
    asyncio.run(api.update_member("t1", "m1", api.MemberPatch(team="sales"), identity={}))
    assert store[0]["team"] == "sales" and store[0]["access"] == ["inbox"]
    asyncio.run(api.update_member("t1", "m1", api.MemberPatch(team=""), identity={}))
    assert store[0]["team"] is None


def test_unknown_team_is_refused(team_api):
    api, _ = team_api
    with pytest.raises(HTTPException):
        asyncio.run(api.update_member("t1", "m1", api.MemberPatch(team="pirates"), identity={}))


def test_adding_someone_to_a_team(team_api):
    api, store = team_api
    asyncio.run(api.add_member("t1", api.MemberIn(name="Lindi", whatsapp="0820000003", team="sales"), identity={}))
    assert store[-1]["team"] == "sales" and store[-1]["access"] == tp.TEAMS["sales"]["access"] and store[-1]["notify"] == []


# ── onboarding, activity and notices ──────────────────────────────────────────────────────

def test_onboarding_takes_a_team():
    from vula.onboard_wa import parse_member
    m = parse_member("Sipho, 083 765 4321, sales")
    assert (m["name"], m["role"], m["team"]) == ("Sipho", "staff", "sales") and "customers" in m["access"]
    m = parse_member("Thandi, 082 123 4567, manager")
    assert m["role"] == "manager" and "team" not in m and m["name"] == "Thandi"


def _members(monkeypatch):
    import vula.team_index as team_index
    team = [{"id": "a", "name": "Ann", "whatsapp": "27820000011", "role": "staff", "team": "sales"},
            {"id": "b", "name": "Ben", "whatsapp": "27820000012", "role": "staff", "team": "bookkeeping"},
            {"id": "c", "name": "Cas", "whatsapp": "27820000013", "role": "staff", "team": "sales"}]
    monkeypatch.setattr(team_index, "active_members", lambda tid: team)
    return team


def test_team_activity_for_one_team(monkeypatch):
    from vula import team_activity as ta
    _members(monkeypatch)
    assert [m["name"] for m in ta.members("t1", team="sales")] == ["Ann", "Cas"]


def test_staff_notice_to_one_team(monkeypatch):
    from vula import staff_notices as sn
    team = _members(monkeypatch)
    assert [m["name"] for m in sn.audience("t1", {"teams": ["sales"]}, team=team)] == ["Ann", "Cas"]


def test_what_did_the_sales_team_do(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    from vula import team_activity as ta
    _members(monkeypatch)
    import vula.team_index as team_index
    monkeypatch.setattr(team_index, "member_for_phone", lambda tid, p: {"role": "owner", "access": []})
    seen = {}

    def summary(tid, a, b, role=None, now=None, team=None):
        seen["team"] = team
        return []
    monkeypatch.setattr(ta, "team_summary", summary)
    out = CommerceAdminSkill()._team_activity("t1", {"action": "team", "team": "the sales team"}, {"phone": "27820000001"})
    assert seen["team"] == "sales" and "Sales team" in out["reply_verbatim"]
    out = CommerceAdminSkill()._team_activity("t1", {"action": "team", "team": "pirates"}, {"phone": "27820000001"})
    assert out["status"] == "need_info" and "Which team?" in out["message"]

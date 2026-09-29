"""Judy (DIGG), 29 Sep voice note: run Atlantis Foods from its programme — a tight four-week job —
against the signed Cost Estimate Rev10; staff get their tasks for the day per room on WhatsApp;
anything outside Rev10 is flagged for a variation order. Rev10 figures are the real filed ones."""
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import project_programme as pp

REV10 = {"total_cents": 59302200, "vat_cents": 7735100, "line_items": [
    {"description": "Reception (12m²)", "total_cents": 7644000},
    {"description": "Seating area (20m²)", "total_cents": 1360000},
    {"description": "Small Meeting Room A (10m²)", "total_cents": 6223400},
    {"description": "Small Meeting Room B (10m²)", "total_cents": 4634800},
    {"description": "Main Boardroom (47m²)", "total_cents": 8822500},
    {"description": "New Office A (10m²)", "total_cents": 2777500},
    {"description": "New Office B (10m²)", "total_cents": 2777500},
    {"description": "Open Plan (33m²)", "total_cents": 1254000},
    {"description": "Bathrooms (8m²)", "total_cents": 0},
    {"description": "Coffee station (4m²)", "total_cents": 0},
    {"description": "Preliminaries & General (P&Gs;) — 4 weeks × R18,000/week", "total_cents": 7200000}]}


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.op, self.payload = db, name, [], "select", None
        self._limit = None

    def select(self, *_a):
        return self

    def eq(self, k, v):
        self.f.append(lambda r: r.get(k) == v)
        return self

    def ilike(self, k, pat):
        needle = pat.strip("%").lower()
        self.f.append(lambda r: needle in str(r.get(k) or "").lower())
        return self

    def gte(self, k, v):
        self.f.append(lambda r: str(r.get(k) or "") >= v)
        return self

    def in_(self, k, vals):
        self.f.append(lambda r: r.get(k) in vals)
        return self

    @property
    def not_(self):
        outer = self

        class _N:
            def in_(self, k, vals):
                outer.f.append(lambda r: r.get(k) not in vals)
                return outer
        return _N()

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows if isinstance(rows, list) else [rows]
        return self

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def upsert(self, row, on_conflict=None, **_k):
        self.op, self.payload, self.key = "upsert", row, (on_conflict or "").split(",")
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        hit = [r for r in rows if all(f(r) for f in self.f)]
        if self.op == "insert":
            import uuid
            for r in self.payload:
                r.setdefault("id", uuid.uuid4().hex)        # as Postgres does
            rows.extend(dict(r) for r in self.payload)
            return type("R", (), {"data": self.payload})()
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return type("R", (), {"data": hit})()
        if self.op == "upsert":
            same = [r for r in rows if all(r.get(k) == self.payload.get(k) for k in self.key)]
            if same:
                same[0].update(self.payload)
            else:
                rows.append(dict(self.payload))
            return type("R", (), {"data": [self.payload]})()
        if self.op == "delete":
            for r in hit:
                rows.remove(r)
            return type("R", (), {"data": hit})()
        return type("R", (), {"data": [dict(r) for r in hit[: self._limit or None]]})()


class _DB:
    def __init__(self):
        self.t = {}

    def table(self, name):
        return _Q(self.t, name)


@pytest.fixture
def db(monkeypatch):
    d = _DB()
    d.t["vula_filed_documents"] = [
        {"id": "rev10", "tenant_id": "digg-demo", "doc_id": "cfa45f7875425861", "project": "ATLANTIS FOODS",
         "filename": "Atlantis_Foods_Paarden_Island_Office_Cost_Estimate_Rev10.pdf", "fields": REV10,
         "created_at": "2026-09-07T19:40:47+00:00"}]
    monkeypatch.setattr("vula.commerce.service._client", lambda: d)
    monkeypatch.setattr("vula.commerce.service.canonical_project", lambda _t, n: n)
    return d


def test_rev10_becomes_per_room_budgets_that_add_up_to_the_signed_total():
    b = pp.baseline_sections(REV10)
    assert b["total_excl_cents"] == 51567100 and b["total_incl_cents"] == 59302200
    assert sum(s["budget_cents"] for s in b["sections"]) == 51567100
    rooms = [s["section"] for s in b["sections"]]
    assert rooms[:3] == ["Reception", "Seating area", "Small Meeting Room A"]
    pg = next(s for s in b["sections"] if s.get("pg_weeks"))
    assert (pg["pg_weeks"], pg["pg_week_cents"]) == (4, 1800000)
    assert b["sections"][-1] == {"section": pp.REMAINDER, "budget_cents": 51567100 - 42693700}


def test_the_signed_baseline_is_locked_and_a_later_estimate_never_replaces_it(db):
    from vula.commerce.service import upsert_project_boq
    got = pp.set_baseline("digg-demo", "ATLANTIS FOODS", "Rev10.pdf")
    assert got["document"].endswith("Rev10.pdf") and got["total_excl_cents"] == 51567100
    row = pp.baseline("digg-demo", "ATLANTIS FOODS")
    assert row["baseline_locked"] and row["baseline_doc_id"] == "rev10" and row["total_cents"] == 51567100
    upsert_project_boq("digg-demo", "ATLANTIS FOODS", 99900000, title="Rev11 draft", sections=[])
    assert pp.baseline("digg-demo", "ATLANTIS FOODS")["total_cents"] == 51567100


def test_a_programme_read_in_the_wrong_year_is_put_right():
    tasks = pp.normalise_tasks([
        {"task": "Skim and paint walls", "room": "Reception", "trade": "Painters", "assignee": "Sipho",
         "start": "2020-10-05", "end": "2020-10-07"},
        {"task": "", "room": "Open Plan"},
        {"task": "Lay carpet tiles", "room": None, "start": "2026-10-20", "end": "2026-10-16"}], 2026)
    assert tasks[0]["start"] == "2026-10-05" and tasks[0]["end"] == "2026-10-07"
    assert len(tasks) == 2 and tasks[1]["room"] == "General"
    assert (tasks[1]["start"], tasks[1]["end"]) == ("2026-10-16", "2026-10-20")


def test_import_links_named_staff_and_lists_who_needs_a_number(db, monkeypatch):
    monkeypatch.setattr(pp, "_people", lambda _t: [
        {"id": "c1", "name": "Sipho Ndlovu", "phone": "27820000001", "kind": "site"},
        {"id": None, "name": "Judy Downing", "phone": "27827077080", "kind": "team"}])
    tasks = pp.normalise_tasks([
        {"task": "Skim and paint walls", "room": "Reception", "trade": "Painters", "assignee": "Sipho",
         "start": "2026-10-05", "end": "2026-10-07"},
        {"task": "Tile splashback", "room": "Coffee station", "trade": "Tilers", "assignee": "Edison",
         "start": "2026-10-06", "end": "2026-10-06"}], 2026)
    doc = {"id": "gantt1", "filename": "Atlantis Programme.pdf"}
    got = pp.import_programme("digg-demo", "ATLANTIS FOODS", doc, tasks)
    assert got["tasks"] == 2 and got["needs_number"] == ["Edison"]
    assert got["rooms"] == ["Coffee station", "Reception"]
    rows = {r["title"]: r for r in db.t["vula_field_tasks"]}
    assert rows["Skim and paint walls"]["assigned_to"] == "c1" and rows["Skim and paint walls"]["room"] == "Reception"
    # a newer programme replaces the open tasks, and never duplicates them
    pp.import_programme("digg-demo", "ATLANTIS FOODS", doc, tasks[:1])
    assert len(db.t["vula_field_tasks"]) == 1


def test_today_per_room_and_overdue():
    tasks = [
        {"title": "Skim walls", "room": "Reception", "start_date": "2026-10-05", "due_date": "2026-10-07", "status": "pending"},
        {"title": "Paint ceiling", "room": "Main Boardroom", "start_date": "2026-10-06", "due_date": "2026-10-06", "status": "pending"},
        {"title": "Strip out", "room": "Open Plan", "start_date": "2026-10-01", "due_date": "2026-10-03", "status": "pending"},
        {"title": "Demolish wall", "room": "Open Plan", "start_date": "2026-10-01", "due_date": "2026-10-02", "status": "complete"}]
    plan = pp.day_plan(tasks, date(2026, 10, 6))
    assert [t["title"] for t in plan["today"]] == ["Paint ceiling", "Skim walls"]
    assert [t["title"] for t in plan["overdue"]] == ["Strip out"]
    msg = pp.staff_message("Sipho Ndlovu", "ATLANTIS FOODS", date(2026, 10, 6), plan["today"])
    assert "Morning Sipho" in msg and "*Main Boardroom*\n• Paint ceiling" in msg and "DONE" in msg


def test_owner_summary_flags_cost_time_and_variations():
    plan = {"today": [{"title": "Skim walls", "room": "Reception", "assignee_name": "Sipho"}],
            "overdue": [{"title": "Strip out", "room": "Open Plan", "assignee_name": "Edison"}]}
    cost = {"budget_cents": 51567100, "spent_cents": 38000000, "left_cents": 13567100, "over": False,
            "spent_pct": 74, "programme_pct": 50, "days_left": 14,
            "pg": {"weeks_in": 5, "weeks": 4, "over_time": True, "week_cents": 1800000},
            "variations": [{"filename": "Quote extra bulkhead.pdf", "amount_cents": 1250000, "vo": None}]}
    msg = pp.owner_message("ATLANTIS FOODS", date(2026, 10, 6), plan, cost, ["Edison"])
    assert "Overdue — 1" in msg and "Strip out — Edison" in msg
    assert "R515,671 baseline excl. VAT" in msg and "Spending ahead of the programme" in msg
    assert "week 5 of 4 priced" in msg and "R18,000" in msg
    assert "Needs a variation order" in msg and "Quote extra bulkhead.pdf — R12,500" in msg
    assert "add staff" in msg


@pytest.mark.parametrize("text,want", [
    ("add staff Sipho Ndlovu 082 000 0001 painter", {"name": "Sipho Ndlovu", "phone": "0820000001", "trade": "painter"}),
    ("Add site staff Edison 27710000000", {"name": "Edison", "phone": "27710000000", "trade": ""}),
    ("Can you add staff to the programme?", None),
])
def test_add_staff_command(text, want):
    assert pp.parse_add_staff(text) == want


@pytest.mark.asyncio
async def test_morning_briefs_send_once_per_person_per_day(db, monkeypatch):
    db.t["vula_field_tasks"] = [
        {"tenant_id": "digg-demo", "project_id": "ATLANTIS FOODS", "source": "programme", "title": "Skim walls",
         "room": "Reception", "start_date": "2026-10-05", "due_date": "2026-10-07", "status": "pending",
         "assigned_to": "c1", "assignee_name": "Sipho"},
        {"tenant_id": "digg-demo", "project_id": "ATLANTIS FOODS", "source": "programme", "title": "Tile splashback",
         "room": "Coffee station", "start_date": "2026-10-06", "due_date": "2026-10-06", "status": "pending",
         "assigned_to": "", "assignee_name": "Edison"}]
    monkeypatch.setattr(pp, "_people", lambda _t: [{"id": "c1", "name": "Sipho", "phone": "27820000001", "kind": "site"}])
    monkeypatch.setattr(pp, "_owners", lambda _t: ["27827077080"])
    monkeypatch.setattr(pp, "cost_position", lambda *a, **k: None)
    send = AsyncMock(return_value=True)
    with patch("vula.api.whatsapp._send_reply", send):
        out = await pp.morning_briefs("digg-demo", date(2026, 10, 6))
    keys = sorted(c.kwargs["idem_key"] for c in send.call_args_list)
    assert keys == ["programme-owner:ATLANTIS FOODS:2026-10-06:27827077080",
                    "programme:ATLANTIS FOODS:2026-10-06:27820000001"]
    assert "Edison" in out["projects"][0]["owner"]            # no number yet → owner is told


# ── ClickUp programmes — Belladonna's real "Work Programme" list (29 Sep) ─────

BELLADONNA = [
    {"name": "Site Establishment & Demolition", "status": "to do", "start_date": None, "due_date": "2026-09-02",
     "assignees": [], "list": "Work Programme"},
    {"name": "Plumbing First Fix (Edison)", "status": "to do", "start_date": None, "due_date": "2026-09-11",
     "assignees": [], "list": "Work Programme"},
    {"name": "Electrical First Fix (Elyas)", "status": "to do", "start_date": None, "due_date": "2026-09-11",
     "assignees": [], "list": "Work Programme"},
    {"name": "Tiling & Waterproofing (Tiling Team)", "status": "to do", "start_date": None, "due_date": "2026-09-18",
     "assignees": [], "list": "Work Programme"},
    {"name": "Electrical & Plumbing Second Fix (Elyas / Edison)", "status": "to do", "start_date": None,
     "due_date": "2026-09-25", "assignees": [], "list": "Work Programme"},
    {"name": "📎 Project Documents", "status": "to do", "start_date": None, "due_date": None,
     "assignees": [], "list": "Work Programme"},
    {"name": "Kitchen units", "status": "complete", "closed": True, "start_date": "2026-09-20",
     "due_date": "2026-09-22", "assignees": ["Edward Mokoena"], "list": "Work Programme"},
]


def test_a_clickup_programme_becomes_daily_tasks():
    tasks = pp.normalise_tasks(pp.tasks_from_clickup(BELLADONNA), 2026)
    by = {t["title"]: t for t in tasks}
    assert "📎 Project Documents" not in by
    assert by["Plumbing First Fix"]["assignee"] == "Edison"
    # only a due date: the phase runs from the day after the previous due date
    assert (by["Plumbing First Fix"]["start"], by["Plumbing First Fix"]["end"]) == ("2026-09-03", "2026-09-11")
    assert by["Tiling & Waterproofing"]["start"] == "2026-09-12"
    assert by["Site Establishment & Demolition"]["start"] == "2026-09-02"
    assert by["Kitchen units"]["assignee"] == "Edward Mokoena" and by["Kitchen units"]["done"]
    assert pp.split_names("Elyas / Edison") == ["Elyas", "Edison"]


def test_two_people_on_one_task_each_get_it(db, monkeypatch):
    monkeypatch.setattr(pp, "_people", lambda _t: [
        {"id": "e1", "name": "Elyas", "phone": "27820000002", "kind": "site"},
        {"id": "e2", "name": "Edison", "phone": "27820000003", "kind": "site"}])
    tasks = pp.normalise_tasks(pp.tasks_from_clickup(BELLADONNA), 2026)
    got = pp.import_programme("digg-demo", "Belladonna", {"id": "clickup:901220795562"}, tasks)
    second = [r for r in db.t["vula_field_tasks"] if r["title"] == "Electrical & Plumbing Second Fix"]
    assert sorted(r["assigned_to"] for r in second) == ["e1", "e2"]
    assert got["needs_number"] == ["Edward Mokoena", "Tiling Team"]
    done = [r for r in db.t["vula_field_tasks"] if r["title"] == "Kitchen units"]
    assert done[0]["status"] == "complete"


def test_which_clickup_lists_are_the_programme(monkeypatch):
    lists = [("a1", "Team Space / ATLANTIS FOODS / Atlantis Branch — Interior Design & Concept"),
             ("a2", "Team Space / ATLANTIS FOODS / Paarden Island Branch — Legalisation & Council Submissions"),
             ("b1", "Team Space / Belladonna / Work Programme"),
             ("b2", "Team Space / Belladonna / Procurement Schedule"),
             ("h1", "Team Space / HPC_Bokaap / Phase 1 — Site Establishment & Demolition"),
             ("h2", "Team Space / HPC_Bokaap / Phase 2 — Builder's Work First Fix"),
             ("h3", "Team Space / HPC_Bokaap / Phase 3 — Acoustic Partitions & Glazed Screens")]
    monkeypatch.setattr("vula.integrations.doc_filing._clickup_candidates", lambda _t: lists)
    assert [l for l, _ in pp.clickup_programme_lists("digg-demo", "Belladonna")] == ["b1"]
    assert pp.clickup_programme_lists("digg-demo", "ATLANTIS FOODS") == []       # design + council only
    assert [l for l, _ in pp.clickup_programme_lists("digg-demo", "HPC_Bokaap")] == ["h1", "h2", "h3"]


@pytest.mark.parametrize("text,want", [
    ("programme Belladonna", "Belladonna"),
    ("Start the programme for Belladonna from ClickUp", "Belladonna"),
    ("What's on the programme for Belladonna today?", None),
    ("programme Nowhere", None),
])
def test_start_programme_command(text, want, monkeypatch):
    monkeypatch.setattr(pp, "running_projects", lambda _t: ["Belladonna", "HPC Bokaap"])
    assert pp.parse_start_programme(text, "digg-demo") == want


# ── Judy sets it up herself on WhatsApp (her voice note, 29 Sep) ──────────────

VOICE_NOTE = ("Linear approach, so input data is ClickUp for project program or any Gantt. It's a four-week "
              "program, very tight, and connected to cost, so need to track that daily. We also need to task "
              "the staff, as per setup in ClickUp … what is the tasks due for the day per room. For costing, "
              "we signed cost revision 10, and that was the invoice that was made out to the client. All "
              "project costs need to be managed against that. Any variation to it need to be flagged with a "
              "variation order.")

REV10_NO_RECEPTION = dict(REV10, total_cents=48313900)


@pytest.fixture
def digg_docs(db, monkeypatch):
    db.t["vula_filed_documents"] += [
        {"id": "rev10nr", "tenant_id": "digg-demo", "project": "ATLANTIS FOODS", "fields": REV10_NO_RECEPTION,
         "filename": "Atlantis_Foods_Paarden_Island_Office_Cost_Estimate_Rev10_No_Reception.pdf",
         "created_at": "2026-09-07T19:40:34+00:00"},
        {"id": "rev07", "tenant_id": "digg-demo", "project": "ATLANTIS FOODS", "fields": REV10,
         "filename": "Atlantis_Foods_Paarden_Island_Office_Cost_Estimate_Rev07 (1).pdf",
         "created_at": "2026-09-02T10:29:24+00:00"},
        {"id": "hpc10", "tenant_id": "digg-demo", "project": "HPC Bokaap", "fields": REV10,
         "filename": "HPC_Estimate_Rev10.pdf", "created_at": "2026-08-01T10:00:00+00:00"}]
    db.t["vula_projects"] = [{"tenant_id": "digg-demo", "name": n, "status": "active"}
                             for n in ("HPC Bokaap", "Porterfield", "Breco Seafoods", "Belladonna",
                                       "Atlantis Paarden Eiland")]
    monkeypatch.setattr("vula.integrations.doc_filing._clickup_candidates", lambda _t: [
        ("a1", "Team Space / ATLANTIS FOODS / Atlantis Branch — Interior Design & Concept"),
        ("a2", "Team Space / ATLANTIS FOODS / Paarden Island Branch — Legalisation & Council Submissions"),
        ("b1", "Team Space / Belladonna / Work Programme")])
    return db


def test_her_words_find_the_project_and_the_signed_document(digg_docs):
    assert pp.find_project("digg-demo", "Atlantis Paarden Island project") == "Atlantis Paarden Eiland"
    assert pp.find_project("digg-demo", "belladonna") == "Belladonna"
    docs = pp.find_baseline_documents("digg-demo", "Atlantis Paarden Eiland", "cost revision 10")
    assert [d["id"] for d in docs] == ["rev10", "rev10nr"]          # not Rev07, not HPC's Rev10


def test_the_plan_says_what_will_happen_and_offers_both_rev10s(digg_docs):
    plan = pp.plan_setup("digg-demo", {"project": "Atlantis Paarden Island", "programme_from": "clickup",
                                       "baseline_document": "cost revision 10"})
    assert plan["project"] == "Atlantis Paarden Eiland" and not plan["create"]
    assert [o["doc_id"] for o in plan["options"]] == ["rev10", "rev10nr"]
    text = pp.plan_text(plan)
    assert "no programme list" in text                       # ClickUp has only design + council lists
    assert "R593,022 incl. VAT" in text and "R483,139 incl. VAT" in text
    assert "variation order" in text and "Nothing changes until you confirm" in text


def test_a_paarden_island_programme_list_in_the_client_folder_is_found(digg_docs, monkeypatch):
    monkeypatch.setattr("vula.integrations.doc_filing._clickup_candidates", lambda _t: [
        ("a2", "Team Space / ATLANTIS FOODS / Paarden Island Branch — Legalisation & Council Submissions"),
        ("a3", "Team Space / ATLANTIS FOODS / Paarden Island Branch — Work Programme")])
    assert [l for l, _ in pp.clickup_programme_lists("digg-demo", "Atlantis Paarden Eiland")] == ["a3"]


@pytest.mark.asyncio
async def test_confirming_locks_the_baseline_and_says_so(digg_docs):
    got = await pp.apply_setup("digg-demo", {"project": "Atlantis Paarden Eiland", "baseline_doc_id": "rev10"})
    assert "✅ Cost baseline locked" in got["reply"] and "R515,671 excl. VAT" in got["reply"]
    assert pp.baseline("digg-demo", "Atlantis Paarden Eiland")["baseline_locked"]
    assert not got["problems"]


@pytest.mark.asyncio
async def test_only_the_owner_can_set_up_and_the_buttons_offer_each_document(digg_docs, monkeypatch):
    from vula.api import whatsapp as wa
    monkeypatch.setattr(pp, "read_setup_instruction", AsyncMock(return_value={
        "is_project_setup": True, "project": "Atlantis Paarden Island", "programme_from": "clickup",
        "baseline_document": "cost revision 10"}))
    monkeypatch.setattr(wa, "_caller_identity", lambda t, p: ("Sipho", "staff"))
    assert not await wa._maybe_project_setup("27820000001", VOICE_NOTE, "digg-demo")
    monkeypatch.setattr(wa, "_caller_identity", lambda t, p: ("Judy", "owner"))
    monkeypatch.setattr(wa, "_get_tenant_wa_creds", AsyncMock(return_value={"phone_id": "x", "token": "y"}))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(wa, "_send_wa_buttons", sent)
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(return_value=True))
    assert await wa._maybe_project_setup("27827077080", VOICE_NOTE, "digg-demo")
    pending = digg_docs.t["commerce_pending_confirmations"]
    assert [r["tool_args"]["baseline_doc_id"] for r in pending] == ["rev10", "rev10nr"]
    assert all(r["tool_name"] == "setup_project" and r["phone"] == "27827077080" for r in pending)
    titles = [b["title"] for b in sent.call_args.args[3]]
    assert titles == ["R593,022 signed", "R483,139 (no recep.)", "Cancel"]
    assert "baseline_locked" not in str(digg_docs.t.get("vula_project_boq"))   # nothing changed yet

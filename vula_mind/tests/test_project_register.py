"""The project register: clients, phases, aliases, safe renames, tenant scoping (2 Oct, Ian:
"how can a tenant add projects and aliases, phases, more projects for one client, each with
their own BOQ and filed invoices and slips?"). Real digg-demo shapes."""
import pytest
from fastapi import HTTPException

from vula.api import projects as api


class FakeDB:
    """A tiny in-memory stand-in for the Supabase table API the projects routes use."""

    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self, name)


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op, self.payload = db, name, [], "select", None

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def in_(self, k, vals): self.filters.append((k, "in", vals)); return self
    def eq(self, k, v): self.filters.append((k, "eq", v)); return self
    def update(self, payload): self.op, self.payload = "update", payload; return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def _match(self, row):
        for k, op, v in self.filters:
            if op == "eq" and row.get(k) != v:
                return False
            if op == "in" and row.get(k) not in v:
                return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            new = {"id": f"p{len(rows) + 1}", "aliases": [], **self.payload}
            rows.append(new)
            return type("R", (), {"data": [new]})()
        hits = [r for r in rows if self._match(r)]
        if self.op == "update":
            for r in hits:
                r.update(self.payload)
        return type("R", (), {"data": [dict(r) for r in hits]})()


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({
        "vula_projects": [
            {"id": "sporty", "tenant_id": "digg-demo", "name": "Sporty TV", "client": "Sporty",
             "status": "active", "aliases": ["Sporty – Phase 2", "SPORTY.TV"], "parent_id": None},
            {"id": "hpc", "tenant_id": "digg-demo", "name": "HPC Bokaap", "number": "DIGG-2024-017",
             "status": "active", "aliases": [], "parent_id": None},
            {"id": "other", "tenant_id": "off-the-hook", "name": "Fish shop fit-out", "status": "active", "aliases": []},
        ],
        "vula_filed_documents": [
            {"id": f"d{i}", "tenant_id": "digg-demo", "project": "Sporty – Phase 2", "category": "Invoice"} for i in range(3)
        ] + [{"id": "h1", "tenant_id": "digg-demo", "project": "HPC Bokaap", "category": "Delivery Note"}],
        "commerce_invoices": [{"id": "i1", "tenant_id": "digg-demo", "project": "Sporty – Phase 2"}],
    })
    monkeypatch.setattr(api, "_client", lambda: fake)
    monkeypatch.setattr("vula.commerce.service._client", lambda: fake)
    return fake


def test_another_tenants_project_is_not_found(db):
    with pytest.raises(HTTPException) as e:
        api._own_project("digg-demo", "other")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_editing_another_tenants_project_is_refused(db):
    with pytest.raises(HTTPException):
        await api.update_project("digg-demo", "other", api.ProjectPatch(name="Hijacked"))
    assert db.tables["vula_projects"][2]["name"] == "Fish shop fit-out"


@pytest.mark.asyncio
async def test_a_duplicate_or_alias_name_is_refused(db):
    with pytest.raises(HTTPException) as e:
        await api.create_project("digg-demo", api.ProjectIn(name="sporty.tv"))
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_a_phase_is_its_own_project_and_takes_the_old_name_with_it(db):
    res = await api.add_phase("digg-demo", "sporty", api.PhaseIn(phase="Phase 2", move_from="Sporty – Phase 2"))
    assert res["name"] == "Sporty TV – Phase 2" and res["parent_id"] == "sporty" and res["client"] == "Sporty"
    assert res["aliases"] == ["Sporty – Phase 2"]
    docs = [d["project"] for d in db.tables["vula_filed_documents"] if d["id"].startswith("d")]
    assert docs == ["Sporty TV – Phase 2"] * 3
    assert db.tables["commerce_invoices"][0]["project"] == "Sporty TV – Phase 2"
    assert db.tables["vula_projects"][0]["aliases"] == ["SPORTY.TV"]     # the name left the main project


@pytest.mark.asyncio
async def test_a_rename_moves_everything_and_keeps_the_old_name(db):
    res = await api.update_project("digg-demo", "hpc", api.ProjectPatch(name="HPC Bo-Kaap"))
    assert res["moved"] == {"vula_filed_documents": 1}
    hpc = db.tables["vula_projects"][1]
    assert hpc["name"] == "HPC Bo-Kaap" and "HPC Bokaap" in hpc["aliases"]
    assert db.tables["vula_filed_documents"][-1]["project"] == "HPC Bo-Kaap"


@pytest.mark.asyncio
async def test_an_alias_moves_documents_filed_under_it(db):
    db.tables["vula_filed_documents"].append({"id": "x", "tenant_id": "digg-demo", "project": "HPC_Bokaap", "category": "Quote / Estimate"})
    res = await api.add_alias("digg-demo", "hpc", api.AliasIn(alias="HPC_Bokaap"))
    assert res["moved"] == {"vula_filed_documents": 1}
    assert db.tables["vula_filed_documents"][-1]["project"] == "HPC Bokaap"


@pytest.mark.asyncio
async def test_the_overview_adds_phases_together(db):
    await api.add_phase("digg-demo", "sporty", api.PhaseIn(phase="Phase 2", move_from="Sporty – Phase 2"))
    ov = await api.project_overview("digg-demo", "sporty")
    assert [p["name"] for p in ov["phases"]] == ["Sporty TV – Phase 2"]
    assert ov["phases"][0]["documents"] == 3
    assert ov["documents_with_phases"] == {"Invoice": 3} and ov["documents"] == {}


# ── WhatsApp ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_whatsapp_part_of_creates_the_phase(db, monkeypatch):
    from vula.commerce import project_admin
    reply = await project_admin.handle("digg-demo", "Sporty – Phase 2 is part of Sporty TV")
    assert "Sporty TV – Phase 2 is now a phase of Sporty TV" in reply and "3 document(s)" in reply


@pytest.mark.asyncio
async def test_whatsapp_add_phase_and_alias(db):
    from vula.commerce import project_admin
    assert "HPC Bokaap – Phase 3" in await project_admin.handle("digg-demo", "Add phase 3 to HPC Bokaap")
    reply = await project_admin.handle("digg-demo", "HPC001 is also called HPC Bokaap")
    assert "“HPC001” now means HPC Bokaap" in reply


@pytest.mark.asyncio
async def test_ordinary_messages_are_not_commands(db):
    from vula.commerce import project_admin
    for text in ("What's on the programme today?", "Add 2kg hake to my order", "Find the Solid Cape invoice"):
        assert await project_admin.handle("digg-demo", text) is None


def test_pick_slips_are_delivery_notes():
    from vula.commerce.doc_quality import category_from_summary
    assert category_from_summary("This document is a pick slip from Solid Cape, detailing a purchase") == "Delivery Note"

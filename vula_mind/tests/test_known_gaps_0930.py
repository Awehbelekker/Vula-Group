"""Known gaps closed 2026-09-30: the owner can ask for today's programme; reps can hand over a
product document (never a money document)."""
import asyncio
from datetime import date

import pytest

from vula.commerce import project_programme as pp


@pytest.mark.parametrize("q", [
    "What's on the programme today?",
    "whats on site tomorrow",
    "Today's tasks",
    "who is working on site today?",
    "what is overdue on the programme",
])
def test_programme_questions(q):
    assert pp.looks_like_programme_question(q)


@pytest.mark.parametrize("q", ["What's the programme for the Atlantis fee?", "how is HPC doing",
                               "What's the slip rating today?"])
def test_not_programme_questions(q):
    assert not pp.looks_like_programme_question(q)


def test_programme_answer_uses_the_brief_rows(monkeypatch):
    tasks = [{"project_id": "Atlantis", "title": "Tile shower", "room": "Bathroom",
              "start_date": "2026-09-30", "due_date": "2026-10-01", "status": "todo",
              "assignee_name": "Sipho"}]
    monkeypatch.setattr(pp, "programme_tasks", lambda tid, project=None: tasks)
    monkeypatch.setattr(pp, "today_sast", lambda: date(2026, 9, 30))
    monkeypatch.setattr(pp, "_people", lambda tid: [])
    monkeypatch.setattr(pp, "cost_position", lambda *a, **k: None)
    out = asyncio.run(pp.programme_answer("digg-demo", "What's on the programme today?"))
    assert "Atlantis" in out and "Tile shower" in out and "Sipho" in out


def test_no_programme_falls_through(monkeypatch):
    monkeypatch.setattr(pp, "programme_tasks", lambda tid, project=None: [])
    assert asyncio.run(pp.programme_answer("gerflor", "What's on the programme today?")) is None


class _Rows:
    def __init__(self, rows): self.rows = rows
    def table(self, _): return self
    def select(self, *_): return self
    def eq(self, *_): return self
    def order(self, *_, **__): return self
    def limit(self, *_): return self
    def execute(self):
        rows = self.rows
        class R: data = rows
        return R()


def test_rep_gets_the_data_sheet_never_an_invoice(monkeypatch):
    from core.skills import commerce_admin as ca
    rows = [
        {"filename": "Mipolam Affinity – slip resistance test (R10) 2026-08-27.pdf", "category": "Report",
         "summary": "Slip resistance test for Mipolam Affinity", "file_url": "https://x/aff.pdf"},
        {"filename": "Invoice - Affinity Flooring 2026-08-01.pdf", "category": "Invoice",
         "summary": "Invoice for Affinity", "file_url": "https://x/inv.pdf"},
    ]
    monkeypatch.setattr(ca.service, "_client", lambda: _Rows(rows))
    out = ca.CommerceAdminSkill()._find_product_document("gerflor", {"query": "Affinity slip test"})
    assert out["found"] and [d["link"] for d in out["documents"]] == ["https://x/aff.pdf"]


def test_rep_toolset_has_product_documents():
    from core.skills.commerce_admin import _tools_for
    names = [t["function"]["name"] for t in _tools_for("gerflor", role="sales_rep")]
    assert "find_product_document" in names and "lookup_business_info" in names

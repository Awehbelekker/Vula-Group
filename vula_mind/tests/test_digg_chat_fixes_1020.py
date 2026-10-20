"""DIGG's chats on 20 Oct 2026, replayed: a captioned slip photo that was lost, a 279 s wait on
the local server, "Save both invoices under belladonna" answering the wrong question, and
"Please find and invoice with Insulation Matirial on"."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vula.api import whatsapp as wa
from vula.commerce import service


# ── 1. a captioned photo keeps an image extension, and its caption names the project ─────────

def test_captioned_photo_is_saved_as_an_image():
    assert wa._image_filename("Belladonna primer", "wamid.HBg", "image/jpeg") == "Belladonna primer.jpg"
    assert wa._image_filename("", "wamid.HBg", "image/png") == "image-wamid.HBg.png"
    assert wa._image_filename("HPC / Bokaap: paint\nsecond line", "x", "image/jpeg") == "HPC Bokaap paint.jpg"
    assert wa._caption_hint("Belladonna primer.jpg") == "Belladonna primer"
    assert wa._caption_hint("image-wamid.HBg.jpg") == ""
    assert wa._caption_hint("receipt-123.jpg") == ""


# ── 2. a local model call never waits litellm's 600 s default ─────────────────────────────────

def test_local_calls_get_the_local_timeout(monkeypatch):
    from config import settings
    from core import supervisor
    monkeypatch.setattr(settings, "local_call_timeout_s", 60)
    kw = {"model": "ollama/llama3.1:8b"}
    supervisor._local_timeout(kw)
    assert kw["timeout"] == 60
    own = {"model": "ollama/llama3.1:8b", "timeout": 5}
    supervisor._local_timeout(own)
    assert own["timeout"] == 5                                   # a caller's own timeout stays
    cloud = {"model": "openrouter/google/gemini-2.5-flash"}
    supervisor._local_timeout(cloud)
    assert "timeout" not in cloud


# ── 3. "Save both invoices under belladonna" goes on what was just sent ──────────────────────

@pytest.mark.parametrize("text,project", [
    ("Save both invoices under belladonna", "belladonna"),
    ("Put all of them on HPC", "HPC"),
    ("these are for Porterfield", "Porterfield"),
    ("file the two slips under HPC Bokaap.", "HPC Bokaap"),
])
def test_both_all_these_under_a_project(text, project):
    m = wa._BATCH_PROJECT_RE.match(text)
    assert m and m.group(m.lastindex) == project


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.patch = db, name, None

    def select(self, *a): return self
    def eq(self, *a): return self
    def in_(self, *a): return self
    def gte(self, *a): return self

    def update(self, patch):
        self.patch = patch
        return self

    def execute(self):
        if self.patch is not None:
            self.db.setdefault("updates", []).append((self.name, self.patch))
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=list(self.db.get(self.name, [])))


@pytest.fixture
def judy(monkeypatch):
    from vula import open_questions as oq
    from vula.commerce import expenses
    db = {
        # 07:20–07:25: the DIY slip (read and booked 5 minutes later) and its filed photo
        "commerce_expenses": [{"id": "7df0", "amount_cents": 186980, "supplier": "DIY", "project": None}],
        "vula_filed_documents": [{"id": "50b8", "filename": "Invoice - DIY 20261020-0724.jpg",
                                  "project": None, "status": "pending_project"}],
    }
    assigned, closed, sent = [], [], []
    monkeypatch.setattr(service, "_client", lambda: SimpleNamespace(table=lambda n: _Q(db, n)))
    monkeypatch.setattr(expenses, "match_project",
                        lambda t, text, projects=None: "Belladonna" if "bella" in text.lower() else None)
    monkeypatch.setattr(expenses, "assign", lambda t, eid, **kw: assigned.append((eid, kw)) or {})
    monkeypatch.setattr(oq, "close_for", lambda t, ref, status="closed": closed.append(ref))
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, **k: sent.append(m)))
    wa._BATCH_PROJECT.clear()
    return SimpleNamespace(db=db, assigned=assigned, closed=closed, sent=sent)


@pytest.mark.asyncio
async def test_save_both_puts_the_slips_just_sent_on_the_project(judy):
    assert await wa._answer_batch_project("digg-demo", "27827000000", "Save both invoices under belladonna")
    assert judy.assigned == [("7df0", {"project": "Belladonna"})]
    assert ("vula_filed_documents", {"project": "Belladonna", "status": "filed"}) in judy.db["updates"]
    assert set(judy.closed) == {"7df0", "50b8"}          # nobody is asked "which project?" again
    assert "R1,869.80 DIY" in judy.sent[0] and "*Belladonna*" in judy.sent[0]
    # the second slip, still being read when she said it, follows
    assert wa._batch_project_for("digg-demo", "0827000000") == "Belladonna"
    assert wa._batch_project_for("digg-demo", "27820000001") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Send all invoices for Jack Hammer", "all invoices for belladonna",
                                  "Please find and invoice with Insulation Matirial on",
                                  "Save both invoices under nowhere"])
async def test_requests_and_unknown_projects_are_not_taken(judy, text):
    assert not await wa._answer_batch_project("digg-demo", "27827000000", text)
    assert not judy.assigned and not judy.sent


# ── 4. what was bought, found on the invoice lines ───────────────────────────────────────────

def test_item_words_drop_request_words_typos_and_map_synonyms():
    assert service._item_words("Please find and invoice with Insulation Matirial on") == ["insulation"]
    assert service._item_words("isolation invoices from last month") == ["insulation"]
    assert service._item_words("isolasie") == ["insulation"]


GREEN_GUARD = {"description": "GREEN GUARD INSULATION 100mm 6.0X1.2m", "quantity": 20,
               "unit_price_cents": 37333, "total_cents": 746660}


def _doc(i, party, total, lines, summary="Tax invoice"):
    return {"id": i, "filename": f"{i}.pdf", "category": "Invoice", "summary": summary,
            "created_at": f"2026-08-{i[-2:]}T10:00:00",
            "fields": {"supplier": party, "total_cents": total, "line_items": lines}}


@pytest.mark.asyncio
async def test_insulation_finds_every_invoice_with_an_insulation_line(monkeypatch):
    by_summary = [_doc("00092115", "SOLID CAPE (PTY) LTD", 916159, [GREEN_GUARD],
                       "invoice from SOLID CAPE for insulation and delivery")]
    by_lines = by_summary + [_doc("00092784", "SOLID CAPE (PTY) LTD", 2873896,
                                  [GREEN_GUARD, {"description": "RHINOBOARD 6.4MM", "total_cents": 1}],
                                  "construction materials")]
    asked = {}
    monkeypatch.setattr(service, "_resolve_supplier_names", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_filed_rows_query", lambda t, terms, c, party_only: list(by_summary))
    monkeypatch.setattr(service, "_filed_rows_by_line_item",
                        lambda t, words, c: asked.setdefault("words", words) and list(by_lines))
    out = await service.find_filed_document("digg-demo", "Insulation Matirial")
    assert asked["words"] == ["insulation"]
    assert out["total_matches"] == 2
    assert out["total_amount_cents"] == 916159 + 2873896
    m = {r["id"]: r for r in out["matches"]}
    assert m["00092784"]["matching_lines"] == ["GREEN GUARD INSULATION 100mm 6.0X1.2m × 20 = R7,466.60"]


@pytest.mark.asyncio
async def test_a_supplier_name_query_is_not_widened_by_line_items(monkeypatch):
    teck = [_doc("00000001", "TECK FLOORING", 50000, [])]
    called = []
    monkeypatch.setattr(service, "_resolve_supplier_names", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_filed_rows_query", lambda t, terms, c, party_only: list(teck))
    monkeypatch.setattr(service, "_filed_rows_by_line_item", lambda *a: called.append(a) or [])
    out = await service.find_filed_document("digg-demo", "Teck flooring invoices")
    assert out["total_matches"] == 1 and not called


@pytest.mark.asyncio
async def test_an_item_only_on_invoice_lines_is_still_found(monkeypatch):
    from unittest.mock import patch
    rows = [_doc("00092114", "SOLID CAPE (PTY) LTD", 161999, [GREEN_GUARD], "insulation and delivery")]
    monkeypatch.setattr(service, "_resolve_supplier_names", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_filed_rows_query", lambda *a, **k: [])
    monkeypatch.setattr(service, "_filed_rows_by_line_item", lambda t, words, c: rows)
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe:
        pipe.return_value.query = AsyncMock(return_value=[])
        out = await service.find_filed_document("digg-demo", "isolation")
    assert out["status"] == "found" and out["total_matches"] == 1
    assert out["matches"][0]["matching_lines"][0].startswith("GREEN GUARD INSULATION")

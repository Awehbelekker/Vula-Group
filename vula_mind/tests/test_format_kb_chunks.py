"""Tests for core.skills.base.format_kb_chunks — the shared RAG-context builder that
reasoning.py and architecture_planning.py now both call instead of inlining their own
"[filename]: text" join (2026-09-21 generalisation of the find_document amount-cross-reference
fix, see tests/test_service_find_filed_document.py for the original incident/fix at the tool
level). A chunk whose source document was also filed normally with a real extracted amount now
carries that verified figure in its tag, so reasoning.py/architecture_planning.py don't have to
ask the model to read a number off fuzzy chunk text — the exact failure mode behind the real
R70,400 "logged" fabrication incident looks_like_tenant_data_question's guard exists to catch."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.base import format_kb_chunks

TID = "test-tenant"


@pytest.mark.asyncio
async def test_empty_chunks_gives_empty_string():
    assert await format_kb_chunks(TID, []) == ""


@pytest.mark.asyncio
async def test_no_filed_match_falls_back_to_plain_filename_tag():
    chunks = [{"filename": "notes.pdf", "text": "some general content", "score": 0.4}]
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(return_value={})):
        out = await format_kb_chunks(TID, chunks)
    assert out == "[notes.pdf]: some general content"


@pytest.mark.asyncio
async def test_filed_match_carries_the_verified_amount_in_the_tag():
    """The exact real scenario: a semantic-only KB hit whose document was also filed normally
    with a real total_cents-derived amount should surface that figure, not just the excerpt."""
    chunks = [{"filename": "POS Account Sale 22-191407.pdf",
               "text": "drill bits for Jack Hammer's account", "score": 0.51}]
    filed = {"POS Account Sale 22-191407.pdf":
             {"amount": 92.0, "party": "Gardens Handiman Centre"}}
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(return_value=filed)):
        out = await format_kb_chunks(TID, chunks)
    assert "R92.00" in out
    assert "Gardens Handiman Centre" in out
    assert "drill bits for Jack Hammer's account" in out


@pytest.mark.asyncio
async def test_filed_match_without_a_party_still_shows_the_amount():
    chunks = [{"filename": "invoice.pdf", "text": "some content", "score": 0.4}]
    filed = {"invoice.pdf": {"amount": 340.0, "party": None}}
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(return_value=filed)):
        out = await format_kb_chunks(TID, chunks)
    assert "R340.00" in out
    assert " — None" not in out


@pytest.mark.asyncio
async def test_multiple_chunks_join_with_blank_line():
    chunks = [
        {"filename": "a.pdf", "text": "first", "score": 0.5},
        {"filename": "b.pdf", "text": "second", "score": 0.4},
    ]
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(return_value={})):
        out = await format_kb_chunks(TID, chunks)
    assert out == "[a.pdf]: first\n\n[b.pdf]: second"


@pytest.mark.asyncio
async def test_cross_reference_failure_still_returns_plain_chunks():
    """Best-effort, fail-open — a broken cross-reference lookup must never break the whole
    grounding context, just fall back to plain filename tags."""
    chunks = [{"filename": "notes.pdf", "text": "some content", "score": 0.4}]
    with patch("vula.commerce.service.filed_amounts_by_filename",
               new=AsyncMock(side_effect=RuntimeError("db down"))):
        out = await format_kb_chunks(TID, chunks)
    assert out == "[notes.pdf]: some content"


# ── Token budget instead of the [:900] cut (2026-10-16, KB retrieval phase 1) ──────────────────

from core.skills.base import fit_kb_chunks, fit_kb_text  # noqa: E402

_LONG_SHEET = (
    "Affinity 255 PUR technical data sheet.\n"
    + "".join(f"Property {i}: a long line of ordinary specification text that pads the sheet out.\n"
              for i in range(40))
    + "Slip resistance (EN 16165 / DIN 51130): R10.\n"
)


@pytest.mark.asyncio
async def test_a_fact_at_the_end_of_a_long_chunk_reaches_the_prompt():
    """The "slip rate on the Affinity" shape: the data sheet IS retrieved, top-ranked, but the
    figure sits in its last row — past character 900, where every chunk used to be cut."""
    assert len(_LONG_SHEET) > 3000 and _LONG_SHEET.index("R10") > 900
    chunks = [{"filename": "Affinity 255 PUR technical data sheet.pdf", "text": _LONG_SHEET},
              {"filename": "other.pdf", "text": "Some other text. " * 80}]
    with patch("vula.commerce.service.filed_amounts_by_filename", new=AsyncMock(return_value={})):
        out = await format_kb_chunks(TID, chunks)
    assert "Slip resistance (EN 16165 / DIN 51130): R10." in out
    assert out.startswith("[Affinity 255 PUR technical data sheet.pdf]: ")
    assert "[other.pdf]: " in out           # the lower-ranked chunk still gets its share


def test_cut_never_splits_a_table_row():
    rows = "".join(f"Creation {i} | Oak | {100 + i} | 600 | 22/09\n" for i in range(60))
    got = fit_kb_text(rows, 500)
    assert len(got) <= 500 and got.endswith(" […]")
    for line in got.replace(" […]", "").splitlines():
        assert line.count("|") == 4, line


def test_cut_prefers_a_sentence_end_on_prose():
    text = "First sentence here. Second sentence is a bit longer than the first one. Third one."
    got = fit_kb_text(text, 60)
    assert got == "First sentence here. […]"


def test_every_chunk_keeps_the_old_900_before_the_top_ones_grow():
    chunks = [{"text": "word " * 1000, "filename": f"d{i}"} for i in range(5)]
    got = fit_kb_chunks(chunks, budget_tokens=2400)
    lens = [len(c["text"]) for c in got]
    assert all(n >= 850 for n in lens), lens            # floor (minus the boundary + marker)
    assert lens[0] > lens[-1]                            # the leftover went to the top-ranked
    assert sum(lens) <= 2400 * 4


def test_fitting_is_idempotent_and_skips_what_the_budget_cannot_reach():
    chunks = [{"text": "Sentence. " * 300, "filename": f"d{i}"} for i in range(4)]
    once = fit_kb_chunks(chunks, budget_tokens=400)
    assert fit_kb_chunks(once, budget_tokens=400) == once
    assert len(once) == 2                                # 1,600 chars: 900 + 700, nothing for 3rd
    assert fit_kb_chunks([], 100) == []

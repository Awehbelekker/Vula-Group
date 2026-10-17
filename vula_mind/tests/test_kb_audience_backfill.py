"""tools/kb_audience_backfill.py (KB retrieval 1b, 2026-10-17): existing KB chunks get an audience —
preview first, apply on request, verified by read-back, resumable, and fully reversible."""
import pytest

from evals.retrieval import offline
from tools.kb_audience_backfill import classify, run
from vula.ingestion.pipeline import VulaIngestionPipeline

T = "shop"


@pytest.mark.parametrize("payload,category,want", [
    ({"source_type": "website", "filename": "Website — about"}, None, "public"),
    ({"source_type": "starter", "filename": "faq.md"}, None, "public"),
    ({"source_type": "learned", "filename": "learned_x.txt"}, None, "staff"),
    ({"filename": "scan.pdf"}, "Invoice", "staff"),
    ({"filename": "scan.pdf"}, "Bank Statement", "owner"),
    ({"filename": "scan.pdf"}, "Menu / Price List", "public"),
    ({"filename": "FNB statement Aug.pdf"}, None, "owner"),
    ({"filename": "Staff roster and wages September.pdf"}, None, "owner"),
    ({"filename": "Supplier invoice co-op.pdf"}, None, "staff"),
    ({"filename": "recipe-cape-salmon-braai.md"}, None, "public"),
    ({"filename": "owner-taught.txt"}, None, "public"),
    ({"filename": "Gym catalogue.pdf"}, None, "staff"),            # unclassifiable → staff
    ({"filename": "x.txt", "doc_id": "invoice_item_42"}, None, "staff"),
])
def test_classify(payload, category, want):
    assert classify(payload, category)[0] == want


def test_menu_by_category_is_flagged_for_review():
    assert classify({"filename": "a.pdf"}, "Menu / Price List")[2] is True
    assert classify({"filename": "a"}, None, shared=True)[0] == "public"


async def _legacy_kb():
    """Ingest, then strip every tag — the state of a collection written before 2026-10-16."""
    p = VulaIngestionPipeline(T)
    await p.ingest_text("Invoice from the co-op: yellowtail R 95 per kg.", "Supplier invoice co-op.pdf")
    await p.ingest_text("Is the fish fresh? Yes, line caught daily.", "Fresh catch FAQ.md")
    await p.ingest_text("Bank statement: closing balance.", "FNB statement Aug.pdf")
    return p


def _tags(mem, p):
    return {pl["filename"]: pl.get("audience") for _v, pl in mem.cols[p.store._collection_name(T)]["points"].values()}


def _strip(mem, p):
    for _v, pl in mem.cols[p.store._collection_name(T)]["points"].values():
        pl.pop("audience", None)


@pytest.mark.asyncio
async def test_preview_writes_nothing_apply_tags_verifies_and_rollback_restores():
    with offline() as mem:
        p = await _legacy_kb()
        _strip(mem, p)
        prev = await run(T)
        assert prev["mode"] == "preview" and prev["written"] == 0
        assert prev["by_audience"] == {"staff": 1, "public": 1, "owner": 1}
        assert set(_tags(mem, p).values()) == {None}

        done = await run(T, apply=True)
        assert done["written"] == done["verified"] == 3 and done["mismatched"] == 0
        assert _tags(mem, p) == {"Supplier invoice co-op.pdf": "staff", "Fresh catch FAQ.md": "public",
                                 "FNB statement Aug.pdf": "owner"}
        # a customer can now only reach the FAQ, even in strict mode
        from unittest.mock import patch
        from vula.ingestion import pipeline as pl
        with patch.object(pl.settings, "kb_customer_audience_strict", True):
            got = {c["filename"] for c in await p.query("yellowtail fresh co-op balance", audience="public")}
        assert got <= {"Fresh catch FAQ.md"}

        again = await run(T, apply=True)                  # idempotent / resumable
        assert again["written"] == 0 and again["already_tagged"] == 3

        back = await run(T, rollback=True)
        assert back["written"] == 3
        assert set(_tags(mem, p).values()) == {None}


@pytest.mark.asyncio
async def test_partly_tagged_document_gets_the_same_tag_and_owner_choice_is_kept():
    with offline() as mem:
        p = VulaIngestionPipeline(T)
        await p.ingest_text("\n\n".join(["Menu. " + "item " * 300] * 3), "Winter menu.pdf", audience="public")
        await p.ingest_text("Price list for customers.", "Price list.pdf")
        col = mem.cols[p.store._collection_name(T)]["points"]
        menu = [pl for _v, pl in col.values() if pl["filename"] == "Winter menu.pdf"]
        assert len(menu) > 1
        menu[0].pop("audience")                            # one chunk from before the change
        for _v, pl in col.values():
            if pl["filename"] == "Price list.pdf":
                pl.update(audience="public", audience_by="owner")
        rep = await run(T, apply=True)
        assert rep["by_reason"] == {"matches the rest of the document": 1} and rep["written"] == 1
        await run(T, apply=True, reclassify=True)          # an owner's choice survives a redo
        assert all(pl.get("audience") == "public" for _v, pl in col.values())

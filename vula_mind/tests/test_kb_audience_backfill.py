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
    ({"filename": "scan.pdf"}, "Menu / Price List", "staff"),    # can't tell whose menu
    ({"filename": "scan.pdf"}, "Specification", "staff"),         # DIGG's council submissions
    ({"filename": "Menu 20260913-1616.jpg"}, None, "staff"),       # a supplier's menu
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
    assert classify({"filename": "SPM price list.pdf"}, None)[2] is True
    assert classify({"filename": "a"}, None, shared=True)[0] == "public"


async def _legacy_kb():
    """Ingest, then strip every tag — the state of a collection written before 2026-10-16."""
    p = VulaIngestionPipeline(T)
    await p.ingest_text("Invoice from the co-op: yellowtail R 95 per kg.", "Supplier invoice co-op.pdf")
    await p.ingest_text("Is the fish fresh? Yes, line caught daily.", "Fresh catch FAQ.md",
                        source_type="website")            # public by source, not by its name
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
        auds = {pl["filename"]: pl.get("audience") for _v, pl in col.values()}
        assert auds["Price list.pdf"] == "public"
        assert auds["Winter menu.pdf"] == "staff"        # a redo re-classifies what no owner chose


@pytest.mark.asyncio
async def test_exposure_check_finds_a_past_reply_that_quoted_a_private_document():
    """tools/kb_exposure_check.py re-runs a past customer question the way the unfiltered
    customer path did, and flags a reply carrying a figure from a staff/owner chunk."""
    from unittest.mock import patch

    from tools.kb_exposure_check import check, rand_figures
    assert rand_figures("yellowtail R 95 per kg, total R9,453.00, 3 items") == {9500, 945300}
    with offline() as mem, patch("tools.kb_audience_backfill._categories", return_value={}):
        p = await _legacy_kb()
        _strip(mem, p)
        turns = [{"id": "t1", "source": "commerce", "phone_hash": "abc", "at": "2026-10-01",
                  "question": "yellowtail price per kg co-op", "reply": "Our yellowtail is R 95 per kg."},
                 {"id": "t2", "source": "commerce", "phone_hash": "abc", "at": "2026-10-02",
                  "question": "is the fish fresh", "reply": "Yes, line caught daily."}]
        rep = await check(T, turns)
    assert rep["reached_private"] == 1 and rep["figure_overlap"] == 1
    assert rep["turns"][0]["docs"] == ["Supplier invoice co-op.pdf"]



@pytest.mark.asyncio
async def test_mark_public_opens_a_reviewed_list_and_the_backfill_leaves_it_alone():
    from unittest.mock import patch

    from vula import kb_maintenance as km
    with offline() as mem:
        p = VulaIngestionPipeline(T)
        await p.ingest_text("Weekly prices: hake R 160 per kg.", "21MAY_v2.pdf")
        await p.ingest_text("Invoice from the co-op: yellowtail R 95 per kg.", "Supplier invoice co-op.pdf")
        with patch("config.settings.kb_mark_public", '{"shop": ["21MAY v2.pdf", "missing.pdf"]}'):
            rep = await km._mark_public()
        assert rep["tenants"][T]["matched"] == 1 and rep["tenants"][T]["not_found"] == ["missing.pdf"]
        tags = _tags(mem, p)
        assert tags["21MAY_v2.pdf"] == "public" and tags["Supplier invoice co-op.pdf"] == "staff"
        await run(T, apply=True, reclassify=True)          # a redo never overrides a reviewed choice
        assert _tags(mem, p)["21MAY_v2.pdf"] == "public"
        await p.ingest_text("Weekly prices: hake R 165 per kg.", "21MAY_v2.pdf")   # re-upload keeps it
        assert _tags(mem, p)["21MAY_v2.pdf"] == "public"


def test_exposure_check_treats_dashboard_admin_sessions_as_staff():
    from tools.kb_exposure_check import is_staff_session
    assert is_staff_session("admin:27820000000")
    assert is_staff_session("webadmin:off-the-hook-abc123")
    assert not is_staff_session("27820000000")
    assert not is_staff_session(None)

"""Guards on the escalate-and-learn loop, from a real production audit (2026-09-01).

vula_learned_answers held exactly two rows in production, both off-the-hook, and BOTH were wrong:

  Q: "What is in the family fish box?"  A: "Yes I can do"
      -> the helper replying about something else entirely
  Q: "Do you deliver to Timbuktu"       A: "Respond to Richard Downing via WhatsApp business
                                            and say delivery will be Monday 10:00-12:00"
      -> the helper instructing Vula, not answering, and naming a real customer

Both were live and being served. Probing production, "do you deliver to Milnerton" returned the
Timbuktu answer: Jaccard 3/5 = 0.6, over the old 0.5 bar, with the place name — the only word
that decides the answer — being the only difference.

These tests reproduce both incidents and lock the guards that stop them.

2026-09-15 (Tenant Mind Phase 2): find_learned_answer() became async and now tries semantic
search (Qdrant) before the plain keyword match below. Every test in the "keyword path" section
explicitly patches _find_learned_answer_semantic to return None — these tests exist to lock the
distinguishing-token guard specifically, and must stay fast, deterministic unit tests, not
depend on network reachability to Ollama/Qdrant. The guard itself is proven to apply identically
on the semantic path by the dedicated tests further down.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula import escalation as esc


def _db_with(rows):
    """Mock whose .eq("status","approved") chain yields `rows` (post-migration-150 shape)."""
    table = MagicMock()
    table.select.return_value.eq.return_value.order.return_value.limit.return_value \
        .eq.return_value.execute.return_value = MagicMock(data=rows)
    db = MagicMock()
    db.table.return_value = table
    return db


def _approved(question, answer):
    return {"question": question, "answer": answer, "status": "approved"}


def _no_semantic_match():
    """Forces find_learned_answer() straight to the keyword path — see module docstring."""
    return patch.object(esc, "_find_learned_answer_semantic", AsyncMock(return_value=None))


# ── the Milnerton/Timbuktu incident (keyword path) ───────────────────────────────

@pytest.mark.asyncio
async def test_different_place_never_matches_a_stored_answer():
    """THE incident: a Milnerton customer must not get the Timbuktu answer."""
    rows = [_approved("Do you deliver to Timbuktu", "Delivery is Monday 10:00-12:00")]
    with patch.object(esc, "_client", lambda: _db_with(rows)), _no_semantic_match():
        assert await esc.find_learned_answer("off-the-hook", "do you deliver to Milnerton") is None


@pytest.mark.asyncio
async def test_same_question_still_matches():
    """The guard must not break the feature it protects."""
    rows = [_approved("Do you deliver to Timbuktu", "Delivery is Monday 10:00-12:00")]
    with patch.object(esc, "_client", lambda: _db_with(rows)), _no_semantic_match():
        got = await esc.find_learned_answer("off-the-hook", "Do you deliver to Timbuktu?")
    assert got == "Delivery is Monday 10:00-12:00"


@pytest.mark.asyncio
async def test_different_product_never_matches():
    rows = [_approved("What is the price of hake", "R160 per kg")]
    with patch.object(esc, "_client", lambda: _db_with(rows)), _no_semantic_match():
        assert await esc.find_learned_answer("off-the-hook", "What is the price of prawns") is None


@pytest.mark.asyncio
async def test_different_quantity_never_matches():
    rows = [_approved("Can I order 5kg of prawns", "Yes, 24 hours notice")]
    with patch.object(esc, "_client", lambda: _db_with(rows)), _no_semantic_match():
        assert await esc.find_learned_answer("off-the-hook", "Can I order 100kg of prawns") is None


@pytest.mark.asyncio
async def test_wording_differences_in_common_words_still_match():
    """'what is in the box' vs 'whats in the box' is the same question."""
    rows = [_approved("What is in the family fish box", "Hake, calamari and prawns")]
    with patch.object(esc, "_client", lambda: _db_with(rows)), _no_semantic_match():
        got = await esc.find_learned_answer("off-the-hook", "Whats in the family fish box?")
    assert got == "Hake, calamari and prawns"


@pytest.mark.asyncio
async def test_unrelated_question_does_not_match():
    rows = [_approved("Do you deliver to Timbuktu", "Monday 10:00-12:00")]
    with patch.object(esc, "_client", lambda: _db_with(rows)), _no_semantic_match():
        assert await esc.find_learned_answer("off-the-hook", "What are your opening hours") is None


# ── approval gate (keyword path) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_answers_are_never_served():
    """Only approved rows come back from the query; nothing unreviewed reaches a customer."""
    with patch.object(esc, "_client", lambda: _db_with([])), _no_semantic_match():
        assert await esc.find_learned_answer("off-the-hook", "Do you deliver to Timbuktu") is None


@pytest.mark.asyncio
async def test_fails_closed_before_migration_150():
    """Without the status column we must serve nothing rather than serve everything."""
    table = MagicMock()
    table.select.return_value.eq.return_value.order.return_value.limit.return_value \
        .eq.side_effect = Exception("column status does not exist")
    db = MagicMock()
    db.table.return_value = table
    with patch.object(esc, "_client", lambda: db), _no_semantic_match():
        assert await esc.find_learned_answer("off-the-hook", "anything at all") is None


# ── semantic path (2026-09-15, Tenant Mind Phase 2) — same guard, different retrieval ──

class _FakePipeline:
    """Stands in for VulaIngestionPipeline: a fixed embedding + a fixed set of Qdrant hits."""
    def __init__(self, tenant_id, hits):
        self.tenant_id = tenant_id
        self.embedder = MagicMock()
        self.embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
        self.store = MagicMock()
        self.store.search = AsyncMock(return_value=hits)


def _semantic_hits(*rows_with_ids):
    """Each entry: (learned_id, score). Payload shape matches what QdrantStore.search returns."""
    return [{"doc_id": f"learned_answer_{lid}", "score": score} for lid, score in rows_with_ids]


@pytest.mark.asyncio
async def test_semantic_match_finds_a_verbose_rewording_jaccard_would_miss():
    """The real, honest value of this feature given how strict the distinguishing-token guard
    already is (by design — see its own docstring): the guard only ever blocks on a genuinely
    NEW non-common word, so anything that clears it also tends to share almost every content
    word already... except when one version is padded with extra common/polite filler. That
    inflates Jaccard's union enough to drop it below MATCH_THRESHOLD even though nothing
    meaningfully different was said — exactly the case a verbose-vs-terse customer produces in
    practice, and exactly what semantic search (order- and padding-insensitive) catches that
    plain word-overlap can't."""
    hits = _semantic_hits(("la1", 0.9))
    row = {"id": "la1", "tenant_id": "off-the-hook", "status": "approved",
          "question": "What is in the family fish box",
          "answer": "Hake, calamari and prawns."}
    padded = ("Hi there, so what is in the family fish box please, thank you so much")
    # Sanity-check the premise: this padding really does drop the OLD keyword path below its
    # own threshold, so a pass here can only be coming from the semantic path.
    with patch.object(esc, "_client", lambda: _db_with([_approved(row["question"], row["answer"])])):
        assert esc._find_learned_answer_keyword(
            "off-the-hook", padded, esc._tokens(padded)) is None

    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline",
              lambda tenant_id: _FakePipeline(tenant_id, hits)),
        patch.object(esc, "get_learned_answer", lambda lid: row if lid == "la1" else None),
    ):
        got = await esc.find_learned_answer("off-the-hook", padded)
    assert got == "Hake, calamari and prawns."


@pytest.mark.asyncio
async def test_semantic_candidate_still_blocked_by_the_distinguishing_token_guard():
    """The Milnerton/Timbuktu guard applies identically to a semantic hit — closeness in
    embedding space is not a safety property by itself."""
    hits = _semantic_hits(("la1", 0.95))
    row = {"id": "la1", "tenant_id": "off-the-hook", "status": "approved",
          "question": "Do you deliver to Timbuktu", "answer": "Monday 10:00-12:00"}
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline",
              lambda tenant_id: _FakePipeline(tenant_id, hits)),
        patch.object(esc, "get_learned_answer", lambda lid: row if lid == "la1" else None),
    ):
        assert await esc.find_learned_answer(
            "off-the-hook", "do you deliver to Milnerton") is None


@pytest.mark.asyncio
async def test_semantic_hit_re_verifies_status_from_postgres_not_qdrant():
    """A Qdrant point for an answer that was approved, then would-be-stale, must never be
    trusted over the live Postgres row — simulated here as a row whose status is no longer
    'approved' by the time of the search (e.g. rejected after being embedded)."""
    hits = _semantic_hits(("la1", 0.95))
    row = {"id": "la1", "tenant_id": "off-the-hook", "status": "rejected",
          "question": "Do you deliver to Milnerton", "answer": "Yes, Mondays"}
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline",
              lambda tenant_id: _FakePipeline(tenant_id, hits)),
        patch.object(esc, "get_learned_answer", lambda lid: row if lid == "la1" else None),
        patch.object(esc, "_client", lambda: _db_with([])),  # keyword fallback: nothing either
    ):
        assert await esc.find_learned_answer("off-the-hook", "do you deliver to Milnerton") is None


@pytest.mark.asyncio
async def test_semantic_hit_from_a_different_tenant_is_never_used():
    hits = _semantic_hits(("la1", 0.95))
    row = {"id": "la1", "tenant_id": "digg", "status": "approved",
          "question": "Do you deliver to Milnerton", "answer": "Yes, Mondays"}
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline",
              lambda tenant_id: _FakePipeline(tenant_id, hits)),
        patch.object(esc, "get_learned_answer", lambda lid: row if lid == "la1" else None),
        patch.object(esc, "_client", lambda: _db_with([])),
    ):
        assert await esc.find_learned_answer("off-the-hook", "do you deliver to Milnerton") is None


@pytest.mark.asyncio
async def test_falls_back_to_keyword_when_semantic_search_is_unavailable():
    """A tenant with no embedded answers yet (or Qdrant unreachable) must still get the
    keyword-matched answer, not silence."""
    rows = [_approved("Do you deliver to Timbuktu", "Delivery is Monday 10:00-12:00")]

    def _raise(*a, **kw):  # VulaIngestionPipeline(...) itself is a sync constructor call
        raise RuntimeError("qdrant unreachable")

    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", side_effect=_raise),
        patch.object(esc, "_client", lambda: _db_with(rows)),
    ):
        got = await esc.find_learned_answer("off-the-hook", "Do you deliver to Timbuktu?")
    assert got == "Delivery is Monday 10:00-12:00"


@pytest.mark.asyncio
async def test_falls_back_to_keyword_when_semantic_finds_no_qualifying_candidate():
    """Semantic search runs fine but every hit gets guard-blocked or fails re-verification —
    the keyword path still gets its own independent chance."""
    hits = _semantic_hits(("la1", 0.95))
    row = {"id": "la1", "tenant_id": "off-the-hook", "status": "approved",
          "question": "Do you deliver to Cape Town", "answer": "Not that one"}
    rows = [_approved("Do you deliver to Milnerton", "Yes, Mondays")]
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline",
              lambda tenant_id: _FakePipeline(tenant_id, hits)),
        patch.object(esc, "get_learned_answer", lambda lid: row if lid == "la1" else None),
        patch.object(esc, "_client", lambda: _db_with(rows)),
    ):
        got = await esc.find_learned_answer("off-the-hook", "do you deliver to Milnerton")
    assert got == "Yes, Mondays"


# ── embed_learned_answer (write side) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_embed_learned_answer_indexes_the_question_not_the_answer():
    captured = {}

    class _CapturingPipeline:
        def __init__(self, tenant_id):
            pass

        async def ingest_text(self, content, filename, doc_id, source_type):
            captured.update(content=content, doc_id=doc_id, source_type=source_type)

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", _CapturingPipeline):
        await esc.embed_learned_answer("off-the-hook", "la1", "Do you deliver to Milnerton")

    assert captured["content"] == "Do you deliver to Milnerton"
    assert captured["doc_id"] == "learned_answer_la1"
    assert captured["source_type"] == "learned_answer_approved"


@pytest.mark.asyncio
async def test_embed_learned_answer_fails_open():
    def _raise(*a, **kw):  # VulaIngestionPipeline(...) itself is a sync constructor call
        raise RuntimeError("qdrant down")

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", side_effect=_raise):
        await esc.embed_learned_answer("off-the-hook", "la1", "anything")  # must not raise


# ── the "instruction to Vula" incident ──────────────────────────────────────────

def test_detects_the_real_richard_downing_instruction():
    assert esc.reply_is_instruction_to_vula(
        "Respond to Richard Downing via WhatsApp business and say delivery will be on Monday "
        "between 10:00 - 12:00") is True


def test_detects_common_instruction_shapes():
    for text in [
        "Tell them we're closed on Sunday",
        "Please reply to her and say we can do it",
        "Let the customer know it's R160",
        "Say that we deliver on Mondays",
        "Sê vir hom ons is toe",
    ]:
        assert esc.reply_is_instruction_to_vula(text) is True, text


def test_real_answers_are_not_flagged_as_instructions():
    for text in [
        "We deliver to Milnerton on Mondays between 10 and 12",
        "The family box has hake, calamari and prawns",
        "R160 per kg",
        "Yes we can do that",
        "Ons lewer Maandae af",
    ]:
        assert esc.reply_is_instruction_to_vula(text) is False, text


def test_instruction_reply_is_relayed_but_never_learned():
    """The helper meant the customer to hear something — relay it, but don't store a directive
    as the canonical answer for everyone else."""
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[{"id": "e1"}])
    esc_row = {"id": "e1", "tenant_id": "off-the-hook", "customer_phone": "27786537562",
               "question": "Do you deliver to Timbuktu"}
    with patch.object(esc, "_client", lambda: db):
        info = esc.answer_escalation(
            esc_row, "Respond to Richard Downing via WhatsApp business and say delivery is Monday")
    assert info is not None, "the customer must still get a reply"
    assert info["learned_id"] is None, "a directive must never be learned"
    assert not any(c.args and c.args[0] == "vula_learned_answers"
                   for c in db.table.call_args_list), "no learned-answer insert should happen"


def test_a_real_answer_is_learned_as_pending():
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[{"id": "e1"}])
    esc_row = {"id": "e1", "tenant_id": "off-the-hook", "customer_phone": "27786537562",
               "question": "What is in the family fish box"}
    with patch.object(esc, "_client", lambda: db):
        info = esc.answer_escalation(esc_row, "Hake, calamari and prawns — feeds four")
    assert info["learned_id"], "a genuine answer should be captured for review"
    inserted = db.table.return_value.insert.call_args[0][0]
    assert inserted["status"] == "pending", "must not be usable until the owner approves"


def test_contact_details_are_redacted_before_storing():
    """A stored answer gets replayed to OTHER customers — it must not carry someone's number."""
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[{"id": "e1"}])
    esc_row = {"id": "e1", "tenant_id": "off-the-hook", "customer_phone": "27786537562",
               "question": "Who do I call about my order"}
    with patch.object(esc, "_client", lambda: db):
        esc.answer_escalation(esc_row, "Call Sam on 082 123 4567 or mail sam@shop.co.za")
    stored = db.table.return_value.insert.call_args[0][0]["answer"]
    assert "082 123 4567" not in stored and "sam@shop.co.za" not in stored
    assert "[phone]" in stored and "[email]" in stored

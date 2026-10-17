"""The owner's business-profile interview replaces the starter KB's [placeholders] (2026-09-30)."""
import asyncio

import pytest

from vula.commerce import business_profile as bp
from vula.ingestion.pipeline import _without_placeholders


@pytest.fixture()
def store(monkeypatch):
    data = {}
    monkeypatch.setattr(bp, "get_answers", lambda tid: dict(data.get(tid, {})))
    monkeypatch.setattr(bp, "_business_type", lambda tid: "food")
    ingested = []

    class _T:
        def upsert(self, row):
            data[row["tenant_id"]] = row["answers"]
            return self

        def execute(self):
            return None

    monkeypatch.setattr(bp, "_client", lambda: type("C", (), {"table": lambda self, n: _T()})())

    class _Store:
        async def delete_document(self, tenant_id, doc_id):
            return 0

    class _P:
        def __init__(self, tenant_id):
            self.store = _Store()

        async def ingest_text(self, content, filename, doc_id, **kw):
            ingested.append((filename, content, kw.get("audience")))
            return type("R", (), {"status": "success"})()

    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline", _P)
    bp._ACTIVE.clear()
    return data, ingested


def test_whatsapp_interview_saves_answers_into_the_kb(store):
    data, ingested = store
    run = lambda t: asyncio.run(bp.handle_interview("oth", "2782", t))
    assert run("hello") is None                                  # not an interview message
    first = run("set up my profile")
    assert "Question 1 of" in first and "what does the business sell" in first
    run("Fresh and frozen seafood for homes and restaurants in Cape Town")
    third = run("skip")                                          # skip the area question
    assert "Question" in third and "working hours" in third
    run("Mon–Fri 8–5, Sat 8–12")
    assert data["oth"]["what_we_do"].startswith("Fresh and frozen")
    assert "area" not in data["oth"]
    # Opening hours are for customers (2026-10-17): they go to the customer-visible document.
    assert ingested and ingested[-1][0] == bp.PUBLIC_PROFILE_FILENAME and "Mon–Fri 8–5" in ingested[-1][1]
    assert ingested[-1][2] == "public"
    assert "Saved" in run("stop")
    assert run("Mon–Fri") is None                                # interview over


def test_unknown_keys_are_ignored(store):
    data, _ = store
    asyncio.run(bp.save_answers("oth", {"hours": "9-5", "evil": "x"}))
    assert data["oth"] == {"hours": "9-5"}


def test_rep_gets_rep_questions(monkeypatch):
    keys = [q["key"] for q in bp.questions("rep")]
    assert "principal" in keys and "territory" in keys


def test_starter_placeholders_are_never_retrieved():
    hits = [
        {"source_type": "starter", "filename": "starter_delivery.md",
         "text": "## Delivery\nWe deliver to [your delivery areas] on [delivery days].\nOrders are packed with ice to keep them cold."},
        {"source_type": "starter", "filename": "starter_hours.md", "text": "Open [your hours]."},
        {"source_type": "document", "filename": "menu.pdf", "text": "Price list [2026]"},
    ]
    out = _without_placeholders(hits)
    assert len(out) == 2
    assert "[your delivery areas]" not in out[0]["text"] and "packed with ice" in out[0]["text"]
    assert out[1]["filename"] == "menu.pdf"



# ── Customer and team halves (2026-10-17, KB retrieval 1b) ─────────────────────────────────────

def test_customer_facts_are_public_and_internal_ones_stay_with_the_team(store):
    data, ingested = store
    asyncio.run(bp.save_answers("oth", {"hours": "9-5", "team": "Sipho quotes, Lindi does accounts",
                                        "payroll": "3 staff on PAYE", "delivery_fee": "R60, free over R600"}))
    docs = {f: (c, a) for f, c, a in ingested}
    pub, team = docs[bp.PUBLIC_PROFILE_FILENAME], docs[bp.PROFILE_FILENAME]
    assert pub[1] == "public" and team[1] == "staff"
    assert "9-5" in pub[0] and "free over R600" in pub[0]
    assert "Sipho" not in pub[0] and "PAYE" not in pub[0]
    assert "Sipho" in team[0] and "PAYE" in team[0] and "9-5" not in team[0]


def test_the_owner_decides_which_answers_customers_see(store):
    data, ingested = store
    asyncio.run(bp.save_answers("oth", {"hours": "9-5", "team": "Sipho quotes"}, public_keys=["team"]))
    docs = {f: c for f, c, _a in ingested}
    assert "Sipho" in docs[bp.PUBLIC_PROFILE_FILENAME] and "9-5" not in docs[bp.PUBLIC_PROFILE_FILENAME]
    assert "9-5" in docs[bp.PROFILE_FILENAME]


def test_defaults_put_no_internal_question_on_the_customer_side():
    public = set(bp.default_public_keys("food")) | set(bp.default_public_keys("trades"))
    assert {"hours", "area", "payment", "cancellations", "delivery_fee"} <= public
    assert not public & {"team", "tax_regime", "payroll", "vat_category", "pricing"}

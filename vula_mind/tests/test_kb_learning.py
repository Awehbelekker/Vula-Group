"""vula/kb_learning.py — ask which document is meant instead of guessing, answer the pick from
that document, and record each staff knowledge question for learning."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.base import SkillInput, SkillOutput
from core.skills.reasoning import ReasoningSkill
from vula import kb_learning as kl

T = "off-the-hook"
INFO = {
    "a": {"label": "Invoice — Atlantis Seafood, R2 324.09, 18 Aug 2026", "kind": "invoice", "party": "Atlantis Seafood"},
    "b": {"label": "Invoice — Atlantis Seafood, R1 980.00, 6 Aug 2026", "kind": "invoice", "party": "Atlantis Seafood"},
    "c": {"label": "Invoice — Torga Fishing, R550.00, 2 Sep 2026", "kind": "invoice", "party": "Torga Fishing"},
}


def _chunks(*ids, text=None):
    return [{"doc_id": i, "filename": f"{i}.pdf",
             "text": text.get(i, "") if text else f"Tax invoice {INFO[i]['party']} total"} for i in ids]


# ── When to ask ──────────────────────────────────────────────────────────────────────────────

def test_a_generic_invoice_question_over_two_lookalikes_is_asked():
    opts = kl.ambiguous(T, "What is the total amount due on the invoice?", _chunks("a", "b"), INFO)
    assert [o["doc_id"] for o in opts] == ["a", "b"]
    assert "1. Invoice — Atlantis Seafood, R2 324.09" in kl.clarify_message(opts)


def test_a_name_on_only_one_document_answers_without_asking():
    assert kl.ambiguous(T, "What's the total on the Torga invoice?", _chunks("a", "c"), INFO) == []


def test_a_name_on_several_narrows_the_choice_to_those():
    opts = kl.ambiguous(T, "What do we owe on the Atlantis invoice?", _chunks("a", "c", "b"), INFO)
    assert [o["doc_id"] for o in opts] == ["a", "b"]


def test_an_invoice_number_on_one_document_answers_without_asking():
    chunks = _chunks("a", "b", text={"a": "Invoice 50141004 Atlantis", "b": "Invoice 50130260 Atlantis"})
    assert kl.ambiguous(T, "When is invoice 50141004 due?", chunks, INFO) == []


@pytest.mark.parametrize("q", ["List all the Atlantis invoices", "What's on the latest invoice?",
                               "How many invoices this month?"])
def test_questions_over_several_or_the_latest_are_not_a_choice(q):
    assert kl.ambiguous(T, q, _chunks("a", "b"), INFO) == []


def test_not_a_money_document_question_is_never_asked():
    assert kl.ambiguous(T, "What is the slip rating of Affinity?", _chunks("a", "b"), INFO) == []


def test_one_money_document_among_other_documents_is_answered():
    info = {"a": INFO["a"], "x": {"label": "Taralay guide", "kind": None, "party": ""}}
    chunks = _chunks("a") + [{"doc_id": "x", "filename": "taralay guide.pdf", "text": "flooring"}]
    assert kl.ambiguous(T, "What's the total on the invoice?", chunks, info) == []


# ── Reading the pick ─────────────────────────────────────────────────────────────────────────

OPTS = [{"doc_id": "a", "label": INFO["a"]["label"]}, {"doc_id": "c", "label": INFO["c"]["label"]}]


@pytest.mark.parametrize("text,doc", [("2", "c"), ("1.", "a"), ("option 2", "c"), ("no. 1", "a"),
                                      ("the second one", "c"), ("first", "a"), ("Torga", "c"),
                                      ("2 please", "c")])
def test_a_pick_is_read(text, doc):
    assert kl.resolve_pick(text, OPTS)["doc_id"] == doc


@pytest.mark.parametrize("text", ["3", "What is our VAT number?", "Atlantis and Torga please",
                                  "ok", ""])
def test_anything_else_is_not_a_pick(text):
    assert kl.resolve_pick(text, OPTS) is None


@pytest.mark.parametrize("text,kind", [("No, that's wrong", "corrected"), ("wrong", "corrected"),
                                       ("That's not it", "corrected"), ("Nee", "corrected"),
                                       ("Thanks!", "confirmed"), ("perfect 👍", "confirmed"),
                                       ("What about the June one?", None), ("Thanks, and the VAT?", None)])
def test_feedback(text, kind):
    assert kl.detect_feedback(text) == kind


# ── Storage ──────────────────────────────────────────────────────────────────────────────────

class _FakeTable:
    def __init__(self, db):
        self.db, self.op, self.filters, self.payload = db, None, [], None

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, vals):
        self.op, self.payload = "update", vals
        return self

    def select(self, *_):
        self.op = "select"
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def gte(self, *_):
        return self

    def in_(self, k, vals):
        self.filters.append((k, ("__in__", tuple(vals))))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *_):
        return self

    def execute(self):
        def ok(r, k, v):
            return r.get(k) in v[1] if isinstance(v, tuple) and v and v[0] == "__in__" else r.get(k) == v
        rows = [r for r in self.db if all(ok(r, k, v) for k, v in self.filters)]
        if self.op == "insert":
            row = {"id": f"t{len(self.db) + 1}", "created_at": datetime.now(timezone.utc).isoformat(),
                   "picked_doc": None, "feedback": None, **self.payload}
            self.db.append(row)
            return MagicMock(data=[row])
        if self.op == "update":
            for r in rows:
                r.update(self.payload)
            return MagicMock(data=rows)
        return MagicMock(data=sorted(rows, key=lambda r: r["created_at"], reverse=True))


@pytest.fixture
def db(monkeypatch):
    rows = []
    monkeypatch.setattr(kl, "_client", lambda: MagicMock(table=lambda name: _FakeTable(rows)))
    return rows


def test_customers_are_never_recorded(db):
    assert kl.record_turn(T, "27820000000", role=None, question="q", outcome="answered") is None
    assert db == []


def test_the_asker_is_stored_as_a_hash_not_a_number(db):
    kl.record_turn(T, "+27 82 000 0000", role="owner", question="q", outcome="answered")
    assert "27820000000" not in str(db[0]) and db[0]["asker"] == kl.asker_key(T, "27820000000")


def test_a_pick_after_a_clarify_is_recorded_and_returned(db):
    kl.record_turn(T, "27820000000", role="staff", question="Total on the invoice?",
                   outcome="clarify", candidates=OPTS)
    pick = kl.take_pick(T, "27820000000", "2")
    assert pick == {"question": "Total on the invoice?", "doc_id": "c", "label": OPTS[1]["label"],
                    "turn_id": "t1"}
    assert db[0]["picked_doc"] == "c"
    assert kl.take_pick(T, "27820000000", "1") is None          # already answered


def test_a_stale_or_superseded_clarify_is_not_a_pick(db):
    kl.record_turn(T, "27820000000", role="staff", question="q", outcome="clarify", candidates=OPTS)
    db[0]["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert kl.take_pick(T, "27820000000", "2") is None
    kl.record_turn(T, "27820000000", role="staff", question="q", outcome="clarify", candidates=OPTS)
    kl.record_turn(T, "27820000000", role="staff", question="new q", outcome="answered")
    assert kl.take_pick(T, "27820000000", "2") is None


def test_feedback_marks_the_last_answer(db):
    kl.record_turn(T, "27820000000", role="owner", question="q", outcome="answered", answer_docs=["a"])
    assert kl.note_feedback(T, "27820000000", "No, that's wrong") == "corrected"
    assert db[0]["feedback"] == "corrected"
    assert kl.note_feedback(T, "27820000000", "thanks") is None   # first verdict stands


# ── The reasoning skill ──────────────────────────────────────────────────────────────────────

def _pipe(chunks):
    p = MagicMock()
    p.query = AsyncMock(return_value=chunks)
    return p


@pytest.mark.asyncio
async def test_reasoning_asks_which_invoice_and_records_it():
    recorded = []
    pipe = _pipe(_chunks("a", "b"))
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipe), \
         patch.object(kl, "describe_docs", return_value=INFO), \
         patch.object(kl, "record_turn", side_effect=lambda *a, **k: recorded.append(k)), \
         patch("litellm.acompletion", new=AsyncMock(side_effect=AssertionError("no model call"))):
        out = await ReasoningSkill().run(SkillInput(
            question="What is the total amount due on the invoice?", tenant_id=T,
            metadata={"caller_role": "owner", "customer_phone": "27820000000"}))
    assert out.answer.startswith("I found more than one document")
    assert out.verification["outcome"] == "accepted"
    assert recorded[0]["outcome"] == "clarify" and len(recorded[0]["candidates"]) == 2


@pytest.mark.asyncio
async def test_a_customer_is_never_asked_to_choose():
    pipe = _pipe(_chunks("a", "b"))
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipe), \
         patch.object(kl, "describe_docs", side_effect=AssertionError("not for customers")), \
         patch("core.skills.reasoning.resolve_generation_route",
               new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))), \
         patch("litellm.acompletion", new=AsyncMock(return_value=MagicMock(
             choices=[MagicMock(message=MagicMock(content="R2 324.09"))]))):
        out = await ReasoningSkill().run(SkillInput(
            question="What is the total amount due on the invoice?", tenant_id=T,
            metadata={"customer_phone": "27820000000"}))
    assert "which one" not in out.answer.lower()


@pytest.mark.asyncio
async def test_the_pick_is_answered_from_that_document_only():
    pipe = _pipe(_chunks("c"))
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipe), \
         patch.object(kl, "describe_docs", return_value=INFO), \
         patch.object(kl, "record_turn"), \
         patch("core.skills.reasoning.resolve_generation_route",
               new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))), \
         patch("litellm.acompletion", new=AsyncMock(return_value=MagicMock(
             choices=[MagicMock(message=MagicMock(content="R550.00 is due."))]))):
        out = await ReasoningSkill().run(SkillInput(
            question="What is the total amount due on the invoice?", tenant_id=T,
            metadata={"caller_role": "owner", "customer_phone": "27820000000", "kb_doc_id": "c"}))
    assert pipe.query.await_args.kwargs["doc_id"] == "c"
    assert "R550.00" in out.answer


# ── WhatsApp ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_whatsapp_answers_a_pick_and_checks_other_messages_for_feedback():
    from vula.api import whatsapp as wa
    pick = {"question": "Total on the invoice?", "doc_id": "c", "label": "x", "turn_id": "t1"}
    with patch.object(kl, "take_pick", return_value=pick), \
         patch.object(kl, "answer_pinned", new=AsyncMock(return_value=SkillOutput(
             answer="R550.00 is due on the Torga invoice.", skill_name="reasoning"))):
        assert await wa._kb_pick_reply(T, "27820000000", "2", "Staci", "owner") \
            == "R550.00 is due on the Torga invoice."
    with patch.object(kl, "take_pick", return_value=None), \
         patch.object(kl, "note_feedback") as fb:
        assert await wa._kb_pick_reply(T, "27820000000", "no, that's wrong", "Staci", "owner") is None
    fb.assert_called_once()
    assert await wa._kb_pick_reply(T, "27820000000", "2", None, None) is None   # a customer


# ── The weekly report and real test questions ───────────────────────────────────────────────

def _turn(db, tenant, outcome, **kw):
    kl.record_turn(tenant, kw.pop("who", "27820000000"), role="owner", question=kw.pop("q", "q"),
                   outcome=outcome, **{k: v for k, v in kw.items() if k in ("candidates", "answer_docs")})
    db[-1].update({k: v for k, v in kw.items() if k in ("picked_doc", "feedback")})


def test_weekly_report_counts_and_lists_what_needs_a_look(db):
    _turn(db, T, "answered", answer_docs=["a"], feedback="corrected", q="Total on Atlantis?")
    _turn(db, T, "answered", answer_docs=["b"], feedback="confirmed")
    _turn(db, T, "answered")
    _turn(db, T, "clarify", candidates=OPTS, picked_doc="a")
    _turn(db, T, "clarify", candidates=OPTS, q="Which receipt?")
    _turn(db, T, "no_doc", q="Where is the lease?")
    _turn(db, "digg-demo", "answered")
    rep = kl.weekly_report()
    s = rep["tenants"][T]
    assert (s["questions"], s["answered"], s["asked_which"], s["picked"], s["nothing_found"],
            s["said_wrong"], s["said_right"]) == (6, 3, 2, 1, 1, 1, 1)
    assert s["right_first_time"] == round(2 / 6, 3)
    assert {i["why"] for i in rep["review"][T]} == {"said wrong", "which one? not answered", "nothing found"}
    line = kl.weekly_line(rep)
    assert "\n" not in line and "off-the-hook: 6 questions, 3 answered (1 called wrong)" in line


def test_only_confirmed_answers_from_one_or_two_documents_become_test_questions(db):
    _turn(db, T, "answered", answer_docs=["a"], feedback="confirmed", q="VAT number of Atlantis?")
    _turn(db, T, "answered", answer_docs=["a"], feedback="corrected")
    _turn(db, T, "answered", answer_docs=["a", "b", "c"], feedback="confirmed")
    _turn(db, T, "clarify", candidates=OPTS, picked_doc="a")
    cases = kl.confirmed_cases([T])
    assert [(c["question"], c["expected_doc_ids"], c["real"]) for c in cases] == \
        [("VAT number of Atlantis?", ["a"], True)]

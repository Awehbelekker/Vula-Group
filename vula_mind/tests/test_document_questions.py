"""Questions about an incoming bill, one after another (2026-10-05, STE Scaffolding invoice).

The invoice came in by email twice. The owner got "is this supplier X?" and "which project?" at
once, answered one, and the other was never followed up; the reply could also land on whichever
pending document was newest rather than the one asked about.
"""
from unittest.mock import patch

import pytest

from vula.integrations import doc_filing

TID, PHONE = "digg-demo", "27645755210"
STE_PDF = {"id": "d2", "tenant_id": TID, "status": "pending_project", "filename": "Tax Invoice STE00866.PDF",
           "summary": "scaffolding material hire", "commerce_invoice_id": "inv2", "created_at": "2026-10-05T14:17:48Z",
           "fields": {"supplier": "STE Scaffolding S A (Pty) Ltd (Cape)", "total_cents": 239717, "date": "2026-10-05"}}
STE_SCAN = {"id": "d1", "tenant_id": TID, "status": "pending_project", "filename": "20261005134914578.pdf",
            "commerce_invoice_id": "inv1", "created_at": "2026-10-05T12:16:10Z",
            "fields": {"supplier": "STE Scaffolding CC (Cape)", "total_cents": 239717, "date": "2026-10-05"}}


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.payload, self.desc_key = db, table, [], None, None

    def select(self, *_a, **_k):
        return self

    def _get(self, r, col):
        if col.startswith("fields->>"):
            v = (r.get("fields") or {}).get(col[len("fields->>"):])
            return None if v is None else str(v)
        return r.get(col)

    def eq(self, col, val):
        self.f.append(lambda r: self._get(r, col) == val)
        return self

    def gte(self, col, val):
        self.f.append(lambda r: (self._get(r, col) or "") >= val)
        return self

    def order(self, col, desc=False):
        self.desc_key = col
        return self

    def limit(self, *_a):
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def execute(self):
        rows = [r for r in self.db[self.table] if all(f(r) for f in self.f)]
        if self.desc_key:
            rows.sort(key=lambda r: self._get(r, self.desc_key) or "", reverse=True)
        if self.payload is not None:
            for r in rows:
                r.update(self.payload)
        return type("R", (), {"data": rows})()


@pytest.fixture()
def db(monkeypatch):
    data = {"vula_filed_documents": [dict(STE_SCAN, fields=dict(STE_SCAN["fields"])),
                                     dict(STE_PDF, fields=dict(STE_PDF["fields"]))],
            "commerce_invoices": [{"id": "inv1", "tenant_id": TID, "status": "draft"},
                                  {"id": "inv2", "tenant_id": TID, "status": "draft"}]}
    client = type("C", (), {"table": lambda self, t: _Q(data, t)})()
    monkeypatch.setattr(doc_filing, "_client", lambda: client)
    return data


def test_second_copy_of_a_bill_is_spotted(db):
    dup = doc_filing.probable_duplicate(TID, STE_PDF)
    assert dup["id"] == "d1"


def test_project_question_gives_the_guess_and_offers_to_drop_a_copy(db):
    with patch("vula.integrations.project_resolver.resolve",
               return_value={"project": "HPC Bokaap", "confidence": 0.7,
                             "reason": "STE Scaffolding's usual project (3 of 3 earlier documents)"}):
        q = doc_filing.project_question(TID, STE_PDF)
    assert "Tax Invoice STE00866.PDF" in q and "R2,397.17" in q
    assert "Is this for *HPC Bokaap*" in q and "usual project" in q
    assert "reply *duplicate*" in q


def test_project_question_without_a_guess_lists_examples(db, monkeypatch):
    monkeypatch.setattr(doc_filing, "project_examples", lambda t: ["HPC Bokaap", "Atlantis Paarden Eiland"])
    lone = dict(STE_PDF, id="d9", fields={"supplier": "Someone", "total_cents": 100, "date": "2026-10-01"})
    with patch("vula.integrations.project_resolver.resolve", return_value=None):
        q = doc_filing.project_question(TID, lone)
    assert "Which project is this for? (e.g. HPC Bokaap, Atlantis Paarden Eiland)" in q
    assert "duplicate" not in q


@pytest.mark.asyncio
async def test_reply_files_the_document_that_was_asked_about(db, monkeypatch):
    # The scan was asked about; the PDF is newer. "duplicate" must drop the scan, not the PDF.
    doc_filing.mark_asked(TID, db["vula_filed_documents"][0], PHONE)
    monkeypatch.setattr("vula.integrations.notify.team_member_for_phone", lambda t, p: {"name": "Richard"})
    out = await doc_filing.resolve_pending_document(TID, PHONE, "duplicate")
    assert out == {"duplicate_dropped": True, "filename": "20261005134914578.pdf"}
    scan, pdf = db["vula_filed_documents"]
    assert scan["status"] == "duplicate" and pdf["status"] == "pending_project"
    assert db["commerce_invoices"][0]["status"] == "cancelled"      # never owed twice
    assert db["commerce_invoices"][1]["status"] == "draft"


@pytest.mark.asyncio
async def test_ask_project_sends_and_marks(db):
    sent = []

    async def fake_send(phone, text, tenant_id=None, **_k):
        sent.append((phone, text))
        return True
    with patch("vula.api.whatsapp._send_reply", new=fake_send), \
         patch("vula.integrations.project_resolver.resolve", return_value=None), \
         patch.object(doc_filing, "project_examples", lambda t: []):
        n = await doc_filing.ask_project(TID, db["vula_filed_documents"][1], [PHONE],
                                         prefix="📎 A document came in by email from accounts@ste.co.za.")
    assert n == 1 and sent[0][1].startswith("📎 A document came in by email")
    assert db["vula_filed_documents"][1]["fields"]["_asked_phone"] == PHONE

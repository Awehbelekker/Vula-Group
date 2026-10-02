"""Documents filed right, read fully, easy to find (vula/commerce/doc_quality.py, 2 Oct).
Summaries and names are the real digg-demo / off-the-hook shapes from production."""
from unittest.mock import MagicMock, patch

import pytest

from vula.commerce import doc_quality as dq


# ── 1. types from the summary Vula already wrote ───────────────────────────────

@pytest.mark.parametrize("summary,cat", [
    ("Delivery note from Solid Cape (Pty) Ltd to Aweh Belekker t/a Digg for various construction materials.", "Delivery Note"),
    ("This is an accounts receivable statement from Solid Cape (PTY) LTD to AWEH BELEKKER T/A DIGG, showing a balance", "Account Statement"),
    ("This document is a payment notification from First National Bank confirming a payment instruction", "Proof of Payment"),
    ("This document is a confirmation of insurance cover for a vehicle owned by Aweh Be Lekker (Pty)Ltd", "Insurance"),
    ("This document is an original title deed for Section 45 Bordeaux", "Legal / Property"),
    ("This document is a brochure for CleverCast, a device designed to streamline meeting collaboration", "Brochure / Product Info"),
    ("This document appears to be a product listing or description for an 'OBI CHAIR POLYPROP SLEIGH BASE'.", "Brochure / Product Info"),
])
def test_a_catch_all_document_gets_the_type_its_summary_names(summary, cat):
    assert dq.category_from_summary(summary) == cat


def test_nothing_recognisable_stays_where_it_is():
    assert dq.category_from_summary('This document appears to be a general document with the word "AVIS"') is None
    assert dq.better_category("General Document", "a general document") == "General Document"


def test_a_real_category_is_never_overridden():
    assert dq.better_category("Drawing / Plan", "delivery note for the plans") == "Drawing / Plan"


def test_filing_never_promotes_to_a_money_category_on_wording_alone():
    s = "A tax invoice from Solid Cape for building materials."
    assert dq.better_category("General Document", s) == "General Document"
    assert dq.better_category("General Document", s, allow_money=True) == "Invoice"


def test_the_analyser_knows_the_new_categories():
    from vula.api.whatsapp import _DOC_CATEGORIES
    assert set(dq.NEW_CATEGORIES) <= set(_DOC_CATEGORIES)


# ── 2. required details ────────────────────────────────────────────────────────

def test_an_invoice_without_a_total_or_supplier_is_flagged():
    assert dq.missing_details("Invoice", {"date": "2026-09-17"}) == ["supplier", "total_cents"]
    assert dq.missing_details("Invoice", {"supplier": "SOLID CAPE", "date": "2026-09-17", "total_cents": 757144}) == []
    assert dq.missing_details("Drawing / Plan", {}) == []


def test_reread_picks_documents_missing_any_required_detail_and_tries_each_once():
    from vula.commerce import reread
    rows = [
        {"id": "a", "category": "Delivery Note", "filename": "00095110.pdf", "file_url": "u", "fields": {"supplier": "SOLID CAPE"}},
        {"id": "b", "category": "Invoice", "filename": "x.pdf", "file_url": "u",
         "fields": {"supplier": "S", "date": "2026-09-01", "total_cents": 100}},
        {"id": "c", "category": "Invoice", "filename": "y.pdf", "file_url": "u", "fields": {"_reread_at": "2026-10-01"}},
    ]
    chain = MagicMock()
    for m in ("table", "select", "eq", "in_", "order", "limit"):
        getattr(chain, m).return_value = chain
    chain.execute.return_value.data = rows
    with patch.object(reread.service, "_client", return_value=chain):
        assert [r["id"] for r in reread.candidates("digg-demo")] == ["a", "c"]
        assert [r["id"] for r in reread.candidates("digg-demo", fresh_only=True)] == ["a"]


def test_a_reread_counts_only_when_it_fills_a_missing_detail():
    from vula.commerce.reread import _improves
    row = {"category": "Invoice", "fields": {"date": "2026-09-17"}}
    assert _improves(row, {"category": "Invoice", "fields": {"supplier": "SOLID CAPE", "total_cents": 757144}})
    assert not _improves(row, {"category": "Invoice", "fields": {"date": "2026-09-17"}})
    assert not _improves(row, {"category": "Quote / Estimate", "fields": {"supplier": "X", "total_cents": 1}})


# ── 3. one project register ────────────────────────────────────────────────────

def _register(rows):
    chain = MagicMock()
    for m in ("table", "select", "eq", "limit", "ilike"):
        getattr(chain, m).return_value = chain
    chain.execute.return_value.data = rows
    return chain


def test_an_alias_maps_to_the_registered_project():
    from vula.commerce import service
    reg = [{"name": "Atlantis Paarden Eiland", "number": None, "aliases": ["ATLANTIS FOODS"]}]
    with patch.object(service, "_client", return_value=_register(reg)):
        assert service.canonical_project("digg-demo", "Atlantis Foods") == "Atlantis Paarden Eiland"
        assert service.canonical_project("digg-demo", "ATLANTIS FOODS") == "Atlantis Paarden Eiland"


def test_the_register_is_read_without_aliases_before_migration_190():
    from vula.commerce import service
    calls = []

    class _Q:
        def __init__(self): self.cols = None
        def table(self, *_): return self
        def select(self, cols, **_): self.cols = cols; return self
        def eq(self, *_): return self
        def limit(self, *_): return self
        def execute(self):
            calls.append(self.cols)
            if "aliases" in self.cols:
                raise Exception("column vula_projects.aliases does not exist")
            return MagicMock(data=[{"name": "HPC Bokaap", "number": "DIGG-2024-017"}])
    with patch.object(service, "_client", return_value=_Q()):
        assert service.registered_projects("digg-demo")[0]["name"] == "HPC Bokaap"
    assert len(calls) == 2


def test_cleanup_preview_lists_changes_without_writing():
    rows = [
        {"id": "d1", "filename": "00093484.pdf", "category": "General Document", "project": "ATLANTIS FOODS",
         "summary": "Delivery note from Solid Cape (Pty) Ltd", "fields": {}, "status": "filed"},
        {"id": "d2", "filename": "HPC - EOT Claim No 2.pdf", "category": "Report", "project": None,
         "summary": "EOT claim", "fields": {}, "status": "pending_project"},
        {"id": "d3", "filename": "z.pdf", "category": "Report", "project": None,
         "summary": "unclear", "fields": {}, "status": "pending_project"},
    ]
    canon = {"ATLANTIS FOODS": "Atlantis Paarden Eiland"}
    resolved = {"d2": {"project": "HPC Bokaap", "kind": "named", "reason": "named in the document"},
                "d3": {"project": "Porterfield", "kind": "usual_supplier"}}

    def fake_resolve(tid, fields, text):
        return resolved["d2"] if "HPC" in text else resolved["d3"]
    with patch.object(dq, "_rows", return_value=rows), \
         patch("vula.commerce.service.canonical_project", side_effect=lambda t, p: canon.get(p, p)), \
         patch("vula.integrations.project_resolver.resolve", side_effect=fake_resolve), \
         patch.object(dq, "_client", side_effect=AssertionError("preview must not write")):
        prev = dq.cleanup_preview("digg-demo")
    assert prev["counts"] == {"recategorise": 1, "rename_project": 1, "file_pending": 1}
    kinds = {(i["kind"], i["id"], i["new"]) for i in prev["items"]}
    assert ("recategorise", "d1", "Delivery Note") in kinds
    assert ("rename_project", "d1", "Atlantis Paarden Eiland") in kinds
    assert ("file_pending", "d2", "HPC Bokaap") in kinds        # a guess (d3) is left for the owner


def test_apply_writes_only_what_still_matches_the_preview():
    items = [{"kind": "rename_project", "id": "d1", "field": "project", "old": "ATLANTIS FOODS",
              "new": "Atlantis Paarden Eiland"},
             {"kind": "file_pending", "id": "d2", "field": "project", "old": None, "new": "HPC Bokaap"}]
    chain = MagicMock()
    for m in ("table", "update", "eq", "is_"):
        getattr(chain, m).return_value = chain
    chain.execute.side_effect = [MagicMock(data=[{"id": "d1"}]), MagicMock(data=[])]
    with patch.object(dq, "_client", return_value=chain), \
         patch.object(dq, "health", return_value={"catch_all": 0, "pending_project": 1, "unregistered_projects": {}}):
        res = dq.apply_cleanup("digg-demo", items)
    assert res["written"] == 1 and res["skipped"] == 1
    chain.eq.assert_any_call("project", "ATLANTIS FOODS")      # guarded on the previewed value
    chain.is_.assert_called_with("project", "null")
    updates = [c.args[0] for c in chain.update.call_args_list]
    assert {"project": "HPC Bokaap", "status": "filed"} in updates


# ── 4. one search, one health card ─────────────────────────────────────────────

def test_one_search_covers_title_party_number_and_project():
    cl = dq.search_clauses("Solid Cape (PTY) LTD")
    for part in ("filename.ilike.%Solid%Cape%PTY%LTD%", "fields->>supplier.ilike.%Solid%Cape%PTY%LTD%",
                 "fields->>invoice_number.ilike.", "project.ilike."):
        assert part in cl
    assert dq.search_clauses("  ,() ") == ""


def test_health_counts_what_needs_attention():
    rows = [
        {"id": "1", "filename": "a.pdf", "category": "General Document", "fields": {}, "project": "HPC Bokaap", "status": "filed"},
        {"id": "2", "filename": "b.pdf", "category": "Invoice", "fields": {"supplier": "S"}, "project": "ATLANTIS FOODS", "status": "filed"},
        {"id": "3", "filename": "c.pdf", "category": "Report", "fields": {}, "project": None, "status": "pending_project"},
    ]
    reg = [{"name": "HPC Bokaap", "aliases": []}, {"name": "Atlantis Paarden Eiland", "aliases": []}]
    with patch.object(dq, "_rows", return_value=rows), \
         patch("vula.commerce.service.registered_projects", return_value=reg):
        h = dq.health("digg-demo")
    assert (h["total"], h["catch_all"], h["missing_details"], h["pending_project"]) == (3, 1, 1, 1)
    assert h["unregistered_projects"] == {"ATLANTIS FOODS": 1}
    assert h["samples"]["missing"][0]["missing"] == ["date", "total"]


# ── 5. measured: owner message and benchmark ───────────────────────────────────

def test_the_owner_message_mentions_unread_documents():
    from vula import owner_advisor
    text = owner_advisor.render({"name": "DIGG", "doc_gaps": {"missing": 63, "unregistered": 192}})
    assert "63 document(s)" in text and "192 document(s)" in text


def test_benchmark_document_quality_rows():
    from evals import benchmark
    h = {"total": 759, "catch_all": 214, "missing_details": 129, "pending_project": 347,
         "unregistered_projects": {"ATLANTIS FOODS": 110}, "categories": {"Invoice": 223, "Quote / Estimate": 113}}
    with patch("vula.commerce.doc_quality.health", return_value=h):
        rows = benchmark.run_document_quality("digg-demo")
    assert len(rows) == 4 and not any(r["ok"] for r in rows)
    assert "28% in a catch-all" in rows[0]["why"][0]

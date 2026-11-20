"""20 Nov 2026 (Ian): "a place where the business uploads its company documents — SARS, company registration
and so on — filed properly, and the accountant has access." DIGG's SARS CRA01 forms, COIDA letter of good
standing and VAT-registration letter were "General Document" and never reached the accountant's folders.
vula/commerce/company_docs.py + finance_folders' "00 Company and tax"."""
import pytest

from vula.commerce import books_review as br, company_docs as cd, doc_audit as da, finance_folders as ff, service
from tests.fake_supabase import FakeSupabase

T = "t1"


@pytest.mark.parametrize("name,summary,kind", [
    ("CRA01_Confirmation_of_Address.pdf", "A SARS Confirmation of Residential / Business Address form", cd.TAX),
    ("Progression letter.pdf", "This letter confirms the company is in the process of VAT registration", cd.TAX),
    ("Notice of registration.pdf", "SARS notice of registration for income tax", cd.TAX),
    ("COR14.3.pdf", "CIPC registration certificate", cd.COMPANY),
    ("Mandla Designs - Resolution.pdf", "Written resolutions of the directors of Mandla Designs", cd.COMPANY),
    ("LOGS 2026.pdf", "A Letter of Good Standing certifying compliance with the Compensation Fund (COIDA)", cd.COMPLIANCE),
    ("BEE affidavit.pdf", "B-BBEE sworn affidavit, EME level 1", cd.COMPLIANCE),
    ("How to request a tax type.pdf", "A step-by-step guide on how to request a tax type on SARS eFiling", None),
    ("Services List.pdf", "Company Partners provides various services, including company registration", None),
    ("Quote 12.pdf", "Quotation for aluminium windows", None)])
def test_a_company_or_tax_document_is_known_by_its_own_words(name, summary, kind):
    assert cd.kind_of(name, summary) == kind


@pytest.fixture
def db(monkeypatch):
    def doc(i, category, filename, summary, **kw):
        return {"id": i, "tenant_id": kw.pop("tenant_id", T), "category": category, "filename": filename,
                "summary": summary, "status": "filed", "doc_status": "current", "fields": kw, "file_url": f"u/{i}.pdf",
                "mime": "application/pdf", "created_at": "2025-05-02T08:00:00Z", "supplier_id": None,
                "commerce_invoice_id": None, "content_hash": f"h-{i}", "project": None}
    d = FakeSupabase({
        "vula_filed_documents": [
            doc("cra", "General Document", "CRA01 - Mandla Designs.pdf", "A SARS CRA01 Confirmation of Business Address for Mandla Designs"),
            doc("vat", "General Document", "Mandla Designs - Progression letter.pdf", "Mandla Designs (Pty) Ltd is in the process of VAT registration"),
            doc("bank", "Correspondence", "Bank Confirmation Letter.pdf", "A bank confirmation letter from FNB for Steel Supplies"),
            doc("inv", "Invoice", "SARS penalty invoice.pdf", "SARS penalty", total_cents=10000),
            doc("cor", "Company Registration", "COR14.3 Mandla Designs.pdf", "CIPC registration certificate of Mandla Designs", date="2019-03-01"),
            doc("x1", "General Document", "CRA01 - Mandla Designs.pdf", "SARS CRA01 for Mandla Designs", tenant_id="other")],
        "vula_merchant_audit": [], "commerce_bank_accounts": [], "commerce_bank_transactions": [],
        "commerce_suppliers": [], "commerce_invoices": []})
    for mod in (da, br, service, cd, ff):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.commerce.project_file._client", lambda: d, raising=False)
    monkeypatch.setattr("vula.commerce.statement_files._client", lambda: d, raising=False)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr("vula.commerce.bank_rec._own_names_now", lambda tid: ["Mandla Designs (Pty) Ltd"])
    return d


def test_intake_files_only_the_business_s_own_company_documents(db):
    assert cd.better("General Document", "SARS CRA01 for Mandla Designs", "cra.pdf", T) == cd.TAX
    assert cd.better("General Document", "B-BBEE certificate for Steel Supplies", "bee.pdf", T) == "General Document"
    assert cd.better("Invoice", "SARS penalty for Mandla Designs", "x.pdf", T) == "Invoice"          # a money document stands


def test_the_preview_offers_them_and_refiling_keeps_the_old_category(db):
    props = {p["id"]: p for p in cd.proposals(T)}
    assert set(props) == {"cra", "vat", "bank"}                     # never the invoice, another business's, or one already filed
    assert props["cra"]["ours"] and props["vat"]["ours"] and not props["bank"]["ours"]
    out = cd.refile(T, ["cra", "vat", "inv", "x1"], actor={"email": "judy@example.com"})
    assert out["refiled"] == ["cra", "vat"] and set(out["skipped"]) == {"inv", "x1"}
    row = next(r for r in db.rows["vula_filed_documents"] if r["id"] == "cra")
    assert row["category"] == cd.TAX and row["fields"]["_recategorised"]["from"] == "General Document"
    assert any(a["action"] == "documents_refiled_company" for a in db.rows["vula_merchant_audit"])
    with pytest.raises(br.Refused, match="Choose at least one"):
        cd.refile(T, [])


def test_the_accountant_s_folder_shows_them_in_every_year(db):
    cd.refile(T, ["cra", "vat"])
    for year in ("2025-03-01", "2026-03-01"):                      # filed in 2019 and 2025, shown in both years
        tree = ff.organise(T, year)
        company = next(f for f in tree["folders"] if f["key"] == "company")
        assert company["title"] == "00 Company and tax"
        groups = {g["name"]: [i["id"] for i in g["items"]] for g in company["groups"]}
        assert groups == {"Company registration (CIPC)": ["cor"], "SARS and tax": sorted(["cra", "vat"])} or \
            groups == {"Company registration (CIPC)": ["cor"], "SARS and tax": ["cra", "vat"]}

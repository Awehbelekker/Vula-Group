"""Gap audit 2.1: a VAT vendor's invoice carries every field SARS asks for (VAT Act s20(4)).

Full tax invoice: the words "Tax Invoice"; supplier name, address and VAT number; recipient
name, address and VAT number; serial number; date; description; quantity; value, VAT and total.
Made-up businesses and figures throughout."""
import pytest

pytest.importorskip("weasyprint")
fitz = pytest.importorskip("pymupdf")

from vula.commerce import pdf  # noqa: E402

SUPPLIER = {"company_name": "Acme Builders (Pty) Ltd", "vat_number": "4123456789",
            "registered_address": "12 Long Street, Cape Town, 8001", "vat_registered": True}
INVOICE = {"tenant_id": "acme", "doc_type": "invoice", "invoice_number": "ACM-INV-00012",
           "issue_date": "2026-10-27", "customer_name": "Bokaap Holdings CC",
           "customer_email": "accounts@bokaap.test",
           "line_items": [{"description": "Tiling labour, main bathroom", "quantity": 12,
                           "unit_price_cents": 45000, "total_cents": 540000}],
           "subtotal_cents": 540000, "vat_rate": 15, "vat_cents": 81000, "total_cents": 621000}


def _text(invoice, settings, monkeypatch, saved=None):
    monkeypatch.setattr(pdf, "saved_client_details", lambda tid, inv: dict(saved or {}))
    data = pdf.render_invoice_pdf(invoice, pdf.merge_branding("acme", settings))
    return " ".join(" ".join(p.get_text().split()) for p in fitz.open(stream=data, filetype="pdf"))


@pytest.mark.parametrize("template", ["classic", "modern", "minimal", "bold", "certificate"])
def test_every_sars_field_on_a_tax_invoice(template, monkeypatch):
    t = _text(INVOICE, {**SUPPLIER, "template_choice": template}, monkeypatch,
              saved={"vat": "4987654321", "address": "4 Wale Street, Cape Town"})
    for field in ("Acme Builders (Pty) Ltd", "12 Long Street", "VAT No: 4123456789",
                  "Bokaap Holdings CC", "4 Wale Street", "VAT No: 4987654321",
                  "ACM-INV-00012", "2026-10-27", "Tiling labour, main bathroom", "12",
                  "R5400.00", "R810.00", "R6210.00"):
        assert field in t, f"{template}: {field} missing"
    assert "TAXINVOICE" in t.upper().replace(" ", "")      # "minimal" letter-spaces its title


def test_vat_line_shows_even_when_the_display_setting_hides_it(monkeypatch):
    t = _text(INVOICE, {**SUPPLIER, "show_vat_breakdown": False}, monkeypatch)
    assert "R810.00" in t


def test_no_tax_invoice_without_a_vat_number(monkeypatch):
    t = _text(INVOICE, {**SUPPLIER, "vat_number": ""}, monkeypatch)
    assert "TAXINVOICE" not in t.upper().replace(" ", "") and "INVOICE" in t.upper()


def test_not_vat_registered_is_a_plain_invoice(monkeypatch):
    t = _text(INVOICE, {**SUPPLIER, "vat_registered": False}, monkeypatch)
    assert "TAXINVOICE" not in t.upper().replace(" ", "")


def test_invoice_values_win_over_the_saved_client(monkeypatch):
    inv = {**INVOICE, "customer_vat": "4111111111", "customer_address": "1 Main Road"}
    t = _text(inv, SUPPLIER, monkeypatch, saved={"vat": "4999999999", "address": "elsewhere"})
    assert "4111111111" in t and "1 Main Road" in t and "4999999999" not in t


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return self

    def select(self, _c):
        return self

    def eq(self, _k, _v):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


@pytest.mark.parametrize("inv", [
    {"customer_email": "Accounts@Bokaap.test"},
    {"customer_phone": "+27 82 000 0009"},
    {"customer_name": "bokaap  holdings cc"},
])
def test_saved_client_matched_by_email_phone_or_name(inv, monkeypatch):
    rows = [{"name": "Other Co", "email": "x@y.test", "phone": "0821111111", "vat_number": "4000000000"},
            {"name": "Bokaap Holdings CC", "email": "accounts@bokaap.test", "phone": "0820000009",
             "address": "4 Wale Street", "vat_number": "4987654321"}]
    monkeypatch.setattr("vula.commerce.service._client", lambda: _DB(rows))
    assert pdf.saved_client_details("acme", inv) == {"vat": "4987654321", "address": "4 Wale Street"}


def test_readiness_flags_a_vat_vendor_without_its_number(monkeypatch):
    from vula import readiness, startup_checks
    monkeypatch.setattr("vula.api.tenants.is_active", lambda t: True)
    monkeypatch.setattr(startup_checks, "unready_payment_tenants", lambda: [])
    tables = {"vula_tenant_config": [{"tenant_id": "oth"}, {"tenant_id": "digg"}],
              "commerce_invoice_settings": [
                  {"tenant_id": "oth", "vat_registered": True, "vat_number": "", "registered_address": ""},
                  {"tenant_id": "digg", "vat_registered": False}]}

    class Q:
        def __init__(self, rows):
            self.rows = rows

        def select(self, _c):
            return self

        def eq(self, k, v):
            return Q([r for r in self.rows if r.get(k) == v])

        def execute(self):
            return type("R", (), {"data": self.rows})()

    db = type("DB", (), {"table": lambda self, n: Q(tables.get(n, []))})()
    gaps = readiness.tenant_gaps(db)
    assert [g["what"] for g in gaps["oth"]] == [
        "Marked VAT-registered but its VAT number and registered address are missing, so invoices "
        "can't be valid tax invoices"]
    assert gaps["digg"] == []


def test_credit_note_carries_the_customer_vat_number(monkeypatch):
    cn = {**INVOICE, "doc_type": "credit_note", "invoice_number": "ACM-CN-00003",
          "notes": "Credit note against invoice ACM-INV-00012"}
    t = _text(cn, SUPPLIER, monkeypatch, saved={"vat": "4987654321", "address": "4 Wale Street"})
    assert "CREDIT NOTE" in t.upper() and "4987654321" in t and "ACM-INV-00012" in t

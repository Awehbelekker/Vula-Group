"""A bank payment notification is machine-generated — its figures come straight from the text
layer, no LLM in the money path. vula/ingestion/payment_notice.py, 2026-09-10."""
from vula.ingestion.payment_notice import parse

FNB = """NOTIFICATION OF PAYMENT
To Whom it may Concern:
First National Bank hereby confirms that the following payment instruction has been received:
Date Actioned
: 2026/07/20
Time Actioned
: 06:59:34
Trace ID
: QFBQ1YMQ
Payer Details
Payment From
*AWEH BE LEKKER (PTY) LTD
Cur/Amount
ZAR44000.00
Payee Details
Recipient/Account no
: ..891598
Name
: Edison Maunganidze
Bank
: FIRST NATIONAL BANK
Branch Code
: 250655
Reference
: HPC GEYSER
END OF NOTIFICATION
"""


def test_fnb_notice_every_field_exact():
    r = parse(FNB)
    assert r["category"] == "Proof of Payment"
    assert r["extraction"] == "deterministic"
    f = r["fields"]
    assert f["amount_cents"] == 4_400_000          # R44,000.00 — exact, not an LLM guess
    assert f["trace_id"] == "QFBQ1YMQ"
    assert f["payee_name"] == "Edison Maunganidze"
    assert f["payer"] == "AWEH BE LEKKER (PTY) LTD"   # leading * stripped
    assert f["payee_bank"] == "FIRST NATIONAL BANK"
    assert f["payee_branch_code"] == "250655"
    assert f["reference"] == "HPC GEYSER"
    assert f["date"] == "2026-07-20"
    assert "44,000.00" in r["summary"] and "Edison Maunganidze" in r["summary"]


def test_cents_precision_is_kept():
    r = parse(FNB.replace("ZAR44000.00", "ZAR27292.48"))
    assert r["fields"]["amount_cents"] == 2_729_248   # not 2729200, not 2729248.0


def test_two_copies_of_one_payment_share_a_trace_id():
    a = parse(FNB)["fields"]["trace_id"]
    b = parse(FNB.replace("Time Actioned\n: 06:59:34", "Time Actioned\n: 06:59:35"))["fields"]["trace_id"]
    assert a == b == "QFBQ1YMQ"   # the content fingerprint will collapse these


def test_not_a_payment_notice_returns_none():
    assert parse("Tax Invoice\nTotal Due: R1,200.00\nThank you for your business") is None
    assert parse("") is None


def test_missing_amount_falls_through_to_the_llm():
    broken = FNB.replace("Cur/Amount\nZAR44000.00", "Cur/Amount")
    assert parse(broken) is None


def test_fnb_match_is_flagged_verified():
    # FNB is the one matcher actually checked against real samples — the caller (_analyze_
    # document) fast-paths ONLY when this is True.
    assert parse(FNB)["verified"] is True


# --- Unverified generic matcher (no real sample of any of these banks yet) ------------------

ABSA_LIKE = """PROOF OF PAYMENT
ABSA Bank Limited confirms the following EFT has been processed.
Beneficiary Name: Edison Maunganidze
Beneficiary Reference: HPC GEYSER
Amount: R 44 000.00
Payment Date: 20 July 2026
Thank you for banking with ABSA.
"""


def test_unverified_generic_matcher_extracts_the_amount_and_is_flagged_unverified():
    r = parse(ABSA_LIKE)
    assert r["verified"] is False
    assert r["category"] == "Proof of Payment"
    assert r["fields"]["amount_cents"] == 4_400_000
    assert r["fields"]["payee_name"] == "Edison Maunganidze"
    assert "ABSA" in r["issuer"]
    assert "UNVERIFIED" in r["summary"]


def test_unverified_matcher_does_not_fire_on_an_ordinary_invoice():
    invoice = "Tax Invoice\nSupplier: ACME\nTotal Due: R1,200.00\nBeneficiary details below."
    assert parse(invoice) is None


def test_unverified_matcher_needs_an_amount():
    no_amount = "PROOF OF PAYMENT\nStandard Bank\nBeneficiary Name: Jane Doe\nReference: ABC123\n"
    assert parse(no_amount) is None


def test_fnb_text_never_reaches_the_unverified_matcher():
    # _MATCHERS tries _parse_fnb first — a real FNB notice must always come back verified=True,
    # never fall through to the generic matcher's verified=False.
    assert parse(FNB)["issuer"] == "FNB"
    assert parse(FNB)["verified"] is True

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

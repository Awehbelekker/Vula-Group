"""Customer phone numbers must not reach the retained logs (POPIA)."""
import logging

from core.log_redaction import PhoneRedactingFilter, _PHONE_RE


def _redact(msg: str) -> str:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, msg, (), None)
    PhoneRedactingFilter().filter(rec)
    return rec.getMessage()


def test_masks_sa_mobile_shapes():
    for raw in ("27821234567", "+27821234567", "0821234567", "+27 82 123 4567"):
        out = _redact(f"WhatsApp inbound from {raw}")
        assert raw not in out
        assert "…" in out


def test_keeps_the_last_two_digits_for_correlation():
    assert _redact("reply to 27821234567 failed").endswith("67 failed")


def test_does_not_eat_money_amounts_or_short_ids():
    for keep in ("R2,750.00", "order OTH-00099", "invoice 12345", "540 2000 m2 in stock"):
        assert keep in _redact(f"note: {keep}")


def test_leaves_clean_messages_untouched():
    msg = "schema check OK — all 16 migration sentinels present"
    assert _redact(msg) == msg


def test_regex_requires_enough_digits():
    # "0821234567" is 10 digits — a real number. "08210" is not.
    assert _PHONE_RE.search("call 0821234567")
    assert not _PHONE_RE.search("stand 08210")

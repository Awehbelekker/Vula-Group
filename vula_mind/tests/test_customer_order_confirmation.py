"""A customer's 'yes' only places the order when it isn't also a change request (2026-09-25)."""
import pytest

from core.skills.commerce_assistant import _is_clear_confirmation


@pytest.mark.parametrize("msg", ["yes", "Yes please", "confirm", "Ja", "yebo", "go ahead 👍",
                                 "yes that's correct", "place the order"])
def test_plain_confirmations(msg):
    assert _is_clear_confirmation(msg)


@pytest.mark.parametrize("msg", ["yes but change the address to 12 Main Rd",
                                 "yes, add another kilo of hake",
                                 "ja maar verander die adres",
                                 "yes wait, make it afternoon delivery",
                                 "no", "what's the total?",
                                 "yes and please also deliver to my office on Friday afternoon not the morning"])
def test_not_a_confirmation(msg):
    assert not _is_clear_confirmation(msg)


def test_language_tie_goes_to_english():
    from core.lang import detect_language
    assert detect_language("I'd like 2 more") == "en"
    assert detect_language("ek wil more bestel asseblief") == "af"

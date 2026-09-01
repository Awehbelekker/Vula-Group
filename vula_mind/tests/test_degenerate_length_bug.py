"""looks_degenerate must not punish an answer for being long.

Real incident (2026-09-01, gerflor): a 1421-character reply listing SFW catalogue prices — a
correct, useful answer of exactly the kind being asked for — was classified as degenerate and
replaced with "Sorry, something went wrong generating that reply". Found by querying the
vula-degenerate-output telemetry for the failure Ian spotted in the transcript.

Cause: the diversity signal was a RATIO, unique/length < 0.06. Plain text has a roughly fixed
alphabet (~27-60 distinct characters) however long it runs, so the ratio falls as length grows
and the check gets STRICTER the longer a normal reply is. Ordinary English tripped it at 648
characters. It also explains why no stored reply anywhere exceeded ~1200 characters — long
answers were being suppressed wholesale.

The genuine signal is an absolute floor: garbled output has 1-3 distinct characters; any real
sentence has well over 12, at any length.
"""
import pytest

from core.llm_router import DEGENERATE_OUTPUT_FALLBACK, looks_degenerate, substitute_if_degenerate


# ── the real incident ───────────────────────────────────────────────────────────

REAL_PRICE_LIST = (
    "The price list for various products is available in the SFW Catalogue - Main Hub 2026.pdf "
    "document. The prices are listed as follows:\n\n"
    "* Sports equipment:\n"
    "  - Standard tennis net only: R1,757.50\n"
    "  - Competition tennis net: R2,145.00\n"
    "  - Net posts (pair, galvanised): R4,320.00\n"
    "  - Basketball ring and net: R1,890.00\n"
    "* Flooring:\n"
    "  - Taraflex Sport M Performance: R485.00 per square metre\n"
    "  - Taraflex Sport M Comfort: R520.00 per square metre\n"
    "* Line marking:\n"
    "  - Single court marking: R4,500.00\n"
    "  - Multi court marking: R7,800.00\n"
) * 3  # the real one ran to 1421 chars


def test_a_long_real_price_list_is_not_degenerate():
    assert len(REAL_PRICE_LIST) > 1200, "must exercise the length that actually failed"
    assert looks_degenerate(REAL_PRICE_LIST) is False


def test_a_long_price_list_is_not_replaced_by_the_error_message():
    out = substitute_if_degenerate(REAL_PRICE_LIST, skill="commerce_admin", tenant_id="gerflor")
    assert out == REAL_PRICE_LIST
    assert out != DEGENERATE_OUTPUT_FALLBACK


@pytest.mark.parametrize("mult", [1, 2, 4, 8, 16])
def test_ordinary_english_is_never_degenerate_at_any_length(mult):
    """The core regression: length alone must not decide this."""
    base = ("Thanks for that. The delivery is scheduled for Monday morning between ten and "
            "twelve, and the driver will call you first. Please let me know if anything "
            "changes. ")
    assert looks_degenerate(base * mult) is False, f"failed at {len(base * mult)} chars"


# ── the real garbling this exists to catch ──────────────────────────────────────

def test_the_original_incident_is_still_caught():
    """off-the-hook, 2026-08-22: ~1000 literal '!' sent to the owner."""
    assert looks_degenerate("!" * 1000) is True


def test_a_slow_repetition_loop_is_still_caught():
    assert looks_degenerate("!! " * 400) is True


def test_a_short_alphabet_loop_is_still_caught():
    assert looks_degenerate("ab ab ab " * 200) is True


def test_garbled_output_is_still_substituted():
    assert substitute_if_degenerate("!" * 1000, skill="commerce_admin") == DEGENERATE_OUTPUT_FALLBACK


# ── things that must stay unflagged ─────────────────────────────────────────────

def test_short_replies_are_never_flagged():
    assert looks_degenerate("Yes 👍") is False
    assert looks_degenerate("R160.00 per kg") is False
    assert looks_degenerate("") is False


def test_an_emoji_heavy_whatsapp_reply_is_fine():
    assert looks_degenerate("Order confirmed ✅ Delivery Monday 🚚 Thanks so much! 🙏") is False


def test_a_long_invoice_list_is_fine():
    """The shape of a real off-the-hook reply: 79 unpaid invoices."""
    body = "\n".join(f"{i}. OFF-INV-{i:05d} — R{i * 137}.50 — outstanding" for i in range(1, 80))
    assert looks_degenerate(body) is False


# ── the two-tier floor (added after the first fix regressed a real case) ────────
# An absolute floor alone was too blunt: "R100.00, R100.00, R100.00" is 6 distinct characters
# and perfectly valid, while "!! !! !! !!" is 2 and is garbage — and a length-only gate let the
# short garbled case slip through. Short text needs a low floor; long text needs a real one.

def test_short_garbling_is_still_caught():
    assert looks_degenerate("!! !! !! !! !! !! !! !! !! !! !! !! !! !! !! !!") is True


def test_a_repeated_real_price_is_not_garbling():
    assert looks_degenerate("R100.00, R100.00, R100.00, R100.00, R100.00, R100.00") is False


def test_the_two_floors_separate_these_by_character_diversity():
    garbled = "!! !! !! !! !! !! !! !! !! !! !! !! !! !! !! !!"
    legit = "R100.00, R100.00, R100.00, R100.00, R100.00, R100.00"
    assert len(set(garbled)) < 4 <= len(set(legit)), "the signal is diversity, not length"

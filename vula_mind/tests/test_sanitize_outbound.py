"""Tests for _sanitize_outbound (vula/api/whatsapp.py) — the final choke-point every outbound
message passes through before reaching a real customer/owner, regardless of which skill/path
produced it. Covers both the markdown-link fix (2026-09-01, real complaint: "the link you can't
select") and the pre-existing raw-JSON-leak guard (2026-07-17) — no dedicated test file existed
for either before this.
"""
import pytest

from vula.api.whatsapp import _sanitize_outbound


# ── markdown link -> plain, tappable URL (2026-09-01) ────────────────────────────

def test_markdown_link_becomes_plain_url_with_label():
    out = _sanitize_outbound("Please checkout here: [Click here](https://offthehook.co.za/cart) to pay.")
    assert out == "Please checkout here: Click here: https://offthehook.co.za/cart to pay."
    assert "[" not in out and "]" not in out


def test_markdown_link_with_empty_label_becomes_bare_url():
    out = _sanitize_outbound("Here: [](https://offthehook.co.za/cart)")
    assert out == "Here: https://offthehook.co.za/cart"


def test_plain_url_is_left_untouched():
    text = "Plain text with a real url https://offthehook.co.za/cart already."
    assert _sanitize_outbound(text) == text


def test_markdown_link_inside_a_json_leaked_message_field_is_also_fixed():
    leaked = '{"message": "Order confirmed, pay here [click](https://x.com/cart)"}'
    out = _sanitize_outbound(leaked)
    assert out == "Order confirmed, pay here click: https://x.com/cart"


def test_multiple_markdown_links_all_get_fixed():
    out = _sanitize_outbound("See [A](https://a.com) or [B](https://b.com).")
    assert out == "See A: https://a.com or B: https://b.com."


# ── raw JSON leak guard (2026-07-17, pre-existing — locking in current behaviour) ─

def test_non_json_text_passes_through_unchanged():
    text = "Your order OFF-00042 is confirmed. Total: R450.00."
    assert _sanitize_outbound(text) == text


def test_json_with_message_field_is_unwrapped():
    leaked = '{"message": "Your order is confirmed."}'
    assert _sanitize_outbound(leaked) == "Your order is confirmed."


def test_json_with_no_recognised_field_gets_a_safe_apology():
    leaked = '{"name": "create_quote", "parameters": {"items": []}}'
    out = _sanitize_outbound(leaked)
    assert out == "Sorry — I got a bit tangled up there. Could you say that again? 🙏"


def test_json_wrapped_in_code_fence_is_still_caught():
    leaked = '```json\n{"reply": "Here you go."}\n```'
    assert _sanitize_outbound(leaked) == "Here you go."


def test_not_actually_json_despite_starting_with_brace_passes_through():
    text = "{this is just a sentence that happens to start with a brace"
    assert _sanitize_outbound(text) == text


def test_empty_message_returns_empty():
    assert _sanitize_outbound("") == ""
    assert _sanitize_outbound(None) == ""


# ── generic "anything else?" closers (2026-09-01) ───────────────────────────────
# CONVERSATION_RULES has forbidden this since 2026-08-08 and it is still emitted: a real Gerflor
# reply closed "Let me know if there's anything else I can help you with." after saving a pricing
# rule. Another prompt rule that doesn't hold on its own, so it gets a deterministic strip.

REAL_CLOSER = ("I've saved the pricing policy: Per-Square price list is NETT, DT gets 7% trade "
               "discount excluding Mactile which is NETT. "
               "Let me know if there's anything else I can help you with.")


def test_the_real_gerflor_closer_is_stripped():
    out = _sanitize_outbound(REAL_CLOSER)
    assert out.endswith("which is NETT.")
    assert "anything else" not in out.lower()


@pytest.mark.parametrize("text,keep", [
    ("Order confirmed. Is there anything else I can help you with?", "Order confirmed."),
    ("Done. Let me know if there is anything else.", "Done."),
    ("Noted. Feel free to ask if there's anything else I can help with!", "Noted."),
    ("Saved. Anything else I can help with?", "Saved."),
    ("Klaar. Laat my weet as daar iets anders is.", "Klaar."),
])
def test_generic_closers_are_stripped(text, keep):
    assert _sanitize_outbound(text) == keep


@pytest.mark.parametrize("text", [
    # A SPECIFIC follow-up names a real next step and is a genuine question — it must survive.
    "I've added 2kg of Hake to your cart. Let me know if you'd like the Prawns as well.",
    "Let me know which delivery slot suits you — morning or afternoon.",
    "Shall I add the Hake Fillets to your cart?",
    "Is there a specific reference number on that invoice?",
    "The price is R198.00 per square metre.",
])
def test_specific_follow_ups_are_kept(text):
    assert _sanitize_outbound(text) == text


def test_a_message_that_is_only_a_closer_is_not_emptied():
    """Sending an empty WhatsApp message would be worse than sending a bland one."""
    only = "Is there anything else I can help you with?"
    assert _sanitize_outbound(only) == only


# ── WhatsApp markup (2026-09-01) ────────────────────────────────────────────────
# WhatsApp's markup is single-character: *bold*, _italic_, ~strike~. Markdown's doubled forms
# render as literal punctuation on a phone. Confirmed in real traffic: 6 of 187 recent replies
# carried "**...**", e.g. "**New Project: Belladonna**" sent to DIGG exactly like that.

def test_markdown_bold_becomes_whatsapp_bold():
    assert _sanitize_outbound("**New Project: Belladonna**") == "*New Project: Belladonna*"


def test_markdown_bold_mid_sentence():
    out = _sanitize_outbound("The total is **R42,522.48** including VAT.")
    assert out == "The total is *R42,522.48* including VAT."


def test_markdown_heading_becomes_bold():
    assert _sanitize_outbound("## Order Summary\nTwo items.") == "*Order Summary*\nTwo items."


def test_double_underscore_becomes_italic():
    assert _sanitize_outbound("That is __urgent__ today.") == "That is _urgent_ today."


@pytest.mark.parametrize("text", [
    "Use 2 * 3 for the area.",            # a bare asterisk is not emphasis
    "* Hake Fillets\n* Snoek",            # an asterisked bullet list must survive
    "Delivery is 5**",                    # unmatched markers left alone
    "snake_case_name stays intact",       # single underscores untouched
])
def test_non_emphasis_asterisks_and_underscores_survive(text):
    assert _sanitize_outbound(text) == text


# ── long replies are split, not truncated ───────────────────────────────────────
# The send path did message[:4096], silently cutting mid-sentence. Latent only because long
# replies were separately being discarded by the degenerate-output length bug; with that fixed,
# a real 79-invoice list would now be cut off.

def test_a_short_message_is_one_part():
    from vula.api.whatsapp import _split_for_whatsapp
    assert _split_for_whatsapp("Order confirmed.") == ["Order confirmed."]


def test_a_long_reply_splits_on_a_natural_boundary():
    from vula.api.whatsapp import _split_for_whatsapp
    body = "\n".join(f"{i}. OFF-INV-{i:05d} — R{i * 137}.50 outstanding" for i in range(1, 200))
    parts = _split_for_whatsapp(body)
    assert len(parts) > 1
    assert all(len(p) <= 3900 for p in parts)
    assert all(p.strip() for p in parts), "no empty parts"
    # nothing is lost: every invoice id still appears somewhere
    joined = "\n".join(parts)
    for i in (1, 99, 199):
        assert f"OFF-INV-{i:05d}" in joined


def test_splitting_does_not_cut_mid_word_when_avoidable():
    from vula.api.whatsapp import _split_for_whatsapp
    body = ("The delivery is scheduled for Monday morning. " * 200).strip()
    parts = _split_for_whatsapp(body)
    assert len(parts) > 1
    for p in parts[:-1]:
        assert not p.endswith(("Th", "Mo", "deliver")), "cut mid-word"


def test_empty_message_yields_no_parts():
    from vula.api.whatsapp import _split_for_whatsapp
    assert _split_for_whatsapp("") == []


# ── chunking as style (2026-09-02) ──────────────────────────────────────────────
# A dense block is the last obviously bot-like thing about a Vula reply: a person sends the
# answer, then the follow-up question, as separate messages. Only applied to a reply that is
# genuinely long AND already has paragraph structure, and it never breaks inside a paragraph or
# a list, so a split can't land mid-thought.

def _parts(text):
    from vula.api.whatsapp import _natural_parts
    return _natural_parts(text)


def test_a_short_reply_is_never_split():
    for t in ["Order confirmed.",
              "R160.00 per kg.",
              "Yes — Monday morning works. I'll put you down for 10am."]:
        assert _parts(t) == [t]


def test_a_long_reply_with_no_paragraphs_is_left_alone():
    """Nothing to split on — better one block than a cut mid-thought."""
    t = "The delivery is scheduled for Monday morning. " * 30
    assert _parts(t.strip()) == [t.strip()]


def test_a_structured_long_reply_becomes_a_few_messages():
    text = (
        "Here are our fresh fish prices for this week.\n\n"
        + "\n".join(f"* Line item {i} — R{100 + i}.00 per kg" for i in range(40))
        + "\n\nAll of these are available for delivery tomorrow if you order before 4pm.\n\n"
        "Would you like me to add any of these to your cart?"
    )
    parts = _parts(text)
    assert 1 < len(parts) <= 3
    assert all(p.strip() for p in parts)
    # the closing question travels as its own message, like a person would send it
    assert parts[-1].endswith("add any of these to your cart?")


def test_a_split_never_lands_inside_a_paragraph():
    text = ("First paragraph. " * 40).strip() + "\n\n" + ("Second paragraph. " * 40).strip()
    for p in _parts(text):
        assert not p.startswith("paragraph"), "split landed mid-sentence"
        assert p == p.strip()


def test_never_more_than_three_messages():
    text = "\n\n".join(f"Block number {i}. " * 20 for i in range(30))
    assert len(_parts(text)) <= 3


def test_nothing_is_lost_when_splitting():
    text = ("Intro paragraph here.\n\n"
            + "\n".join(f"* Item {i}" for i in range(60))
            + "\n\nClosing question?")
    parts = _parts(text)
    joined = "\n\n".join(parts)
    for probe in ("Intro paragraph here.", "* Item 0", "* Item 59", "Closing question?"):
        assert probe in joined


def test_empty_input_is_safe():
    assert _parts("") == []
    assert _parts("   ") == []


# ── a data object is an answer wearing the wrong clothes (2026-09-02) ───────────
# Reproduced on the real off-the-hook tenant: finance_admin answered "how many unpaid invoices
# and what's the total?" with {"unpaid_invoices": 0, "total": "R0"} — correct — and the owner
# received "Sorry, I got a bit tangled up there" because the JSON guard suppressed it wholesale.
# A leaked TOOL CALL is unusable and must still be suppressed; flat data should be read out.

def test_the_real_finance_answer_is_rendered_not_apologised_for():
    out = _sanitize_outbound('{"unpaid_invoices": 0, "total": "R0"}')
    assert out == "Unpaid invoices: 0. Total: R0."
    assert "tangled" not in out


def test_a_leaked_tool_call_is_still_suppressed():
    for leak in ['{"name": "create_quote", "parameters": {"items": []}}',
                 '{"name": "add_to_cart", "arguments": {"product": "Hake"}}']:
        assert _sanitize_outbound(leak).startswith("Sorry")


def test_a_message_field_still_wins_over_rendering():
    out = _sanitize_outbound('{"message": "Your order is confirmed.", "total": 450}')
    assert out == "Your order is confirmed."


@pytest.mark.parametrize("leak", [
    '{"a": {"nested": 1}}',          # nested — would render as junk
    '{"items": [1, 2, 3]}',          # list value
    '{"x": null}',                   # nothing to say
    '{}',                            # empty
])
def test_anything_not_plainly_presentable_still_apologises(leak):
    assert _sanitize_outbound(leak).startswith("Sorry")


def test_an_over_long_object_is_not_rendered_as_a_sentence():
    big = "{" + ", ".join(f'"k{i}": {i}' for i in range(9)) + "}"
    assert _sanitize_outbound(big).startswith("Sorry")


def test_rendered_keys_read_as_words():
    out = _sanitize_outbound('{"outstanding_balance": "R1,200.00"}')
    assert out == "Outstanding balance: R1,200.00."

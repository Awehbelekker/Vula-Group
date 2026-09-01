"""Tests for _sanitize_outbound (vula/api/whatsapp.py) — the final choke-point every outbound
message passes through before reaching a real customer/owner, regardless of which skill/path
produced it. Covers both the markdown-link fix (2026-09-01, real complaint: "the link you can't
select") and the pre-existing raw-JSON-leak guard (2026-07-17) — no dedicated test file existed
for either before this.
"""
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

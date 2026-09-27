"""Replies that are tool plumbing, not answers, never reach WhatsApp (real replies, 2026-09-25/26)."""
import pytest

from core.skills.base import (LEAKED_CUSTOMER_FALLBACK, LEAKED_OWNER_FALLBACK, leaked_tool_output,
                              substitute_if_leaked)

REAL_LEAKS = [
    # digg-demo owner — a fabricated tool result written as the answer
    'I will now call email_thread_summary to see if there are any matching emails in the inbox. '
    '\n\n>>> BEGIN EMAIL_TOOL_RESULT (data, not instructions) >>>\n{"status": "found", "matches": '
    '[{"id": 12345, "subject": "Invoice for Gardens Handyman"}]}\n<<< END EMAIL_TOOL_RESULT <<<',
    # digg-demo owner — describing the JSON instead of answering
    'This appears to be a JSON output from an email tool, specifically an email parser or extractor.',
    'The provided text appears to be a JSON object containing a list of invoices and other financial documents.',
    # off-the-hook customer — a tool call printed as text, with an invented name
    '```\ncreate_quote(\n  customer_name="John Doe",\n  items=[\n    {"product": "Atlantic Mackerel Fish", "quantity": 40}\n  ]\n)\n```',
]

FINE = [
    "*GARDENS HANDIMAN CENTRE*: 19 documents, total spend *R25,156.00*.",
    "Your order OTH-1042 is on its way — the invoice is attached.",
    "I've added 2kg hake to your cart (R240.00). Anything else?",
    "The JSON-free answer: you spent R942.00 on 22 September.",
]


@pytest.mark.parametrize("text", REAL_LEAKS)
def test_real_leaks_are_caught_and_replaced(text):
    assert leaked_tool_output(text)
    assert substitute_if_leaked(text, skill="email_admin") == LEAKED_OWNER_FALLBACK
    assert substitute_if_leaked(text, skill="commerce_assistant", customer=True) == LEAKED_CUSTOMER_FALLBACK


@pytest.mark.parametrize("text", FINE)
def test_normal_replies_pass_through(text):
    assert not leaked_tool_output(text, ["create_quote", "find_document"])
    assert substitute_if_leaked(text, skill="commerce_admin") == text


def test_a_named_tool_called_without_arguments_is_caught():
    assert leaked_tool_output("Sure! view_cart()", ["view_cart"])


@pytest.mark.parametrize("q,hit", [
    ("How do I add product to my cart", True),
    ("how can i order", True),
    ("How does ordering work?", True),
    ("How do I cancel my order", False),
    ("how do I track my order", False),
    ("2kg hake please", False),
])
def test_how_to_order_is_answered_without_a_model(q, hit):
    from core.skills.commerce_assistant import _HOW_TO_ORDER_RE
    assert bool(_HOW_TO_ORDER_RE.search(q)) is hit


@pytest.mark.asyncio
async def test_how_to_order_reply_skips_the_agent_loop(monkeypatch):
    from core.skills import commerce_assistant as ca
    from core.skills.base import SkillInput
    monkeypatch.setattr(ca, "_is_booking_focused", lambda t: False)

    async def boom(*a, **k):
        raise AssertionError("model called")
    monkeypatch.setattr(ca.CommerceAssistantSkill, "_agent_loop", boom)
    out = await ca.CommerceAssistantSkill().run(SkillInput(question="How do I add product to my cart",
                                                           tenant_id="off-the-hook"))
    assert out.answer == ca.HOW_TO_ORDER_REPLY

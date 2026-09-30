"""The daily conversation check flags the real failures from the week of 23–29 Sep 2026."""
from vula.conversation_check import classify, render_text

MAURITIUS = ("We do not deliver to Mauritius. We deliver to these areas: Table View, Sunningdale, "
             "West Beach, Parklands.")


def test_the_real_repeat_is_flagged():
    assert "repeat" in classify(MAURITIUS, MAURITIUS, "Okay")


def test_raw_tool_text_and_json_are_flagged():
    assert "leak" in classify('{"status": "not_found_live", "message": "No emails found"}', None, None)
    assert "leak" in classify('```\ncreate_quote(\n  customer_name="John Doe")\n```', None, None)
    assert "leak" in classify("Relevant knowledge:\n[conv_Church_layout.txt]: neighbour, the property", None, None)


def test_cant_answer_and_promise():
    assert "cant_answer" in classify("I can’t answer that. You can ask the shop's human team.", None, None)
    assert "cant_answer" in classify("Sorry, I couldn't work that out — could you rephrase?", None, None)
    assert "promise" in classify("I'll find out the price of Atlantic Mackerel for you and someone "
                                 "will get back to you.", None, None)


def test_caveat_and_pushback():
    assert "caveat" in classify("Answer.\n\n⚠️ Worth double-checking — if anything's off, tell me.", None, None)
    assert "pushback" in classify("Here you go", None, "That's not what I asked")
    assert "pushback" in classify("Here you go", None, "?")


def test_a_good_reply_is_not_flagged():
    assert classify("*GARDENS HANDIMAN CENTRE*: 16 documents, total spend *R21,256.00*.", None,
                    "Thanks") == []


def test_report_text():
    d = {"day": "2026-09-24", "totals": {"replies": 2, "flagged": 1}, "tenants": [
        {"tenant_id": "off-the-hook", "replies": 2, "flagged": 1, "unfollowed_promises": 1,
         "counts": {"repeat": 1, "leak": 0, "cant_answer": 0, "promise": 0, "caveat": 0, "pushback": 0},
         "examples": {"repeat": [{"question": "Okay", "reply": MAURITIUS, "next": None}]}}]}
    out = render_text(d)
    assert "off-the-hook — 1/2" in out and "no escalation raised" in out and "Mauritius" in out

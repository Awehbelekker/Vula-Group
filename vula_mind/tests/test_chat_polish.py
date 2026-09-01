"""Polish fixes from reading real transcripts (2026-09-01).

Two things aggregate stats missed but full transcripts showed plainly:

1. An off-the-hook customer wrote English throughout and got a reply opening "Sawubona!".
   Their session had preferred_language='en' stored correctly, so this was not a data bug —
   the prompt advertises isiZulu/isiXhosa/Sesotho and, for a KNOWN-English customer, added no
   language instruction at all, leaving the model free to sprinkle.

2. A Gerflor reply: "The cost of Creation 55 ref 0504 TWIST is not explicitly stated in the
   provided tool results. However, the results do provide information on various Gerflor
   products, including their prices." Claiming absence while describing what it found.
"""
from core.skills.base import CONVERSATION_RULES, HONESTY_RULES, behaviour_preamble
from core.skills.commerce_assistant import CommerceAssistantSkill

TENANT = "off-the-hook"


# ── language mirroring ──────────────────────────────────────────────────────────

def test_shared_rules_forbid_greeting_in_an_unused_language():
    low = CONVERSATION_RULES.lower()
    assert "mirror only" in low
    assert "hasn't used themselves" in low


def test_a_known_english_customer_gets_an_explicit_english_instruction():
    """The gap: name == 'English' previously produced NO language block at all."""
    prompt = CommerceAssistantSkill()._system_prompt(TENANT, kb_context="",
                                                     preferred_language="en")
    assert "This customer writes in English" in prompt
    assert "do not open with a greeting or phrase in another language" in prompt


def test_a_non_english_customer_still_gets_their_own_language():
    prompt = CommerceAssistantSkill()._system_prompt(TENANT, kb_context="",
                                                     preferred_language="af")
    assert "Afrikaans" in prompt
    assert "This customer writes in English" not in prompt


def test_unknown_language_adds_no_block():
    prompt = CommerceAssistantSkill()._system_prompt(TENANT, kb_context="")
    assert "This customer writes in English" not in prompt
    assert "This customer usually speaks" not in prompt


# ── no self-contradicting "not found" ───────────────────────────────────────────

def test_honesty_rules_forbid_claiming_absence_while_describing_a_find():
    low = HONESTY_RULES.lower()
    assert "never say something isn't available and then describe having found it" in low
    assert "name what you did find" in low


def test_the_rule_reaches_a_real_skill_prompt():
    prompt = CommerceAssistantSkill()._system_prompt(TENANT, kb_context="")
    assert "then describe having found it" in prompt


def test_preamble_carries_both_rules():
    p = behaviour_preamble(agentic=True).lower()
    assert "mirror only" in p
    assert "then describe having found it" in p

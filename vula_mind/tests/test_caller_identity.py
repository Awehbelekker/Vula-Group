"""Vula must not treat the tenant's own owner/staff as a client.

Confirmed live on DIGG (2026-09-17), from Judy Downing's real WhatsApp thread — she is the
practice OWNER and correctly registered as one in vula_team_members, yet:

  * she asked Vula to group her own supplier invoices and got back
    "Thanks for your question! Let me check with the team and get right back to you 🙏" —
    a customer holding line aimed at the person who IS the team;
  * a general question of hers ("how can I colour a cast iron fireplace?") was answered out of
    a *client's* Canal West HOA guide;
  * the correction she researched herself was recited back to her as though she'd asked it.

Two causes, both about what the prompt says rather than what the database holds:
  1. ChatHistoryDB.format_for_prompt() hardcoded the label "Client" for every user turn, so the
     model read its own history back as "Client: <the owner's message>";
  2. the knowledge/RAG path never resolved or passed caller identity at all (its metadata key
     is literally `customer_phone`), so no skill knew who it was talking to — only the
     commerce-admin path did that lookup.

These pin both, plus the role-aware escalation, and — just as importantly — that a genuine
customer conversation is completely unchanged.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.base import behaviour_preamble, caller_block
from vula.chat.history import ChatHistoryDB, ChatMessage


# ── The history label ─────────────────────────────────────────────────────────────

def _history_db(messages):
    db = ChatHistoryDB()
    db.get = MagicMock(return_value=messages)
    return db


_THREAD = [
    ChatMessage(role="user", text="Group all the Jack Hammer invoices by material", created_at=""),
    ChatMessage(role="assistant", text="Sure — here's the breakdown.", created_at=""),
]


def test_owner_turns_are_labelled_with_their_real_identity_not_client():
    out = _history_db(_THREAD).format_for_prompt(
        "digg-demo", "27827077080", user_label="Judy Downing (owner)")
    assert "Judy Downing (owner): Group all the Jack Hammer invoices by material" in out
    assert "Client:" not in out


def test_customer_thread_still_says_client_by_default():
    """The default must not move — every customer-facing path relies on it."""
    out = _history_db(_THREAD).format_for_prompt("off-the-hook", "27820001111")
    assert "Client: Group all the Jack Hammer invoices by material" in out


def test_assistant_turns_are_unaffected_by_the_label():
    out = _history_db(_THREAD).format_for_prompt("digg-demo", "x", user_label="Judy Downing (owner)")
    assert "Vula AI: Sure — here's the breakdown." in out


# ── The prompt block ──────────────────────────────────────────────────────────────

def test_caller_block_names_the_person_and_their_role():
    block = caller_block("Judy Downing", "owner")
    assert "Judy Downing (owner)" in block
    assert "NOT a customer" in block


def test_caller_block_is_empty_for_an_unknown_caller():
    """An ordinary customer must get exactly the prompt they got before this existed."""
    assert caller_block("", "") == ""
    assert caller_block(None, None) == ""


def test_caller_block_survives_a_name_or_role_being_missing():
    assert "owner" in caller_block("", "owner")
    assert "Judy Downing" in caller_block("Judy Downing", "")


def test_behaviour_preamble_includes_the_caller_block_when_identity_is_known():
    with_caller = behaviour_preamble(caller_name="Judy Downing", caller_role="owner")
    without = behaviour_preamble()
    assert "Judy Downing (owner)" in with_caller
    assert "Judy Downing" not in without
    # the rest of the policy is untouched — this only ever appends
    assert len(with_caller) > len(without)


def test_behaviour_preamble_forbids_customer_holding_lines_for_insiders():
    block = behaviour_preamble(caller_name="Judy Downing", caller_role="owner")
    assert "let me check with the team" in block.lower()


# ── Identity lookup ───────────────────────────────────────────────────────────────

def _team_db(rows):
    mock_db = MagicMock()
    (mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value
     .execute.return_value) = MagicMock(data=rows)
    return mock_db


def test_caller_identity_matches_the_owner_by_normalised_number():
    from vula.api.whatsapp import _caller_identity
    rows = [{"name": "Judy Downing", "whatsapp": "27827077080", "role": "owner"},
            {"name": "Richard", "whatsapp": "27645755210", "role": "sales_rep"}]
    with patch("vula.commerce.service._client", return_value=_team_db(rows)):
        # the local 0-prefixed form must resolve to the same person as the 27… form
        assert _caller_identity("digg-demo", "0827077080") == ("Judy Downing", "owner")
        assert _caller_identity("digg-demo", "27827077080") == ("Judy Downing", "owner")


def test_caller_identity_returns_none_for_an_unknown_number():
    from vula.api.whatsapp import _caller_identity
    rows = [{"name": "Judy Downing", "whatsapp": "27827077080", "role": "owner"}]
    with patch("vula.commerce.service._client", return_value=_team_db(rows)):
        assert _caller_identity("digg-demo", "27820009999") == (None, None)


def test_caller_identity_fails_open_on_a_db_error():
    from vula.api.whatsapp import _caller_identity
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        assert _caller_identity("digg-demo", "27827077080") == (None, None)


def test_is_insider_covers_the_team_roles_only():
    from vula.api.whatsapp import _is_insider
    for role in ("owner", "manager", "admin", "staff", "sales_rep", "OWNER"):
        assert _is_insider(role), role
    for role in ("customer", "client", "", None, "viewer"):
        assert not _is_insider(role), role


# ── Role-aware escalation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_does_not_get_the_customer_holding_line():
    """The exact 2026-09-17 DIGG symptom: the owner asked about her own invoices and was told
    Vula would "check with the team" — she IS the team."""
    from vula.api.whatsapp import _maybe_escalate_and_learn

    with (
        patch("vula.escalation.find_learned_answer", new=AsyncMock(return_value=None)),
        patch("vula.escalation.create_escalation") as mock_create,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        reply = await _maybe_escalate_and_learn(
            "digg-demo", "27827077080", "Group all the Jack Hammer invoices by material",
            "I don't know that one.", confidence=0.1, caller_role="owner")

    assert reply == "I don't know that one."      # Vula's real answer, not a holding line
    assert "check with the team" not in reply
    mock_create.assert_not_called()               # no helper ping either
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_a_real_customer_still_gets_escalated_and_held():
    """The whole escalate-and-learn loop must be untouched for actual customers."""
    from vula.api.whatsapp import _maybe_escalate_and_learn

    with (
        patch("vula.escalation.find_learned_answer", new=AsyncMock(return_value=None)),
        patch("vula.escalation.create_escalation",
              return_value={"id": "e1", "helper_phone": "27821112222", "helper_name": "Staci"}),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        reply = await _maybe_escalate_and_learn(
            "off-the-hook", "27820001111", "Do you deliver to Bellville?",
            "I don't know that one.", confidence=0.1)

    assert "check with the team" in reply
    mock_send.assert_called_once()                # the helper still gets pinged


@pytest.mark.asyncio
async def test_an_insider_still_gets_a_learned_answer():
    """Skipping the escalate-and-hold half must not cost them a better answer."""
    from vula.api.whatsapp import _maybe_escalate_and_learn

    with (
        patch("vula.escalation.find_learned_answer",
              new=AsyncMock(return_value="Jack Hammer bill on 12 Aug was for rebar.")),
        patch("vula.escalation.create_escalation") as mock_create,
    ):
        reply = await _maybe_escalate_and_learn(
            "digg-demo", "27827077080", "What was the Jack Hammer invoice for?",
            "I don't know that one.", confidence=0.1, caller_role="owner")

    assert reply == "Jack Hammer bill on 12 Aug was for rebar."
    mock_create.assert_not_called()


# ── End-to-end wiring on the path DIGG actually uses ──────────────────────────────

@pytest.mark.asyncio
async def test_admin_rag_path_labels_history_and_passes_identity_to_the_skills():
    from vula.api.whatsapp import _handle_message

    mock_history_db = MagicMock()
    mock_history_db.save = MagicMock()
    mock_history_db.format_for_prompt = MagicMock(return_value="")

    bridge = MagicMock()
    bridge.get_or_create_session = AsyncMock(return_value={"id": "s1"})
    bridge.append_message = AsyncMock(return_value=None)

    with (
        patch("vula.api.whatsapp._maybe_helper_escalation_answer", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._maybe_allocate_pending_expense", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._maybe_bank_review_answer", new=AsyncMock(return_value=None)),
        patch("vula.integrations.notify.handle_preference_command", return_value=None),
        patch("vula.integrations.doc_filing.resolve_pending_document", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._active_project_for_phone", return_value=None),
        patch("vula.api.whatsapp._caller_identity", return_value=("Judy Downing", "owner")),
        patch("vula.chat.history.get_db", return_value=mock_history_db),
        patch("vula.api.whatsapp._rag_reply",
              new=AsyncMock(return_value="Here's the answer.")) as mock_rag,
        patch("vula.api.whatsapp._maybe_escalate_and_learn",
              new=AsyncMock(side_effect=lambda tid, ph, txt, reply, conf, caller_role=None: reply)),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)),
        patch("vula.commerce.service", bridge),
        patch("vula.api.whatsapp._maybe_capture_owner_correction", new=AsyncMock(return_value=None)),
    ):
        await _handle_message("27827077080", "Group the Jack Hammer invoices", "wamid.9",
                              route_tenant_id="digg-demo")

    # the history the model reads back must name her, not call her a client
    assert mock_history_db.format_for_prompt.call_args.kwargs["user_label"] == "Judy Downing (owner)"
    # and the skills must be told who they're talking to
    assert mock_rag.call_args.kwargs["caller_name"] == "Judy Downing"
    assert mock_rag.call_args.kwargs["caller_role"] == "owner"


@pytest.mark.asyncio
async def test_unknown_sender_on_a_tenant_line_keeps_the_client_label():
    """A stranger messaging DIGG's number is a prospective client — unchanged behaviour."""
    from vula.api.whatsapp import _handle_message

    mock_history_db = MagicMock()
    mock_history_db.save = MagicMock()
    mock_history_db.format_for_prompt = MagicMock(return_value="")

    bridge = MagicMock()
    bridge.get_or_create_session = AsyncMock(return_value={"id": "s1"})
    bridge.append_message = AsyncMock(return_value=None)

    with (
        patch("vula.api.whatsapp._maybe_helper_escalation_answer", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._maybe_allocate_pending_expense", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._maybe_bank_review_answer", new=AsyncMock(return_value=None)),
        patch("vula.integrations.notify.handle_preference_command", return_value=None),
        patch("vula.integrations.doc_filing.resolve_pending_document", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._active_project_for_phone", return_value=None),
        patch("vula.api.whatsapp._caller_identity", return_value=(None, None)),
        patch("vula.chat.history.get_db", return_value=mock_history_db),
        patch("vula.api.whatsapp._rag_reply", new=AsyncMock(return_value="Here's the answer.")),
        patch("vula.api.whatsapp._maybe_escalate_and_learn",
              new=AsyncMock(side_effect=lambda tid, ph, txt, reply, conf, caller_role=None: reply)),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)),
        patch("vula.commerce.service", bridge),
        patch("vula.api.whatsapp._maybe_capture_owner_correction", new=AsyncMock(return_value=None)),
    ):
        await _handle_message("27820009999", "Do you do house plans?", "wamid.10",
                              route_tenant_id="digg-demo")

    assert mock_history_db.format_for_prompt.call_args.kwargs["user_label"] == "Client"

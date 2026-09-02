"""A rep's photo with no caption must be read and asked about, not filed in silence.

Ian, 2026-09-02: "not all users will add captions. reps might be busy and miss it and it will
not be logged. vula could ask followup questions."

Before this, the vision pass only ran when `caption.strip()` was non-empty. An uncaptioned photo
that wasn't a receipt went straight to document ingest — so a screenshot of a company's details,
or a business card snapped between meetings, disappeared into storage with no acknowledgement
and nothing logged.

The ordering matters more than the feature: an uncaptioned RECEIPT must still reach the books
through the existing scanner (_handle_media), which is a real working flow. The new path is
deliberately placed after it and only runs when the receipt path declined the photo.
"""
import inspect

import vula.api.whatsapp as wa


SRC = inspect.getsource(wa.whatsapp_webhook) if hasattr(wa, "whatsapp_webhook") else ""
FILE_SRC = inspect.getsource(wa)


def test_the_uncaptioned_rep_branch_exists():
    assert "The rep sent this photo with no caption" in FILE_SRC


def test_it_only_fires_for_an_uncaptioned_photo():
    """A captioned photo keeps the original path — this is an addition, not a replacement."""
    assert "not caption.strip()" in FILE_SRC
    assert 'msg_type == "image" and caption.strip()' in FILE_SRC, \
        "the original captioned path must still exist"


def test_it_runs_after_the_receipt_scanner_not_before():
    """An uncaptioned receipt must still reach the books, not the agent."""
    receipt_call = FILE_SRC.index("handled = await _handle_media(")
    new_branch = FILE_SRC.index("The rep sent this photo with no caption")
    assert receipt_call < new_branch, \
        "the receipt scanner must get first refusal on an uncaptioned photo"


def test_it_only_runs_when_the_receipt_path_declined():
    branch = FILE_SRC[FILE_SRC.index("An UNCAPTIONED photo from a rep"):]
    head = branch[:branch.index("description = await _describe_photo_for_rep")]
    assert "not handled" in head, "must not divert a photo the receipt scanner already took"


def test_it_is_scoped_to_registered_sales_reps():
    branch = FILE_SRC[FILE_SRC.index("An UNCAPTIONED photo from a rep"):]
    head = branch[:branch.index("description = await _describe_photo_for_rep")]
    assert "_sender_is_sales_rep" in head
    assert "route_tenant" in head


def test_the_prompt_asks_rather_than_assumes():
    branch = FILE_SRC[FILE_SRC.index("The rep sent this photo with no caption"):]
    prompt = branch[:branch.index("_run_commerce_admin")]
    assert "ask what" in prompt.lower()
    assert "unless one is" in prompt.lower(), "should act when the answer is obvious"


def test_the_prompt_forbids_inventing_detail():
    """The photo is the only evidence — the standing failure mode is confident invention."""
    branch = FILE_SRC[FILE_SRC.index("The rep sent this photo with no caption"):]
    prompt = branch[:branch.index("_run_commerce_admin")]
    assert "Never invent details that aren't visible" in prompt


def test_document_ingest_still_runs_when_the_agent_does_not_handle_it():
    """Nothing may be lost: if the agent declines, the photo is still filed as before."""
    branch = FILE_SRC[FILE_SRC.index("An UNCAPTIONED photo from a rep"):]
    assert "_handle_document_ingest" in branch

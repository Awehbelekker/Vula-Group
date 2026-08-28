"""Regression tests for the 2026-08-28 gerflor CRM-misroute incident: a sales rep shared a
document photo captioned "As per our telecon." and the agent called dynamics_lookup (producing
a confusing "Dynamics 365 not connected" reply) instead of just acknowledging the document,
because nothing steered it away from treating a mentioned company/institution name as an
implicit lookup request. Two fixes: (1) dynamics_lookup's own tool description now says it's
only for an EXPLICIT lookup request, (2) the sales_rep system prompt now has explicit guidance
for a shared-photo-with-no-clear-instruction message.
"""
from core.skills.commerce_admin import CommerceAdminSkill, CRM_TOOLS

TID = "test-tenant"


def _dynamics_lookup_spec():
    for spec in CRM_TOOLS:
        if spec["function"]["name"] == "dynamics_lookup":
            return spec
    raise AssertionError("dynamics_lookup tool spec not found in CRM_TOOLS")


def test_dynamics_lookup_description_requires_explicit_request():
    desc = _dynamics_lookup_spec()["function"]["description"]
    assert "explicit" in desc.lower()
    assert "not a lookup request" in desc.lower() or "sharing a document" in desc.lower()


def test_sales_rep_prompt_has_shared_photo_no_instruction_guidance():
    skill = CommerceAdminSkill()
    prompt = skill._system_prompt(TID, role="sales_rep", name="Thabo")
    assert "[What's in the photo:" in prompt
    assert "don't reach for a tool speculatively" in prompt.lower() or \
           "don't guess" in prompt.lower()


def test_owner_prompt_unaffected_by_shared_photo_guidance():
    # This guidance is specific to the caption-routed rep photo flow — the owner/staff branch
    # never receives a "[What's in the photo: ...]" message shape, so it shouldn't carry this
    # rep-specific bullet.
    skill = CommerceAdminSkill()
    prompt = skill._system_prompt(TID, role=None, name="Owner")
    assert "[What's in the photo:" not in prompt

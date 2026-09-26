"""Tests for vula.integrations.doc_filing.match_project's ClickUp tier — real DIGG incident,
2026-09-21, found while investigating a "bigger concern" about document allocation/KB filing.

Root cause, confirmed against real production ClickUp list data and real filed-document
summaries: DIGG's ClickUp structure breaks two projects (Sporty – Phase 2, ATLANTIS FOODS) into
many small, room/branch-named sub-lists ("Sporty P2 – Office (First Floor)", "Sporty P2 –
Meeting Room", "...Interior Design & Concept"). match_project() scored token overlap against
the FULL raw nested list name, but _project_label() — which computes the actual returned
project — already discards that deepest segment. So a document that never mentioned Sporty or
Atlantis at all could win a confident match purely off generic room/business vocabulary
("office", "floor", "meeting", "design") sitting in a segment the result itself throws away.

Real, reproduced false positives before this fix (see the session's investigation): a document
about Earthchild Head Office matched "Sporty P2 – Office (First Floor)" on the single word
"office"; a document about a completely different building (170 Buitengracht / Oro Props)
matched the same list at CONFIDENCE 1.0 on "office"+"floor"; an HPC delay-claim document matched
"ATLANTIS FOODS" on the word "Design" (from the architect's own company name, "Jodi de Villiers
Design"); an HPC meeting-minutes document matched "Sporty P2 – Meeting Room" on the word
"meeting". None of these documents mentioned Sporty or Atlantis anywhere.

Quantified impact before the fix: ~17% of all project-assigned documents for this tenant were
confirmably or definitionally misfiled, concentrated almost entirely in these two multi-sub-list
projects (see the session's cross-contamination analysis).

Fix: score against _tokens(_project_label(lname)) — the same text actually returned as the
result — instead of _tokens(lname). No hardcoded word list, no per-tenant corpus query: a real
project mention still matches every one of that project's sub-lists identically (the label is
shared), since the label IS the project's own distinguishing name.
"""
from vula.integrations import doc_filing as df

TID = "test-tenant"

# A representative slice of DIGG's real ClickUp structure (see vula_clickup_accounts.list_ids)
# — enough to reproduce the incident without the full 26-list set.
_REAL_LISTS = {
    "901216127965": "Team Space / SPORTY.TV",
    "901217344951": "Team Space / HPC_Bokaap / Phase 1 — Site Establishment & Demolition",
    "901217344994": "Team Space / HPC_Bokaap / Phase 11 — Commissioning, Snagging & Handover",
    "901217585882": "Team Space / Sporty – Phase 2 / Sporty P2 – Reception",
    "901217585890": "Team Space / Sporty – Phase 2 / Sporty P2 – Office (First Floor)",
    "901217585891": "Team Space / Sporty – Phase 2 / Sporty P2 – Meeting Room",
    "901218996719": "Team Space / ATLANTIS FOODS / Atlantis Branch — Interior Design & Concept",
    "901218996723": ("Team Space / ATLANTIS FOODS / Paarden Island Branch — "
                      "Legalisation & Council Submissions"),
}


def _patch(monkeypatch, lists=None, field_projects=None):
    monkeypatch.setattr(df, "_clickup_candidates", lambda tid: list((lists or _REAL_LISTS).items()))
    monkeypatch.setattr(df, "_field_projects", lambda tid: field_projects or [])


# ── The four real false positives, confirmed fixed ──────────────────────────────

def test_earthchild_document_no_longer_matches_sporty_office(monkeypatch):
    """Real incident: an Earthchild Head Office drawing matched 'Sporty P2 – Office (First
    Floor)' on the single word 'office' — Earthchild is never mentioned in any DIGG ClickUp list."""
    _patch(monkeypatch)
    text = ("NORTH ELEVATION.pdf This document is a North Elevation drawing for the "
            "Earthchild Head Office project located in Wessex Road, Paardeneiland.")
    assert df.match_project(TID, text) is None


def test_oro_props_document_no_longer_matches_at_confidence_1(monkeypatch):
    """Real incident: this one matched at confidence 1.0 (two-token overlap: 'office'+'floor'),
    the highest confidence tier — for a building (170 Buitengracht / Oro Props) never mentioned
    in any DIGG ClickUp list."""
    _patch(monkeypatch)
    text = ("COUNCIL SUBMISSION - REV 4.2.pdf This document is a floor plan for internal "
            "alterations and additions, detailing office layouts, room specifications, and "
            "demolition plans for a council submission.")
    assert df.match_project(TID, text) is None


def test_hpc_delay_claim_no_longer_matches_atlantis_on_the_word_design(monkeypatch):
    """Real incident: the architect's own company name, 'Jodi de Villiers Design', supplied the
    single word 'Design' that matched 'Atlantis Branch — Interior Design & Concept' — a
    document entirely about the Cape Town High Performance Centre (HPC), a different, already-
    correctly-registered project."""
    _patch(monkeypatch)
    text = ("HPC _EOT Delay Claim 2026 - Jodi de Villiers Design.pdf This document is an "
            "adjudication report from Jodi de Villiers Design regarding a delay claim "
            "submitted by DIGG (Pty) Ltd for the Cape Town High Performance Centre project.")
    assert df.match_project(TID, text) is None


def test_hpc_meeting_minutes_no_longer_matches_sporty_meeting_room(monkeypatch):
    """Real incident: a document literally titled 'Meeting Minutes' matched 'Sporty P2 –
    Meeting Room' on the word 'meeting' alone — about HPC, not Sporty."""
    _patch(monkeypatch)
    text = ("HPC PC and VAT Discussions Meeting Minutes.pdf These meeting minutes detail "
            "discussions regarding VAT for the Cape Town High Performance Centre project.")
    assert df.match_project(TID, text) is None


# ── Legitimate matches still work ────────────────────────────────────────────────

def test_real_hpc_bokaap_document_still_matches(monkeypatch):
    _patch(monkeypatch)
    text = "Bokaap site photo.pdf Progress photo from the HPC Bokaap site, phase 1 demolition."
    result = df.match_project(TID, text)
    assert result["project"] == "HPC_Bokaap"
    assert result["ambiguous"] is False


def test_real_atlantis_document_still_matches_at_high_confidence(monkeypatch):
    text = "Atlantis Foods branch concept sketch.pdf Interior concept sketch for the Atlantis branch."
    _patch(monkeypatch)
    result = df.match_project(TID, text)
    assert result["project"] == "ATLANTIS FOODS"
    assert result["confidence"] == 1.0
    assert result["ambiguous"] is False


def test_a_document_mentioning_neither_project_matches_nothing(monkeypatch):
    _patch(monkeypatch)
    text = "Random unrelated correspondence about an entirely different topic."
    assert df.match_project(TID, text) is None


# ── Known, safe trade-off: two real projects sharing the identical label token ──────

def test_sporty_tv_and_sporty_phase_2_are_now_honestly_ambiguous_not_a_regression(monkeypatch):
    """SPORTY.TV and Sporty – Phase 2 reduce to the identical single label-token {'sporty'} —
    'TV' is too short (< _MIN_TOKEN_LEN) and 'Phase 2' is stopword+digit. Before this fix, room-
    level word leakage (e.g. 'reception') could accidentally break the tie in Sporty – Phase 2's
    favour — but that's the exact same leakage mechanism that caused the false positives above,
    so it wasn't a reliable disambiguator, just lucky sometimes. Now it's honestly ambiguous
    (asks rather than guesses) instead of silently guessing right or wrong. Not a regression:
    trading occasional lucky-correct guesses for consistent never-wrong-confidently behaviour."""
    _patch(monkeypatch)
    text = "Sporty Phase 2 site progress photo.pdf Progress photo from the Sporty Phase 2 site."
    result = df.match_project(TID, text)
    assert result["ambiguous"] is True
    assert set(result["candidates"]) == {"SPORTY.TV", "Sporty – Phase 2"}

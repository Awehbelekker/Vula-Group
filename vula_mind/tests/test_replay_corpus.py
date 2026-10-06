"""Chat rework step 4: every case in evals/cases/replay.yaml — a real message and where it must
go — replayed through the real question path (evals/replay.py). A change that routes a known
message somewhere else fails here, before it ships."""
import re

import pytest

from evals.replay import load_cases, mismatch, replay

CASES = load_cases()


def test_corpus_cases_are_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in CASES:
        assert c.get("tenant") and c.get("text") and c.get("expect"), c.get("id")
        # public repository: never a phone number in a case
        for field in (c["text"], c.get("last_request") or ""):
            assert not re.search(r"\d{9,}", re.sub(r"[\s-]", "", field)), c["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_message_goes_where_it_should(case):
    got = await replay(case)
    problem = mismatch(case, got)
    assert problem is None, f"{case['id']}: {case['text']!r} {problem}"


def test_export_turns_makes_reviewable_cases_without_phones_or_replies():
    from tools.export_turns import candidates
    rows = [{"tenant_id": "digg-demo", "kind": "text", "text": "Who owes me money?",
             "started_at": "2026-10-06T09:00:00Z", "phone": "27820000000",
             "replies": [{"text": "R1,000"}],
             "steps": [{"step": "route", "name": "owner_admin", "skill": "commerce_admin"}]},
            {"tenant_id": "digg-demo", "kind": "document", "text": "", "steps": []}]
    out = candidates(rows)
    assert len(out) == 1
    c = out[0]
    assert c["text"] == "Who owes me money?" and c["expect"] == {"route": "owner_admin",
                                                                  "skill": "commerce_admin"}
    flat = repr(c)
    assert "27820000000" not in flat and "R1,000" not in flat

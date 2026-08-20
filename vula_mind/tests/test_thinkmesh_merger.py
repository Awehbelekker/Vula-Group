"""Tests for core/thinkmesh/merger.py's zero-successful-branches fallback.

2026-08-18: confirmed live in DIGG's real chat history — "Hi what info do you have on solid
Cape" got the literal internal string "[No successful branches — all failed]" as its WhatsApp
reply, twice, because nothing between the merger and _rag_reply distinguished a real answer from
an internal failure marker. Fixed by making the failure text itself a real, honest customer-
facing message instead.
"""
from core.thinkmesh.graph import BranchStatus, GraphStatus, TaskBranch, TaskGraph
from core.thinkmesh.merger import ThinKMeshMerger


def test_zero_successful_branches_returns_friendly_message_not_internal_string():
    graph = TaskGraph(original_prompt="Hi what info do you have on solid Cape")
    graph.branches = [TaskBranch(status=BranchStatus.FAILED, answer="")]

    merged = ThinKMeshMerger().merge(graph)

    assert "[No successful branches" not in merged.final_answer
    assert "No successful branches" not in merged.final_answer
    assert merged.final_answer  # not empty — always something real to send
    assert merged.status == GraphStatus.ERROR


def test_single_successful_branch_still_returns_its_answer_unchanged():
    graph = TaskGraph(original_prompt="what's the retention?")
    graph.branches = [TaskBranch(status=BranchStatus.COMPLETE, answer="Retention is 5%.")]

    merged = ThinKMeshMerger().merge(graph)

    assert merged.final_answer == "Retention is 5%."
    assert merged.status == GraphStatus.DONE

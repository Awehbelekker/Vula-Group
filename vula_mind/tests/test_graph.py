"""Tests for TaskGraph — the central data contract."""
import time
from core.thinkmesh.graph import (
    TaskGraph, ReflectionLog,
    BranchStatus, GraphStatus, MergeStrategy,
)


def test_task_graph_defaults():
    g = TaskGraph(original_prompt="hello")
    assert g.original_prompt == "hello"
    assert g.status == GraphStatus.QUEUED
    assert g.branches == []
    assert g.task_id  # non-empty UUID


def test_add_branch():
    g = TaskGraph(original_prompt="test")
    b = g.add_branch(skill_id="reasoning", prompt="test")
    assert len(g.branches) == 1
    assert b.skill_id == "reasoning"
    assert b.status == BranchStatus.PENDING


def test_successful_branches_filter():
    g = TaskGraph(original_prompt="test")
    b1 = g.add_branch(skill_id="reasoning", prompt="test")
    b2 = g.add_branch(skill_id="reasoning", prompt="test")
    b1.status = BranchStatus.COMPLETE
    b2.status = BranchStatus.FAILED

    assert len(g.successful_branches) == 1
    assert g.successful_branches[0] is b1


def test_aliases():
    g = TaskGraph(original_prompt="hello")
    g.add_branch(skill_id="reasoning", prompt="hello")
    g.branches[0].status = BranchStatus.COMPLETE
    # elapsed_ms computes live from time.time() until completed_at is set — freeze it first so
    # total_latency_ms and elapsed_ms (both read it fresh) don't flake by a few live microseconds.
    g.completed_at = time.time()

    assert g.goal == g.original_prompt
    assert g.graph_id == g.task_id
    assert g.done_branches == g.successful_branches
    assert g.merged_output == g.final_answer
    assert g.total_latency_ms == g.elapsed_ms
    assert g.primary_skill == "reasoning"


def test_elapsed_ms_increases():
    g = TaskGraph(original_prompt="test")
    t1 = g.elapsed_ms
    time.sleep(0.05)
    t2 = g.elapsed_ms
    assert t2 > t1


def test_completed_at_freezes_elapsed():
    g = TaskGraph(original_prompt="test")
    g.completed_at = g.created_at + 1.0
    assert abs(g.elapsed_ms - 1000.0) < 1.0


def test_reflection_log_creation():
    log = ReflectionLog(
        graph_id="abc123",
        goal="test goal",
        merge_strategy_used=MergeStrategy.FASTEST,
        outcome_score=0.85,
        skills_used=["reasoning"],
        model_tiers_used=["7b"],
        winning_branch_id="branch1",
        total_latency_ms=1500.0,
        what_worked="Good reasoning trace.",
        what_to_try_next="Try 14b model.",
    )
    assert log.outcome_score == 0.85
    assert log.timestamp > 0

"""core/memory/reflection.py — tenant fencing (Mass Mind Phase 0, 2026-09-15).

The reflection store (routing hints HRM uses to pick a model tier, and what
core/skills/memory_recall.py echoes into a customer-facing answer) had NO tenant column at
all — one shared SQLite file, every tenant's goal text and winning model tier pooled together.
Confirmed live before this fix: a tenant's get_routing_hints() query could return, and did
directly use, another tenant's stored data. This is the "fence the reflection store" step from
the Mass Mind architecture scoping (see the published design doc) — no other behavior changes
yet; get_stats() stays deliberately global by default since it only backs master-key-only
operator views (/metrics, /agent/stats), never a tenant request.
"""
from pathlib import Path

import pytest

from core.memory.reflection import ReflectionAgent
from core.thinkmesh.graph import MergeStrategy, ReflectionLog, TaskGraph, GraphStatus

TENANT_A = "off-the-hook"
TENANT_B = "digg"


def _agent(tmp_path: Path) -> ReflectionAgent:
    return ReflectionAgent(db_path=tmp_path / "reflection.db")


def _graph(tenant_id: str, prompt: str, score_inputs: bool = True) -> TaskGraph:
    g = TaskGraph(original_prompt=prompt, tenant_id=tenant_id)
    g.status = GraphStatus.EXECUTING
    b = g.add_branch(skill_id="commerce_assistant")
    from core.thinkmesh.graph import BranchStatus, ModelTier
    b.status = BranchStatus.COMPLETE
    b.confidence = 0.9
    b.model_tier = ModelTier.REASONER
    g.final_answer = "a genuinely long enough answer to score well here, more than fifty words " * 2
    g.completed_at = g.created_at + 0.2
    return g


def test_fresh_db_has_tenant_column(tmp_path):
    agent = _agent(tmp_path)
    import sqlite3
    conn = sqlite3.connect(agent.db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(reflections)").fetchall()}
    conn.close()
    assert "tenant_id" in cols


def test_legacy_db_without_tenant_column_gets_backfilled(tmp_path):
    """A store created before 2026-09-15 has the table with no tenant_id at all."""
    import sqlite3
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, graph_id TEXT NOT NULL, goal TEXT NOT NULL,
            primary_skill TEXT, winning_tier TEXT, outcome_score REAL, merge_strategy TEXT,
            total_latency_ms INTEGER, what_worked TEXT, what_to_try_next TEXT,
            skills_used TEXT, tiers_used TEXT, timestamp REAL
        )
    """)
    conn.execute("INSERT INTO reflections (graph_id, goal, timestamp) VALUES ('g1', 'old goal', 1.0)")
    conn.commit()
    conn.close()

    agent = ReflectionAgent(db_path=db_path)  # __init__ runs _init_db — must not raise
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT tenant_id FROM reflections WHERE graph_id='g1'").fetchone()
    conn.close()
    assert row[0] == "default"


def test_reflect_writes_the_graph_tenant_id(tmp_path):
    agent = _agent(tmp_path)
    graph = _graph(TENANT_A, "do you deliver seafood to Milnerton")
    agent.reflect(graph)

    import sqlite3
    conn = sqlite3.connect(agent.db_path)
    row = conn.execute("SELECT tenant_id FROM reflections WHERE graph_id=?", (graph.graph_id,)).fetchone()
    conn.close()
    assert row[0] == TENANT_A


def test_get_routing_hints_never_crosses_tenants(tmp_path):
    """The actual leak this closes: two tenants asking near-identical questions must never
    see each other's stored goal/tier in their routing hints."""
    agent = _agent(tmp_path)
    agent.reflect(_graph(TENANT_A, "do you deliver seafood to Milnerton today"))
    agent.reflect(_graph(TENANT_B, "do you deliver architecture drawings to Milnerton today"))

    hints_a = agent.get_routing_hints(TENANT_A, "deliver seafood Milnerton")
    hints_b = agent.get_routing_hints(TENANT_B, "deliver seafood Milnerton")  # same query text

    assert len(hints_a) == 1
    assert "seafood" in hints_a[0]["goal_preview"]
    assert len(hints_b) == 1
    assert "architecture" in hints_b[0]["goal_preview"]
    assert hints_a[0]["goal_preview"] != hints_b[0]["goal_preview"]


def test_get_routing_hints_requires_a_tenant_id(tmp_path):
    """Signature-level guard: the old (goal, limit) call shape must now fail loudly rather
    than silently querying with no fence, the way the pre-fix bug worked for its whole life."""
    agent = _agent(tmp_path)
    with pytest.raises(TypeError):
        agent.get_routing_hints("do you deliver to Milnerton")  # tenant_id omitted


def test_unrelated_tenant_with_no_history_gets_nothing(tmp_path):
    agent = _agent(tmp_path)
    agent.reflect(_graph(TENANT_A, "do you deliver seafood to Milnerton"))
    assert agent.get_routing_hints("brand-new-tenant", "deliver seafood Milnerton") == []


def test_get_stats_defaults_to_global_for_operator_views(tmp_path):
    """get_stats() backs /metrics and /agent/stats — both master-key-only, neither scoped to
    a tenant request — so the default (no tenant_id) must stay platform-wide."""
    agent = _agent(tmp_path)
    agent.reflect(_graph(TENANT_A, "question one"))
    agent.reflect(_graph(TENANT_B, "question two"))
    assert agent.get_stats()["total_reflections"] == 2


def test_get_stats_filters_when_a_tenant_id_is_given(tmp_path):
    agent = _agent(tmp_path)
    agent.reflect(_graph(TENANT_A, "question one"))
    agent.reflect(_graph(TENANT_A, "question two"))
    agent.reflect(_graph(TENANT_B, "question three"))
    assert agent.get_stats(TENANT_A)["total_reflections"] == 2
    assert agent.get_stats(TENANT_B)["total_reflections"] == 1
    assert agent.get_stats("nobody-home")["total_reflections"] == 0


@pytest.mark.asyncio
async def test_agent_runner_passes_the_real_tenant_id(monkeypatch, tmp_path):
    """core/agent_runner.py is the live call site that feeds HRM's model-tier selection —
    confirm it threads the request's own tenant_id through, not the request text alone."""
    import core.agent_runner as ar

    captured = {}

    class _FakeAgent:
        def get_routing_hints(self, tenant_id, goal, limit=5):
            captured["tenant_id"] = tenant_id
            return []

        def reflect(self, graph, user_feedback=None):
            pass

    monkeypatch.setattr("core.memory.reflection.ReflectionAgent", lambda: _FakeAgent())

    from core.skills.base import SkillOutput

    async def _fake_skill(inp):
        return SkillOutput(answer="ok, more than ten words in this answer to look substantive enough",
                            skill_name="reasoning", confidence=0.8)

    # agent_runner.py does `from core.skills.loader import get_skill` at module load — the name
    # it calls is bound in ITS OWN namespace, so the patch target is ar.get_skill, not the
    # source module (patching core.skills.loader.get_skill would miss the already-bound copy).
    monkeypatch.setattr(ar, "get_skill", lambda skill_id: _fake_skill)

    runner = ar.AgentRunner()
    await runner.run(question="hello", tenant_id="off-the-hook", max_branches=1)

    assert captured["tenant_id"] == "off-the-hook"


@pytest.mark.asyncio
async def test_memory_recall_passes_the_request_tenant_id(monkeypatch):
    """core/skills/memory_recall.py echoes routing-hint text straight into a customer-facing
    answer — confirm it's fenced to the requesting tenant, not global."""
    from core.skills.memory_recall import MemoryRecallSkill
    from core.skills.base import SkillInput

    captured = {}

    class _FakeAgent:
        def get_routing_hints(self, tenant_id, goal, limit=5):
            captured["tenant_id"] = tenant_id
            return []

    monkeypatch.setattr("core.memory.reflection.ReflectionAgent", lambda: _FakeAgent())

    async def _no_kb(*a, **k):
        return []

    class _FakePipeline:
        def __init__(self, tenant_id):
            pass

        async def query(self, question, top_k=5):
            return []

    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline", _FakePipeline)

    skill = MemoryRecallSkill()
    await skill.run(SkillInput(question="remember what we discussed", tenant_id="digg"))

    assert captured["tenant_id"] == "digg"


def test_reflection_log_defaults_to_default_tenant_when_unset():
    """Direct dataclass default — anything constructing a ReflectionLog without a tenant_id
    (e.g. an older caller) degrades to 'default' rather than crashing or leaving it None."""
    log = ReflectionLog(
        graph_id="g1", goal="x", merge_strategy_used=MergeStrategy.FASTEST, outcome_score=0.5,
        skills_used=[], model_tiers_used=[], winning_branch_id=None, total_latency_ms=0.0,
        what_worked="", what_to_try_next="",
    )
    assert log.tenant_id == "default"

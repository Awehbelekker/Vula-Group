"""core/memory/reflection.py — tenant fencing + Supabase storage (Mass Mind foundation, 2026-09-15).

Two changes landed together, deliberately: (1) the store had NO tenant column at all — one pool,
every tenant's routing history mixed together, confirmed live to leak another tenant's stored
goal text and winning model tier into the CURRENT tenant's routing decision (HRMOrchestrator)
and a customer-facing answer (core/skills/memory_recall.py). (2) the store lived in local SQLite
on a Railway service confirmed (via Railway's own service config: "volumes": []) to have no
persistent volume — every redeploy wiped it, several times a day. Fencing a store that resets on
every deploy would still be worthless for the Mass Mind rollup phases that read from it, so both
had to be fixed together, per migration 159.

Uses a minimal in-memory fake Supabase client, matching the established pattern in
tests/test_voice_profile.py and tests/test_flows.py.
"""
import pytest

from core.memory.reflection import ReflectionAgent
from core.thinkmesh.graph import BranchStatus, GraphStatus, MergeStrategy, ModelTier, ReflectionLog, TaskGraph

TENANT_A = "off-the-hook"
TENANT_B = "digg"


# ── fake Supabase client ─────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _FakeQuery:
    def __init__(self, rows):
        self._filtered = list(rows)
        self._order_keys: list[tuple[str, bool]] = []
        self._limit_n = None
        self._want_count = False

    def select(self, *_a, count=None, **_kw):
        self._want_count = count == "exact"
        return self

    def eq(self, key, val):
        self._filtered = [r for r in self._filtered if r.get(key) == val]
        return self

    def gt(self, key, val):
        self._filtered = [r for r in self._filtered
                           if r.get(key) is not None and r.get(key) > val]
        return self

    def order(self, key, desc=False):
        self._order_keys.append((key, desc))
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def execute(self):
        rows = list(self._filtered)
        total = len(rows)
        # Multi-key stable sort: apply lowest-priority .order() call first, highest last, so
        # the first .order() call (the real code's primary sort key) dominates.
        for key, desc in reversed(self._order_keys):
            rows.sort(key=lambda r, k=key: r.get(k), reverse=desc)
        if self._limit_n is not None:
            rows = rows[: self._limit_n]
        return _FakeResult(rows, count=total if self._want_count else None)


class _FakeInsert:
    def __init__(self, store, row):
        self._store = store
        self._row = dict(row)

    def execute(self):
        self._row.setdefault("created_at", len(self._store))  # monotonic insertion order
        self._store.append(self._row)
        return _FakeResult([self._row])


class _FakeTable:
    def __init__(self, store):
        self._store = store

    def select(self, *a, **kw):
        return _FakeQuery(self._store).select(*a, **kw)

    def insert(self, row):
        return _FakeInsert(self._store, row)


class _FakeClient:
    def __init__(self):
        self._tables: dict[str, list] = {}

    def table(self, name):
        return _FakeTable(self._tables.setdefault(name, []))


class _BrokenTable:
    """Simulates migration 159 not yet applied — every call raises, matching a real
    postgrest error for a table that doesn't exist."""
    def select(self, *a, **kw):
        raise Exception('relation "vula_reflections" does not exist')

    def insert(self, row):
        raise Exception('relation "vula_reflections" does not exist')


class _BrokenClient:
    def table(self, name):
        return _BrokenTable()


def _agent(monkeypatch, client=None) -> ReflectionAgent:
    fake = client or _FakeClient()
    monkeypatch.setattr("core.memory.reflection._client", lambda: fake)
    return ReflectionAgent()


def _graph(tenant_id: str, prompt: str) -> TaskGraph:
    g = TaskGraph(original_prompt=prompt, tenant_id=tenant_id)
    g.status = GraphStatus.EXECUTING
    b = g.add_branch(skill_id="commerce_assistant")
    b.status = BranchStatus.COMPLETE
    b.confidence = 0.9
    b.model_tier = ModelTier.REASONER
    g.final_answer = "a genuinely long enough answer to score well here, more than fifty words " * 2
    g.completed_at = g.created_at + 0.2
    return g


# ── writes ────────────────────────────────────────────────────────────────────────────

def test_reflect_writes_the_graph_tenant_id(monkeypatch):
    fake = _FakeClient()
    agent = _agent(monkeypatch, fake)
    graph = _graph(TENANT_A, "do you deliver seafood to Milnerton")
    agent.reflect(graph)

    rows = fake._tables["vula_reflections"]
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == TENANT_A


def test_reflect_fails_open_when_the_table_is_missing(monkeypatch):
    """migration 159 not yet applied — must never break the caller's request."""
    agent = _agent(monkeypatch, _BrokenClient())
    log = agent.reflect(_graph(TENANT_A, "anything"))  # must not raise
    assert log.tenant_id == TENANT_A  # reflect() still returns a real ReflectionLog either way


# ── get_routing_hints: the actual leak this closes ──────────────────────────────────────

def test_get_routing_hints_never_crosses_tenants(monkeypatch):
    fake = _FakeClient()
    agent = _agent(monkeypatch, fake)
    agent.reflect(_graph(TENANT_A, "do you deliver seafood to Milnerton today"))
    agent.reflect(_graph(TENANT_B, "do you deliver architecture drawings to Milnerton today"))

    hints_a = agent.get_routing_hints(TENANT_A, "deliver seafood Milnerton")
    hints_b = agent.get_routing_hints(TENANT_B, "deliver seafood Milnerton")  # same query text

    assert len(hints_a) == 1 and "seafood" in hints_a[0]["goal_preview"]
    assert len(hints_b) == 1 and "architecture" in hints_b[0]["goal_preview"]


def test_get_routing_hints_requires_a_tenant_id(monkeypatch):
    """Signature-level guard: the old (goal, limit) call shape must now fail loudly rather
    than silently querying with no fence, the way the pre-fix bug worked for its whole life."""
    agent = _agent(monkeypatch)
    with pytest.raises(TypeError):
        agent.get_routing_hints("do you deliver to Milnerton")  # tenant_id omitted


def test_unrelated_tenant_with_no_history_gets_nothing(monkeypatch):
    agent = _agent(monkeypatch)
    agent.reflect(_graph(TENANT_A, "do you deliver seafood to Milnerton"))
    assert agent.get_routing_hints("brand-new-tenant", "deliver seafood Milnerton") == []


def test_get_routing_hints_excludes_low_outcome_scores(monkeypatch):
    fake = _FakeClient()
    agent = _agent(monkeypatch, fake)
    good = _graph(TENANT_A, "delivery hours for Milnerton customers")
    bad = _graph(TENANT_A, "delivery hours for Milnerton customers")
    bad.branches[0].confidence = 0.05  # drags outcome_score well under the 0.6 bar
    bad.final_answer = "no"
    agent.reflect(good)
    agent.reflect(bad)

    hints = agent.get_routing_hints(TENANT_A, "delivery hours Milnerton")
    assert len(hints) == 1
    assert hints[0]["score"] > 0.6


def test_get_routing_hints_fails_open_when_the_table_is_missing(monkeypatch):
    agent = _agent(monkeypatch, _BrokenClient())
    assert agent.get_routing_hints(TENANT_A, "anything at all here") == []


# ── get_stats ─────────────────────────────────────────────────────────────────────────

def test_get_stats_defaults_to_global_for_operator_views(monkeypatch):
    """get_stats() backs /metrics and /agent/stats — both master-key-only, neither scoped to
    a tenant request — so the default (no tenant_id) must stay platform-wide."""
    agent = _agent(monkeypatch)
    agent.reflect(_graph(TENANT_A, "question one"))
    agent.reflect(_graph(TENANT_B, "question two"))
    assert agent.get_stats()["total_reflections"] == 2


def test_get_stats_filters_when_a_tenant_id_is_given(monkeypatch):
    fake = _FakeClient()
    agent = _agent(monkeypatch, fake)
    agent.reflect(_graph(TENANT_A, "question one"))
    agent.reflect(_graph(TENANT_A, "question two"))
    agent.reflect(_graph(TENANT_B, "question three"))
    assert agent.get_stats(TENANT_A)["total_reflections"] == 2
    assert agent.get_stats(TENANT_B)["total_reflections"] == 1
    assert agent.get_stats("nobody-home")["total_reflections"] == 0


def test_get_stats_computes_averages_and_top_skill(monkeypatch):
    fake = _FakeClient()
    agent = _agent(monkeypatch, fake)
    for _ in range(3):
        agent.reflect(_graph(TENANT_A, "a commerce question"))
    stats = agent.get_stats(TENANT_A)
    assert stats["most_used_skill"] == "commerce_assistant"
    assert 0.0 < stats["avg_outcome_score"] <= 1.0
    assert stats["avg_latency_ms"] >= 0


def test_get_stats_fails_open_when_the_table_is_missing(monkeypatch):
    agent = _agent(monkeypatch, _BrokenClient())
    stats = agent.get_stats()
    assert stats == {"total_reflections": 0, "avg_outcome_score": 0, "avg_latency_ms": 0,
                      "most_used_skill": None}


# ── real call sites ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_runner_passes_the_real_tenant_id(monkeypatch):
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

"""Tests for the depth-pass shared-knowledge promotion queue (vula/api/master.py):
GET /master/learned-answers and POST /master/learned-answers/{id}/promote.

Two promotion sources land in the same vula_learned_answers table (migration 164) — Vula's own
accuracy-gated web research (source='web_research', see core/verification.py's two-signal gate)
and tenant-reviewed answers. Promoting to the cross-tenant 'network' collection additionally
requires that tenant's own opt-in (vula_tenant_config.share_knowledge_with_network) — a tenant's
content must never reach another tenant without it having explicitly turned that on.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api import master

_IDENTITY = {"user_id": "m1", "email": "master@vula.app", "role": "master"}


def _make_list_db(approved_rows, research_rows, tenant_config_rows):
    learned_table = MagicMock()
    node = learned_table.select.return_value.eq.return_value
    node.is_.return_value.order.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=approved_rows)
    node.eq.return_value.is_.return_value.order.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=research_rows)

    config_table = MagicMock()
    config_table.select.return_value.execute.return_value = MagicMock(data=tenant_config_rows)

    def _table(name):
        return {"vula_learned_answers": learned_table, "vula_tenant_config": config_table}[name]

    mock_db = MagicMock()
    mock_db.table.side_effect = _table
    return mock_db


def _make_promote_db(tenant_config_rows, update_return=None):
    config_table = MagicMock()
    config_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=tenant_config_rows)
    learned_table = MagicMock()
    learned_table.update.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=update_return or [{}])

    def _table(name):
        return {"vula_tenant_config": config_table, "vula_learned_answers": learned_table}[name]

    mock_db = MagicMock()
    mock_db.table.side_effect = _table
    return mock_db, config_table, learned_table


# ── GET /master/learned-answers ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_learned_answers_lists_approved_and_pending_research_not_yet_promoted():
    approved = [{"id": "a1", "tenant_id": "digg-demo", "status": "approved",
                "source": "owner_correction", "question": "q1", "answer": "a1"}]
    research = [{"id": "r1", "tenant_id": "off-the-hook", "status": "pending",
                "source": "web_research", "question": "q2", "answer": "a2"}]
    mock_db = _make_list_db(approved, research, [{"tenant_id": "digg-demo", "display_name": "DIGG"}])
    with patch("vula.api.master._client", return_value=mock_db):
        result = await master.master_learned_answers()

    ids = {r["id"] for r in result["candidates"]}
    assert ids == {"a1", "r1"}
    named = {r["id"]: r["tenant_display_name"] for r in result["candidates"]}
    assert named["a1"] == "DIGG"
    assert named["r1"] == "off-the-hook"  # no config row for this tenant -> falls back to raw id


@pytest.mark.asyncio
async def test_learned_answers_empty_before_migration_164():
    mock_db = MagicMock()
    mock_db.table.side_effect = RuntimeError("column promoted_to_shared_kb_at does not exist")
    with patch("vula.api.master._client", return_value=mock_db):
        result = await master.master_learned_answers()
    assert result["candidates"] == []
    assert "error" in result


# ── POST /master/learned-answers/{id}/promote ──────────────────────────────────────

@pytest.mark.asyncio
async def test_promote_construction_ingests_and_stamps():
    mock_db, _, learned_table = _make_promote_db([])
    row = {"id": "r1", "tenant_id": "digg-demo", "question": "q", "answer": "a",
          "promoted_to_shared_kb_at": None}
    ingest_result = MagicMock(status="success", error=None)
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=ingest_result)

    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.escalation.get_learned_answer", return_value=row),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
        patch("vula.api.master.audit") as mock_audit,
    ):
        result = await master.master_promote_learned_answer(
            "r1", {"target": "construction"}, identity=_IDENTITY)

    assert result["promoted"] is True
    mock_pipeline.ingest_text.assert_called_once()
    # NOT source_type="learned" — that tag is excluded from authoritative_only=True retrieval
    # (VulaIngestionPipeline._NON_AUTHORITATIVE), which would make a promoted row invisible.
    assert mock_pipeline.ingest_text.call_args.kwargs["source_type"] == "promoted"
    learned_table.update.assert_called_once()
    patch_body = learned_table.update.call_args[0][0]
    assert patch_body["promoted_by"] == _IDENTITY["email"]
    assert patch_body["share_scope"] == "internal"
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_promote_network_403s_without_tenant_opt_in():
    mock_db, _, learned_table = _make_promote_db([{"share_knowledge_with_network": False}])
    row = {"id": "r1", "tenant_id": "digg-demo", "question": "q", "answer": "a",
          "promoted_to_shared_kb_at": None}

    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.escalation.get_learned_answer", return_value=row),
    ):
        with pytest.raises(Exception) as exc:
            await master.master_promote_learned_answer(
                "r1", {"target": "network"}, identity=_IDENTITY)

    assert getattr(exc.value, "status_code", None) == 403
    learned_table.update.assert_not_called()


@pytest.mark.asyncio
async def test_promote_network_succeeds_when_tenant_opted_in():
    mock_db, _, learned_table = _make_promote_db([{"share_knowledge_with_network": True}])
    row = {"id": "r1", "tenant_id": "digg-demo", "question": "q", "answer": "a",
          "promoted_to_shared_kb_at": None}
    ingest_result = MagicMock(status="success", error=None)
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=ingest_result)

    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.escalation.get_learned_answer", return_value=row),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
        patch("vula.api.master.audit"),
    ):
        result = await master.master_promote_learned_answer(
            "r1", {"target": "network"}, identity=_IDENTITY)

    assert result["promoted"] is True
    patch_body = learned_table.update.call_args[0][0]
    assert patch_body["share_scope"] == "network"


@pytest.mark.asyncio
async def test_promote_already_promoted_is_noop_not_double_ingest():
    mock_db, _, learned_table = _make_promote_db([])
    row = {"id": "r1", "tenant_id": "digg-demo", "question": "q", "answer": "a",
          "promoted_to_shared_kb_at": "2026-09-17T00:00:00+00:00"}

    with (
        patch("vula.api.master._client", return_value=mock_db),
        patch("vula.escalation.get_learned_answer", return_value=row),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
    ):
        result = await master.master_promote_learned_answer(
            "r1", {"target": "construction"}, identity=_IDENTITY)

    assert result["already_promoted"] is True
    learned_table.update.assert_not_called()
    mock_pipeline_cls.assert_not_called()


@pytest.mark.asyncio
async def test_promote_unknown_target_400s():
    with patch("vula.escalation.get_learned_answer", return_value={"id": "r1"}):
        with pytest.raises(Exception) as exc:
            await master.master_promote_learned_answer(
                "r1", {"target": "bogus"}, identity=_IDENTITY)
    assert getattr(exc.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_promote_missing_row_404s():
    with patch("vula.escalation.get_learned_answer", return_value=None):
        with pytest.raises(Exception) as exc:
            await master.master_promote_learned_answer(
                "nope", {"target": "construction"}, identity=_IDENTITY)
    assert getattr(exc.value, "status_code", None) == 404

"""Shared pytest fixtures for the Vula test suite."""
import os
import tempfile

import pytest

# Point at test doubles before any app imports
os.environ.setdefault("OLLAMA_BASE", "http://localhost:11434")
os.environ.setdefault("QDRANT_BASE", "http://localhost:6333")
os.environ.setdefault("API_KEY", "")
os.environ.setdefault("DEBUG", "true")

# Isolate persistent SQLite stores (tenants.db, ingestion_log.db, reflection.db)
# in a throwaway directory so tests never pollute the developer's ~/.vula data
# or each other. Honour an explicitly provided DATA_DIR if the caller set one.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="vula_test_data_"))


@pytest.fixture(autouse=True)
def _isolate_tenant_db(tmp_path, monkeypatch):
    """Give every test its own empty tenant registry.

    The tenant DB is a process-wide singleton backed by SQLite on disk. Without
    this, a test that onboards a tenant (writing a phone→tenant row) leaks that
    row into later tests in the same session (e.g. the WhatsApp tenant-lookup
    test). Pointing the singleton at a per-test temp file keeps them isolated.
    """
    import vula.models.tenants as tenants_mod

    monkeypatch.setattr(
        tenants_mod, "_db", tenants_mod.LocalTenantDB(db_path=tmp_path / "tenants.db")
    )
    yield


@pytest.fixture(autouse=True)
def _no_real_whatsapp_creds_by_default(monkeypatch):
    """No test should be able to reach the real Meta Graph API just by exercising the WhatsApp
    webhook without explicitly mocking credentials.

    2026-09-08: _mark_read_and_typing's call site was changed from `await`ed inline to
    `asyncio.create_task(...)` (fire-and-forget, to stop it adding latency to every real inbound
    message). That decoupled it from the request/test lifecycle it used to share — a test that
    exercises the webhook route without mocking WhatsApp credentials (most of them; they only
    care about the handler dispatch, not this cosmetic side effect) used to have this call
    resolve and fail safely INSIDE its own synchronous `await`, within the test's own call
    stack. Now it can keep running as an orphaned background task after the test function has
    already returned — attempting a REAL network call to graph.facebook.com with whatever real
    global WHATSAPP_TOKEN/WHATSAPP_PHONE_ID happen to be set in the dev environment's own .env,
    landing at some arbitrary point during a LATER, unrelated test. Defaulting both the
    per-tenant lookup and the global env fallback to "not configured" here closes that off for
    every test at once; a test that specifically wants to exercise real-looking WhatsApp-send
    behaviour (e.g. tests/test_typing_indicator.py) already patches these explicitly per-test,
    which correctly overrides this default for the scope of its own `with patch(...)` block.
    """
    import vula.api.whatsapp as wa_mod
    from unittest.mock import AsyncMock

    monkeypatch.setattr(wa_mod, "_get_tenant_wa_creds", AsyncMock(return_value=None))
    monkeypatch.setattr(wa_mod.settings, "whatsapp_token", "")
    monkeypatch.setattr(wa_mod.settings, "whatsapp_phone_id", "")
    yield


@pytest.fixture
def sample_goal() -> str:
    return "What is the capital of South Africa?"


@pytest.fixture
def complex_goal() -> str:
    return "Analyse and compare the pros and cons of using DeepSeek R1 vs Qwen 2.5 for a privacy-first local AI system."

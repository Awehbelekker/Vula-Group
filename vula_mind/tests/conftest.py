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


@pytest.fixture
def sample_goal() -> str:
    return "What is the capital of South Africa?"


@pytest.fixture
def complex_goal() -> str:
    return "Analyse and compare the pros and cons of using DeepSeek R1 vs Qwen 2.5 for a privacy-first local AI system."

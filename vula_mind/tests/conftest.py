"""Shared pytest fixtures for the Vula test suite."""
import os
import pytest

# Point at test doubles before any app imports
os.environ.setdefault("OLLAMA_BASE", "http://localhost:11434")
os.environ.setdefault("QDRANT_BASE", "http://localhost:6333")
os.environ.setdefault("API_KEY", "")
os.environ.setdefault("DEBUG", "true")


@pytest.fixture
def sample_goal() -> str:
    return "What is the capital of South Africa?"


@pytest.fixture
def complex_goal() -> str:
    return "Analyse and compare the pros and cons of using DeepSeek R1 vs Qwen 2.5 for a privacy-first local AI system."

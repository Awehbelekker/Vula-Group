"""Tests for SemanticChunker — no external services needed."""
import pytest
from vula.ingestion.pipeline import SemanticChunker


@pytest.fixture
def chunker():
    return SemanticChunker(chunk_size=50, overlap=10)


def test_empty_text_returns_empty(chunker):
    assert chunker.chunk("") == []
    assert chunker.chunk("   ") == []


def test_short_text_returns_single_chunk(chunker):
    text = "This is a short sentence."
    chunks = chunker.chunk(text)
    assert len(chunks) == 1
    assert "short sentence" in chunks[0]


def test_long_text_splits_into_multiple_chunks():
    chunker = SemanticChunker(chunk_size=20, overlap=5)
    # Chunker splits on paragraph boundaries (\n\n); generate enough paragraphs to exceed chunk_size
    text = "\n\n".join([f"Paragraph {i} " + " ".join([f"word{j}" for j in range(15)]) for i in range(8)])
    chunks = chunker.chunk(text)
    assert len(chunks) > 1


def test_chunks_are_non_empty(chunker):
    text = "\n\n".join([f"Paragraph {i} with some content." for i in range(10)])
    chunks = chunker.chunk(text)
    for chunk in chunks:
        assert len(chunk.strip()) > 0


def test_chunk_minimum_length_filter(chunker):
    text = "ok\n\nThis is a proper paragraph with enough words to make it through."
    chunks = chunker.chunk(text)
    # "ok" is too short to be kept as its own chunk
    for chunk in chunks:
        assert len(chunk.strip()) > 20


def test_table_content_preserved(chunker):
    table = "Name | Age | City\nAlice | 30 | Cape Town\nBob | 25 | Johannesburg"
    chunks = chunker.chunk(table)
    combined = " ".join(chunks)
    assert "Cape Town" in combined

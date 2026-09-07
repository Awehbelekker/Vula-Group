"""Re-uploading a document must REPLACE it, not add a second copy.

2026-09-07. _doc_id hashed the file's MTIME, so the very same document ingested twice got two
different ids and neither replaced the other. Every re-upload silently duplicated. Found on
gerflor, which was holding two copies each of the DT stock sheet, the gym catalogue and the SPM
price list — and re-ingesting the gym catalogue to pick up its OCR'd pages produced a third.
Duplicate chunks then compete against each other in search.

This is not a developer-only path: a tenant re-sending a corrected drawing or an updated
statement hits it every time.
"""
import os
import pathlib
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.ingestion.pipeline import VulaIngestionPipeline


def _pipeline():
    p = VulaIngestionPipeline.__new__(VulaIngestionPipeline)
    p.tenant_id = "gerflor"
    return p


def _tmpfile(content: bytes, name: str = "catalogue.pdf") -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    f = d / name
    f.write_bytes(content)
    return f


# ── document identity ───────────────────────────────────────────────────────────

def test_the_same_file_keeps_its_id_when_only_the_timestamp_moves():
    """Re-uploading an unchanged file must be idempotent: same id, same chunk ids, same point
    ids, so the write overwrites instead of adding a second copy."""
    p = _pipeline()
    f = _tmpfile(b"identical bytes")
    first = p._doc_id(f)
    time.sleep(0.05)
    os.utime(f, None)
    assert p._doc_id(f) == first


def test_changed_content_gets_a_new_id():
    p = _pipeline()
    f = _tmpfile(b"version one")
    first = p._doc_id(f)
    f.write_bytes(b"version two - now with the OCR'd pages")
    assert p._doc_id(f) != first


def test_the_same_bytes_under_a_different_name_are_a_different_document():
    p = _pipeline()
    a = _tmpfile(b"same bytes", "stock.pdf")
    b = _tmpfile(b"same bytes", "prices.pdf")
    assert p._doc_id(a) != p._doc_id(b)


def test_the_same_file_for_another_tenant_is_a_different_document():
    a = _pipeline()
    b = VulaIngestionPipeline.__new__(VulaIngestionPipeline)
    b.tenant_id = "off-the-hook"
    f = _tmpfile(b"shared bytes")
    assert a._doc_id(f) != b._doc_id(f)


def test_an_unreadable_file_does_not_break_ingestion():
    """Identity must never be the thing that fails an ingest."""
    p = _pipeline()
    f = _tmpfile(b"x")
    with patch("builtins.open", side_effect=OSError("locked")):
        assert p._doc_id(f)


# ── superseding the previous version ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_updated_file_clears_its_predecessor():
    p = _pipeline()
    p.store = MagicMock(
        doc_ids_for_filename=AsyncMock(return_value=["old1", "old2", "keep"]),
        delete_document=AsyncMock(return_value=10),
    )
    removed = await p._supersede_older_versions("catalogue.pdf", "keep")
    deleted = [c.args[1] for c in p.store.delete_document.await_args_list]
    assert sorted(deleted) == ["old1", "old2"], "the new version is never deleted"
    assert removed == 20


@pytest.mark.asyncio
async def test_an_identical_reupload_removes_nothing():
    """Content-hashed ids mean the re-ingest IS the same document — there is nothing to clear,
    and the upsert simply overwrites."""
    p = _pipeline()
    p.store = MagicMock(
        doc_ids_for_filename=AsyncMock(return_value=["same"]),
        delete_document=AsyncMock(return_value=10),
    )
    assert await p._supersede_older_versions("catalogue.pdf", "same") == 0
    p.store.delete_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_first_time_upload_has_nothing_to_supersede():
    p = _pipeline()
    p.store = MagicMock(doc_ids_for_filename=AsyncMock(return_value=[]),
                        delete_document=AsyncMock())
    assert await p._supersede_older_versions("new.pdf", "abc") == 0
    p.store.delete_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lookup_failure_does_not_abort_the_ingest():
    """Better a possible duplicate than a document that fails to store at all."""
    p = _pipeline()
    p.store = MagicMock(doc_ids_for_filename=AsyncMock(side_effect=RuntimeError("qdrant down")),
                        delete_document=AsyncMock())
    assert await p._supersede_older_versions("x.pdf", "abc") == 0


def test_supersede_runs_before_the_upsert():
    """Order matters: clearing the old version after writing the new one would delete both when
    ids collide."""
    import inspect
    src = inspect.getsource(VulaIngestionPipeline.ingest_file)
    assert src.index("_supersede_older_versions") < src.index("upsert_chunks")

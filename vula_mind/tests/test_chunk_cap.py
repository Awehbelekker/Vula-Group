"""HPC's PC No. 10, 26 Oct: a 500 KB valuation sheet was one 288,000-character chunk.

A spreadsheet becomes " | "-joined rows with single newlines and no sentence ends, so the chunker
never split it; the embeddings API refused the chunk (400 Bad Request) and the whole upload failed —
nothing filed, no BOQ read.
"""
from vula.ingestion.pipeline import MAX_CHUNK_CHARS, SemanticChunker

ROW = " | ".join(["1", "", "One brick walls circular on plan", "", "m²", "15", "677.79", "R10,166.85",
                  "15.00", "R10,166.85", "15.00", "R10,166.85", "-", "R-"] + [""] * 10)


def test_a_big_sheet_is_split_by_row_and_never_over_the_cap():
    text = "ITEM NO | QUANTITY | RATE | AMOUNT\n" + "\n".join(f"{n} {ROW}" for n in range(1500))
    chunks = SemanticChunker().chunk(text)
    assert len(chunks) > 50 and max(map(len, chunks)) <= MAX_CHUNK_CHARS
    joined = "\n".join(chunks)
    assert all(f"{n} 1 |" in joined for n in (0, 750, 1499))      # every row is still there


def test_one_unbroken_run_is_cut_to_the_cap():
    assert [len(c) for c in SemanticChunker().chunk("x" * 9000)] == [4000, 4000, 1000]


def test_ordinary_prose_chunks_as_before():
    text = "\n\n".join(f"Paragraph {i}. It has two sentences." for i in range(5))
    assert SemanticChunker().chunk(text) == [text]

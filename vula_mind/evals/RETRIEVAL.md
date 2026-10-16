# Retrieval eval — results

How to run and what each number means: the docstring of `evals/retrieval.py`. Cases:
`cases/retrieval.yaml`; fixture knowledge bases: `cases/retrieval_fixture.yaml`.

```bash
cd vula_mind
python -m evals.run retrieval                          # offline fixture subset (CI)
python -m evals.run retrieval --live                   # real tenants, read-only, needs the keys
python -m evals.run retrieval-synth --tenant gerflor   # unreviewed synthetic cases -> evals/reports/
```

Live runs also happen inside Master › Benchmark (`POST /v1/master/benchmark`, `only: "retrieval"`
for just these); the numbers land in the report's `retrieval` block.

**Fixture numbers are not quality numbers.** The fixture uses a stub embedder (idf-weighted word
overlap), so absolute recall says little about bge-m3. What they do measure exactly is the
pipeline's own logic — query variants, the keyword pass, merging, truncation into the prompt, and
filters — which is what Step C changes. Live numbers are the real baseline and need a run on
the server.

## Baseline — before Step C (commit with this file, 2026-10-16)

Fixture, 114 cases (3 tenants: flooring 50, architect 35, seafood 29). 71/114 passed.

| | n | recall@5 | recall@20 | MRR | nDCG@10 | in prompt | fact in prompt | abstention | leak | p50 / p95 ms | ctx tokens | embeds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **all** | 114 | 0.957 | 0.957 | 0.957 | 0.952 | 0.948 | **0.759** | 0.423 | **1.0** | 3.1 / 9.3 | 127 | 1.27 |
| fixture-architect | 35 | 0.893 | 0.893 | 0.929 | 0.901 | 0.929 | 1.0 | 0.381 | – | 2.6 / 4.1 | 103 | 1.09 |
| fixture-flooring | 50 | 1.0 | 1.0 | 0.985 | 0.989 | 0.971 | 0.682 | 0.438 | – | 5.7 / 11.5 | 179 | 1.5 |
| fixture-seafood | 29 | 0.9 | 0.9 | 0.9 | 0.9 | 0.9 | 1.0 | 0.467 | 1.0 | 2.1 / 4.8 | 67 | 1.10 |
| lookup | 24 | 1.0 | 1.0 | 0.979 | 0.985 | 1.0 | 0.875 | – | – | | 140 | |
| multi_hop | 2 | 0.75 | 0.75 | 1.0 | 0.807 | 1.0 | – | – | – | | 157 | |
| policy | 14 | 0.857 | 0.857 | 0.857 | 0.857 | 0.857 | 1.0 | – | – | | 115 | |
| price | 8 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | – | – | | 186 | |
| sensitive | 4 | – | – | – | – | – | – | – | **1.0** | | 70 | |
| spec | 10 | 1.0 | 1.0 | 1.0 | 1.0 | 0.9 | **0.4** | – | – | | 316 | |
| unanswerable | 52 | – | – | – | – | – | – | 0.423 | – | | 82 | |

What the baseline shows:

* **Truncation (C1).** On spec questions the right data sheet is found every time (recall 1.0)
  but the figure reaches the prompt only 40% of the time: the slip-resistance and warranty rows
  sit past character 900 of the chunk, and `format_kb_chunks` cut every chunk at `[:900]`.
* **Customer leak (C5).** All 4 sensitive customer questions pulled a supplier invoice or the
  wages roster into the customer assistant's grounding — and so did 2 of the "unanswerable"
  customer questions ("is there parking at the shop" → the staff roster).
* **Abstention** is 42% at retrieval level: the 0.30 threshold lets a loosely related chunk
  through for most absent questions. Not changed in this session (C4 only moves the threshold).

Live baseline: **not yet run** — this session has no route to production Qdrant. Run
`python -m evals.run retrieval --live` on a machine with the keys (or Master › Benchmark with
`only: "retrieval"`) and paste the summary here before reviewing Step C.

## After Step C

(filled in by the Step C commits — see below)

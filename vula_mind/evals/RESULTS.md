# Eval results — trend (accuracy program, one table per workstream, dated)

Detail and method per layer: [RETRIEVAL.md](RETRIEVAL.md) (retrieval). Fixture numbers measure
pipeline logic on invented documents, not embedding quality; live numbers are pending a run with
production access.

## WS1/WS2 — retrieval (fixture, 114 cases)

| date | change | recall@5 | MRR | nDCG@10 | in prompt | fact in prompt | spec fact | abstention | customer leak | grounding tok |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-16 | baseline (`ba2011c`) | 0.957 | 0.957 | 0.952 | 0.948 | 0.759 | 0.4 | 0.423 | 1.0 | 127 |
| 2026-10-16 | C1 token budget | 0.957 | 0.957 | 0.952 | 0.948 | 0.966 | 0.9 | 0.423 | 1.0 | 169 |
| 2026-10-16 | C1–C5 | 0.957 | 0.957 | 0.952 | 0.948 | 0.966 | 0.9 | 0.442 | 0.0 | 166 |
| 2026-10-17 | 1b (paths, keyword pass, backfill) | 0.957 | 0.957 | 0.952 | 0.948 | 0.966 | 0.9 | 0.442 | 0.0 | 166 |

## 1b — customer view (fixture: 3 tenants, every case question + 20 probes = 174 searches)

| date | filter | chunk hits | staff/owner hits |
|---|---|---|---|
| 2026-10-17 | none (pre-C5 behaviour) | 940 | 793 |
| 2026-10-17 | customer filter, lenient | 3 | 0 |
| 2026-10-17 | customer filter, strict | 3 | 0 |

Live (gerflor, digg-demo, off-the-hook): not yet run — `python -m evals.run customer-view`.

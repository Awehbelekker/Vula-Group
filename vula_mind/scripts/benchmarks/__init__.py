"""Tier 2 of the reliability program (2026-08-22/23): a curated set of real-shaped scenarios run
against the actual live model to produce a pass-rate scorecard — the answer to "is Vula getting
better" as a number instead of a feeling. See scripts/benchmark_chat.py to run it.

Tier 1 (tests/test_known_bad_transcripts.py) is deterministic, mocked, and runs in every CI push
— it catches literal regressions of bugs already found. This is different on purpose: it hits the
real configured LLM route (local or cloud, whichever production would actually use) so it can
catch NEW failures a mock can't, at the cost of being slower, non-deterministic, and needing a
network/API budget — which is exactly why it's a separate, on-demand script, not a pytest file.
"""

# Changelog

All notable changes to knowledge-weaver. Versioning follows [VERSIONING.md](VERSIONING.md).

## v1.0.0 — first stable release (2026-06-04)

First production-validated release. Bundles the W1→W3.4 optimization program plus
the post-swap data recovery. Implements `KW_MEMORY_FILE_SPEC` **v1.1**.

### Added
- **W1 — entity resolution**: vector-first three-band resolver (merge / review /
  distinct) with an identifier guard, a `merge_review` queue, `merge_log`
  rollback, and registry alias normalization.
- **W2 — structured handoff**: ingest DMA's `### 结构化事实` JSON facts
  (`KNOWLEDGE_WEAVER_EXTRACT_MODE=json`); registry lexicon injection.
- **W3.1/3.2 — historical rebuild**: `backfill_facts.py`, `rebuild_from_facts.py`.
- **W3.4 — typed knowledge graph**: closed relation vocab
  (依赖/使用/包含/导致/取代/关于/矛盾/相关) with schema-constrained LLM typing,
  triplet hints, and online edge typing in consolidation.
- **Retrieval**: jieba Chinese FTS, RRF fusion (k=60), registered-project nodes.
- **Recovery + dedup**: `recover_from_old.py` ports rebuild-lost entities from the
  pre-swap DB (carrying day_count + vector; tech gated by accumulated signal);
  `semantic_dedupe` second-pass merge (auto / review-only); merging keeps the
  merged-away name as a searchable FTS alias (recall-preserving).
- **Backlog**: W3.3 supersede de-ranking, W6 embedding-drift check.
- `__version__` exposed from package metadata; `VERSIONING.md`.

### Notes
- Production DB rebuilt from 656 noisy → clean structured entities; generic
  RELATES_TO edges typed into the closed vocabulary; 51 real decisions + signal-
  gated tech recovered from the pre-swap DB.

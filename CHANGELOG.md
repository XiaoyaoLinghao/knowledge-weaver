# Changelog

All notable changes to knowledge-weaver. Versioning follows [VERSIONING.md](VERSIONING.md).

## v1.1.2 — stop re-judging dismissed pairs (2026-06-04)

### Fixed
- Self-maintenance cost leak: with `KW_SEMANTIC_DEDUPE=review` +
  `KW_TIEBREAK_REVIEWS=1`, every consolidation re-queued and re-judged (LLM cost)
  the same vector-similar-but-distinct pairs, because a dismissed pair was not
  treated as "already decided". Now `insert_review` is idempotent against
  pending AND dismissed, and `semantic_dedupe` skips any pair that already has a
  review — so a distinct pair is judged once, not every cron cycle.

## v1.1.1 — tie-break count fix (2026-06-04)

### Fixed
- `tiebreak.apply_tiebreak`: a no-op merge (target already removed by a prune or
  an earlier merge in the same batch) is now counted as `dismissed` — matching
  the review status it sets — instead of being reported as a phantom `merged`.
  Cosmetic count only; data was always correct.

## v1.1.0 — de-ruling + cleanup (2026-06-04)

Implements `KW_MEMORY_FILE_SPEC` v1.1 (unchanged).

### Added
- **T1 — de-enumerated identifier guard**: `_digit_variant_pair` (resolver) flags
  same-template-differ-in-digits pairs (v0.2.0/v2.9.0, COMP7940/COMP7240,
  ports) without enumerating formats — a new identifier shape needs no new regex.
  The old `_IDENTIFIER_RE` stays only as the LLM-unavailable fallback. Used by
  both ingest resolution and `semantic_dedupe`.
- **T1 — LLM tie-breaker** (`tiebreak.py` + `scripts/tiebreak_reviews.py`):
  asks "same thing or two different things?" over the merge-review queue,
  auto-merging 'same' / dismissing 'different' / keeping genuine ambiguity.
  Clears the review backlog; env-gated in consolidation (`KW_TIEBREAK_REVIEWS=1`).

### Changed
- **T4 — `导致` typing prompt tightened**: counterfactual test ("if A didn't
  happen, would B?") + explicit "temporal order / investigation steps /
  co-occurrence are NOT causation" + a negative few-shot. Reduces over-applied
  causal edges (spot-check was 67%).

### Tooling
- **T2.1 — residue prune** (`prune_residue.py` + `scripts/prune_residue.py`):
  one-off cleanup of bare version numbers / internal codes (P1-1, WI2, Track2)
  the legacy regex extractor mis-typed as `tech` (signal-gate can't catch them).

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

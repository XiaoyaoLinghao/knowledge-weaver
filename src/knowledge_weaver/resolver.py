"""Entity resolution — vector-first semantic dedup with three-band confidence.

Upgrades the legacy name-first ``_find_similar_entity`` (pipeline.py): that
matched on ``SequenceMatcher`` name ratio first and only consulted embeddings
for the 0.70-0.85 "borderline" band — which *structurally misses* cross-language
/ divergent aliases (e.g. "HomeBrain" vs "家庭大脑", whose name ratio ≈ 0, never
reaching the embedding pass).

This resolver:

1. **Authoritative** — registered aliases (from the MEMORY.md registry)
   deterministically normalize to the canonical entity id, whether or not the
   canonical entity already exists.
2. **Vector-first recall** — fts + vector candidates of the *same type*, scored
   by cosine against the candidate's stored embedding (consistent metric).
3. **Three bands**
   - merge   : name ratio >= NAME_HIGH  OR  cosine >= RESOLVE_HIGH  -> auto-merge
   - review  : RESOLVE_MID <= cosine < RESOLVE_HIGH                 -> review queue
   - distinct: otherwise                                            -> keep separate

Thresholds are env-configurable; calibrate with
``scripts/calibrate_resolution.py`` against a real DB.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from knowledge_weaver.db import (
    _cosine,
    get_entity_vector,
    search_entities_fts,
    search_entity_vectors,
)


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name)
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


# Cosine >= HIGH -> auto-merge; [MID, HIGH) -> review; name ratio >= NAME_HIGH -> auto-merge.
RESOLVE_HIGH = _env_float("KNOWLEDGE_WEAVER_RESOLVE_HIGH", 0.90)
RESOLVE_MID = _env_float("KNOWLEDGE_WEAVER_RESOLVE_MID", 0.82)
NAME_HIGH = _env_float("KNOWLEDGE_WEAVER_RESOLVE_NAME_HIGH", 0.85)
CANDIDATE_K = 10


@dataclass
class Resolution:
    """Outcome of resolving a freshly-extracted entity against existing ones."""

    action: str  # "normalize" | "merge" | "review" | "distinct"
    into_id: Optional[str] = None
    score: float = 0.0
    reason: str = ""


def load_alias_map() -> dict[str, str]:
    """Map each registered alias's entity-id -> canonical entity-id.

    Honors KNOWLEDGE_WEAVER_REGISTRY_PATH; never raises (empty on any failure).
    """
    try:
        from knowledge_weaver.registry import alias_to_canonical_map, load_registry

        path = os.environ.get(
            "KNOWLEDGE_WEAVER_REGISTRY_PATH", "/root/.openclaw/workspace/MEMORY.md"
        )
        return alias_to_canonical_map(load_registry(path))
    except Exception:
        return {}


def resolve_entity(
    conn,
    *,
    new_id: str,
    entity_type: str,
    name: str,
    summary: str,
    embedder=None,
    alias_to_canonical: Optional[dict[str, str]] = None,
) -> Resolution:
    """Decide how a freshly-extracted entity relates to existing entities.

    ``new_id`` is the slug id the extractor assigned (only called when no exact
    id match exists). Returns a :class:`Resolution`; the caller acts on it.
    """
    # 1. Authoritative: registered alias -> normalize to canonical id.
    if alias_to_canonical:
        canonical = alias_to_canonical.get(new_id)
        if canonical and canonical != new_id:
            return Resolution("normalize", into_id=canonical, score=1.0,
                              reason="registered-alias")

    # 2. Candidate recall (same type only): fts + vector.
    candidates: dict[str, object] = {}
    try:
        for row in search_entities_fts(conn, name, limit=CANDIDATE_K):
            if row["id"] != new_id and row["type"] == entity_type:
                candidates[row["id"]] = row
    except Exception:
        pass

    query_vec = None
    if embedder is not None and summary:
        try:
            query_vec = embedder.embed(summary[:500]) or None
        except Exception:
            query_vec = None
    if query_vec:
        try:
            for row in search_entity_vectors(conn, query_vec, limit=CANDIDATE_K):
                if row["id"] != new_id and row["type"] == entity_type:
                    candidates.setdefault(row["id"], row)
        except Exception:
            pass

    if not candidates:
        return Resolution("distinct")

    # 3. Score each candidate: name ratio + embedding cosine (consistent metric).
    merge_id: Optional[str] = None
    merge_score, merge_reason = 0.0, ""
    review_id: Optional[str] = None
    review_score = 0.0
    for cid, row in candidates.items():
        cand_name = (row["name"] or "")  # type: ignore[index]
        name_ratio = SequenceMatcher(None, name.lower(), cand_name.lower()).ratio()
        cos = 0.0
        if query_vec:
            cvec = get_entity_vector(conn, cid)
            if cvec:
                cos = _cosine(query_vec, cvec)

        if name_ratio >= NAME_HIGH or cos >= RESOLVE_HIGH:
            s = max(name_ratio, cos)
            if s > merge_score:
                merge_id, merge_score = cid, s
                merge_reason = "name" if name_ratio >= NAME_HIGH else "vector"
        elif cos >= RESOLVE_MID:
            if cos > review_score:
                review_id, review_score = cid, cos

    if merge_id is not None:
        return Resolution("merge", into_id=merge_id, score=round(merge_score, 4),
                          reason=f"auto:{merge_reason}")
    if review_id is not None:
        return Resolution("review", into_id=review_id, score=round(review_score, 4),
                          reason="review:vector")
    return Resolution("distinct")

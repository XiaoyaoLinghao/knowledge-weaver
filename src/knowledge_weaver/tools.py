"""MCP tool implementations — 6 plain Python functions.

These are NOT yet MCP-decorated tools (that happens in server.py / Task 10).
Each function receives a sqlite3.Connection and keyword parameters,
and returns a dict suitable for MCP JSON response.
"""

from __future__ import annotations

import datetime
import difflib
import json
import logging
import os
from collections import deque
from typing import Any

from knowledge_weaver.db import (
    get_entities_by_ids,
    get_entity,
    get_relations_for_entities,
    get_relations_for_entity,
    list_all_entities,
    list_all_manifest,
    list_entities_by_type,
    log_access,
    search_entities_fts,
)
from knowledge_weaver.scorer import ImportanceScorer, filter_by_score, score_entity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return {}
    return dict(row)


def _resolve_entity_id(conn, topic: str) -> str | None:
    """Try to resolve a topic string to an entity_id.

    Strategy:
    1. Exact match on entity id
    2. FTS match on name/summary
    """
    # Try exact ID match
    entity = get_entity(conn, topic)
    if entity:
        return topic

    # FTS fallback
    rows = search_entities_fts(conn, topic, limit=1)
    if rows:
        return rows[0]["id"]

    return None


def _collect_related_ids(conn, entity_id: str, max_depth: int) -> set[str]:
    """BFS traversal of the relations graph up to max_depth hops.

    Uses batch queries per BFS level instead of per-entity queries.
    """
    visited: set[str] = {entity_id}
    current_level: set[str] = {entity_id}
    all_relations: list[tuple[str, str, str, float, str]] = []  # (from, to, type, weight, src_id)

    for depth in range(max_depth):
        if not current_level:
            break
        # Batch-load relations for all entities at this level
        level_rels = get_relations_for_entities(conn, list(current_level))
        next_level: set[str] = set()
        for rel in level_rels:
            from_id = rel["from_entity"]
            to_id = rel["to_entity"]
            if from_id in current_level and to_id not in visited:
                visited.add(to_id)
                next_level.add(to_id)
                all_relations.append((from_id, to_id, rel["rel_type"], rel["weight"], from_id))
            elif to_id in current_level and from_id not in visited:
                visited.add(from_id)
                next_level.add(from_id)
                all_relations.append((to_id, from_id, rel["rel_type"], rel["weight"], to_id))
        current_level = next_level

    return visited


def _build_entity_result(row) -> dict[str, Any]:
    """Build a standard entity result dict from a DB row."""
    metadata = {}
    try:
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
    except (json.JSONDecodeError, TypeError):
        pass

    return {
        "entity_id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "summary": row["summary"][:500] if row["summary"] else "",
        "importance": row["importance"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "day_count": row["day_count"],
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Tool 1: knowledge_search
# ---------------------------------------------------------------------------


def knowledge_search(
    conn,
    *,
    query: str,
    entity_type: str | None = None,
    max_results: int = 10,
    min_score: float = 0.3,
    embedder=None,
) -> dict:
    """Semantic / text search for knowledge entities.

    If an embedder is provided, use embedding-based cosine similarity.
    Otherwise fall back to FTS (LIKE-based) search via search_entities_fts.
    Apply min_score filter using ImportanceScorer.
    """
    # Step 1: candidate retrieval
    if embedder is not None:
        # Embedding-based search path
        try:
            query_vec = embedder.embed(query)
            if query_vec:
                from knowledge_weaver.db import search_entity_vectors
                candidates_raw = search_entity_vectors(conn, query_vec, limit=max_results * 3)
                candidates = [_row_to_dict(r) for r in candidates_raw]
            else:
                candidates = None
        except Exception:
            logger.warning("Embedding search failed, falling back to FTS")
            candidates = None

        if candidates is None:
            # FTS fallback
            rows = search_entities_fts(conn, query, limit=max_results * 3)
            candidates = [_row_to_dict(r) for r in rows]
    else:
        # FTS path (no embedder)
        rows = search_entities_fts(conn, query, limit=max_results * 3)
        candidates = [_row_to_dict(r) for r in rows]

    # Step 2: type filter
    if entity_type:
        candidates = [c for c in candidates if c.get("type") == entity_type]

    # Step 3: importance score filter
    today = datetime.date.today()
    scored_candidates = filter_by_score(candidates, min_score=min_score, today=today)

    # Step 4: build results
    # fix: batch-load relations for all candidates to eliminate N+1
    candidates_slice = scored_candidates[:max_results]
    all_candidate_ids = [c.get("id", "") for c in candidates_slice if c.get("id")]
    batch_rels = get_relations_for_entities(conn, all_candidate_ids)
    # Build eid -> [related_rels] map
    from collections import defaultdict
    rels_by_eid: dict[str, list] = defaultdict(list)
    for rel in batch_rels:
        rels_by_eid[rel["from_entity"]].append(rel)
        rels_by_eid[rel["to_entity"]].append(rel)

    results = []
    for entity in candidates_slice:
        eid = entity.get("id", "")
        related_rels = rels_by_eid.get(eid, [])
        related_ids = [
            r["to_entity"] if r["from_entity"] == eid else r["from_entity"]
            for r in related_rels[:5]
        ]

        # Calculate a combined "similarity_score" using fuzzy name matching
        importance = entity.get("importance", 0.0)
        name = entity.get("name", "") or ""
        summary = entity.get("summary", "") or ""
        query_lower = query.lower()
        name_ratio = difflib.SequenceMatcher(None, query_lower, name.lower()).ratio()
        summary_hit = 1.0 if query_lower in summary.lower() else 0.0
        similarity_score = importance * 0.5 + name_ratio * 0.3 + summary_hit * 0.2
        similarity_score = round(min(1.0, similarity_score), 4)

        results.append({
            "entity_id": eid,
            "name": entity.get("name", ""),
            "type": entity.get("type", ""),
            "summary": (entity.get("summary", "") or "")[:200],
            "similarity_score": round(similarity_score, 4),
            "importance": importance,
            "first_seen": entity.get("first_seen", ""),
            "last_seen": entity.get("last_seen", ""),
            "related_entities": related_ids,
        })

        # Log access
        try:
            log_access(conn, eid, "knowledge_search", query)
        except Exception:
            logger.warning("Failed to log access for %s", eid)

    return {"results": results, "total_hits": len(results)}


# ---------------------------------------------------------------------------
# Tool 2: knowledge_trace
# ---------------------------------------------------------------------------


def knowledge_trace(
    conn,
    *,
    topic: str,
    max_depth: int = 2,
) -> dict:
    """Trace a topic's full timeline across all indexed days.

    Resolve topic -> entity_id, BFS traverse relations, collect timeline + decisions.
    """
    entity_id = _resolve_entity_id(conn, topic)
    if entity_id is None:
        return {"entity": None, "timeline": [], "related": [], "decisions": []}

    # Get core entity
    entity_row = get_entity(conn, entity_id)
    if entity_row is None:
        return {"entity": None, "timeline": [], "related": [], "decisions": []}

    entity_data = _build_entity_result(entity_row)

    # BFS traverse relations
    related_ids = _collect_related_ids(conn, entity_id, max_depth)

    # Batch-load all related entities
    other_ids = [rid for rid in related_ids if rid != entity_id]
    related_entity_map = get_entities_by_ids(conn, other_ids)

    # Pre-load relations for the source entity (for rel_type lookup)
    source_rels = get_relations_for_entity(conn, entity_id)
    source_rel_map: dict[str, tuple[str, float]] = {}
    for rel in source_rels:
        other = rel["to_entity"] if rel["from_entity"] == entity_id else rel["from_entity"]
        source_rel_map[other] = (rel["rel_type"], rel["weight"])

    # Build related entities list
    related = []
    for rid in other_ids:
        rrow = related_entity_map.get(rid)
        if rrow is None:
            continue
        rel_type, weight = source_rel_map.get(rid, ("RELATES_TO", 0.5))
        related.append({
            "entity_id": rid,
            "name": rrow["name"],
            "type": rrow["type"],
            "rel_type": rel_type,
            "weight": weight,
        })

    # Collect timeline: sort entities by first_seen date
    timeline_entities = [entity_row]
    for rid in other_ids:
        rrow = related_entity_map.get(rid)
        if rrow:
            timeline_entities.append(rrow)

    timeline = sorted(
        [{"date": e["first_seen"], "summary": e["summary"][:200]}
         for e in timeline_entities if e["first_seen"]],
        key=lambda x: x["date"],
    )

    # Collect decisions among related entities
    decisions = []
    for rid in other_ids:
        rrow = related_entity_map.get(rid)
        if rrow and rrow["type"] == "decision":
            decisions.append({
                "date": rrow["last_seen"],
                "content": rrow["summary"][:200],
            })
    # Also check the entity itself
    if entity_row["type"] == "decision":
        decisions.insert(0, {
            "date": entity_row["last_seen"],
            "content": entity_row["summary"][:200],
        })

    # Log access
    try:
        log_access(conn, entity_id, "knowledge_trace", topic)
    except Exception:
        logger.warning("Failed to log access for %s", entity_id)

    return {
        "entity": entity_data,
        "timeline": timeline,
        "related": related[:20],
        "decisions": decisions,
    }


# ---------------------------------------------------------------------------
# Tool 3: active_projects
# ---------------------------------------------------------------------------


def active_projects(
    conn,
    *,
    lookback_days: int = 14,
) -> dict:
    """List currently active projects with status and open tasks."""
    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=lookback_days)).isoformat()

    # Query type='project' with last_seen within lookback
    project_rows = list_entities_by_type(conn, "project")
    active = [r for r in project_rows if r["last_seen"] >= cutoff]

    # Pre-load all tasks once (instead of per-project query)
    all_tasks = list_entities_by_type(conn, "task")

    projects = []
    for row in active:
        # Attach open task entities (tasks with same project in name/summary)
        open_tasks = [
            t["summary"][:100]
            for t in all_tasks
            if row["name"].lower() in (t["name"] + t["summary"]).lower()
        ][:5]

        status = "active" if row["day_count"] >= 3 else "recent"

        projects.append({
            "entity_id": row["id"],
            "name": row["name"],
            "last_active": row["last_seen"],
            "active_days": row["day_count"],
            "status": status,
            "open_tasks": open_tasks,
            "latest_summary": row["summary"][:200],
            "importance": row["importance"],
        })

    # Log access
    try:
        for p in projects:
            log_access(conn, p["entity_id"], "active_projects", "")
    except Exception:
        logger.warning("Failed to log access in active_projects")

    return {"projects": projects}


# ---------------------------------------------------------------------------
# Tool 4: preference_lookup
# ---------------------------------------------------------------------------


def preference_lookup(
    conn,
    *,
    topic: str | None = None,
    domain: str | None = None,
) -> dict:
    """Query user preferences and habits."""
    rows = list_entities_by_type(conn, "preference")

    # Optional topic filter
    if topic:
        topic_lower = topic.lower()
        rows = [
            r for r in rows
            if topic_lower in (r["name"] + r["summary"]).lower()
        ]

    # Optional domain filter (check metadata.domain)
    if domain:
        domain_lower = domain.lower()
        filtered = []
        for r in rows:
            try:
                metadata = json.loads(r["metadata"]) if r["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                metadata = {}
            if metadata.get("domain", "").lower() == domain_lower:
                filtered.append(r)
        rows = filtered

    preferences = []
    for r in rows:
        preferences.append({
            "entity_id": r["id"],
            "content": r["summary"],
            "first_seen": r["first_seen"],
            "day_count": r["day_count"],
            "strength": r["importance"],
        })

    # Log access
    try:
        for p in preferences:
            log_access(conn, p["entity_id"], "preference_lookup", topic or "")
    except Exception:
        logger.warning("Failed to log access in preference_lookup")

    return {"preferences": preferences}


# ---------------------------------------------------------------------------
# Tool 5: decision_history
# ---------------------------------------------------------------------------


def decision_history(
    conn,
    *,
    topic: str,
    include_risk: bool = True,
) -> dict:
    """Query historical decisions matching a topic, with related risks and tasks."""
    rows = list_entities_by_type(conn, "decision")

    # Topic filter
    topic_lower = topic.lower()
    matched = [
        r for r in rows
        if topic_lower in (r["name"] + r["summary"]).lower()
    ]

    # Pre-load all relations for matched decisions in one query
    matched_ids = [r["id"] for r in matched]
    all_rels = get_relations_for_entities(conn, matched_ids) if matched_ids else []
    # Build a dict: entity_id -> list of (other_id, rel_type) for quick lookup
    rel_map: dict[str, list[tuple[str, str]]] = {eid: [] for eid in matched_ids}
    related_entity_ids: set[str] = set()
    for rel in all_rels:
        if rel["from_entity"] in rel_map:
            other = rel["to_entity"]
            rel_map[rel["from_entity"]].append((other, rel["rel_type"]))
            related_entity_ids.add(other)
        if rel["to_entity"] in rel_map:
            other = rel["from_entity"]
            rel_map[rel["to_entity"]].append((other, rel["rel_type"]))
            related_entity_ids.add(other)

    # Batch-load all related entities
    related_entities = get_entities_by_ids(conn, list(related_entity_ids)) if related_entity_ids else {}

    # Pre-load risk entities if needed
    risk_by_topic: dict[str, str] = {}
    if include_risk:
        risk_rows = list_entities_by_type(conn, "risk")
        for risk in risk_rows:
            if topic_lower in (risk["name"] + risk["summary"]).lower():
                risk_by_topic[risk["id"]] = risk["id"]

    decisions = []
    for r in matched:
        eid = r["id"]

        # Collect related risks via pre-loaded relations
        related_risks = []
        follow_up_tasks = []

        for other_id, _rel_type in rel_map.get(eid, []):
            other = related_entities.get(other_id)
            if other is None:
                continue
            if other["type"] == "risk":
                related_risks.append(other["id"])
            elif other["type"] == "task":
                follow_up_tasks.append(other["id"])

        # If include_risk, add topic-matched risks
        if include_risk:
            for risk_id in risk_by_topic:
                if risk_id not in related_risks:
                    related_risks.append(risk_id)

        decisions.append({
            "entity_id": eid,
            "content": r["summary"],
            "date": r["last_seen"],
            "rationale": r["name"],
            "related_risks": related_risks[:10],
            "follow_up_tasks": follow_up_tasks[:10],
        })

    # Log access
    try:
        for d in decisions:
            log_access(conn, d["entity_id"], "decision_history", topic)
    except Exception:
        logger.warning("Failed to log access in decision_history")

    return {"decisions": decisions}


# ---------------------------------------------------------------------------
# Tool 6: knowledge_stats
# ---------------------------------------------------------------------------


def knowledge_stats(conn) -> dict:
    """System status overview: entity counts, relations, indexed days."""
    # Count entities by type
    type_rows = conn.execute(
        "SELECT type, COUNT(*) as cnt FROM entities GROUP BY type ORDER BY cnt DESC"
    ).fetchall()
    entity_counts = {r["type"]: r["cnt"] for r in type_rows}
    total_entities = sum(entity_counts.values())

    # Total relations
    rel_row = conn.execute("SELECT COUNT(*) as cnt FROM relations").fetchone()
    total_relations = rel_row["cnt"] if rel_row else 0

    # Processed days from manifest
    manifest_rows = list_all_manifest(conn)
    indexed_days = len(manifest_rows)

    # Last consolidation time
    last_consolidation = ""
    if manifest_rows:
        latest = manifest_rows[-1]
        last_consolidation = latest["processed_at"] or ""

    # DB size (approximate)
    db_size_mb = 0.0
    try:
        db_path_row = conn.execute("PRAGMA database_list").fetchone()
        if db_path_row and db_path_row["file"]:
            db_size_mb = round(os.path.getsize(db_path_row["file"]) / (1024 * 1024), 2)
    except Exception:
        pass

    # --- Quality metrics ---

    # Relation type distribution
    rel_types = conn.execute(
        "SELECT rel_type, COUNT(*) as cnt FROM relations GROUP BY rel_type ORDER BY cnt DESC"
    ).fetchall()
    relation_type_counts = {r["rel_type"]: r["cnt"] for r in rel_types}

    # Orphan entities (no relations at all)
    orphan_row = conn.execute("""
        SELECT COUNT(*) as cnt FROM entities e
        WHERE NOT EXISTS (SELECT 1 FROM relations r WHERE r.from_entity=e.id OR r.to_entity=e.id)
    """).fetchone()
    orphan_count = orphan_row["cnt"] if orphan_row else 0

    # Relation density (avg relations per entity)
    avg_relations = round(total_relations / total_entities, 2) if total_entities else 0.0

    # Importance score distribution (quartiles)
    scores = [r[0] for r in conn.execute(
        "SELECT importance FROM entities ORDER BY importance"
    ).fetchall()]
    score_quartiles = {}
    if scores:
        n = len(scores)
        score_quartiles = {
            "min": round(scores[0], 4),
            "p25": round(scores[n // 4], 4),
            "p50": round(scores[n // 2], 4),
            "p75": round(scores[3 * n // 4], 4),
            "max": round(scores[-1], 4),
            "distinct_values": len(set(round(s, 4) for s in scores)),
        }

    # Gini coefficient for importance distribution (0=equal, 1=concentrated)
    gini = 0.0
    if scores and len(scores) > 1:
        sorted_s = sorted(scores)
        n_s = len(sorted_s)
        cum = 0.0
        for i, s in enumerate(sorted_s):
            cum += (i + 1) * s
        total = sum(sorted_s)
        if total > 0:
            gini = round((2 * cum / (n_s * total)) - (n_s + 1) / n_s, 4)

    # Noise ratio: entities with importance < 0.3
    noise_count = sum(1 for s in scores if s < 0.3)
    noise_ratio = round(noise_count / total_entities, 4) if total_entities else 0.0

    # Relation density by type
    rel_density = {}
    for etype, cnt in entity_counts.items():
        type_rel_row = conn.execute(
            """SELECT COUNT(*) as cnt FROM relations r
               JOIN entities e ON (r.from_entity=e.id OR r.to_entity=e.id)
               WHERE e.type=?""",
            (etype,),
        ).fetchone()
        type_rels = type_rel_row["cnt"] if type_rel_row else 0
        rel_density[etype] = round(type_rels / cnt, 2) if cnt else 0.0

    return {
        "entity_counts": entity_counts,
        "total_entities": total_entities,
        "total_relations": total_relations,
        "relation_type_counts": relation_type_counts,
        "indexed_days": indexed_days,
        "last_consolidation": last_consolidation,
        "embedding_model": os.environ.get("EMBEDDING_MODEL", ""),
        "db_size_mb": db_size_mb,
        # Quality metrics
        "orphan_entities": orphan_count,
        "avg_relations_per_entity": avg_relations,
        "importance_distribution": score_quartiles,
        "gini_coefficient": gini,
        "noise_ratio": noise_ratio,
        "noise_entity_count": noise_count,
        "relation_density_by_type": rel_density,
    }

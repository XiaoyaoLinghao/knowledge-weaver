#!/usr/bin/env python3
"""Clean noise data from the knowledge base and recalculate entity importance scores.

Steps:
  A — Remove noise entities (timestamps, common tech words, structural markers)
      along with their relations and vectors.
  B — Remove weak RELATES_TO relations with DMA-category evidence.
  C — Full rescore of all remaining entities via score_entity().
  D — Print before/after statistics.
"""

import argparse
import json
import os
import sys
import sqlite3
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from knowledge_weaver.scorer import score_entity
from knowledge_weaver.extractor import (
    _TIMESTAMP_LOG_RE,
    _TECH_COMMON_WORDS,
    _STRUCTURAL_TECH_RE,
)

# DMA category names used as evidence — these are section headers, not real signals
WEAK_EVIDENCE = [
    "核心要点",
    "决策与结论",
    "已完成事项",
    "待办与计划",
    "用户偏好与习惯",
    "技术/项目要点",
    "风险与注意事项",
    "创意与想法",
]


def get_stats(conn: sqlite3.Connection) -> dict:
    """Collect current database statistics."""
    total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    by_type = dict(
        conn.execute("SELECT type, COUNT(*) FROM entities GROUP BY type").fetchall()
    )
    relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    vectors = conn.execute("SELECT COUNT(*) FROM entity_vectors").fetchone()[0]
    return {
        "total_entities": total,
        "by_type": by_type,
        "total_relations": relations,
        "total_vectors": vectors,
    }


def print_stats(label: str, s: dict) -> None:
    """Print formatted statistics."""
    print(f"\n{label}:")
    print(f"  Total entities:   {s['total_entities']}")
    for etype in sorted(s["by_type"]):
        print(f"    {etype}: {s['by_type'][etype]}")
    print(f"  Total relations:   {s['total_relations']}")
    print(f"  Total vectors:     {s['total_vectors']}")


# ---------------------------------------------------------------------------
# Step A — Collect and delete noise entities
# ---------------------------------------------------------------------------


def collect_noise_ids(conn: sqlite3.Connection) -> set[str]:
    """Collect IDs of noise entities to remove.

    A1 — Timestamp-log fact entities (Python-side regex).
    A2 — Common tech words (parameterized SQL IN query).
    A3 — Structural marker tech entities (Python-side regex).
    """
    noise: set[str] = set()

    # A1 — timestamp facts
    for row in conn.execute("SELECT id, name FROM entities WHERE type = 'fact'"):
        if _TIMESTAMP_LOG_RE.match(row["name"]):
            noise.add(row["id"])

    # A2 — common tech words
    if _TECH_COMMON_WORDS:
        ph = ",".join(["?"] * len(_TECH_COMMON_WORDS))
        upper_words = tuple(w.upper() for w in _TECH_COMMON_WORDS)
        for row in conn.execute(
            f"SELECT id FROM entities WHERE type = 'tech' AND UPPER(name) IN ({ph})",
            upper_words,
        ):
            noise.add(row["id"])

    # A3 — structural marker tech entities
    for row in conn.execute("SELECT id, name FROM entities WHERE type = 'tech'"):
        if _STRUCTURAL_TECH_RE.match(row["name"]):
            noise.add(row["id"])

    return noise


def delete_noise(conn: sqlite3.Connection, noise_ids: set[str]) -> tuple[int, int]:
    """Delete noise entities, relations, and vectors in the correct order.

    SQLite PRAGMA foreign_keys defaults to OFF, so we must delete manually:
      1. relations referencing noise entities
      2. entity_vectors for noise entities
      3. the noise entities themselves
    """
    if not noise_ids:
        return 0, 0

    ph = ",".join(["?"] * len(noise_ids))
    ids = tuple(noise_ids)

    # 1 — relations
    cur = conn.execute(
        f"DELETE FROM relations WHERE from_entity IN ({ph}) OR to_entity IN ({ph})",
        ids + ids,
    )
    rels_deleted = cur.rowcount

    # 2 — entity_vectors
    conn.execute(f"DELETE FROM entity_vectors WHERE entity_id IN ({ph})", ids)

    # 3 — entities
    cur = conn.execute(f"DELETE FROM entities WHERE id IN ({ph})", ids)
    ents_deleted = cur.rowcount

    conn.commit()
    return ents_deleted, rels_deleted


# ---------------------------------------------------------------------------
# Step B — Clean weak RELATES_TO relations
# ---------------------------------------------------------------------------


def clean_weak_relations(conn: sqlite3.Connection) -> int:
    """Remove RELATES_TO relations whose evidence is a DMA category name."""
    if not WEAK_EVIDENCE:
        return 0
    ph = ",".join(["?"] * len(WEAK_EVIDENCE))
    cur = conn.execute(
        f"DELETE FROM relations WHERE rel_type = 'RELATES_TO' AND evidence IN ({ph})",
        tuple(WEAK_EVIDENCE),
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Step C — Full rescore of remaining entities
# ---------------------------------------------------------------------------


def rescore_all(conn: sqlite3.Connection) -> int:
    """Recalculate importance for every remaining entity using score_entity()."""
    access_map: dict[str, int] = {}
    for row in conn.execute(
        "SELECT entity_id, COUNT(*) AS cnt FROM access_log GROUP BY entity_id"
    ):
        access_map[row["entity_id"]] = row["cnt"]

    today = date.today()
    rows = conn.execute("SELECT * FROM entities").fetchall()
    updated = 0

    for row in rows:
        entity = dict(row)
        # Populate fields that score_entity expects from metadata JSON
        try:
            meta = json.loads(entity.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            meta = {}
        entity["tags"] = meta.get("tags", [])
        entity["distinct_categories"] = meta.get("distinct_categories", 0)

        new_score = score_entity(
            entity, access_count=access_map.get(entity["id"], 0), today=today
        )
        conn.execute(
            "UPDATE entities SET importance = ? WHERE id = ?",
            (new_score, entity["id"]),
        )
        updated += 1

    conn.commit()
    return updated


# ---------------------------------------------------------------------------
# Step D — Statistics & main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean noise data and rescore entity importance in the knowledge base."
    )
    parser.add_argument(
        "--db-path",
        default="/root/.openclaw/knowledge/knowledge.db",
        help="Path to the SQLite database (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without modifying the database",
    )
    args = parser.parse_args()

    if not os.path.exists(args.db_path):
        print(f"Database not found: {args.db_path}")
        sys.exit(1)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row

    # --- Stats before ---
    before = get_stats(conn)
    print_stats("Before cleaning", before)

    # --- Step A: identify noise ---
    noise_ids = collect_noise_ids(conn)
    print(f"\nNoise entities to remove: {len(noise_ids)}")
    if noise_ids:
        sample = list(noise_ids)[:10]
        ph = ",".join(["?"] * len(sample))
        names = conn.execute(
            f"SELECT name FROM entities WHERE id IN ({ph})", tuple(sample)
        ).fetchall()
        for n in names:
            print(f"  - {n['name']}")

    # --- Step B: count weak relations ---
    weak_count = 0
    if WEAK_EVIDENCE:
        ph = ",".join(["?"] * len(WEAK_EVIDENCE))
        weak_count = conn.execute(
            f"SELECT COUNT(*) FROM relations WHERE rel_type = 'RELATES_TO' AND evidence IN ({ph})",
            tuple(WEAK_EVIDENCE),
        ).fetchone()[0]
    print(f"Weak RELATES_TO relations to remove: {weak_count}")

    if args.dry_run:
        print("\n[Dry run — no changes made]")
        conn.close()
        return

    # --- Execute ---
    ents_a, rels_a = delete_noise(conn, noise_ids)
    print(f"\nStep A: Removed {ents_a} noise entities + {rels_a} related relations")

    rels_b = clean_weak_relations(conn)
    print(f"Step B: Removed {rels_b} weak RELATES_TO relations")

    n = rescore_all(conn)
    print(f"Step C: Rescored {n} entities")

    # --- Stats after ---
    after = get_stats(conn)
    print_stats("After cleaning", after)

    ents_diff = before["total_entities"] - after["total_entities"]
    rels_diff = before["total_relations"] - after["total_relations"]
    print(f"\nSummary: {ents_diff} entities and {rels_diff} relations removed")

    conn.close()


if __name__ == "__main__":
    main()

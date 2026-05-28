"""SQLite schema and CRUD operations for Knowledge Weaver."""

import json
import logging
import sqlite3
import struct
from typing import Optional

logger = logging.getLogger(__name__)

# fix: unified from embedder module — production vectors are 1024-dim
from knowledge_weaver.embedder import DEFAULT_DIMENSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    summary     TEXT NOT NULL,
    importance  REAL NOT NULL DEFAULT 0.0,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    day_count   INTEGER NOT NULL DEFAULT 1,
    source_lines TEXT NOT NULL DEFAULT '[]',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_importance ON entities(importance DESC);
CREATE INDEX IF NOT EXISTS idx_entities_last_seen ON entities(last_seen);
CREATE INDEX IF NOT EXISTS idx_entities_first_seen ON entities(first_seen);

CREATE TABLE IF NOT EXISTS relations (
    id          TEXT PRIMARY KEY,
    from_entity TEXT NOT NULL REFERENCES entities(id),
    to_entity   TEXT NOT NULL REFERENCES entities(id),
    rel_type    TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 0.5,
    evidence    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_entity);
CREATE INDEX IF NOT EXISTS idx_relations_to ON relations(to_entity);
CREATE INDEX IF NOT EXISTS idx_relations_from_to ON relations(from_entity, to_entity);
CREATE INDEX IF NOT EXISTS idx_relations_to_from ON relations(to_entity, from_entity);

CREATE TABLE IF NOT EXISTS daily_manifest (
    date        TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    file_hash   TEXT NOT NULL,
    entity_count INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ok'
);

CREATE TABLE IF NOT EXISTS access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id   TEXT NOT NULL REFERENCES entities(id),
    tool        TEXT NOT NULL,
    query       TEXT NOT NULL,
    accessed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_access_log_entity ON access_log(entity_id);
CREATE INDEX IF NOT EXISTS idx_access_log_time ON access_log(accessed_at);
"""

VECTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity_vectors (
    entity_id  TEXT PRIMARY KEY,
    embedding  TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
"""

# Per-connection sqlite-vec availability (keyed by id(conn), cleared on close)
_vec_loaded_conns: dict[int, bool] = {}


def _mark_vec_loaded(conn: sqlite3.Connection) -> None:
    _vec_loaded_conns[id(conn)] = True


def _is_vec_loaded(conn: sqlite3.Connection) -> bool:
    return _vec_loaded_conns.get(id(conn), False)


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize SQLite database with all tables and indexes. Returns connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()

    # Always create the plain-text fallback table
    conn.execute(VECTOR_SCHEMA)
    conn.commit()

    # Try to enable sqlite-vec extension and create virtual table
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        _init_vec_virtual_table(conn)
        _mark_vec_loaded(conn)
        _migrate_vectors_to_vec(conn)
    except Exception:
        # Check if entity_vec already exists (from a previous successful init)
        try:
            conn.execute("SELECT count(*) FROM entity_vec LIMIT 1")
            _mark_vec_loaded(conn)
        except Exception:
            logger.warning(
                "sqlite-vec extension not available; using Python-based vector search. "
                "Install sqlite-vec for native vector search support."
            )

    # Create FTS5 virtual table and rebuild index
    _init_fts_table(conn)

    return conn


def _init_vec_virtual_table(conn: sqlite3.Connection) -> None:
    """Create the vec0 virtual table for native vector search."""
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS entity_vec "
        f"USING vec0(entity_id TEXT PRIMARY KEY, embedding float[{DEFAULT_DIMENSION}])"
    )
    conn.commit()


def _migrate_vectors_to_vec(conn: sqlite3.Connection) -> None:
    """Migrate existing JSON vectors from entity_vectors to entity_vec virtual table."""
    # Check if entity_vec already has data
    count = conn.execute("SELECT count(*) FROM entity_vec").fetchone()[0]
    if count > 0:
        return
    # Migrate from plain-text table
    rows = conn.execute("SELECT entity_id, embedding FROM entity_vectors").fetchall()
    for row in rows:
        try:
            vec = json.loads(row[1])
            vec_bytes = struct.pack(f"{len(vec)}f", *vec)
            conn.execute(
                "INSERT OR REPLACE INTO entity_vec(entity_id, embedding) VALUES (?, ?)",
                (row[0], vec_bytes),
            )
        except (json.JSONDecodeError, struct.error):
            continue
    conn.commit()
    logger.info("Migrated %d vectors to sqlite-vec virtual table", len(rows))


def _init_fts_table(conn: sqlite3.Connection) -> None:
    """Create FTS5 virtual table and rebuild index from existing entities."""
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS entity_fts USING fts5("
        "entity_id UNINDEXED, name, summary, type, tokenize='unicode61')"
    )
    # Rebuild only if FTS table is empty
    count = conn.execute("SELECT count(*) FROM entity_fts").fetchone()[0]
    if count == 0:
        conn.execute(
            "INSERT INTO entity_fts(entity_id, name, summary, type) "
            "SELECT id, name, summary, type FROM entities"
        )
        conn.commit()
        logger.info("FTS5 index rebuilt from %d entities", count)


# --- Entity operations ---


def insert_entity(conn: sqlite3.Connection, entity: dict, auto_commit: bool = True) -> None:
    """Insert or UPSERT an entity record."""
    defaults = {
        "day_count": 1,
        "source_lines": "[]",
        "metadata": "{}",
    }
    e = {**defaults, **entity}
    conn.execute(
        """INSERT INTO entities (id, type, name, summary, importance, first_seen, last_seen,
           day_count, source_lines, metadata, updated_at)
           VALUES (:id, :type, :name, :summary, :importance, :first_seen, :last_seen,
           :day_count, :source_lines, :metadata, datetime('now'))
           ON CONFLICT(id) DO UPDATE SET
           type=excluded.type, name=excluded.name, summary=excluded.summary,
           importance=excluded.importance, last_seen=excluded.last_seen,
           day_count=excluded.day_count, source_lines=excluded.source_lines,
           metadata=excluded.metadata, updated_at=datetime('now')""",
        e,
    )
    # Sync to FTS5 index
    try:
        conn.execute("DELETE FROM entity_fts WHERE entity_id=?", (e["id"],))
        conn.execute(
            "INSERT INTO entity_fts(entity_id, name, summary, type) VALUES (?, ?, ?, ?)",
            (e["id"], e["name"], e["summary"], e["type"]),
        )
    except Exception:
        pass  # FTS table may not exist yet
    if auto_commit:
        conn.commit()


def get_entity(conn: sqlite3.Connection, entity_id: str) -> Optional[sqlite3.Row]:
    """Get entity by ID."""
    return conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()


def list_entities_by_type(conn: sqlite3.Connection, entity_type: str) -> list[sqlite3.Row]:
    """List entities of a given type, ordered by importance DESC."""
    return conn.execute(
        "SELECT * FROM entities WHERE type=? ORDER BY importance DESC",
        (entity_type,),
    ).fetchall()


def list_all_entities(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """List all entities ordered by importance DESC."""
    return conn.execute("SELECT * FROM entities ORDER BY importance DESC").fetchall()


def search_entities_fts(
    conn: sqlite3.Connection, query: str, limit: int = 10
) -> list[sqlite3.Row]:
    """Full-text search using FTS5, with LIKE fallback."""
    try:
        rows = conn.execute(
            """SELECT e.* FROM entity_fts f
               JOIN entities e ON f.entity_id = e.id
               WHERE entity_fts MATCH ?
               ORDER BY f.rank LIMIT ?""",
            (query, limit),
        ).fetchall()
        if rows:
            return rows
    except Exception:
        pass
    # Fallback to LIKE if FTS5 unavailable or no results
    return conn.execute(
        """SELECT * FROM entities WHERE name LIKE ? OR summary LIKE ?
           ORDER BY importance DESC LIMIT ?""",
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()


def delete_entity(conn: sqlite3.Connection, entity_id: str, auto_commit: bool = True) -> None:
    """Delete entity and its relations."""
    conn.execute("DELETE FROM relations WHERE from_entity=? OR to_entity=?", (entity_id, entity_id))
    conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))
    if auto_commit:
        conn.commit()


# --- Relation operations ---


def insert_relation(conn: sqlite3.Connection, rel: dict, auto_commit: bool = True) -> None:
    """Insert or REPLACE a relation. Self-loops are silently rejected."""
    if rel.get("from_entity") == rel.get("to_entity"):
        return
    conn.execute(
        """INSERT OR REPLACE INTO relations (id, from_entity, to_entity, rel_type, weight, evidence)
           VALUES (:id, :from_entity, :to_entity, :rel_type, :weight, :evidence)""",
        rel,
    )
    if auto_commit:
        conn.commit()


def get_relations_for_entity(
    conn: sqlite3.Connection, entity_id: str
) -> list[sqlite3.Row]:
    """Get all relations where entity appears as from_entity or to_entity."""
    return conn.execute(
        "SELECT * FROM relations WHERE from_entity=? OR to_entity=? ORDER BY weight DESC",
        (entity_id, entity_id),
    ).fetchall()


def get_relations_for_entities(
    conn: sqlite3.Connection, entity_ids: list[str]
) -> list[sqlite3.Row]:
    """Get all relations where any of the given entities appear as from or to."""
    if not entity_ids:
        return []
    placeholders = ",".join(["?" for _ in entity_ids])
    return conn.execute(
        f"SELECT * FROM relations WHERE from_entity IN ({placeholders}) OR to_entity IN ({placeholders}) ORDER BY weight DESC",
        entity_ids + entity_ids,
    ).fetchall()


def get_entities_by_ids(
    conn: sqlite3.Connection, entity_ids: list[str]
) -> dict[str, sqlite3.Row]:
    """Get multiple entities by ID. Returns {id: Row} dict."""
    if not entity_ids:
        return {}
    placeholders = ",".join(["?" for _ in entity_ids])
    rows = conn.execute(
        f"SELECT * FROM entities WHERE id IN ({placeholders})",
        entity_ids,
    ).fetchall()
    return {r["id"]: r for r in rows}


# --- daily_manifest operations ---


def get_manifest(conn: sqlite3.Connection, date: str) -> Optional[sqlite3.Row]:
    """Get manifest entry for a given date."""
    return conn.execute(
        "SELECT * FROM daily_manifest WHERE date=?", (date,)
    ).fetchone()


def upsert_manifest(conn: sqlite3.Connection, entry: dict, auto_commit: bool = True) -> None:
    """Insert or update a daily manifest entry."""
    conn.execute(
        """INSERT INTO daily_manifest (date, file_path, file_hash, entity_count, processed_at, status)
           VALUES (:date, :file_path, :file_hash, :entity_count, datetime('now'), :status)
           ON CONFLICT(date) DO UPDATE SET
           file_path=excluded.file_path, file_hash=excluded.file_hash,
           entity_count=excluded.entity_count, processed_at=datetime('now'),
           status=excluded.status""",
        entry,
    )
    if auto_commit:
        conn.commit()


def list_all_manifest(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """List all manifest entries ordered by date."""
    return conn.execute(
        "SELECT * FROM daily_manifest ORDER BY date"
    ).fetchall()


# --- access_log operations ---


def log_access(
    conn: sqlite3.Connection, entity_id: str, tool: str, query: str,
    auto_commit: bool = True,
) -> None:
    """Log an access event for an entity."""
    conn.execute(
        """INSERT INTO access_log (entity_id, tool, query)
           VALUES (?, ?, ?)""",
        (entity_id, tool, query),
    )
    if auto_commit:
        conn.commit()


def get_access_count(conn: sqlite3.Connection, entity_id: str) -> int:
    """Get the number of access events for an entity."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM access_log WHERE entity_id=?",
        (entity_id,),
    ).fetchone()
    return row["cnt"] if row else 0


def _can_use_vec(conn: sqlite3.Connection) -> bool:
    """Check if sqlite-vec virtual table is usable with this connection.

    Results are tracked per-connection — a connection that fails to load
    the extension will not retry, and a connection that succeeds will
    not need to re-load.
    """
    if _is_vec_loaded(conn):
        return True
    try:
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        conn.execute("SELECT count(*) FROM entity_vec LIMIT 1")
        _mark_vec_loaded(conn)
        return True
    except Exception:
        return False


def upsert_entity_vector(conn: sqlite3.Connection, entity_id: str, embedding: list[float], auto_commit: bool = True) -> None:
    """Store embedding in both plain-text table and sqlite-vec virtual table (if available)."""
    # Always write to plain-text fallback
    conn.execute(
        "INSERT OR REPLACE INTO entity_vectors(entity_id, embedding) VALUES (?, ?)",
        (entity_id, json.dumps(embedding)),
    )
    # Also write to sqlite-vec virtual table if available
    if _can_use_vec(conn):
        try:
            vec_bytes = struct.pack(f"{len(embedding)}f", *embedding)
            conn.execute(
                "INSERT OR REPLACE INTO entity_vec(entity_id, embedding) VALUES (?, ?)",
                (entity_id, vec_bytes),
            )
        except Exception:
            logger.debug("Failed to insert into entity_vec virtual table")
    if auto_commit:
        conn.commit()


def _cosine(vec_a: list[float], vec_b: list[float]) -> float:
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search_entity_vectors(conn: sqlite3.Connection, query_vec: list[float],
                          limit: int = 10) -> list[sqlite3.Row]:
    """Search entities by cosine similarity.

    Uses sqlite-vec virtual table when available (O(log N)),
    falls back to Python O(N) scan otherwise.
    """
    if _can_use_vec(conn):
        try:
            return _search_entity_vectors_vec(conn, query_vec, limit)
        except Exception:
            logger.debug("sqlite-vec search failed, falling back to Python scan")

    return _search_entity_vectors_python(conn, query_vec, limit)


def _search_entity_vectors_vec(conn: sqlite3.Connection, query_vec: list[float],
                                limit: int = 10) -> list[sqlite3.Row]:
    """Native vector search using sqlite-vec virtual table."""
    vec_bytes = struct.pack(f"{len(query_vec)}f", *query_vec)
    rows = conn.execute(
        "SELECT entity_id, distance FROM entity_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (vec_bytes, limit),
    ).fetchall()
    top_ids = [r[0] for r in rows]
    if not top_ids:
        return []
    placeholders = ",".join(["?" for _ in top_ids])
    return conn.execute(
        f"SELECT * FROM entities WHERE id IN ({placeholders})",
        top_ids,
    ).fetchall()


def _search_entity_vectors_python(conn: sqlite3.Connection, query_vec: list[float],
                                   limit: int = 10) -> list[sqlite3.Row]:
    """Fallback: Python O(N) cosine similarity scan."""
    rows = conn.execute(
        "SELECT v.entity_id, v.embedding FROM entity_vectors v"
    ).fetchall()
    scored = []
    for r in rows:
        try:
            vec = json.loads(r[1])
            sim = _cosine(query_vec, vec)
            scored.append((sim, r[0]))
        except (json.JSONDecodeError, TypeError, IndexError):
            pass
    scored.sort(key=lambda x: x[0], reverse=True)
    top_ids = [eid for _, eid in scored[:limit]]
    if not top_ids:
        return []
    placeholders = ",".join(["?" for _ in top_ids])
    return conn.execute(
        f"SELECT * FROM entities WHERE id IN ({placeholders})",
        top_ids,
    ).fetchall()

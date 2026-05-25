"""SQLite schema and CRUD operations for Knowledge Weaver."""

import json
import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

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
CREATE VIRTUAL TABLE IF NOT EXISTS entity_vectors USING vec0(
    entity_id  TEXT PRIMARY KEY,
    embedding  FLOAT[768]
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize SQLite database with all tables and indexes. Returns connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()

    # Best-effort sqlite-vec virtual table creation
    try:
        _init_vector_table(conn)
    except Exception:
        logger.warning(
            "sqlite-vec extension not available; vector table creation skipped. "
            "Embedding-based search will be unavailable."
        )

    return conn


def _init_vector_table(conn: sqlite3.Connection) -> None:
    """Try to load sqlite-vec and create the virtual table."""
    try:
        conn.execute("SELECT vec_version()")
    except Exception:
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
    conn.execute(VECTOR_SCHEMA)
    conn.commit()


# --- Entity operations ---


def insert_entity(conn: sqlite3.Connection, entity: dict) -> None:
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
    """Simple LIKE-based search on name and summary."""
    return conn.execute(
        """SELECT * FROM entities WHERE name LIKE ? OR summary LIKE ?
           ORDER BY importance DESC LIMIT ?""",
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()


def delete_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    """Delete entity and its relations."""
    conn.execute("DELETE FROM relations WHERE from_entity=? OR to_entity=?", (entity_id, entity_id))
    conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))
    conn.commit()


# --- Relation operations ---


def insert_relation(conn: sqlite3.Connection, rel: dict) -> None:
    """Insert or REPLACE a relation."""
    conn.execute(
        """INSERT OR REPLACE INTO relations (id, from_entity, to_entity, rel_type, weight, evidence)
           VALUES (:id, :from_entity, :to_entity, :rel_type, :weight, :evidence)""",
        rel,
    )
    conn.commit()


def get_relations_for_entity(
    conn: sqlite3.Connection, entity_id: str
) -> list[sqlite3.Row]:
    """Get all relations where entity appears as from_entity or to_entity."""
    return conn.execute(
        "SELECT * FROM relations WHERE from_entity=? OR to_entity=? ORDER BY weight DESC",
        (entity_id, entity_id),
    ).fetchall()


# --- daily_manifest operations ---


def get_manifest(conn: sqlite3.Connection, date: str) -> Optional[sqlite3.Row]:
    """Get manifest entry for a given date."""
    return conn.execute(
        "SELECT * FROM daily_manifest WHERE date=?", (date,)
    ).fetchone()


def upsert_manifest(conn: sqlite3.Connection, entry: dict) -> None:
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
    conn.commit()


def list_all_manifest(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """List all manifest entries ordered by date."""
    return conn.execute(
        "SELECT * FROM daily_manifest ORDER BY date"
    ).fetchall()


# --- access_log operations ---


def log_access(
    conn: sqlite3.Connection, entity_id: str, tool: str, query: str
) -> None:
    """Log an access event for an entity."""
    conn.execute(
        """INSERT INTO access_log (entity_id, tool, query)
           VALUES (?, ?, ?)""",
        (entity_id, tool, query),
    )
    conn.commit()


def get_access_count(conn: sqlite3.Connection, entity_id: str) -> int:
    """Get the number of access events for an entity."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM access_log WHERE entity_id=?",
        (entity_id,),
    ).fetchone()
    return row["cnt"] if row else 0

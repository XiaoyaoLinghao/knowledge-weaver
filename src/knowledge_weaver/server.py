"""Knowledge Weaver MCP Server entry point.

Creates a FastMCP server that registers 7 tools (6 knowledge + 1 consolidate),
reads environment variables for configuration, and supports CLI subcommands.

Usage:
    python -m knowledge_weaver.server           # serve via stdio (default)
    python -m knowledge_weaver.server serve      # explicit serve
    python -m knowledge_weaver.server consolidate  # run consolidation
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

DB_PATH = os.environ.get(
    "KNOWLEDGE_WEAVER_DB_PATH",
    "/root/.openclaw/knowledge/knowledge.db",
)
MEMORY_DIR = os.environ.get(
    "KNOWLEDGE_WEAVER_MEMORY_DIR",
    "/root/.openclaw/workspace/memory",
)
LOG_LEVEL = os.environ.get(
    "KNOWLEDGE_WEAVER_LOG_LEVEL",
    "INFO",
).upper()


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )


def _get_conn() -> sqlite3.Connection:
    """Get a fresh DB connection for tool calls."""
    from knowledge_weaver.db import init_db
    return init_db(DB_PATH)


def get_embedder():
    """Lazy embedder factory — returns EmbeddingClient or None."""
    from knowledge_weaver.embedder import get_embedder as _get
    return _get()


# ---------------------------------------------------------------------------
# MCP Server creation
# ---------------------------------------------------------------------------

def create_server() -> FastMCP:
    """Create and configure the MCP server with all 7 tools."""
    mcp = FastMCP("knowledge-weaver")

    # --- Tool 1: knowledge_search ---
    @mcp.tool()
    async def knowledge_search(
        query: str,
        entity_type: str = "",
        max_results: int = 10,
        min_score: float = 0.3,
    ) -> str:
        """Search knowledge entities semantically by query text.

        Args:
            query: The search query text.
            entity_type: Optional filter by entity type (project, decision, preference, etc).
            max_results: Maximum number of results to return (default 10).
            min_score: Minimum importance score threshold (default 0.3).
        """
        from knowledge_weaver.tools import knowledge_search as _search
        conn = _get_conn()
        try:
            result = _search(
                conn,
                query=query,
                entity_type=entity_type or None,
                max_results=max_results,
                min_score=min_score,
            )
        finally:
            conn.close()
        return json.dumps(result, ensure_ascii=False)

    # --- Tool 2: knowledge_trace ---
    @mcp.tool()
    async def knowledge_trace(topic: str, max_depth: int = 2) -> str:
        """Trace a topic's full timeline across all indexed days, including related entities and decisions.

        Args:
            topic: The topic to trace.
            max_depth: Maximum relation traversal depth (default 2).
        """
        from knowledge_weaver.tools import knowledge_trace as _trace
        conn = _get_conn()
        try:
            result = _trace(conn, topic=topic, max_depth=max_depth)
        finally:
            conn.close()
        return json.dumps(result, ensure_ascii=False)

    # --- Tool 3: active_projects ---
    @mcp.tool()
    async def active_projects(lookback_days: int = 14) -> str:
        """List currently active projects with status and open tasks.

        Args:
            lookback_days: How many days back to consider a project active (default 14).
        """
        from knowledge_weaver.tools import active_projects as _active
        conn = _get_conn()
        try:
            result = _active(conn, lookback_days=lookback_days)
        finally:
            conn.close()
        return json.dumps(result, ensure_ascii=False)

    # --- Tool 4: preference_lookup ---
    @mcp.tool()
    async def preference_lookup(topic: str = "", domain: str = "") -> str:
        """Look up user preferences and habits, optionally filtered by topic or domain.

        Args:
            topic: Optional topic filter.
            domain: Optional domain filter.
        """
        from knowledge_weaver.tools import preference_lookup as _lookup
        conn = _get_conn()
        try:
            result = _lookup(conn, topic=topic or None, domain=domain or None)
        finally:
            conn.close()
        return json.dumps(result, ensure_ascii=False)

    # --- Tool 5: decision_history ---
    @mcp.tool()
    async def decision_history(topic: str, include_risk: bool = True) -> str:
        """Query historical decisions and their rationale on a given topic.

        Args:
            topic: The topic to search decisions for.
            include_risk: Whether to include related risks (default True).
        """
        from knowledge_weaver.tools import decision_history as _history
        conn = _get_conn()
        try:
            result = _history(conn, topic=topic, include_risk=include_risk)
        finally:
            conn.close()
        return json.dumps(result, ensure_ascii=False)

    # --- Tool 6: knowledge_stats ---
    @mcp.tool()
    async def knowledge_stats() -> str:
        """System status overview: entity counts, indexed days, DB size."""
        from knowledge_weaver.tools import knowledge_stats as _stats
        conn = _get_conn()
        try:
            result = _stats(conn)
        finally:
            conn.close()
        return json.dumps(result, ensure_ascii=False)

    # --- Tool 7: knowledge_consolidate ---
    @mcp.tool()
    async def knowledge_consolidate() -> str:
        """Manually trigger knowledge consolidation from DMA daily memory files.

        Normally runs via cron. This tool allows on-demand consolidation.
        Requires EMBEDDING_BASE_URL and EMBEDDING_API_KEY to be configured for embeddings.
        """
        from knowledge_weaver.pipeline import run_consolidation
        embedder = get_embedder()
        if embedder is None:
            return json.dumps({
                "status": "error",
                "error": "Embedding not configured. Set EMBEDDING_BASE_URL, EMBEDDING_API_KEY, and EMBEDDING_MODEL.",
            }, ensure_ascii=False)

        result = run_consolidation(DB_PATH, MEMORY_DIR, embedder)
        return json.dumps({
            "status": result.status,
            "files_processed": result.files_processed,
            "files_skipped": result.files_skipped,
            "entities_created": result.entities_created,
            "entities_updated": result.entities_updated,
            "errors": result.errors,
        }, ensure_ascii=False)

    return mcp


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def run_consolidation_cli() -> int:
    """Run consolidation from the command line."""
    from knowledge_weaver.pipeline import run_consolidation as _run

    embedder = get_embedder()
    if embedder is None:
        print("WARNING: Embedding not configured. Set EMBEDDING_BASE_URL, EMBEDDING_API_KEY, and EMBEDDING_MODEL.")
        print("Running consolidation without embeddings (vector search will be unavailable).")

    result = _run(DB_PATH, MEMORY_DIR, embedder)
    print(f"Consolidation: {result.status}")
    print(f"  Files processed: {result.files_processed}")
    print(f"  Files skipped:   {result.files_skipped}")
    print(f"  Entities created: {result.entities_created}")
    print(f"  Entities updated: {result.entities_updated}")
    if result.errors:
        for err in result.errors:
            print(f"  ERROR: {err}")
    return 0 if result.status == 'ok' else 1


def main() -> int:
    """CLI entry point — dispatches to serve or consolidate."""
    _configure_logging()

    if len(sys.argv) > 1:
        subcommand = sys.argv[1]
        if subcommand == "consolidate":
            return run_consolidation_cli()
        elif subcommand == "serve":
            mcp = create_server()
            mcp.run()
            return 0
        else:
            print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
            print("Usage: python -m knowledge_weaver.server [serve|consolidate]", file=sys.stderr)
            return 1
    else:
        # Default: serve MCP via stdio
        mcp = create_server()
        mcp.run()
        return 0


if __name__ == "__main__":
    sys.exit(main())

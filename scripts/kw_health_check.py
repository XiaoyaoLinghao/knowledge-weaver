#!/usr/bin/env python3
"""
Knowledge Weaver Data Quality Health Check (每日 cron).

Performs read-only quality checks against the SQLite knowledge database
and emits a JSON report to health_reports/YYYY-MM-DD.json.

Usage:
    kw_health_check.py              # full run, write JSON report
    kw_health_check.py --dry-run    # print JSON to stdout only, no file write
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta

# ── Absolute paths ───────────────────────────────────────────────────────────

DB_PATH = "/home/openclaw/.openclaw/knowledge/knowledge.db"
REPORTS_DIR = "/home/openclaw/.openclaw/knowledge/health_reports"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# DMA health check paths (module-level for testability)
DMA_LOG_PATH = "/home/openclaw/.openclaw/logs/daily-memory-archiver.log"
DMA_CHECKPOINT_PATH = "/home/openclaw/.openclaw/skills/daily-memory-archiver/config/.archive_merge_checkpoint.json"
MEMORY_DIR = "/home/openclaw/.openclaw/workspace/memory/"

# Known entity types
KNOWN_TYPES = ("tech", "decision", "risk", "preference", "task", "idea", "fact", "project")


def ensure_dir(path: str) -> None:
    """Create directory if it doesn't exist."""
    os.makedirs(path, exist_ok=True)


def get_db_connection(readonly: bool = True) -> sqlite3.Connection:
    """Open a read-only connection to the knowledge DB. Raises on failure."""
    if not os.path.isfile(DB_PATH):
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    if readonly:
        # URI mode with ?mode=ro avoids accidental writes
        uri = f"file:{DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Check functions (each returns a dict) ────────────────────────────────────

def check_entity_total(cur: sqlite3.Cursor) -> dict:
    """Count total entities."""
    cur.execute("SELECT COUNT(*) AS cnt FROM entities")
    row = cur.fetchone()
    return {"total_entities": row["cnt"]}


def _severity_worse(a: str, b: str) -> str:
    """Return the worse (higher priority) of two severity levels."""
    order = {"ok": 0, "warning": 1, "alert": 2}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def check_dma_health() -> dict:
    """Check DMA (Daily Memory Archiver) health.

    Performs read-only checks on DMA log freshness, error counts,
    memory file output, and checkpoint freshness.
    """
    now = datetime.now()
    today_str = date.today().isoformat()
    status = "ok"
    messages: list[str] = []

    # ── 1. Log freshness ──
    log_freshness_hours: float | None = None
    if os.path.isfile(DMA_LOG_PATH):
        log_mtime = datetime.fromtimestamp(os.path.getmtime(DMA_LOG_PATH))
        log_freshness_hours = round((now - log_mtime).total_seconds() / 3600, 2)
        if log_freshness_hours > 24:
            status = _severity_worse(status, "alert")
            messages.append(
                f"DMA 日志超过 24 小时未更新（{log_freshness_hours:.1f}h），可能静默停摆"
            )
        elif log_freshness_hours > 12:
            status = _severity_worse(status, "warning")
            messages.append(
                f"DMA 日志超过 12 小时未更新（{log_freshness_hours:.1f}h），可能延迟"
            )
    else:
        log_freshness_hours = None
        status = _severity_worse(status, "alert")
        messages.append("DMA 日志文件不存在（DMA 可能未安装或已停摆）")

    # ── 2. ERROR/FATAL scan (past 24h) ──
    error_count_24h = 0
    cutoff = now - timedelta(days=1)
    error_pattern = re.compile(r"(ERROR|FATAL|错误|失败|无法)", re.IGNORECASE)
    if os.path.isfile(DMA_LOG_PATH):
        try:
            with open(DMA_LOG_PATH, "r") as f:
                in_old_block = False
                for line in f:
                    if line.startswith("[") and len(line) > 20:
                        try:
                            line_ts = datetime.strptime(
                                line[1:20], "%Y-%m-%d %H:%M:%S"
                            )
                            in_old_block = line_ts < cutoff
                        except ValueError:
                            pass
                    if in_old_block:
                        continue
                    if error_pattern.search(line):
                        error_count_24h += 1
        except OSError:
            pass

    if error_count_24h > 10:
        status = _severity_worse(status, "alert")
        messages.append(
            f"DMA 日志中近 24 小时出现 {error_count_24h} 条 ERROR/FATAL（持续故障）"
        )
    elif error_count_24h > 0:
        status = _severity_worse(status, "warning")
        messages.append(
            f"DMA 日志中近 24 小时出现 {error_count_24h} 条 ERROR/FATAL"
        )

    # ── 3. Checkpoint freshness (before memory check — used to qualify file-missing alerts) ──
    checkpoint_freshness_hours: float | None = None
    if os.path.isfile(DMA_CHECKPOINT_PATH):
        cp_mtime = datetime.fromtimestamp(os.path.getmtime(DMA_CHECKPOINT_PATH))
        checkpoint_freshness_hours = round(
            (now - cp_mtime).total_seconds() / 3600, 2
        )
        if checkpoint_freshness_hours > 48:
            status = _severity_worse(status, "alert")
            messages.append(
                f"DMA 检查点超过 48 小时未更新（{checkpoint_freshness_hours:.1f}h），检查点卡死"
            )
        elif checkpoint_freshness_hours > 24:
            status = _severity_worse(status, "warning")
            messages.append(
                f"DMA 检查点超过 24 小时未更新（{checkpoint_freshness_hours:.1f}h）"
            )
    else:
        checkpoint_freshness_hours = None

    # ── 4. Memory file output ──
    memory_file_exists = False
    memory_file_size = 0
    memory_path = os.path.join(MEMORY_DIR, f"{today_str}.md")
    if os.path.isfile(memory_path):
        memory_file_exists = True
        memory_file_size = os.path.getsize(memory_path)
        if memory_file_size < 100:
            status = _severity_worse(status, "warning")
            messages.append(
                f"今日 Memory 文件存在但异常小（{memory_file_size} bytes），产出可能为空"
            )
    elif (checkpoint_freshness_hours is not None
          and checkpoint_freshness_hours <= 24):
        # Checkpoint is fresh → DMA is running, just hasn't created today's file yet
        status = _severity_worse(status, "warning")
        messages.append(
            f"今日 Memory 文件尚未生成（{today_str}.md），但 DMA 检查点 {checkpoint_freshness_hours:.1f}h 前已更新，今日首次归档可能尚未触发"
        )
    elif checkpoint_freshness_hours is None:
        status = _severity_worse(status, "alert")
        messages.append(
            f"今日 Memory 文件缺失（{today_str}.md），且 DMA 检查点文件不存在，DMA 可能未安装"
        )
    else:
        status = _severity_worse(status, "alert")
        messages.append(
            f"今日 Memory 文件缺失（{today_str}.md），且 DMA 检查点已 {checkpoint_freshness_hours:.1f}h 未更新，归档可能停摆"
        )

    # ── Build message ──
    if not messages:
        message = "DMA 健康检查通过"
    else:
        message = "; ".join(messages)

    return {
        "log_freshness_hours": log_freshness_hours,
        "error_count_24h": error_count_24h,
        "memory_file_exists": memory_file_exists,
        "memory_file_size": memory_file_size,
        "checkpoint_freshness_hours": checkpoint_freshness_hours,
        "status": status,
        "message": message,
    }


def check_orphan_ratio(cur: sqlite3.Cursor) -> dict:
    """Proportion of entities with no relations at all."""
    cur.execute("""
        SELECT COUNT(*) AS total FROM entities
    """)
    total = cur.fetchone()["total"]
    if total == 0:
        return {"orphan_entities": 0, "orphan_ratio": 0.0, "status": "ok", "message": "No entities"}

    cur.execute("""
        SELECT COUNT(*) AS orphans
        FROM entities e
        WHERE NOT EXISTS (SELECT 1 FROM relations r WHERE r.from_entity = e.id)
          AND NOT EXISTS (SELECT 1 FROM relations r WHERE r.to_entity   = e.id)
    """)
    orphans = cur.fetchone()["orphans"]
    ratio = round(orphans / total, 4)
    status = "alert" if ratio > 0.40 else "ok"
    message = (f"Isolated entities above 40% threshold ({ratio:.1%})"
               if ratio > 0.40 else "Acceptable isolation level")
    return {"orphan_entities": orphans, "orphan_ratio": ratio,
            "status": status, "message": message}


def check_name_conflicts(cur: sqlite3.Cursor) -> dict:
    """Entities sharing the same name but different types."""
    cur.execute("""
        SELECT name, COUNT(DISTINCT type) AS type_count,
               GROUP_CONCAT(DISTINCT type) AS types
        FROM entities
        GROUP BY name
        HAVING COUNT(DISTINCT type) > 1
        ORDER BY type_count DESC
    """)
    conflicts = [
        {"name": r["name"], "type_count": r["type_count"], "types": r["types"]}
        for r in cur.fetchall()
    ]
    count = len(conflicts)
    status = "warning" if count > 5 else "ok"
    message = (f"Name-cross-type conflicts exceed threshold ({count} > 5)"
               if count > 5 else "Name conflicts within acceptable range")
    return {"conflict_count": count, "conflicts": conflicts,
            "status": status, "message": message}


def check_cross_day_aggregation(cur: sqlite3.Cursor) -> dict:
    """Cross-day aggregation quality — checks day_count=2 ratio."""
    cur.execute("SELECT COUNT(*) AS total FROM entities")
    total = cur.fetchone()["total"]
    if total == 0:
        return {"day2_entities": 0, "day2_ratio": 0.0, "status": "ok", "message": "No entities"}

    cur.execute("SELECT COUNT(*) AS cnt FROM entities WHERE day_count = 2")
    day2 = cur.fetchone()["cnt"]
    ratio = round(day2 / total, 4)
    status = "alert" if ratio > 0.50 else "ok"
    message = (f"聚合失效：{ratio:.1%} 实体未跨文件合并（day_count=2）"
               if ratio > 0.50 else "Cross-day aggregation acceptable")
    return {"day2_entities": day2, "day2_ratio": ratio,
            "status": status, "message": message}


def check_type_distribution(cur: sqlite3.Cursor) -> dict:
    """Count per type including an 'other' bucket for unrecognised types."""
    type_counts = {}
    for t in KNOWN_TYPES:
        cur.execute("SELECT COUNT(*) AS cnt FROM entities WHERE type = ?", (t,))
        type_counts[t] = cur.fetchone()["cnt"]

    # Catch any non-standard types
    placeholders = ",".join("?" * len(KNOWN_TYPES))
    cur.execute(
        f"SELECT COUNT(*) AS cnt FROM entities WHERE type NOT IN ({placeholders})",
        KNOWN_TYPES,
    )
    type_counts["other"] = cur.fetchone()["cnt"]

    # Also add day_count distribution for richer analysis
    cur.execute("""
        SELECT day_count, COUNT(*) AS cnt
        FROM entities
        GROUP BY day_count
        ORDER BY day_count
    """)
    day_dist = {str(r["day_count"]): r["cnt"] for r in cur.fetchall()}

    return {"type_distribution": type_counts, "day_count_distribution": day_dist}


def check_embedding_coverage(cur: sqlite3.Cursor) -> dict:
    """Check what proportion of entities have vector embeddings."""
    cur.execute("SELECT COUNT(*) AS total FROM entities")
    total = cur.fetchone()["total"]
    if total == 0:
        return {"total_entities": 0, "vector_count": 0, "coverage_ratio": 0.0,
                "status": "ok", "message": "No entities",
                "embedding_model": None, "dimension": None, "orphan_vectors": 0}

    cur.execute("SELECT COUNT(*) AS cnt FROM entity_vectors")
    vector_count = cur.fetchone()["cnt"]
    ratio = round(vector_count / total, 4)

    if ratio < 0.70:
        status = "alert"
    elif ratio < 0.90:
        status = "warning"
    else:
        status = "ok"

    message = (f"Embedding coverage critically low ({ratio:.1%})"
               if ratio < 0.70 else
               f"Embedding coverage below target ({ratio:.1%})"
               if ratio < 0.90 else
               f"Embedding coverage acceptable ({ratio:.1%})")

    # Read embedding metadata (model, dimension) — carried forward from old vector_dimensions
    cur.execute("SELECT key, value FROM embedding_meta")
    meta = {r["key"]: r["value"] for r in cur.fetchall()}

    # Sample first vector to infer dimension
    dimension = None
    cur.execute("SELECT embedding FROM entity_vectors LIMIT 1")
    row = cur.fetchone()
    if row:
        try:
            parsed = json.loads(row["embedding"])
            if isinstance(parsed, list):
                dimension = len(parsed)
        except (json.JSONDecodeError, TypeError):
            pass

    # Check orphan vectors (no matching entity)
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM entity_vectors ev
        WHERE NOT EXISTS (SELECT 1 FROM entities e WHERE e.id = ev.entity_id)
    """)
    orphan_vectors = cur.fetchone()["cnt"]

    return {
        "total_entities": total,
        "vector_count": vector_count,
        "coverage_ratio": ratio,
        "status": status,
        "message": message,
        "embedding_model": meta.get("model", "unknown"),
        "dimension": dimension,
        "orphan_vectors": orphan_vectors,
    }


def check_new_entity_inflow(cur: sqlite3.Cursor) -> dict:
    """Check whether new entities continue to flow into the knowledge base."""
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM entities
        WHERE first_seen >= date('now', '-7 days')
    """)
    new_count = cur.fetchone()["cnt"]

    if new_count == 0:
        status = "alert"
    elif new_count < 5:
        status = "warning"
    else:
        status = "ok"

    message = ("Ingestion pipeline appears stalled — 0 new entities in past 7 days"
               if new_count == 0 else
               f"Low entity inflow — only {new_count} new entities in past 7 days"
               if new_count < 5 else
               f"{new_count} new entities in past 7 days")

    # Last consolidation time (most recent daily_manifest entry)
    cur.execute("SELECT MAX(date) AS last_date FROM daily_manifest")
    row = cur.fetchone()
    last_consolidation = row["last_date"] if row and row["last_date"] else None

    return {
        "new_entities_7d": new_count,
        "status": status,
        "message": message,
        "last_consolidation": last_consolidation,
    }


def check_relation_integrity(cur: sqlite3.Cursor) -> dict:
    """Check referential integrity — dangling from_entity / to_entity references."""
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM relations r
        WHERE NOT EXISTS (SELECT 1 FROM entities e WHERE e.id = r.from_entity)
           OR NOT EXISTS (SELECT 1 FROM entities e WHERE e.id = r.to_entity)
    """)
    broken = cur.fetchone()["cnt"]

    if broken > 10:
        status = "alert"
    elif broken > 0:
        status = "warning"
    else:
        status = "ok"

    message = (f"Critically broken relations ({broken} dangling references)"
               if broken > 10 else
               f"Some broken relations detected ({broken} dangling references)"
               if broken > 0 else
               "All relations have valid entity references")

    return {
        "broken_relations": broken,
        "status": status,
        "message": message,
    }


def check_pending_merges(cur: sqlite3.Cursor) -> dict:
    """Count pending merge reviews."""
    cur.execute("SELECT COUNT(*) AS cnt FROM merge_review WHERE status = 'pending'")
    pending = cur.fetchone()["cnt"]

    # Also get breakdown by kind
    cur.execute("""
        SELECT kind, COUNT(*) AS cnt
        FROM merge_review
        WHERE status = 'pending'
        GROUP BY kind
    """)
    by_kind = {r["kind"]: r["cnt"] for r in cur.fetchall()}

    return {"pending_merges": pending, "pending_by_kind": by_kind}


def check_coverage_days(cur: sqlite3.Cursor) -> dict:
    """Knowledge coverage from daily_manifest."""
    cur.execute("SELECT COUNT(DISTINCT date) AS cnt FROM daily_manifest")
    days = cur.fetchone()["cnt"]

    # Earliest and latest covered dates
    cur.execute("SELECT MIN(date) AS first, MAX(date) AS last FROM daily_manifest")
    row = cur.fetchone()
    return {
        "coverage_days": days,
        "first_coverage": row["first"],
        "last_coverage": row["last"],
    }


def load_previous_report() -> dict | None:
    """Load yesterday's JSON report for trend comparison."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    path = os.path.join(REPORTS_DIR, f"{yesterday}.json")
    if os.path.isfile(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def compare_with_previous(current_total: int, prev: dict | None) -> dict:
    """Calculate entity count trend vs previous report."""
    if prev is None or "checks" not in prev:
        return {"trend": "N/A", "previous_total": None, "delta": 0, "delta_pct": 0.0}

    prev_total = prev.get("checks", {}).get("entity_total", {}).get("total_entities")
    if prev_total is None:
        return {"trend": "N/A", "previous_total": None, "delta": 0, "delta_pct": 0.0}

    delta = current_total - prev_total
    pct = round(delta / prev_total * 100, 2) if prev_total else 0.0
    trend = "stable"
    if pct > 10:
        trend = "growing"
    elif pct < -10:
        trend = "shrinking"

    return {"trend": trend, "previous_total": prev_total, "delta": delta, "delta_pct": pct}


# ── Main orchestrator ────────────────────────────────────────────────────────

def run_health_check(dry_run: bool = False) -> str:
    """Execute all checks and return the JSON report string.

    Returns the JSON string.  Writes to file unless dry_run is True.
    Exits with code 1 on critical failure (DB unreadable).
    """
    # Validate / create directories
    ensure_dir(REPORTS_DIR)

    try:
        conn = get_db_connection(readonly=True)
    except FileNotFoundError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as e:
        print(f"FATAL: Cannot open database: {e}", file=sys.stderr)
        sys.exit(1)

    cur = conn.cursor()
    today = date.today().isoformat()
    run_at = datetime.now().isoformat(timespec="seconds")

    checks: dict[str, dict] = {}

    # 1. Entity total
    try:
        checks["entity_total"] = check_entity_total(cur)
    except sqlite3.Error as e:
        checks["entity_total"] = {"error": str(e)}

    total = checks.get("entity_total", {}).get("total_entities", 0)

    # 2. DMA health
    try:
        checks["dma_health"] = check_dma_health()
    except Exception as e:
        checks["dma_health"] = {"error": str(e)}

    # 3. New entity inflow
    try:
        checks["new_entity_inflow"] = check_new_entity_inflow(cur)
    except sqlite3.Error as e:
        checks["new_entity_inflow"] = {"error": str(e)}

    # 4. Embedding coverage
    try:
        checks["embedding_coverage"] = check_embedding_coverage(cur)
    except sqlite3.Error as e:
        checks["embedding_coverage"] = {"error": str(e)}

    # 5. Orphan ratio
    try:
        checks["orphan_ratio"] = check_orphan_ratio(cur)
    except sqlite3.Error as e:
        checks["orphan_ratio"] = {"error": str(e)}

    # 6. Cross-day aggregation
    try:
        checks["cross_day_aggregation"] = check_cross_day_aggregation(cur)
    except sqlite3.Error as e:
        checks["cross_day_aggregation"] = {"error": str(e)}

    # 7. Type distribution
    try:
        checks["type_distribution"] = check_type_distribution(cur)
    except sqlite3.Error as e:
        checks["type_distribution"] = {"error": str(e)}

    # 8. Name conflicts
    try:
        checks["name_conflicts"] = check_name_conflicts(cur)
    except sqlite3.Error as e:
        checks["name_conflicts"] = {"error": str(e)}

    # 9. Relation integrity
    try:
        checks["relation_integrity"] = check_relation_integrity(cur)
    except sqlite3.Error as e:
        checks["relation_integrity"] = {"error": str(e)}

    # 10. Pending merges
    try:
        checks["pending_merges"] = check_pending_merges(cur)
    except sqlite3.Error as e:
        checks["pending_merges"] = {"error": str(e)}

    # 11. Coverage days
    try:
        checks["coverage_days"] = check_coverage_days(cur)
    except sqlite3.Error as e:
        checks["coverage_days"] = {"error": str(e)}

    conn.close()

    # ── Trend vs previous report ──
    prev = load_previous_report()
    trend = compare_with_previous(total, prev)

    # ── Overall status ──
    # Priority: alert > warning > ok
    overall = "ok"
    severity = {"ok": 0, "warning": 1, "alert": 2}
    alert_checks = [
        "dma_health", "new_entity_inflow", "embedding_coverage", "orphan_ratio",
        "cross_day_aggregation", "name_conflicts", "relation_integrity",
    ]
    for check_key in alert_checks:
        s = checks.get(check_key, {}).get("status", "ok")
        if severity.get(s, 0) > severity.get(overall, 0):
            overall = s

    report = {
        "report_date": today,
        "generated_at": run_at,
        "overall_status": overall,
        "checks": checks,
        "trend": trend,
        "thresholds_evaluated": {
            "dma_health_log_stale > 24h": checks.get("dma_health", {}).get("log_freshness_hours", 0) is not None
                and (checks.get("dma_health", {}).get("log_freshness_hours", 0) or 0) > 24,
            "dma_health_errors > 10": checks.get("dma_health", {}).get("error_count_24h", 0) > 10,
            "dma_health_memory_missing": not checks.get("dma_health", {}).get("memory_file_exists", True),
            "dma_health_checkpoint_stale > 48h": checks.get("dma_health", {}).get("checkpoint_freshness_hours", 0) is not None
                and (checks.get("dma_health", {}).get("checkpoint_freshness_hours", 0) or 0) > 48,
            "new_entity_inflow == 0": checks.get("new_entity_inflow", {}).get("status") == "alert",
            "embedding_coverage < 70%": checks.get("embedding_coverage", {}).get("status") == "alert",
            "orphan_ratio > 40%": checks.get("orphan_ratio", {}).get("status") == "alert",
            "cross_day_day2 > 50%": checks.get("cross_day_aggregation", {}).get("status") == "alert",
            "name_conflicts > 5": checks.get("name_conflicts", {}).get("status") == "warning",
            "broken_relations > 0": checks.get("relation_integrity", {}).get("status") in ("warning", "alert"),
            "entity_delta > ±10%": trend.get("trend", "N/A") in ("growing", "shrinking"),
        },
    }

    json_text = json.dumps(report, ensure_ascii=False, indent=2)

    if dry_run:
        print(json_text)
    else:
        report_path = os.path.join(REPORTS_DIR, f"{today}.json")
        with open(report_path, "w") as f:
            f.write(json_text)
            f.write("\n")
        print(f"Report written to {report_path}")

    return json_text


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Weaver Health Check")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON to stdout only; do not write report file.",
    )
    args = parser.parse_args()
    run_health_check(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

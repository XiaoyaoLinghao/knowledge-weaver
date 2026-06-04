"""Recover real entities lost in a historical rebuild by porting them from the
pre-swap DB.

Real-data finding (2026-06-04): the backfill rebuild dropped 51 decisions
(100% real knowledge, 0 noise) and ~42 meaningful tech terms. Those entities
still exist — cleanly, WITH their accumulated day_count/first_seen — in the
pre-swap DB. Re-extracting them from compressed summaries is strictly lossier
than porting the existing rows. Porting also carries the stored bge-m3 vector,
so no re-embedding is needed (same model/dim).

An entity is "missing" iff its (deterministic) id is absent from the new DB.
Decisions are 0% noise -> port all. Tech is ~80% noise -> gate behind a keep_fn
(rule or LLM keep/drop). After porting, run semantic_dedupe (catch near-dups vs
existing new entities) then build_typed_relations + rebuild_fts.
"""
from __future__ import annotations

import json
import re

from knowledge_weaver.db import get_entity_vector, insert_entity, upsert_entity_vector

# Columns carried over verbatim (incl. accumulated day_count + alias metadata).
PORTABLE_FIELDS = (
    "id", "type", "name", "summary", "importance",
    "first_seen", "last_seen", "day_count", "source_lines", "metadata",
)


def missing_entities(old_conn, new_conn, types: list[str]) -> list[dict]:
    """Old entities of the given types whose id is absent from the new DB."""
    new_ids = {r[0] for r in new_conn.execute("SELECT id FROM entities").fetchall()}
    ph = ",".join("?" * len(types))
    rows = old_conn.execute(
        f"SELECT * FROM entities WHERE type IN ({ph})", tuple(types)
    ).fetchall()
    return [dict(r) for r in rows if r["id"] not in new_ids]


def port_entities(old_conn, new_conn, rows: list[dict], *, keep_fn=None,
                  dry_run: bool = False) -> dict:
    """Insert each (kept) old row into the new DB, carrying its stored vector."""
    ported = skipped = with_vector = 0
    for e in rows:
        if keep_fn is not None and not keep_fn(e):
            skipped += 1
            continue
        vec = get_entity_vector(old_conn, e["id"])
        if vec:
            with_vector += 1
        if not dry_run:
            insert_entity(new_conn, {k: e.get(k) for k in PORTABLE_FIELDS},
                          auto_commit=False)
            if vec:
                upsert_entity_vector(new_conn, e["id"], vec, auto_commit=False)
        ported += 1
    if not dry_run:
        new_conn.commit()
    return {"candidates": len(rows), "ported": ported,
            "skipped": skipped, "with_vector": with_vector}


# A coarse rule for obvious tech noise (≤4-char all-caps abbrevs, bare filenames).
# Deliberately conservative: when unsure, KEEP and let semantic_dedupe / review
# sort it out. For real precision on tech, prefer the LLM keep filter below.
# Alphabetic extension (.json/.py) = filename noise; numeric (.1, glm-5.1) = a
# version and KEPT. That distinction is why the extension must be [A-Za-z].
_OBVIOUS_NOISE_RE = re.compile(r"^[A-Z]{1,4}$|^[\w-]+\.[A-Za-z]{1,5}$")


def rule_keep_tech(entity: dict) -> bool:
    name = (entity.get("name") or "").strip()
    if len(name) <= 2:
        return False
    return not _OBVIOUS_NOISE_RE.match(name)


def _has_relation(conn, entity_id: str) -> bool:
    """True if the entity is connected by ≥1 edge in the (old) DB."""
    return conn.execute(
        "SELECT 1 FROM relations WHERE from_entity=? OR to_entity=? LIMIT 1",
        (entity_id, entity_id),
    ).fetchone() is not None


def signal_keep_tech(entity: dict, old_conn, *, min_day_count: int = 2) -> bool:
    """Signal-gate for tech recovery (PRIMARY filter).

    A tech entity is worth keeping only if it left real accumulated signal in the
    old (incrementally-built) DB: it recurred (``day_count >= min_day_count``) OR
    it is connected to something (≥1 relation). A one-off, isolated mention
    (day_count=1, no edges) is dropped as low-value noise — the decision/context
    that referenced it is what carries meaning, and that is recovered separately.

    Name-pattern noise (short abbrev / bare filename) is excluded first via
    ``rule_keep_tech``. This gate adds NO new name patterns — it reads the signal
    already in the data, consistent with the "stop hand-rolling rules" direction.
    """
    if not rule_keep_tech(entity):
        return False
    if (entity.get("day_count") or 0) >= min_day_count:
        return True
    return _has_relation(old_conn, entity["id"])


def llm_keep_filter(rows: list[dict], *, api_url: str, api_key: str, model: str,
                    chunk: int = 25, timeout: float = 90.0) -> set[str]:
    """Return the set of entity ids an LLM judges to be MEANINGFUL (keep).

    Isolated for mockability; the DB porting stays deterministic.
    """
    import httpx

    url = api_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    sys_prompt = (
        "你在清洗一个技术知识库。给你若干候选技术实体（name + 摘要）。"
        "判断每一个是否是【有意义的具体技术实体】（如芯片型号 PCA9685、模型名 glm-5.1、"
        "commit hash、具体库/框架/协议名），还是【噪声】（无意义短缩写、泛化词、裸文件名、"
        "拼写碎片）。只对有意义的回 keep，噪声回 drop。"
        '只输出一个 JSON 对象 {"1":"keep","2":"drop",...}，不要任何其它文字。'
    )
    keep: set[str] = set()
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        lines = [f'[{j+1}] {e.get("name")} — {(e.get("summary") or "")[:80]}'
                 for j, e in enumerate(batch)]
        try:
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model, "temperature": 0.1, "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": "候选：\n" + "\n".join(lines)},
                ]},
                timeout=timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
            mapping = json.loads(content)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! keep-filter chunk {i // chunk + 1} failed: {exc}; keeping all in chunk")
            keep.update(e["id"] for e in batch)
            continue
        for j, e in enumerate(batch):
            v = mapping.get(str(j + 1)) or mapping.get(j + 1)
            if isinstance(v, str) and v.strip().lower() == "keep":
                keep.add(e["id"])
    return keep

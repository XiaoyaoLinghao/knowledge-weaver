"""W3.4 core: re-type generic RELATES_TO edges into the typed knowledge-graph
vocabulary (relations_schema.REL_TYPES) via an injected typer, or prune them.

Lives in the package (not just the script) so both the one-off rebuild
(scripts/build_typed_relations.py) AND online consolidation (server CLI) can
type new edges. The LLM call (``llm_type_pairs``) is isolated so the DB surgery
(``type_relations``) is deterministically testable with a mock.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from knowledge_weaver.db import get_entity, init_db, insert_relation
from knowledge_weaver.linker import generate_relation_id
from knowledge_weaver.relations_schema import (
    REL_TYPES,
    schema_prompt,
    triplet_hints_prompt,
)


def candidates(conn) -> list[dict]:
    """Generic RELATES_TO edges joined with both endpoints' type/name/summary."""
    rows = conn.execute(
        "SELECT id, from_entity, to_entity, weight, evidence FROM relations "
        "WHERE rel_type='RELATES_TO'"
    ).fetchall()
    out = []
    for r in rows:
        a, b = get_entity(conn, r["from_entity"]), get_entity(conn, r["to_entity"])
        if a is None or b is None:
            continue
        out.append({
            "rel_id": r["id"], "from_id": r["from_entity"], "to_id": r["to_entity"],
            "weight": r["weight"], "evidence": r["evidence"],
            "from": f"{a['type']}: {a['name']} — {(a['summary'] or '')[:150]}",
            "to": f"{b['type']}: {b['name']} — {(b['summary'] or '')[:150]}",
        })
    return out


def type_relations(db_path: str, type_fn, *, dry_run: bool = False) -> dict:
    """Re-type RELATES_TO edges via ``type_fn(cands) -> {rel_id: type|none}``.

    Replaces each edge with a typed edge (closed vocab) or prunes it (none)."""
    conn = init_db(db_path)
    cands = candidates(conn)
    decisions = type_fn(cands) if cands else {}

    typed = pruned = 0
    type_counts: Counter = Counter()
    for c in cands:
        t = decisions.get(c["rel_id"])
        if t not in REL_TYPES:  # none / invalid -> prune spurious edge
            pruned += 1
            if not dry_run:
                conn.execute("DELETE FROM relations WHERE id=?", (c["rel_id"],))
            continue
        typed += 1
        type_counts[t] += 1
        if not dry_run:
            conn.execute("DELETE FROM relations WHERE id=?", (c["rel_id"],))
            insert_relation(conn, {
                "id": generate_relation_id(c["from_id"], c["to_id"], t),
                "from_entity": c["from_id"], "to_entity": c["to_id"],
                "rel_type": t, "weight": c["weight"], "evidence": "llm-typed",
            }, auto_commit=False)
    if not dry_run:
        conn.commit()
    remaining = conn.execute(
        "SELECT rel_type, COUNT(*) c FROM relations GROUP BY rel_type").fetchall()
    conn.close()
    return {
        "candidates": len(cands), "typed": typed, "pruned": pruned,
        "by_type": dict(type_counts),
        "relations_after": {r["rel_type"]: r["c"] for r in remaining},
    }


def build_typing_system_prompt() -> str:
    """Tightened typing prompt: schema + triplet hints + rules (prefer specific
    types; 导致 only for true causation; prune coincidental; 相关 last resort) +
    few-shot. Pulled out so it is testable without an LLM call."""
    return (
        "你是知识图谱关系判定助手。给你若干有方向的实体对（A→B），"
        "请判断每一对中 A 与 B 之间最贴切的关系类型。\n\n"
        + schema_prompt()
        + "\n\n常见组合参考（优先据此往具体类型判，非硬性约束）：\n"
        + triplet_hints_prompt()
        + "\n\n判定规则：\n"
        "1. 优先选具体类型（依赖/使用/包含/导致/取代/关于）；只有确实找不到更具体关系、"
        "且两者确有实质关联时，才用「相关」。\n"
        "2. 「导致」仅用于真正的因果（A 是 B 发生的原因）；先后顺序或伴随出现【不算因果】，"
        "应判其它类型或 none。\n"
        "3. 若两者只是碰巧在同一段对话里出现、彼此没有实质关系 → 输出 none（剪掉），"
        "不要塞进「相关」。\n"
        "4. 注意方向 A→B，按内容判最贴切的类型。\n\n"
        "示例：\n"
        "  A(project: HomeBrain — 智能家庭中枢) → B(tech: FastAPI — 后端框架) ⇒ 使用\n"
        "  A(decision: 改用规则引擎 — 弃用LLM聚合) → B(task: 重构聚合层) ⇒ 导致\n"
        "  A(project: KW — 知识抽取) → B(tech: sqlite-vec — 向量库) ⇒ 依赖\n"
        "  A(fact: 今天天气好) → B(decision: 后端用FastAPI) ⇒ none\n\n"
        "对每一对，按其序号输出关系类型或 none；只输出一个 JSON 对象，"
        '形如 {"1": "使用", "2": "none", ...}，不要任何其它文字。'
    )


def llm_type_pairs(cands: list[dict], *, api_url: str, api_key: str, model: str,
                   chunk: int = 20, timeout: float = 90.0) -> dict:
    """Type candidate edges via an OpenAI-compatible chat model (chunked)."""
    import httpx

    url = api_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    sys_prompt = build_typing_system_prompt()
    out: dict = {}
    for i in range(0, len(cands), chunk):
        batch = cands[i:i + chunk]
        lines = [f'[{j + 1}] A({c["from"]})  →  B({c["to"]})' for j, c in enumerate(batch)]
        try:
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model, "temperature": 0.1, "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": "实体对：\n" + "\n".join(lines)},
                ]},
                timeout=timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
            mapping = json.loads(content)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! chunk {i // chunk + 1} failed: {exc}; leaving as RELATES_TO")
            continue
        for j, c in enumerate(batch):
            t = mapping.get(str(j + 1)) or mapping.get(j + 1)
            if isinstance(t, str):
                out[c["rel_id"]] = t.strip()
    return out

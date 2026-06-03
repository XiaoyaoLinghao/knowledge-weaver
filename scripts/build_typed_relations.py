#!/usr/bin/env python3
"""W3.4: upgrade KW's generic co-occurrence edges into a typed knowledge graph.

KW currently has ~hundreds of generic ``RELATES_TO`` edges (entities that merely
co-occurred in a file). This re-types each such candidate edge into a meaningful
directed relation from a CLOSED vocabulary (relations_schema.REL_TYPES) via the
LLM, or prunes it (``none``) when there is no real relation — turning the
"association graph" into a "knowledge graph" and removing spurious edges.

The LLM call is isolated in ``llm_type_pairs`` so the DB surgery is
deterministically testable with a mock typer.

Config (env): KW_REL_API_URL, KW_REL_API_KEY, KW_REL_MODEL
Usage:
    KW_REL_API_URL=... KW_REL_API_KEY=... KW_REL_MODEL=deepseek-v4-pro \
      python scripts/build_typed_relations.py <db_path> [--dry-run] [--chunk 20]
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from knowledge_weaver.db import get_entity, init_db, insert_relation  # noqa: E402
from knowledge_weaver.linker import generate_relation_id  # noqa: E402
from knowledge_weaver.relations_schema import REL_TYPES, schema_prompt  # noqa: E402


def _candidates(conn) -> list[dict]:
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
            "from": f"{a['type']}: {a['name']} — {(a['summary'] or '')[:60]}",
            "to": f"{b['type']}: {b['name']} — {(b['summary'] or '')[:60]}",
        })
    return out


def type_relations(db_path: str, type_fn, *, dry_run: bool = False) -> dict:
    """Re-type RELATES_TO edges via ``type_fn(candidates) -> {rel_id: type|none}``.

    Replaces each edge with a typed edge (closed vocab) or prunes it (none)."""
    conn = init_db(db_path)
    cands = _candidates(conn)
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
    remaining = conn.execute("SELECT rel_type, COUNT(*) c FROM relations GROUP BY rel_type").fetchall()
    conn.close()
    return {
        "candidates": len(cands), "typed": typed, "pruned": pruned,
        "by_type": dict(type_counts),
        "relations_after": {r["rel_type"]: r["c"] for r in remaining},
    }


# --------------------------------------------------------------------------- #
# LLM typer (isolated; mocked in tests)                                        #
# --------------------------------------------------------------------------- #
def llm_type_pairs(cands: list[dict], *, api_url: str, api_key: str, model: str,
                   chunk: int = 20, timeout: float = 90.0) -> dict:
    import httpx

    url = api_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    sys_prompt = (
        "你是知识图谱关系判定助手。给你若干有方向的实体对（A→B），"
        "请判断每一对中 A 与 B 之间最贴切的关系类型。\n\n" + schema_prompt() +
        "\n\n对每一对，按其序号输出关系类型或 none；只输出一个 JSON 对象，"
        '形如 {"1": "依赖", "2": "none", ...}，不要任何其它文字。'
    )
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


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="W3.4 type RELATES_TO edges into a knowledge graph")
    ap.add_argument("db_path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--chunk", type=int, default=20)
    args = ap.parse_args()

    api_url = os.environ.get("KW_REL_API_URL")
    api_key = os.environ.get("KW_REL_API_KEY")
    model = os.environ.get("KW_REL_MODEL")
    if not args.dry_run and not (api_url and api_key and model):
        print("Set KW_REL_API_URL / KW_REL_API_KEY / KW_REL_MODEL (or --dry-run).")
        return 1

    def type_fn(cands):
        if args.dry_run:
            return {}  # report candidate count only
        return llm_type_pairs(cands, api_url=api_url, api_key=api_key,
                              model=model, chunk=args.chunk)

    r = type_relations(args.db_path, type_fn, dry_run=args.dry_run)
    print(f"candidates (RELATES_TO): {r['candidates']}")
    if args.dry_run:
        print("dry-run: not calling LLM / not writing. Re-run without --dry-run to type.")
        return 0
    print(f"typed: {r['typed']}  pruned(none): {r['pruned']}")
    print(f"by type: {r['by_type']}")
    print(f"relations after: {r['relations_after']}")
    print("\nBack up the DB before running for real; pruning is destructive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

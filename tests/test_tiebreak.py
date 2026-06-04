"""T1 tie-breaker: LLM verdict 'same'->merge, 'different'->dismiss, deterministic
DB surgery with a mock judge (no network)."""
from knowledge_weaver.db import (
    count_pending_reviews,
    get_entity,
    init_db,
    insert_entity,
    insert_review,
)
from knowledge_weaver.tiebreak import tiebreak_reviews


def _ent(conn, eid, name):
    insert_entity(conn, {
        "id": eid, "type": "tech", "name": name, "summary": name,
        "importance": 0.5, "first_seen": "2026-01-01", "last_seen": "2026-01-01",
    })


def test_tiebreak_merges_same_dismisses_different(temp_db_path):
    conn = init_db(temp_db_path)
    _ent(conn, "tech:kw_long", "Knowledge Weaver")
    _ent(conn, "tech:kw_cn", "知识编织器")
    _ent(conn, "tech:v020", "v0.2.0")
    _ent(conn, "tech:v290", "v2.9.0")
    insert_review(conn, kind="merge", new_entity_id="tech:kw_long",
                  candidate_id="tech:kw_cn", entity_type="tech", score=0.85, reason="t")
    insert_review(conn, kind="merge", new_entity_id="tech:v020",
                  candidate_id="tech:v290", entity_type="tech", score=0.92, reason="t")
    assert count_pending_reviews(conn) == 2

    def judge(pairs):  # same for the alias pair, different for the version pair
        return {p["review_id"]: ("same" if "Weaver" in p["a"] else "different")
                for p in pairs}

    r = tiebreak_reviews(conn, judge)
    assert r["merged"] == 1 and r["dismissed"] == 1
    assert get_entity(conn, "tech:kw_long") is None          # merged away
    assert get_entity(conn, "tech:kw_cn") is not None         # survivor
    assert get_entity(conn, "tech:v020") is not None          # version pair kept distinct
    assert get_entity(conn, "tech:v290") is not None
    assert count_pending_reviews(conn) == 0                   # queue cleared
    conn.close()


def test_tiebreak_dry_run_writes_nothing(temp_db_path):
    conn = init_db(temp_db_path)
    _ent(conn, "tech:a_long", "Alpha Service")
    _ent(conn, "tech:b_long", "阿尔法服务")
    insert_review(conn, kind="merge", new_entity_id="tech:a_long",
                  candidate_id="tech:b_long", entity_type="tech", score=0.85, reason="t")
    r = tiebreak_reviews(conn, lambda pairs: {p["review_id"]: "same" for p in pairs},
                         dry_run=True)
    assert r["merged"] == 1 and r["candidates"] == 1
    assert get_entity(conn, "tech:a_long") is not None        # nothing written
    assert count_pending_reviews(conn) == 1
    conn.close()

"""Deterministic tests for W1 entity resolution (resolver + review queue + log).

Uses a mock embedder producing 1024-dim vectors with *exact* controlled cosine
against a fixed base, so band classification is fully deterministic (no real
embedding model / network needed).
"""
import math

from knowledge_weaver.db import (
    count_pending_reviews,
    get_entity_vector,
    init_db,
    insert_entity,
    insert_review,
    list_pending_reviews,
    record_merge,
    set_review_status,
    upsert_entity_vector,
)
from knowledge_weaver.embedder import DEFAULT_DIMENSION
from knowledge_weaver.resolver import resolve_entity

DIM = DEFAULT_DIMENSION


def _vec_at_cos(c: float) -> list[float]:
    """Unit vector whose cosine with BASE = [1,0,0,...] equals exactly ``c``."""
    v = [0.0] * DIM
    v[0] = c
    v[1] = math.sqrt(max(0.0, 1.0 - c * c))
    return v


BASE = _vec_at_cos(1.0)  # [1, 0, 0, ...]


class MockEmbedder:
    def __init__(self, mapping: dict[str, list[float]]):
        self.mapping = mapping

    def embed(self, text: str):
        for k, v in self.mapping.items():
            if text.startswith(k) or k.startswith(text):
                return v
        return None

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def _add_entity(conn, eid, etype, name, summary, vec=None):
    insert_entity(conn, {
        "id": eid, "type": etype, "name": name, "summary": summary,
        "importance": 0.5, "first_seen": "2026-01-01", "last_seen": "2026-01-01",
    })
    if vec is not None:
        upsert_entity_vector(conn, eid, vec)


# --------------------------------------------------------------------------- #
# Band classification                                                         #
# --------------------------------------------------------------------------- #
def test_high_band_cross_language_merge(temp_db_path):
    """The core fix: HomeBrain / 家庭大脑 (name ratio ~0) merge via vector."""
    conn = init_db(temp_db_path)
    _add_entity(conn, "proj:homebrain", "project", "HomeBrain", "home brain hub", BASE)
    emb = MockEmbedder({"jiating danao": _vec_at_cos(0.95)})

    res = resolve_entity(
        conn, new_id="proj:jiatingdanao", entity_type="project",
        name="家庭大脑", summary="jiating danao", embedder=emb,
    )
    assert res.action == "merge"
    assert res.into_id == "proj:homebrain"
    assert res.reason == "auto:vector"
    conn.close()


def test_mid_band_goes_to_review(temp_db_path):
    conn = init_db(temp_db_path)
    _add_entity(conn, "proj:homebrain", "project", "HomeBrain", "home brain hub", BASE)
    emb = MockEmbedder({"jiating danao": _vec_at_cos(0.85)})  # in [0.82, 0.90)

    res = resolve_entity(
        conn, new_id="proj:jiatingdanao", entity_type="project",
        name="家庭大脑", summary="jiating danao", embedder=emb,
    )
    assert res.action == "review"
    assert res.into_id == "proj:homebrain"
    conn.close()


def test_low_band_distinct(temp_db_path):
    conn = init_db(temp_db_path)
    _add_entity(conn, "proj:homebrain", "project", "HomeBrain", "home brain hub", BASE)
    emb = MockEmbedder({"unrelated": _vec_at_cos(0.50)})

    res = resolve_entity(
        conn, new_id="proj:other", entity_type="project",
        name="完全无关", summary="unrelated", embedder=emb,
    )
    assert res.action == "distinct"
    conn.close()


def test_name_high_same_script_merge(temp_db_path):
    """Same-script near-identical name auto-merges even when cosine is low."""
    conn = init_db(temp_db_path)
    _add_entity(conn, "proj:exampleproject", "project", "ExampleProject",
                "an example", BASE)
    emb = MockEmbedder({"example proj": _vec_at_cos(0.50)})  # low cos on purpose

    res = resolve_entity(
        conn, new_id="proj:exampleprojektt", entity_type="project",
        name="ExampleProjektt", summary="example proj", embedder=emb,
    )
    assert res.action == "merge"
    assert res.into_id == "proj:exampleproject"
    assert res.reason == "auto:name"
    conn.close()


def test_type_isolation(temp_db_path):
    """A high-cosine candidate of a DIFFERENT type is not a merge target."""
    conn = init_db(temp_db_path)
    _add_entity(conn, "person:homebrain", "person", "HomeBrain", "a person", BASE)
    emb = MockEmbedder({"jiating danao": _vec_at_cos(0.95)})

    res = resolve_entity(
        conn, new_id="proj:jiatingdanao", entity_type="project",
        name="家庭大脑", summary="jiating danao", embedder=emb,
    )
    assert res.action == "distinct"
    conn.close()


def test_no_candidates_distinct(temp_db_path):
    conn = init_db(temp_db_path)
    res = resolve_entity(
        conn, new_id="proj:fresh", entity_type="project",
        name="全新项目", summary="brand new", embedder=MockEmbedder({}),
    )
    assert res.action == "distinct"
    conn.close()


def test_authoritative_normalize(temp_db_path):
    """Registered alias normalizes to canonical id, even with no embedder/DB hit."""
    conn = init_db(temp_db_path)
    res = resolve_entity(
        conn, new_id="proj:jiatingdanao", entity_type="project",
        name="家庭大脑", summary="", embedder=None,
        alias_to_canonical={"proj:jiatingdanao": "proj:homebrain"},
    )
    assert res.action == "normalize"
    assert res.into_id == "proj:homebrain"
    assert res.reason == "registered-alias"
    conn.close()


# --------------------------------------------------------------------------- #
# Review queue + merge log helpers                                            #
# --------------------------------------------------------------------------- #
def test_review_queue_idempotent_and_count(temp_db_path):
    conn = init_db(temp_db_path)
    rid1 = insert_review(conn, kind="merge", new_entity_id="proj:a",
                         candidate_id="proj:b", entity_type="project",
                         score=0.85, reason="review:vector")
    rid2 = insert_review(conn, kind="merge", new_entity_id="proj:a",
                         candidate_id="proj:b", entity_type="project",
                         score=0.85, reason="review:vector")
    assert rid1 == rid2  # idempotent per (kind,new,candidate) while pending
    assert count_pending_reviews(conn) == 1

    rows = list_pending_reviews(conn)
    assert len(rows) == 1 and rows[0]["new_entity_id"] == "proj:a"

    set_review_status(conn, rid1, "rejected")
    assert count_pending_reviews(conn) == 0
    conn.close()


def test_record_merge_log(temp_db_path):
    conn = init_db(temp_db_path)
    record_merge(conn, merged_from_id="proj:jiatingdanao",
                 merged_into_id="proj:homebrain", reason="auto:vector",
                 score=0.95, from_snapshot='{"id":"proj:jiatingdanao"}')
    row = conn.execute(
        "SELECT merged_from_id, merged_into_id, score FROM merge_log"
    ).fetchone()
    assert row[0] == "proj:jiatingdanao"
    assert row[1] == "proj:homebrain"
    assert abs(row[2] - 0.95) < 1e-9
    conn.close()


def test_get_entity_vector_roundtrip(temp_db_path):
    conn = init_db(temp_db_path)
    _add_entity(conn, "proj:x", "project", "X", "summary", _vec_at_cos(0.9))
    got = get_entity_vector(conn, "proj:x")
    assert got is not None and len(got) == DIM
    assert abs(got[0] - 0.9) < 1e-6
    assert get_entity_vector(conn, "proj:missing") is None
    conn.close()

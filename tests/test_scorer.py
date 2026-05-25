"""Tests for the scorer module — importance scoring with 5-factor formula."""

from datetime import date

from knowledge_weaver.scorer import ImportanceScorer, score_entity, filter_by_score


# ---------------------------------------------------------------------------
# Factor-level tests
# ---------------------------------------------------------------------------

class TestFreshness:
    def test_freshness_recent(self):
        """0 days since last seen → freshness = 1.0"""
        scorer = ImportanceScorer()
        assert scorer.freshness(0) == 1.0

    def test_freshness_old(self):
        """60 days since last seen → freshness = 0.0"""
        scorer = ImportanceScorer()
        assert scorer.freshness(60) == 0.0

    def test_freshness_partial(self):
        """15 days since last seen → freshness = 0.5"""
        scorer = ImportanceScorer()
        assert scorer.freshness(15) == 0.5


class TestFrequency:
    def test_frequency_high(self):
        """14 days → capped at 1.0"""
        scorer = ImportanceScorer()
        assert scorer.frequency(14) == 1.0

    def test_frequency_low(self):
        """1 day → 1/7"""
        scorer = ImportanceScorer()
        assert scorer.frequency(1) == 1 / 7


class TestDiversity:
    def test_diversity(self):
        scorer = ImportanceScorer()
        # 0 categories → 0.0
        assert scorer.diversity(0) == 0.0
        # 2 categories → 0.5
        assert scorer.diversity(2) == 0.5
        # 4 categories → 1.0 (capped)
        assert scorer.diversity(4) == 1.0
        # 8 categories → 1.0 (capped)
        assert scorer.diversity(8) == 1.0


class TestRichness:
    def test_richness(self):
        scorer = ImportanceScorer()
        # 0 tags → 0.0
        assert scorer.richness(0) == 0.0
        # 3 tags → 0.6
        assert scorer.richness(3) == 0.6
        # 5 tags → 1.0 (capped)
        assert scorer.richness(5) == 1.0


class TestAccess:
    def test_access_count(self):
        scorer = ImportanceScorer()
        # 0 accesses → 0.0
        assert scorer.access(0) == 0.0
        # 3 accesses → 0.6
        assert scorer.access(3) == 0.6
        # 5 accesses → 1.0 (capped)
        assert scorer.access(5) == 1.0


# ---------------------------------------------------------------------------
# Composite score tests
# ---------------------------------------------------------------------------

class TestCalculateImportance:
    def test_calculate_importance_full(self):
        """All factors at max → importance ≈ 1.0"""
        scorer = ImportanceScorer()
        score = scorer.calculate(
            days_since_last_seen=0,
            day_count=14,
            distinct_categories=8,
            tag_count=10,
            access_count=10,
        )
        assert score == 1.0

    def test_calculate_importance_zero(self):
        """All factors at 0 → importance = 0.0"""
        scorer = ImportanceScorer()
        score = scorer.calculate(
            days_since_last_seen=60,
            day_count=0,
            distinct_categories=0,
            tag_count=0,
            access_count=0,
        )
        assert score == 0.0

    def test_score_bounded(self):
        """Score always in [0.0, 1.0]."""
        scorer = ImportanceScorer()
        for args in [
            (0, 1, 1, 1, 0),
            (0, 14, 8, 10, 20),
            (60, 0, 0, 0, 0),
            (15, 3, 2, 1, 4),
        ]:
            score = scorer.calculate(*args)
            assert 0.0 <= score <= 1.0, f"score {score} out of bounds for {args}"


# ---------------------------------------------------------------------------
# Convenience function tests
# ---------------------------------------------------------------------------

class TestScoreEntity:
    def test_score_entity_basic(self):
        entity = {
            "last_seen": "2026-05-25",
            "day_count": 7,
            "distinct_categories": 4,
            "tags": ["a", "b", "c", "d", "e"],
        }
        score = score_entity(entity, access_count=5, today=date(2026, 5, 25))
        assert score == 1.0

    def test_score_entity_stale(self):
        entity = {
            "last_seen": "2026-04-25",
            "day_count": 1,
            "distinct_categories": 1,
            "tags": [],
        }
        score = score_entity(entity, access_count=0, today=date(2026, 5, 25))
        assert 0.0 <= score < 0.5


# ---------------------------------------------------------------------------
# Filter and sort tests
# ---------------------------------------------------------------------------

class TestFilterByScore:
    def test_scoring_filter(self):
        """filter_by_score removes entities below min_score."""
        entities = [
            {"id": "e1", "last_seen": "2026-05-25", "day_count": 7, "distinct_categories": 4, "tags": list(range(5))},
            {"id": "e2", "last_seen": "2026-04-25", "day_count": 1, "distinct_categories": 1, "tags": []},
        ]
        today = date(2026, 5, 25)
        result = filter_by_score(entities, min_score=0.5, today=today)
        ids = [e["id"] for e in result]
        assert "e1" in ids
        assert "e2" not in ids

    def test_score_and_sort(self):
        """Multiple entities returned in descending importance order."""
        entities = [
            {"id": "low", "last_seen": "2026-05-10", "day_count": 1, "distinct_categories": 1, "tags": []},
            {"id": "high", "last_seen": "2026-05-25", "day_count": 7, "distinct_categories": 4, "tags": list(range(5))},
            {"id": "mid", "last_seen": "2026-05-20", "day_count": 3, "distinct_categories": 2, "tags": ["a"]},
        ]
        today = date(2026, 5, 25)
        result = filter_by_score(entities, min_score=0.0, today=today)
        ids = [e["id"] for e in result]
        assert ids == ["high", "mid", "low"]

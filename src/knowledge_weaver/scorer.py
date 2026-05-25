"""Importance scorer — 5-factor weighted scoring for knowledge entities."""

from __future__ import annotations

from datetime import date


class ImportanceScorer:
    """Calculates importance score for entities using multiple factors."""

    WEIGHTS: dict[str, float] = {
        "freshness": 0.35,
        "frequency": 0.30,
        "diversity": 0.15,
        "richness": 0.10,
        "access": 0.10,
    }

    def calculate(
        self,
        days_since_last_seen: int,
        day_count: int,
        distinct_categories: int = 1,
        tag_count: int = 0,
        access_count: int = 0,
    ) -> float:
        """Calculate composite importance score (0.0 - 1.0)."""
        score = (
            self.WEIGHTS["freshness"] * self.freshness(days_since_last_seen)
            + self.WEIGHTS["frequency"] * self.frequency(day_count)
            + self.WEIGHTS["diversity"] * self.diversity(distinct_categories)
            + self.WEIGHTS["richness"] * self.richness(tag_count)
            + self.WEIGHTS["access"] * self.access(access_count)
        )
        return round(max(0.0, min(1.0, score)), 4)

    def freshness(self, days_since_last_seen: int) -> float:
        """max(0, 1 - days_since_last_seen / 30)"""
        return max(0.0, 1.0 - days_since_last_seen / 30.0)

    def frequency(self, day_count: int) -> float:
        """min(1, day_count / 7)"""
        return min(1.0, day_count / 7.0)

    def diversity(self, distinct_categories: int) -> float:
        """min(1, distinct_categories / 4)"""
        return min(1.0, distinct_categories / 4.0)

    def richness(self, tag_count: int) -> float:
        """min(1, tag_count / 5)"""
        return min(1.0, tag_count / 5.0)

    def access(self, access_count: int) -> float:
        """min(1, access_count / 5)"""
        return min(1.0, access_count / 5.0)


def score_entity(
    entity: dict,
    access_count: int = 0,
    today: date | None = None,
) -> float:
    """Convenience function to score a single entity dict."""
    if today is None:
        today = date.today()

    last_seen_str = entity.get("last_seen")
    if last_seen_str:
        last_seen = date.fromisoformat(last_seen_str)
        days_since = (today - last_seen).days
    else:
        days_since = 999

    day_count = entity.get("day_count", 0)
    distinct_categories = entity.get("distinct_categories", 0)
    tags = entity.get("tags", [])
    tag_count = len(tags) if isinstance(tags, (list, tuple)) else 0

    scorer = ImportanceScorer()
    return scorer.calculate(
        days_since_last_seen=max(0, days_since),
        day_count=day_count,
        distinct_categories=distinct_categories,
        tag_count=tag_count,
        access_count=access_count,
    )


def filter_by_score(
    entities: list[dict],
    min_score: float = 0.0,
    access_counts: dict[str, int] | None = None,
    today: date | None = None,
) -> list[dict]:
    """Filter and sort entities by importance score, return sorted list."""
    if access_counts is None:
        access_counts = {}

    scored: list[tuple[float, dict]] = []
    for entity in entities:
        entity_id = entity.get("id", "")
        ac = access_counts.get(entity_id, 0)
        s = score_entity(entity, access_count=ac, today=today)
        if s >= min_score:
            scored.append((s, entity))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entity for _, entity in scored]

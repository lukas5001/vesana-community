"""Deterministic ranking helpers for the profile library.

The trending score is intentionally pure and side-effect free so it can be
unit-tested without a database. Real community votes land in C4; until then
``vote_score`` defaults to 0 and the formula leans on engagement + recency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Weights for the trending formula.
IMPORT_WEIGHT = 3
DOWNLOAD_WEIGHT = 1
VOTE_WEIGHT = 2
RECENCY_BOOST = 5
RECENCY_WINDOW = timedelta(days=30)


def _as_aware(value: datetime) -> datetime:
    """Treat naive datetimes as UTC so comparisons stay deterministic."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def trending_score(
    *,
    import_count: int,
    download_count: int,
    updated_at: datetime,
    now: datetime,
    vote_score: int = 0,
) -> int:
    """Compute a deterministic trending score for a profile.

    ``import_count`` is weighted highest (a real adoption signal), then votes,
    then downloads. Profiles updated within the last 30 days get a flat boost
    so fresh content surfaces. The result is monotonic in every engagement
    input, which the tests assert.
    """
    score = (
        import_count * IMPORT_WEIGHT + vote_score * VOTE_WEIGHT + download_count * DOWNLOAD_WEIGHT
    )
    if _as_aware(now) - _as_aware(updated_at) <= RECENCY_WINDOW:
        score += RECENCY_BOOST
    return score

from __future__ import annotations

import pytest

from ao3kit.scrape import (
    calculate_quality_score,
    calculate_quality_score_raw,
    normalize_quality_score,
    resolve_quality_score,
    work_matches_filters,
)
from ao3kit.scrape import WorkMetadata, WorkRecord


def test_calculate_quality_score_raw_matches_userscript_formula():
    # kudos=200, hits=1000, words=12000 from cover EPUB fixture
    raw = calculate_quality_score_raw(200, 1000, 12000)
    assert raw is not None
    assert raw == pytest.approx(28.4, abs=0.1)


def test_normalize_quality_score_caps_at_100():
    assert normalize_quality_score(11.0) == 50
    assert normalize_quality_score(22.0) == 100
    assert normalize_quality_score(30.0) == 100


def test_calculate_quality_score_returns_normalized_int():
    assert calculate_quality_score(200, 1000, 12000) == 100
    assert calculate_quality_score(55, 1000, 5000) == 25


def test_resolve_quality_score_prefers_stored_normalized_pair():
    assert resolve_quality_score(
        quality_score=62,
        quality_score_raw=13.6,
    ) == 62


def test_resolve_quality_score_normalizes_legacy_quality_score_field():
    assert resolve_quality_score(quality_score=13.6) == 62


def test_resolve_quality_score_computes_from_stats():
    assert resolve_quality_score(kudos=200, hits=1000, words=12000) == 100


def test_work_matches_filters_uses_normalized_score():
    work = WorkRecord(
        work_id="1",
        url="https://archiveofourown.org/works/1",
        title="Test",
        metadata=WorkMetadata(kudos=200, hits=1000, words=12000),
    )
    assert work_matches_filters(work, min_score=40) is True
    assert work_matches_filters(work, min_score=100) is False


def test_work_matches_filters_rejects_low_normalized_score():
    work = WorkRecord(
        work_id="2",
        url="https://archiveofourown.org/works/2",
        title="Low",
        metadata=WorkMetadata(kudos=60, hits=10000, words=5000),
    )
    assert calculate_quality_score_raw(60, 10000, 5000) == pytest.approx(0.6, abs=0.1)
    assert calculate_quality_score(60, 10000, 5000) == 3
    assert work_matches_filters(work, min_score=40) is False
    assert work_matches_filters(work, min_score=1) is True


def test_quality_score_requires_min_kudos():
    assert calculate_quality_score(10, 1000, 5000) is None

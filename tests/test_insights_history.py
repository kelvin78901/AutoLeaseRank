"""Tests for alr/insights/history.py (see alr/insights/CONTRACT.md, history section)."""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from alr.insights.history import dropping_now, listing_timeline, load_history
from alr.insights.models import HistoryPoint

C1 = datetime(2026, 10, 1, 6, tzinfo=timezone.utc)
C2 = datetime(2026, 10, 5, 6, tzinfo=timezone.utc)
C3 = datetime(2026, 10, 9, 6, tzinfo=timezone.utc)
C4 = datetime(2026, 10, 13, 6, tzinfo=timezone.utc)


def _pt(key, minutes, eff):
    return HistoryPoint(
        crawl_ts=C1 + timedelta(minutes=minutes),
        listing_key=key,
        effective_monthly=eff,
        days_on_market=None,
        price_drops=None,
        favorites=None,
    )


def _manual_drops(points):
    """Independent computation of the expected drops, ordered per the contract."""
    by_key = defaultdict(list)
    for p in points:
        if p.effective_monthly is not None:
            by_key[p.listing_key].append(p)
    out = []
    for key, rows in by_key.items():
        rows.sort(key=lambda p: p.crawl_ts)
        if len(rows) < 2:
            continue
        prev, last = rows[-2], rows[-1]
        if last.effective_monthly < prev.effective_monthly and prev.effective_monthly > 0:
            out.append((prev.effective_monthly - last.effective_monthly, key, prev, last))
    out.sort(key=lambda t: (-t[0], t[1]))
    return out


def test_load_history_all_rows_ordered_and_aware_utc(insights_con, insights_history_rows):
    pts = load_history(insights_con)
    assert len(pts) == len(insights_history_rows) == 28
    assert [(p.crawl_ts, p.listing_key) for p in pts] == sorted(
        (p.crawl_ts, p.listing_key) for p in pts
    )
    assert (pts[0].crawl_ts, pts[0].listing_key) == (C1, "leasehackr:2001")
    for p in pts:
        assert p.crawl_ts.tzinfo is not None
        assert p.crawl_ts.utcoffset() == timedelta(0)
    expected = sorted(
        (datetime.fromisoformat(r["crawl_ts"].replace("Z", "+00:00")), r["listing_key"])
        for r in insights_history_rows
    )
    assert [(p.crawl_ts, p.listing_key) for p in pts] == expected


def test_load_history_null_row_round_trips(insights_con):
    pts = load_history(insights_con, "marketcheck:4002")
    assert len(pts) == 4
    first = pts[0]
    assert first.crawl_ts == C1
    assert first.effective_monthly is None
    assert first.days_on_market is None
    assert first.price_drops is None
    assert first.favorites is None


def test_load_history_filters_by_key(insights_con, insights_history):
    pts = load_history(insights_con, "swapalease:3006")
    assert [p.crawl_ts for p in pts] == [C1, C4]
    assert {p.listing_key for p in pts} == {"swapalease:3006"}
    expected = [p for p in insights_history if p.listing_key == "swapalease:3006"]
    assert sorted(p.effective_monthly for p in pts) == sorted(p.effective_monthly for p in expected)


def test_load_history_unknown_and_empty(insights_con, mem_con):
    assert load_history(insights_con, "nope") == []
    assert load_history(mem_con) == []
    assert load_history(mem_con, "leasehackr:2001") == []


def test_load_history_sql_metacharacters_are_parameterized(insights_con):
    assert load_history(insights_con, "x' OR '1'='1") == []
    assert load_history(insights_con, "'; DROP TABLE history; --") == []
    assert len(load_history(insights_con)) == 28


def test_listing_timeline_ordered_despite_unsorted_file(insights_history):
    tl = listing_timeline(insights_history, "leasehackr:2001")
    assert [p.crawl_ts for p in tl] == [C1, C2, C3, C4]
    assert [p.effective_monthly for p in tl] == [900, 880, 870, 845]
    tl2 = listing_timeline(insights_history, "marketcheck:4002")
    assert len(tl2) == 4
    assert tl2[0].effective_monthly is None


def test_listing_timeline_unknown_key_and_stability(insights_history):
    assert listing_timeline(insights_history, "nope") == []
    a, b = _pt("k", 0, 10.0), _pt("k", 0, 20.0)
    assert listing_timeline([a, b], "k") == [a, b]
    assert listing_timeline([b, a], "k") == [b, a]


def test_dropping_now_keys_match_manual_computation(insights_history):
    drops = dropping_now(insights_history)
    manual = _manual_drops(insights_history)
    assert {d.listing_key for d in drops} == {t[1] for t in manual}
    keys = {d.listing_key for d in drops}
    # risen at latest crawl, unchanged and single-row keys are excluded
    assert not keys & {"leasehackr:2008", "swapalease:3001", "leasehackr:2005"}
    # sold listing (absent from newest crawl) that dropped over its last two crawls is kept
    assert "swapalease:3999" in keys


def test_dropping_now_values_match_manual_computation(insights_history):
    drops = dropping_now(insights_history)
    manual = _manual_drops(insights_history)
    assert len(drops) == len(manual)
    for d, (raw, key, prev, last) in zip(drops, manual):
        assert d.listing_key == key
        assert d.previous_crawl_ts == prev.crawl_ts
        assert d.latest_crawl_ts == last.crawl_ts
        assert d.previous_effective_monthly == prev.effective_monthly
        assert d.latest_effective_monthly == last.effective_monthly
        assert d.drop_dollars == round(raw, 2)
        assert d.drop_pct == round(raw / prev.effective_monthly * 100, 2)
        assert d.previous_crawl_ts.utcoffset() == timedelta(0)
    by_key = {d.listing_key: d for d in drops}
    assert by_key["swapalease:3999"].previous_crawl_ts == C1
    assert by_key["swapalease:3999"].latest_crawl_ts == C2
    assert by_key["swapalease:3006"].previous_crawl_ts == C1
    assert by_key["marketcheck:4002"].previous_effective_monthly == 1130.0


def test_dropping_now_ordering_and_tie_break(insights_history):
    drops = dropping_now(insights_history)
    assert [d.listing_key for d in drops] == [
        "marketcheck:4001",
        "leasehackr:2004",
        "swapalease:3006",
        "marketcheck:4002",
        "swapalease:3999",
        "leasehackr:2001",
    ]
    # the $40 tie is broken by key, not by drop_pct (2004 has the larger pct anyway;
    # 3006 must come after despite equal dollars)
    assert drops[1].drop_dollars == drops[2].drop_dollars == 40.0


def test_dropping_now_limit(insights_history):
    full = dropping_now(insights_history)
    assert [d.listing_key for d in dropping_now(insights_history, limit=2)] == [
        "marketcheck:4001",
        "leasehackr:2004",
    ]
    assert dropping_now(insights_history, limit=3) == full[:3]
    assert dropping_now(insights_history, limit=1) == full[:1]
    assert dropping_now(insights_history, limit=100) == full


@pytest.mark.parametrize("limit", [0, -1])
def test_dropping_now_invalid_limit_raises(insights_history, limit):
    with pytest.raises(ValueError):
        dropping_now(insights_history, limit=limit)
    with pytest.raises(ValueError):
        dropping_now([], limit=limit)


def test_dropping_now_empty_input():
    assert dropping_now([]) == []


def test_dropping_now_ignores_null_effective_monthly():
    rows = [
        _pt("a", 0, 100.0),
        _pt("a", 10, 90.0),
        _pt("a", 20, None),  # latest row null: last two non-null rows are used
        _pt("b", 0, None),
        _pt("b", 10, 50.0),  # only one non-null row: skipped
        _pt("c", 0, 0.0),
        _pt("c", 10, -5.0),  # previous == 0 -> not dropping
    ]
    drops = dropping_now(rows)
    assert [d.listing_key for d in drops] == ["a"]
    assert drops[0].previous_effective_monthly == 100.0
    assert drops[0].latest_effective_monthly == 90.0
    assert drops[0].latest_crawl_ts == C1 + timedelta(minutes=10)


def test_dropping_now_rounding_and_unrounded_sort():
    rows = [
        _pt("a", 0, 100.0), _pt("a", 10, 89.999),   # drop 10.001
        _pt("b", 0, 100.0), _pt("b", 10, 89.996),   # drop 10.004
    ]
    drops = dropping_now(rows)
    assert [d.listing_key for d in drops] == ["b", "a"]
    assert drops[0].drop_dollars == drops[1].drop_dollars == 10.0
    assert drops[0].drop_pct == round(10.004 / 100 * 100, 2)


def test_dropping_now_input_order_irrelevant_and_not_mutated(insights_history):
    expected = dropping_now(insights_history)
    snapshot = list(insights_history)
    dumped = [p.model_dump() for p in insights_history]
    shuffled = list(insights_history)
    random.Random(7).shuffle(shuffled)
    assert dropping_now(shuffled) == expected
    assert insights_history == snapshot
    assert [p.model_dump() for p in insights_history] == dumped


def test_dropping_now_from_db_equals_from_fixture(insights_con, insights_history):
    assert dropping_now(load_history(insights_con)) == dropping_now(insights_history)

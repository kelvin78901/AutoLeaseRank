"""segments: percentile + segment_stats against alr/insights/CONTRACT.md."""
import statistics
from collections import Counter, defaultdict

import pytest

from alr.insights.models import GROUP_BY_FIELDS, SegmentStats
from alr.insights.segments import percentile, segment_stats
from alr.schema import EnrichedListing


def _deciles(values):
    """p10/p50/p90 oracle; statistics.quantiles needs >= 2 points."""
    if len(values) == 1:
        return values[0], values[0], values[0]
    q = statistics.quantiles(values, n=10, method="inclusive")
    return q[0], q[4], q[8]


def _mk(rows, key, **over):
    return EnrichedListing(**{**rows[0], "listing_key": key, **over})


def test_percentile_matches_statistics_inclusive_deciles(insights_listings):
    values = [l.effective_monthly for l in insights_listings]
    dec = statistics.quantiles(values, n=10, method="inclusive")
    for i, expected in enumerate(dec, start=1):
        assert percentile(values, i / 10) == pytest.approx(expected)
    assert percentile(values, 0.1) == pytest.approx(352.0)
    assert percentile(values, 0.5) == pytest.approx(545.0)
    assert percentile(values, 0.9) == pytest.approx(845.0)


def test_percentile_contract_examples_and_edges():
    assert percentile([10, 20, 30, 40], 0.1) == pytest.approx(13.0)
    assert percentile([10, 20, 30, 40], 0.5) == pytest.approx(25.0)
    assert percentile([5], 0.9) == 5.0
    assert isinstance(percentile([5], 0.9), float)
    vals = [7.5, 3.0, 9.0, 1.0]
    assert percentile(vals, 0) == 1.0
    assert percentile(vals, 1) == 9.0
    # not rounded
    assert percentile([1, 2], 1 / 3) == pytest.approx(1.3333333333333)
    assert percentile([1, 2], 1 / 3) != 1.33


def test_percentile_input_order_irrelevant_and_unchanged():
    unsorted = [40, 10, 30, 20]
    snapshot = list(unsorted)
    assert percentile(unsorted, 0.1) == percentile(sorted(unsorted), 0.1)
    assert unsorted == snapshot


def test_percentile_invalid_arguments_raise():
    with pytest.raises(ValueError):
        percentile([], 0.5)
    for q in (-0.01, 1.01):
        with pytest.raises(ValueError):
            percentile([1, 2, 3], q)


@pytest.mark.parametrize("group_by", GROUP_BY_FIELDS)
def test_segment_counts_match_counter(insights_listings, group_by):
    got = segment_stats(insights_listings, group_by, min_count=1)
    expected = Counter(str(getattr(l, group_by)) for l in insights_listings)
    assert {g.key: g.count for g in got} == dict(expected)
    assert len(got) == len(expected)
    assert sum(g.count for g in got) == len(insights_listings)
    assert all(isinstance(g, SegmentStats) for g in got)


@pytest.mark.parametrize("group_by", GROUP_BY_FIELDS)
def test_segment_percentiles_match_oracle_rounded(insights_listings, group_by):
    got = segment_stats(insights_listings, group_by)
    buckets = defaultdict(list)
    for l in insights_listings:
        buckets[str(getattr(l, group_by))].append(l.effective_monthly)
    for g in got:
        p10, p50, p90 = _deciles(buckets[g.key])
        assert g.p10 == pytest.approx(round(p10, 2), abs=1e-9)
        assert g.p50 == pytest.approx(round(p50, 2), abs=1e-9)
        assert g.p90 == pytest.approx(round(p90, 2), abs=1e-9)
        assert g.p10 <= g.p50 <= g.p90
    largest = got[0]
    assert largest.count == max(len(v) for v in buckets.values())


def test_segment_ordering_count_desc_then_key_asc(insights_listings):
    for group_by in GROUP_BY_FIELDS:
        got = segment_stats(insights_listings, group_by)
        order = [(-g.count, g.key) for g in got]
        assert order == sorted(order)
    # fixture ties: BMW/Toyota (5) and Coupe/Truck (3)
    makes = [g.key for g in segment_stats(insights_listings, "make")]
    assert makes[:2] == ["BMW", "Toyota"]
    bodies = [g.key for g in segment_stats(insights_listings, "body")]
    assert bodies.index("Coupe") < bodies.index("Truck")


def test_segment_ordering_forced_tie(insights_rows):
    ls = [
        _mk(insights_rows, "t:1", make="Zed"),
        _mk(insights_rows, "t:2", make="Abe"),
        _mk(insights_rows, "t:3", make="Mid"),
        _mk(insights_rows, "t:4", make="Mid"),
    ]
    assert [g.key for g in segment_stats(ls, "make")] == ["Mid", "Abe", "Zed"]


def test_segment_min_count(insights_listings):
    counts = Counter(l.make for l in insights_listings)
    assert min(counts.values()) == 1
    got = segment_stats(insights_listings, "make", min_count=2)
    assert {g.key for g in got} == {k for k, c in counts.items() if c >= 2}
    assert "Kia" not in {g.key for g in got}
    assert "Volvo" not in {g.key for g in got}
    assert segment_stats(insights_listings, "make", min_count=max(counts.values()) + 1) == []


def test_segment_na_state_is_ordinary_group(insights_listings):
    got = {g.key: g for g in segment_stats(insights_listings, "state")}
    n_na = sum(1 for l in insights_listings if l.state == "NA")
    assert n_na > 0
    assert got["NA"].count == n_na


def test_segment_keys_case_sensitive_and_rounding(insights_rows):
    ls = [
        _mk(insights_rows, "t:1", make="BMW", effective_monthly=100.0),
        _mk(insights_rows, "t:2", make="bmw", effective_monthly=100.0),
        _mk(insights_rows, "t:3", make="bmw", effective_monthly=101.0),
        _mk(insights_rows, "t:4", make="bmw", effective_monthly=101.0),
    ]
    got = {g.key: g for g in segment_stats(ls, "make")}
    assert set(got) == {"BMW", "bmw"}
    # bmw values [100, 101, 101]: p10 = 100.2, p50 = 101
    assert got["bmw"].p10 == 100.2
    ls2 = [
        _mk(insights_rows, "t:5", effective_monthly=1.0),
        _mk(insights_rows, "t:6", effective_monthly=2.0),
    ]
    (g,) = segment_stats(ls2, "make")
    assert g.p10 == round(1 + 1 / 10, 2)
    ls3 = [_mk(insights_rows, f"t:{i}", make="X", effective_monthly=v) for i, v in enumerate([1.0, 2.0])]
    (g3,) = segment_stats(ls3, "make")
    assert g3.p10 == 1.1  # 1.1 exactly after rounding, not 1.1000000000000001 repr noise
    ls4 = [_mk(insights_rows, f"u:{i}", make="X", effective_monthly=v) for i, v in enumerate([0.0, 1.0])]
    (g4,) = segment_stats(ls4, "make")
    assert g4.p10 == round(percentile([0.0, 1.0], 0.1), 2) == 0.1


def test_segment_invalid_arguments_and_empty(insights_listings):
    for bad in ("model", "MAKE", ""):
        with pytest.raises(ValueError):
            segment_stats(insights_listings, bad)
        with pytest.raises(ValueError):
            segment_stats([], bad)
    for mc in (0, -1):
        with pytest.raises(ValueError):
            segment_stats(insights_listings, "make", min_count=mc)
        with pytest.raises(ValueError):
            segment_stats([], "make", min_count=mc)
    assert segment_stats([], "make") == []


def test_segment_stats_does_not_mutate_inputs(insights_listings):
    keys = [l.listing_key for l in insights_listings]
    dumps = [l.model_dump() for l in insights_listings]
    for group_by in GROUP_BY_FIELDS:
        segment_stats(insights_listings, group_by, min_count=2)
    assert [l.listing_key for l in insights_listings] == keys
    assert [l.model_dump() for l in insights_listings] == dumps

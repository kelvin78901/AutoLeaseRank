"""quality: source_quality against alr/insights/CONTRACT.md."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from alr.insights.models import MAX_STALE_DAYS, SourceQuality
from alr.insights.quality import source_quality
from alr.schema import EnrichedListing


def _mk(rows, key, **over):
    return EnrichedListing(**{**rows[0], "listing_key": key, **over})


def _by_source(listings):
    d = defaultdict(list)
    for l in listings:
        d[l.source].append(l)
    return d


def test_one_entry_per_source_ordered(insights_listings, insights_now):
    got = source_quality(insights_listings, 7, now=insights_now)
    counts = Counter(l.source for l in insights_listings)
    assert all(isinstance(g, SourceQuality) for g in got)
    assert [g.source for g in got] == [s for s, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    assert {g.source: g.count for g in got} == dict(counts)
    assert sum(g.count for g in got) == len(insights_listings)


def test_source_ordering_tie_broken_by_source_asc(insights_rows):
    ls = [
        _mk(insights_rows, "z:1", source="zeta"),
        _mk(insights_rows, "a:1", source="alpha"),
        _mk(insights_rows, "m:1", source="mid"),
        _mk(insights_rows, "m:2", source="mid"),
    ]
    got = source_quality(ls, 7, now=datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert [g.source for g in got] == ["mid", "alpha", "zeta"]


def test_fill_rates_match_manual(insights_listings, insights_now):
    got = {g.source: g for g in source_quality(insights_listings, 7, now=insights_now)}
    for src, ls in _by_source(insights_listings).items():
        n = len(ls)
        g = got[src]
        assert g.vin_fill == round(sum(1 for l in ls if (l.vin or "").strip()) / n, 4)
        assert g.hp_fill == round(sum(1 for l in ls if l.hp > 0) / n, 4)
        assert g.state_fill == round(
            sum(1 for l in ls if (l.state or "").strip().upper() not in ("", "NA")) / n, 4)
        assert g.year_fill == round(sum(1 for l in ls if l.year > 0) / n, 4)
    assert got["leasehackr"].vin_fill == 0.4444
    assert got["swapalease"].hp_fill == 0.7143
    assert got["marketcheck"].vin_fill == 1.0


def test_fill_rates_independent_of_stale_days(insights_listings, insights_now):
    def fills(days):
        return [(g.source, g.vin_fill, g.hp_fill, g.state_fill, g.year_fill)
                for g in source_quality(insights_listings, days, now=insights_now)]
    assert fills(0) == fills(7) == fills(3650)


def test_staleness_matches_manual_count(insights_listings, insights_now):
    for days in (0, 7, 30, 3650):
        cutoff = insights_now - timedelta(days=days)
        got = {g.source: g for g in source_quality(insights_listings, days, now=insights_now)}
        for src, ls in _by_source(insights_listings).items():
            stale = sum(1 for l in ls if l.crawled_at < cutoff)
            assert got[src].stale_count == stale
            assert got[src].stale_share == round(stale / len(ls), 4)
    got7 = {g.source: g.stale_count for g in source_quality(insights_listings, 7, now=insights_now)}
    assert got7 == {"leasehackr": 4, "swapalease": 4, "marketcheck": 3}
    got0 = source_quality(insights_listings, 0, now=insights_now)
    assert all(g.stale_count == g.count and g.stale_share == 1.0 for g in got0)
    got_big = source_quality(insights_listings, 3650, now=insights_now)
    assert all(g.stale_count == 0 and g.stale_share == 0.0 for g in got_big)


def test_listing_exactly_stale_days_old_is_not_stale(insights_listings, insights_now):
    # leasehackr:2004 crawled 2026-10-08T00:00Z == cutoff for stale_days=7
    lh = lambda now: {g.source: g for g in source_quality(insights_listings, 7, now=now)}["leasehackr"]
    assert lh(insights_now).stale_count == 4
    assert lh(insights_now + timedelta(microseconds=1)).stale_count == 5


def test_naive_now_and_naive_crawled_at_are_utc(insights_listings, insights_now, insights_rows):
    assert source_quality(insights_listings, 7, now=datetime(2026, 10, 15)) == \
        source_quality(insights_listings, 7, now=insights_now)
    tz = timezone(timedelta(hours=-5))
    assert source_quality(insights_listings, 7, now=insights_now.astimezone(tz)) == \
        source_quality(insights_listings, 7, now=insights_now)
    ls = [
        _mk(insights_rows, "n:1", source="s", crawled_at=datetime(2026, 10, 1, 0, 0)),
        _mk(insights_rows, "n:2", source="s", crawled_at=datetime(2026, 10, 8, 0, 0)),
        _mk(insights_rows, "n:3", source="s", crawled_at=datetime(2026, 10, 14, tzinfo=timezone.utc)),
    ]
    (g,) = source_quality(ls, 7, now=datetime(2026, 10, 15))
    assert g.stale_count == 1  # only 10-01; 10-08 sits exactly on the cutoff


def test_blank_vin_and_state_variants(insights_rows):
    ls = [
        _mk(insights_rows, "b:1", source="s", vin="   ", state=" na "),
        _mk(insights_rows, "b:2", source="s", vin=None, state=""),
        _mk(insights_rows, "b:3", source="s", vin="VIN123", state="CA"),
        _mk(insights_rows, "b:4", source="s", vin="", state="NA"),
    ]
    (g,) = source_quality(ls, 7, now=datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert g.vin_fill == 0.25
    assert g.state_fill == 0.25


def test_now_none_uses_real_clock(insights_listings):
    got = source_quality(insights_listings, MAX_STALE_DAYS)
    assert all(g.stale_count == 0 for g in got)


def test_invalid_stale_days_and_empty(insights_listings):
    with pytest.raises(ValueError):
        source_quality(insights_listings, -1)
    with pytest.raises(ValueError):
        source_quality([], -1)
    assert source_quality([]) == []


def test_source_quality_does_not_mutate_inputs(insights_listings, insights_now):
    keys = [l.listing_key for l in insights_listings]
    dumps = [l.model_dump() for l in insights_listings]
    source_quality(insights_listings, 7, now=insights_now)
    assert [l.listing_key for l in insights_listings] == keys
    assert [l.model_dump() for l in insights_listings] == dumps

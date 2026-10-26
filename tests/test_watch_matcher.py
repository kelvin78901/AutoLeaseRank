"""Offline tests for the watch matcher (alr/watch/matcher.py), per alr/watch/CONTRACT.md.
Expectations are computed from the fixture listings, not hard-coded keys."""
from datetime import datetime, timezone

import pytest

from alr.schema import ScoredListing
from alr.watch.matcher import match_watch, matches, scored_snapshot
from alr.watch.models import Watch, WatchCreate

BIG = 10_000.0


def _wc(**kw) -> WatchCreate:
    kw.setdefault("name", "w")
    kw.setdefault("max_effective_monthly", BIG)
    return WatchCreate(**kw)


def _norm(s):
    return s.strip().casefold()


def _expected(listings, pred, limit=None):
    hits = [l for l in listings if pred(l)]
    hits.sort(key=lambda l: (-l.personalized_score, l.listing_key))
    return [l.listing_key for l in hits[:limit]]


def _keys(res):
    return [l.listing_key for l in res]


def test_no_optional_criteria_returns_all_sorted(scored_listings):
    res = match_watch(_wc(), scored_listings)
    assert len(res) == len(scored_listings)
    scores = [l.personalized_score for l in res]
    assert scores == sorted(scores, reverse=True)
    assert _keys(res) == _expected(scored_listings, lambda l: True)


@pytest.mark.parametrize("make", ["bmw", " BMW ", "Bmw"])
def test_make_filter_case_insensitive_and_whitespace_tolerant(scored_listings, make):
    res = match_watch(_wc(make=make), scored_listings)
    assert res
    assert all(_norm(l.make) == "bmw" for l in res)
    assert _keys(res) == _expected(scored_listings, lambda l: _norm(l.make) == "bmw")


def test_make_and_model_combined(scored_listings):
    target = next(l for l in scored_listings if l.make == "Tesla")
    w = _wc(make=target.make.upper(), model=f" {target.model.lower()} ")
    res = match_watch(w, scored_listings)
    assert target.listing_key in _keys(res)
    assert all(_norm(l.make) == _norm(target.make) and _norm(l.model) == _norm(target.model) for l in res)
    # same model under a different make must not match
    assert not match_watch(_wc(make="Honda", model=target.model), scored_listings)


def test_model_is_equality_not_substring(scored_listings):
    assert any("Model" in l.model and l.model != "Model" for l in scored_listings)
    assert match_watch(_wc(model="Model"), scored_listings) == []
    assert match_watch(_wc(make="BM"), scored_listings) == []


def test_max_effective_monthly_boundary_inclusive(scored_listings):
    target = scored_listings[0]
    bound = target.effective_monthly
    res = match_watch(_wc(max_effective_monthly=bound), scored_listings)
    assert target.listing_key in _keys(res)
    assert all(l.effective_monthly <= bound for l in res)
    assert _keys(res) == _expected(scored_listings, lambda l: l.effective_monthly <= bound)
    below = match_watch(_wc(max_effective_monthly=bound - 0.01), scored_listings)
    assert target.listing_key not in _keys(below)


def test_min_score_boundary_inclusive_and_zero_filters_nothing(scored_listings):
    target = scored_listings[0]
    s = int(target.personalized_score)
    assert s == target.personalized_score and 0 < s <= 99  # fixture has integral scores here
    res = match_watch(_wc(min_score=s), scored_listings)
    assert target.listing_key in _keys(res)
    assert all(l.personalized_score >= s for l in res)
    assert _keys(res) == _expected(scored_listings, lambda l: l.personalized_score >= s)
    assert len(match_watch(_wc(min_score=0), scored_listings)) == len(scored_listings)
    top = max(l.personalized_score for l in scored_listings)
    just_above = match_watch(_wc(min_score=min(99, int(top) + 1)), scored_listings)
    assert target.listing_key not in _keys(just_above) or top >= 99


def test_max_miles_per_month_boundary_inclusive_and_none_no_filter(scored_listings):
    bound = 1000
    assert any(l.miles_per_month == bound for l in scored_listings)
    res = match_watch(_wc(max_miles_per_month=bound), scored_listings)
    assert all(l.miles_per_month <= bound for l in res)
    assert any(l.miles_per_month == bound for l in res)
    assert _keys(res) == _expected(scored_listings, lambda l: l.miles_per_month <= bound)
    assert len(match_watch(_wc(max_miles_per_month=None), scored_listings)) == len(scored_listings)


def test_all_criteria_combined_equals_manual_filter(scored_listings):
    w = _wc(make=" bmw", max_effective_monthly=700, min_score=40, max_miles_per_month=1250)
    manual = _expected(
        scored_listings,
        lambda l: _norm(l.make) == "bmw"
        and l.effective_monthly <= 700
        and l.personalized_score >= 40
        and l.miles_per_month <= 1250,
    )
    assert manual
    assert _keys(match_watch(w, scored_listings)) == manual
    assert all(matches(w, l) for l in match_watch(w, scored_listings))


def test_tie_on_score_broken_by_listing_key_asc(scored_listings):
    by_score: dict[float, list[str]] = {}
    for l in scored_listings:
        by_score.setdefault(l.personalized_score, []).append(l.listing_key)
    ties = [ks for ks in by_score.values() if len(ks) > 1]
    assert ties
    res = _keys(match_watch(_wc(), scored_listings))
    for ks in ties:
        positions = [res.index(k) for k in sorted(ks)]
        assert positions == sorted(positions)
    # shuffled input gives the same output
    assert _keys(match_watch(_wc(), list(reversed(scored_listings)))) == res


def test_limit_caps_output(scored_listings):
    full = _keys(match_watch(_wc(), scored_listings))
    assert _keys(match_watch(_wc(), scored_listings, limit=2)) == full[:2]
    assert _keys(match_watch(_wc(), scored_listings, limit=1000)) == full


@pytest.mark.parametrize("limit", [0, -1])
def test_limit_below_one_raises(scored_listings, limit):
    with pytest.raises(ValueError):
        match_watch(_wc(), scored_listings, limit=limit)


def test_no_matches_returns_empty_list(scored_listings):
    assert match_watch(_wc(make="Porsche"), scored_listings) == []
    assert match_watch(_wc(), []) == []


def test_match_watch_does_not_mutate_inputs(scored_listings):
    before_order = list(scored_listings)
    before_dump = [l.model_dump() for l in scored_listings]
    res = match_watch(_wc(make="bmw"), scored_listings, limit=2)
    assert scored_listings == before_order
    assert all(a is b for a, b in zip(scored_listings, before_order))
    assert [l.model_dump() for l in scored_listings] == before_dump
    assert all(any(r is l for l in scored_listings) for r in res)


def test_matches_accepts_watch_instance(scored_listings):
    wc = _wc(make="Tesla")
    w = Watch(**wc.model_dump(), id=1, created_at=datetime.now(timezone.utc))
    for l in scored_listings:
        assert matches(w, l) == matches(wc, l) == (_norm(l.make) == "tesla")


def test_scored_snapshot_returns_scored_listing_for_every_input(enriched_listings):
    before = [l.model_dump() for l in enriched_listings]
    out = scored_snapshot(enriched_listings)
    assert len(out) == len(enriched_listings)
    assert all(isinstance(s, ScoredListing) for s in out)
    assert {s.listing_key for s in out} == {l.listing_key for l in enriched_listings}
    assert len({s.listing_key for s in out}) == len(out)
    scores = [s.personalized_score for s in out]
    assert scores == sorted(scores, reverse=True)
    assert [l.model_dump() for l in enriched_listings] == before


def test_scored_snapshot_empty_input():
    assert scored_snapshot([]) == []


def test_scored_snapshot_passes_scorer_through(enriched_listings):
    class Stub:
        def contributions(self, l):
            return []

        def __call__(self, l):
            return -float(l.effective_monthly)

    out = scored_snapshot(enriched_listings, Stub())
    assert out and all(s.scorer == "ltr" for s in out)

"""Tests for alr/insights/comparables.py, written against alr/insights/CONTRACT.md."""
import math

import pytest

from alr.insights.comparables import EFF_SCALE, HP_SCALE, distance, find_comparables

SUV_TARGET = "leasehackr:2004"


def _by_key(listings):
    return {x.listing_key: x for x in listings}


def _brute_force(listings, key, k):
    by_key = _by_key(listings)
    target = by_key[key]
    cands = [x for x in listings if x.body == target.body and x.listing_key != key]
    cands.sort(key=lambda c: (distance(target, c), c.listing_key))
    return [(c, round(distance(target, c), 4)) for c in cands[:k]]


def test_scales_and_distance_formula(insights_listings):
    assert HP_SCALE == EFF_SCALE == 100.0
    by = _by_key(insights_listings)
    a, b = by["leasehackr:2004"], by["leasehackr:2005"]
    expected = math.hypot((a.hp - b.hp) / HP_SCALE, (a.effective_monthly - b.effective_monthly) / EFF_SCALE)
    assert distance(a, b) == pytest.approx(expected, abs=1e-12)
    assert distance(a, b) == pytest.approx(0.5)
    # not rounded
    c = by["swapalease:3007"]
    d = distance(a, c)
    assert d != round(d, 4)
    assert round(d, 4) == 0.5573


def test_distance_symmetric_and_zero(insights_listings):
    by = _by_key(insights_listings)
    a, b, c = by["leasehackr:2004"], by["leasehackr:2005"], by["swapalease:3002"]
    assert distance(a, b) == distance(b, a)
    assert distance(a, c) == distance(c, a)
    assert distance(a, a) == 0.0
    twin = a.model_copy(update={"listing_key": "x:1", "model": "Other"})
    assert distance(a, twin) == 0.0
    hp_only = a.model_copy(update={"hp": a.hp + 100})
    eff_only = a.model_copy(update={"effective_monthly": a.effective_monthly + 100})
    assert distance(a, hp_only) == pytest.approx(1.0)
    assert distance(a, eff_only) == pytest.approx(1.0)


def test_results_share_body_and_exclude_target(insights_listings):
    by = _by_key(insights_listings)
    for key, target in by.items():
        res = find_comparables(insights_listings, key, 50)
        assert key not in {r.listing_key for r in res}
        assert all(r.body == target.body for r in res)


def test_matches_brute_force_for_every_listing(insights_listings):
    for key in _by_key(insights_listings):
        for k in (1, 2, 5):
            res = find_comparables(insights_listings, key, k)
            expected = _brute_force(insights_listings, key, k)
            assert [(r.listing_key, r.distance) for r in res] == [
                (c.listing_key, d) for c, d in expected
            ]
            assert [r.distance for r in res] == sorted(r.distance for r in res)


def test_contract_table_values(insights_listings):
    res = find_comparables(insights_listings, SUV_TARGET, 5)
    assert [(r.listing_key, r.distance) for r in res] == [
        ("leasehackr:2005", 0.5),
        ("swapalease:3002", 0.5),
        ("swapalease:3007", 0.5573),
        ("swapalease:3001", 2.3952),
        ("leasehackr:2001", 2.4749),
    ]
    res = find_comparables(insights_listings, "leasehackr:2003", 3)
    assert [(r.listing_key, r.distance) for r in res] == [
        ("leasehackr:2002", 0.9881),
        ("leasehackr:2006", 1.0096),
        ("leasehackr:2007", 2.2589),
    ]
    res = find_comparables(insights_listings, "swapalease:3003", 5)
    assert [(r.listing_key, r.distance) for r in res] == [
        ("marketcheck:4004", 2.6405),
        ("leasehackr:2008", 4.45),
    ]


def test_equidistant_tie_ordered_by_listing_key(insights_listings):
    keys = [x.listing_key for x in insights_listings]
    # in the fixture file swapalease:3002 precedes leasehackr:2005
    assert keys.index("swapalease:3002") < keys.index("leasehackr:2005")
    res = find_comparables(insights_listings, SUV_TARGET, 2)
    assert [r.distance for r in res] == [0.5, 0.5]
    assert [r.listing_key for r in res] == ["leasehackr:2005", "swapalease:3002"]
    # reversed input must not change the order
    res_rev = find_comparables(list(reversed(insights_listings)), SUV_TARGET, 2)
    assert [r.listing_key for r in res_rev] == ["leasehackr:2005", "swapalease:3002"]
    assert [r.listing_key for r in find_comparables(insights_listings, SUV_TARGET, 1)] == ["leasehackr:2005"]


def test_k_larger_than_candidates_and_single_body(insights_listings):
    res = find_comparables(insights_listings, SUV_TARGET, 50)
    suvs = [x for x in insights_listings if x.body == "SUV" and x.listing_key != SUV_TARGET]
    assert len(res) == len(suvs) == 7
    assert {r.listing_key for r in res} == {x.listing_key for x in suvs}
    assert res[-1].listing_key == "marketcheck:4003"
    assert res[-1].distance == 3.2311
    assert find_comparables(insights_listings, "leasehackr:2009", 5) == []


def test_errors(insights_listings):
    with pytest.raises(KeyError):
        find_comparables(insights_listings, "nope", 5)
    with pytest.raises(KeyError):
        find_comparables(insights_listings, "swapalease:3999", 5)
    for bad_k in (0, -1):
        with pytest.raises(ValueError):
            find_comparables(insights_listings, SUV_TARGET, bad_k)
    # ValueError wins over KeyError
    with pytest.raises(ValueError):
        find_comparables(insights_listings, "nope", 0)


def test_comparable_fields_echo_candidate(insights_listings):
    by = _by_key(insights_listings)
    res = find_comparables(insights_listings, SUV_TARGET, 7)
    assert res
    for r in res:
        src = by[r.listing_key]
        assert r.source == src.source
        assert r.make == src.make
        assert r.model == src.model
        assert r.body == src.body
        assert r.hp == src.hp
        assert r.effective_monthly == src.effective_monthly


def test_body_match_exact_and_inputs_not_mutated(insights_listings):
    lower = insights_listings[0].model_copy(update={"listing_key": "adhoc:1", "body": "suv"})
    data = list(insights_listings) + [lower]
    before = [x.model_dump() for x in data]
    keys_before = [x.listing_key for x in data]
    res = find_comparables(data, SUV_TARGET, 50)
    assert "adhoc:1" not in {r.listing_key for r in res}
    assert [x.listing_key for x in data] == keys_before
    assert [x.model_dump() for x in data] == before

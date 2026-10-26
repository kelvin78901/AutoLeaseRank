"""TestClient tests for the /insights routes (HTTP section of alr/insights/CONTRACT.md).
The app never touches the real DB: every dependency is overridden with the shared fixtures."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

import alr.api.watch as watch_api
from alr.api.insights import get_insights_con, get_insights_snapshot
from alr.api.main import app
from alr.insights.comparables import find_comparables
from alr.insights.history import dropping_now, listing_timeline
from alr.insights.models import (DEFAULT_DROPPING_LIMIT, DEFAULT_K, DEFAULT_STALE_DAYS,
                                 MAX_DROPPING_LIMIT, MAX_K, MAX_STALE_DAYS, Comparable,
                                 DroppingResponse, HistoryResponse, PriceDrop, QualityResponse,
                                 SegmentsResponse)
from alr.insights.segments import segment_stats


@pytest.fixture
def client(insights_con, insights_listings):
    app.dependency_overrides[get_insights_con] = lambda: insights_con
    app.dependency_overrides[get_insights_snapshot] = lambda: insights_listings
    app.dependency_overrides[watch_api.get_con] = lambda: insights_con
    try:
        yield TestClient(app)  # no `with`: the startup hook must not read the real DB
    finally:
        app.dependency_overrides.clear()


def _assert_error(r, status):
    assert r.status_code == status
    body = r.json()
    assert list(body) == ["error"]
    assert isinstance(body["error"], str) and body["error"]


def test_routes_registered_before_static_mount():
    paths = [getattr(r, "path", None) for r in app.routes]
    wanted = ["/insights/segments", "/insights/history/{listing_key:path}",
              "/insights/dropping", "/insights/comparables/{listing_key:path}",
              "/insights/quality"]
    mounts = [i for i, r in enumerate(app.routes) if isinstance(r, Mount)]
    limit = min(mounts) if mounts else len(app.routes)
    for p in wanted:
        assert p in paths, p
        assert paths.index(p) < limit, p


@pytest.mark.parametrize("group_by", ["make", "body", "state"])
def test_segments_each_group_by(client, insights_listings, group_by):
    r = client.get("/insights/segments", params={"group_by": group_by, "min_count": 1})
    assert r.status_code == 200
    data = r.json()
    assert data["group_by"] == group_by and data["min_count"] == 1
    groups = data["groups"]
    assert sum(g["count"] for g in groups) == len(insights_listings)
    assert [(-g["count"], g["key"]) for g in groups] == sorted((-g["count"], g["key"]) for g in groups)
    expected = [s.model_dump() for s in segment_stats(insights_listings, group_by, 1)]
    assert groups == expected
    SegmentsResponse.model_validate(data)


def test_segments_default_group_by_is_make(client, insights_listings):
    data = client.get("/insights/segments").json()
    assert data["group_by"] == "make"
    assert data["groups"] == [s.model_dump() for s in segment_stats(insights_listings, "make", 1)]


def test_segments_min_count_filters(client, insights_listings):
    full = client.get("/insights/segments", params={"min_count": 1}).json()["groups"]
    r = client.get("/insights/segments", params={"min_count": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["min_count"] == 3
    assert data["groups"] == [g for g in full if g["count"] >= 3]
    assert len(data["groups"]) < len(full)
    assert data["groups"] == [s.model_dump() for s in segment_stats(insights_listings, "make", 3)]


@pytest.mark.parametrize("params", [{"group_by": "color"}, {"min_count": 0}, {"min_count": "abc"}])
def test_segments_invalid_params_422(client, params):
    _assert_error(client.get("/insights/segments", params=params), 422)


def test_history_ordered_and_echoes_key(client, insights_history):
    key = "leasehackr:2001"
    expected = listing_timeline(insights_history, key)
    assert len(expected) >= 2
    r = client.get(f"/insights/history/{quote(key, safe='')}")
    assert r.status_code == 200
    data = r.json()
    assert data["listing_key"] == key
    assert data["count"] == len(data["points"]) == len(expected)
    ts = [datetime.fromisoformat(p["crawl_ts"]) for p in data["points"]]
    assert all(t.tzinfo is not None for t in ts)
    assert ts == sorted(ts)
    assert HistoryResponse.model_validate(data).points == expected


def test_history_unknown_key_404(client):
    r = client.get("/insights/history/leasehackr:9999")
    _assert_error(r, 404)
    assert r.json() == {"error": "no history for listing leasehackr:9999"}


def test_dropping_matches_oracle(client, insights_history):
    expected = dropping_now(insights_history, 500)
    assert expected
    r = client.get("/insights/dropping", params={"limit": 500})
    assert r.status_code == 200
    resp = DroppingResponse.model_validate(r.json())
    assert resp.limit == 500
    assert resp.count == len(resp.items) == len(expected)
    assert resp.items == expected


def test_dropping_limit_caps_and_default(client, insights_history):
    total = len(dropping_now(insights_history, MAX_DROPPING_LIMIT))
    assert total >= 2
    data = client.get("/insights/dropping", params={"limit": 1}).json()
    assert data["limit"] == 1 and data["count"] == 1 and len(data["items"]) == 1
    assert PriceDrop.model_validate(data["items"][0]) == dropping_now(insights_history, 1)[0]
    default = client.get("/insights/dropping").json()
    assert default["limit"] == DEFAULT_DROPPING_LIMIT
    assert default["count"] == min(total, DEFAULT_DROPPING_LIMIT)


@pytest.mark.parametrize("limit", [0, MAX_DROPPING_LIMIT + 1])
def test_dropping_limit_out_of_range_422(client, limit):
    _assert_error(client.get("/insights/dropping", params={"limit": limit}), 422)


def test_comparables_same_body_sorted_and_limited(client, insights_listings):
    target = next(l for l in insights_listings if l.body == "SUV")
    r = client.get(f"/insights/comparables/{quote(target.listing_key, safe='')}", params={"k": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["listing_key"] == target.listing_key
    assert data["body"] == target.body and data["k"] == 3
    comps = [Comparable.model_validate(c) for c in data["comparables"]]
    assert data["count"] == len(comps) == 3
    assert target.listing_key not in [c.listing_key for c in comps]
    assert all(c.body == target.body for c in comps)
    d = [c.distance for c in comps]
    assert d == sorted(d)
    assert comps == find_comparables(insights_listings, target.listing_key, 3)


def test_comparables_default_k(client, insights_listings):
    target = next(l for l in insights_listings if l.body == "SUV")
    data = client.get(f"/insights/comparables/{target.listing_key}").json()
    assert data["k"] == DEFAULT_K
    assert data["count"] == len(data["comparables"]) <= DEFAULT_K


def test_comparables_unknown_key_404(client):
    r = client.get("/insights/comparables/nope:1")
    _assert_error(r, 404)
    assert r.json() == {"error": "listing nope:1 not found"}


@pytest.mark.parametrize("k", [0, MAX_K + 1])
def test_comparables_k_out_of_range_422(client, insights_listings, k):
    key = insights_listings[0].listing_key
    _assert_error(client.get(f"/insights/comparables/{key}", params={"k": k}), 422)


def test_comparables_key_forms_route(client, insights_listings):
    key = next(l.listing_key for l in insights_listings if l.body == "SUV")
    plain = client.get(f"/insights/comparables/{key}")
    encoded = client.get(f"/insights/comparables/{quote(key, safe='')}")
    assert plain.status_code == encoded.status_code == 200
    assert plain.json() == encoded.json()
    slashed = [l.listing_key for l in insights_listings if "/" in l.listing_key]
    for s in slashed:  # the fixtures may have none; then this loop is a no-op
        assert client.get(f"/insights/comparables/{s}").status_code == 200


def test_quality_sources_and_staleness(client, insights_listings):
    r = client.get("/insights/quality", params={"stale_days": 14})
    assert r.status_code == 200
    data = r.json()
    resp = QualityResponse.model_validate(data)
    assert resp.stale_days == 14
    assert resp.as_of.tzinfo is not None
    assert sum(s.count for s in resp.sources) == len(insights_listings)
    cutoff = resp.as_of - timedelta(days=resp.stale_days)
    for s in resp.sources:
        mine = [l for l in insights_listings if l.source == s.source]
        assert s.count == len(mine)
        stale = 0
        for l in mine:
            t = l.crawled_at
            t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
            stale += t < cutoff
        assert s.stale_count == stale


def test_quality_default_stale_days(client):
    assert client.get("/insights/quality").json()["stale_days"] == DEFAULT_STALE_DAYS


@pytest.mark.parametrize("days", [-1, MAX_STALE_DAYS + 1])
def test_quality_stale_days_out_of_range_422(client, days):
    _assert_error(client.get("/insights/quality", params={"stale_days": days}), 422)


def test_watch_errors_unchanged(client):
    r = client.get("/watch/999999")
    assert r.status_code == 404
    assert r.json() == {"error": "watch 999999 not found"}
    r = client.post("/watch", json={"name": "x", "max_effective_monthly": 700, "min_score": 100})
    assert r.status_code == 422
    assert list(r.json()) == ["error"]


def test_other_endpoints_keep_fastapi_default_format(client):
    r = client.get("/listing/does-not-exist")
    assert r.status_code == 404
    assert "detail" in r.json()

"""HTTP tests for /watch (see alr/watch/CONTRACT.md). Offline: in-memory DB, overridden
dependencies, TestClient used without a `with` block so startup never touches data/."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from alr.api.main import app
from alr.api.watch import get_con, get_snapshot
from alr.watch.models import MAX_MATCH_LIMIT

VALID = {"name": "cheap bmw", "max_effective_monthly": 700}


@pytest.fixture
def client(mem_con, enriched_listings):
    app.dependency_overrides[get_con] = lambda: mem_con
    app.dependency_overrides[get_snapshot] = lambda: (enriched_listings, None)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create(client, **over) -> dict:
    r = client.post("/watch", json={**VALID, **over})
    assert r.status_code == 201, r.text
    return r.json()


def _assert_error_shape(r, status):
    assert r.status_code == status
    body = r.json()
    assert list(body) == ["error"]
    assert isinstance(body["error"], str) and body["error"]


def test_post_creates_watch_with_defaults(client):
    r = client.post("/watch", json=VALID)
    assert r.status_code == 201
    w = r.json()
    assert w["id"] > 0
    assert datetime.fromisoformat(w["created_at"]).tzinfo is not None
    assert w["name"] == "cheap bmw"
    assert w["max_effective_monthly"] == 700
    assert w["min_score"] == 0
    assert w["make"] is None and w["model"] is None
    assert w["max_miles_per_month"] is None


def test_post_echoes_all_criteria(client):
    w = _create(client, make="BMW", model="X3", min_score=40, max_miles_per_month=1250)
    assert (w["make"], w["model"], w["min_score"], w["max_miles_per_month"]) == (
        "BMW", "X3", 40, 1250)
    assert client.get(f"/watch/{w['id']}").json() == w


@pytest.mark.parametrize("body", [
    {**VALID, "min_score": 100},
    {"name": "x"},
    {**VALID, "name": ""},
])
def test_post_invalid_body_is_422_error(client, body):
    _assert_error_shape(client.post("/watch", json=body), 422)


def test_list_watches_empty_then_ordered(client):
    assert client.get("/watch").json() == []
    a = _create(client, name="a")
    b = _create(client, name="b")
    got = client.get("/watch").json()
    assert [w["id"] for w in got] == [a["id"], b["id"]]
    assert a["id"] < b["id"]


def test_get_watch_hit_miss_and_bad_id(client):
    w = _create(client)
    r = client.get(f"/watch/{w['id']}")
    assert r.status_code == 200 and r.json() == w
    r = client.get("/watch/9999")
    assert r.status_code == 404
    assert r.json() == {"error": "watch 9999 not found"}
    _assert_error_shape(client.get("/watch/abc"), 422)


def test_delete_watch(client):
    w = _create(client)
    r = client.delete(f"/watch/{w['id']}")
    assert r.status_code == 204
    assert r.content == b""
    assert client.get(f"/watch/{w['id']}").status_code == 404
    r = client.delete(f"/watch/{w['id']}")
    assert r.status_code == 404
    assert r.json() == {"error": f"watch {w['id']} not found"}


def test_matches_satisfy_all_criteria_and_sorted(client):
    w = _create(client, make="bmw", min_score=1, max_miles_per_month=1500,
                max_effective_monthly=700)
    r = client.get(f"/watch/{w['id']}/matches")
    assert r.status_code == 200
    body = r.json()
    assert body["watch"]["id"] == w["id"]
    ms = body["matches"]
    assert body["count"] == len(ms)
    assert ms, "fixture has BMWs under $700, expected at least one match"
    for m in ms:
        assert m["make"].strip().casefold() == "bmw"
        assert m["effective_monthly"] <= 700
        assert m["personalized_score"] >= 1
        assert m["miles_per_month"] <= 1500
    scores = [m["personalized_score"] for m in ms]
    assert scores == sorted(scores, reverse=True)


def test_matches_limit_caps_and_validates(client):
    w = _create(client, max_effective_monthly=10_000)
    base = f"/watch/{w['id']}/matches"
    full = client.get(base).json()
    assert full["count"] == len(full["matches"]) > 1
    one = client.get(base, params={"limit": 1}).json()
    assert one["count"] == 1 and len(one["matches"]) == 1
    assert one["matches"][0]["listing_key"] == full["matches"][0]["listing_key"]
    _assert_error_shape(client.get(base, params={"limit": 0}), 422)
    _assert_error_shape(client.get(base, params={"limit": MAX_MATCH_LIMIT + 1}), 422)
    r = client.get("/watch/9999/matches")
    assert r.status_code == 404
    assert r.json() == {"error": "watch 9999 not found"}


def test_matches_none_satisfy(client):
    w = _create(client, make="Porsche")
    r = client.get(f"/watch/{w['id']}/matches")
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert r.json()["matches"] == []


def test_non_watch_errors_keep_default_format(client):
    r = client.get("/listing/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert "detail" in body
    assert "error" not in body

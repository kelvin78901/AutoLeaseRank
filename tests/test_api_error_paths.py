"""App-level error-path tests against alr.api.main.app (test plan 4b of alr/api/CONTRACT.md).
Every dependency is overridden with the shared fixtures; the real data/ DB is never touched."""
import ast
import pathlib

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

import alr.api.common as common
import alr.api.insights as insights_api
import alr.api.main as main_mod
import alr.api.watch as watch_api
from alr.api.main import app

API_DIR = pathlib.Path(main_mod.__file__).parent


@pytest.fixture
def client(insights_con, insights_listings):
    app.dependency_overrides[common.get_con] = lambda: insights_con
    app.dependency_overrides[insights_api.get_insights_snapshot] = lambda: insights_listings
    app.dependency_overrides[watch_api.get_snapshot] = lambda: insights_listings
    try:
        yield TestClient(app, raise_server_exceptions=False)  # no `with`: startup must not read data/
    finally:
        app.dependency_overrides.clear()


def _assert_json_error(r, status):
    assert r.status_code == status
    body = r.json()
    assert list(body) == ["error"]
    assert isinstance(body["error"], str) and body["error"]


def _assert_default(r, status):
    assert r.status_code == status
    body = r.json()
    assert "detail" in body and "error" not in body


def test_top_deals_bad_budget_is_default_422(client):
    r = client.get("/top_deals", params={"budget": "abc"})
    _assert_default(r, 422)
    assert isinstance(r.json()["detail"], list)


def test_unrelated_path_500_is_plain_text(client, monkeypatch):
    class Boom:
        def __len__(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(main_mod.S, "listings", Boom())
    r = client.get("/health")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "Internal Server Error"


def test_insights_snapshot_failure_is_json_500(insights_con):
    def boom():
        raise RuntimeError("snapshot exploded")

    app.dependency_overrides[common.get_con] = lambda: insights_con
    app.dependency_overrides[insights_api.get_insights_snapshot] = boom
    try:
        r = TestClient(app, raise_server_exceptions=False).get("/insights/segments")
    finally:
        app.dependency_overrides.clear()
    _assert_json_error(r, 500)
    assert r.headers["content-type"].startswith("application/json")


def test_insights_con_failure_is_json_500(insights_listings):
    def boom():
        raise RuntimeError("no connection")
        yield  # pragma: no cover  (makes this a generator dependency)

    app.dependency_overrides[common.get_con] = boom
    app.dependency_overrides[insights_api.get_insights_snapshot] = lambda: insights_listings
    try:
        r = TestClient(app, raise_server_exceptions=False).get("/insights/dropping")
    finally:
        app.dependency_overrides.clear()
    _assert_json_error(r, 500)


@pytest.mark.parametrize("path", ["/insightsX", "/insightsfoo", "/insights-x"])
def test_insights_lookalikes_get_default_404(client, path):
    r = client.get(path)
    _assert_default(r, 404)


@pytest.mark.parametrize("path", ["/insights", "/insights/nope"])
def test_insights_unknown_paths_get_json_404(client, path):
    _assert_json_error(client.get(path), 404)


def test_unknown_listing_is_default_404(client, monkeypatch):
    monkeypatch.setattr(main_mod.S, "listings", [])
    _assert_default(client.get("/listing/does-not-exist"), 404)


def test_watch_bad_id_is_json_422(client):
    _assert_json_error(client.get("/watch/abc"), 422)


def _tree(name):
    return ast.parse((API_DIR / name).read_text())


def test_insights_does_not_import_watch():
    for node in ast.walk(_tree("insights.py")):
        if isinstance(node, ast.Import):
            assert all(a.name != "alr.api.watch" for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod != "alr.api.watch" and mod.split(".")[-1] != "watch", ast.dump(node)
            if node.level and not mod:  # `from . import watch`
                assert all(a.name != "watch" for a in node.names)
            if mod == "alr.api":
                assert all(a.name != "watch" for a in node.names)


@pytest.mark.parametrize("name", ["watch.py", "insights.py"])
def test_routers_define_no_exception_handlers(name):
    for node in ast.walk(_tree(name)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert not node.name.startswith("install_"), node.name
        if isinstance(node, ast.Call):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            assert fname not in ("exception_handler", "add_exception_handler"), fname


def test_get_con_is_one_shared_object():
    assert watch_api.get_con is common.get_con is insights_api.get_insights_con


def test_app_handlers_come_from_errors_module():
    for key in (StarletteHTTPException, RequestValidationError, Exception):
        assert app.exception_handlers[key].__module__ == "alr.api.errors", key

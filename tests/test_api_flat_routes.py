"""add_flat_routes keeps every route setting (alr/api/CONTRACT.md, test plan c)."""
from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.datastructures import DefaultPlaceholder
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.routing import Route

from alr.api import common
from alr.api.common import add_flat_routes

T = "Flat Routes Test"
V = "9.9.9"

# attributes the contract table copies (methods is compared as a set, separately)
SAME_ATTRS = [
    "path", "endpoint", "response_model", "status_code", "summary", "description",
    "response_description", "responses", "deprecated", "operation_id",
    "response_model_include", "response_model_exclude", "response_model_by_alias",
    "response_model_exclude_unset", "response_model_exclude_defaults",
    "response_model_exclude_none", "include_in_schema", "response_class", "name",
    "callbacks", "openapi_extra", "generate_unique_id_function", "strict_content_type",
    "tags", "dependencies",
]


class Item(BaseModel):
    id: int
    name: str
    note: str | None = None
    secret: str = "s"


class CustomRoute(APIRoute):
    """Marker subclass: route_class_override must carry it over."""


def _unwrap(v):
    # DefaultPlaceholder has no __eq__, and app.router may resolve it to its value
    # (contract note 2), so compare the wrapped value
    return v.value if isinstance(v, DefaultPlaceholder) else v


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True)


def _build(calls: list[str]):
    def router_dep():
        calls.append("router")

    def route_dep():
        calls.append("route")

    cb_router = APIRouter()

    @cb_router.post("{$callback_url}/done")
    def _on_done(body: Item): ...

    rt = APIRouter(
        route_class=CustomRoute,
        dependencies=[Depends(router_dep)],
        tags=["rt-tag"],
        responses={404: {"description": "router missing"}},
    )

    @rt.get(
        "/full",
        dependencies=[Depends(route_dep)],
        tags=["full"],
        name="full_route",
        summary="Full summary",
        description="Full description",
        response_description="Full response",
        responses={418: {"description": "teapot"}},
        status_code=202,
        deprecated=True,
        operation_id="full_op",
        openapi_extra={"x-extra": {"a": 1}},
        callbacks=cb_router.routes,
    )
    def full():
        calls.append("endpoint")
        return {"ok": True}

    @rt.get("/text", response_class=PlainTextResponse)
    def text():
        return "hello"

    @rt.get("/html", response_class=HTMLResponse)
    def html():
        return "<b>hi</b>"

    @rt.get(
        "/item",
        response_model=Item,
        response_model_exclude_none=True,
        response_model_exclude={"secret"},
    )
    def item():
        return {"id": 1, "name": "n", "note": None, "secret": "x"}

    @rt.api_route("/multi", methods=["GET", "POST"])
    def multi():
        return {"m": 1}

    @rt.get("/hidden", include_in_schema=False)
    def hidden():
        return {"hidden": True}

    @rt.get("/stream")
    def stream() -> Iterator[Item]:
        yield Item(id=1, name="a")

    return rt


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def rt(calls):
    return _build(calls)


@pytest.fixture
def app(rt):
    a = FastAPI(title=T, version=V)
    assert add_flat_routes(a, rt) is None
    return a


def _app_routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in app.router.routes if isinstance(r, APIRoute)]


def _pairs(app, rt):
    app_routes = _app_routes(app)
    assert len(app_routes) == len(rt.routes)
    return list(zip(app_routes, rt.routes))


# ---------------------------------------------------------------- attribute parity

def test_routes_in_router_order(app, rt):
    assert [(r.path, r.name) for r in _app_routes(app)] == [(r.path, r.name) for r in rt.routes]


def test_attribute_parity(app, rt):
    for a, r in _pairs(app, rt):
        for attr in SAME_ATTRS:
            assert _unwrap(getattr(a, attr)) == _unwrap(getattr(r, attr)), (r.path, attr)
        assert set(a.methods) == set(r.methods), r.path


def test_stream_item_type_kept(app, rt):
    a, r = next((a, r) for a, r in _pairs(app, rt) if r.path == "/stream")
    assert r.stream_item_type is not None
    assert a.stream_item_type == r.stream_item_type
    assert a.response_model is None


def test_custom_route_class_kept(app, rt):
    for a, r in _pairs(app, rt):
        assert type(r) is CustomRoute
        assert type(a) is type(r)


def test_containers_are_copies(app, rt):
    for a, r in _pairs(app, rt):
        assert a.tags is not r.tags
        assert a.dependencies is not r.dependencies
        assert a.responses is not r.responses
    full_a, full_r = next((a, r) for a, r in _pairs(app, rt) if r.path == "/full")
    assert full_a.tags == ["rt-tag", "full"]
    assert set(full_a.responses) == {404, 418}
    full_a.tags.append("mutated")
    assert "mutated" not in full_r.tags


def test_specific_values(app):
    full = next(r for r in _app_routes(app) if r.path == "/full")
    assert full.name == "full_route"
    assert full.summary == "Full summary"
    assert full.description == "Full description"
    assert full.response_description == "Full response"
    assert full.status_code == 202
    assert full.deprecated is True
    assert full.operation_id == "full_op"
    assert full.openapi_extra == {"x-extra": {"a": 1}}
    assert full.callbacks
    multi = next(r for r in _app_routes(app) if r.path == "/multi")
    assert set(multi.methods) == {"GET", "POST"}
    hidden = next(r for r in _app_routes(app) if r.path == "/hidden")
    assert hidden.include_in_schema is False


# ---------------------------------------------------------------- dependencies

def test_dependency_order(app, calls):
    r = TestClient(app).get("/full")
    assert r.status_code == 202
    assert calls == ["router", "route", "endpoint"]


def test_router_dependency_only_for_plain_route(app, calls):
    assert TestClient(app).get("/text").status_code == 200
    assert calls == ["router"]


def test_router_dependency_401_blocks_endpoint():
    ran: list[str] = []

    def deny():
        raise HTTPException(status_code=401, detail="nope")

    rt = APIRouter(dependencies=[Depends(deny)])

    @rt.get("/locked")
    def locked():
        ran.append("endpoint")
        return {"x": 1}

    app = FastAPI(title=T, version=V)
    add_flat_routes(app, rt)
    r = TestClient(app).get("/locked")
    assert r.status_code == 401
    assert ran == []


def test_dependency_overrides_apply(rt, calls):
    app = FastAPI(title=T, version=V)
    add_flat_routes(app, rt)
    router_dep = next(r for r in rt.routes if r.path == "/text").dependencies[0].dependency
    app.dependency_overrides[router_dep] = lambda: calls.append("overridden")
    try:
        assert TestClient(app).get("/text").status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert calls == ["overridden"]


# ---------------------------------------------------------------- responses

def test_response_class_content_type(app):
    c = TestClient(app)
    t = c.get("/text")
    assert t.headers["content-type"].startswith("text/plain")
    assert t.text == "hello"
    h = c.get("/html")
    assert h.headers["content-type"].startswith("text/html")
    assert h.text == "<b>hi</b>"
    j = c.get("/multi")
    assert j.headers["content-type"].startswith("application/json")


def test_response_model_options(app):
    r = TestClient(app).get("/item")
    assert r.status_code == 200
    assert r.json() == {"id": 1, "name": "n"}


def test_status_code_returned(app):
    assert TestClient(app).get("/full").status_code == 202


def test_multi_methods_served(app):
    c = TestClient(app)
    assert c.get("/multi").json() == {"m": 1}
    assert c.post("/multi").json() == {"m": 1}
    assert c.put("/multi").status_code == 405


def test_hidden_route_served(app):
    r = TestClient(app).get("/hidden")
    assert r.status_code == 200
    assert r.json() == {"hidden": True}


# ---------------------------------------------------------------- OpenAPI

def test_openapi_equals_get_openapi(app, rt):
    ref = get_openapi(title=T, version=V, routes=rt.routes)
    assert _dump(app.openapi()) == _dump(ref)


def test_openapi_equals_include_router():
    # include_router drops a streamed route's itemSchema, so use a router without one
    calls: list[str] = []
    rt = _build(calls)
    rt.routes[:] = [r for r in rt.routes if r.path != "/stream"]
    a = FastAPI(title=T, version=V)
    add_flat_routes(a, rt)
    b = FastAPI(title=T, version=V)
    b.include_router(rt)
    assert _dump(a.openapi()) == _dump(b.openapi())


def test_openapi_hides_excluded_route(app):
    spec = app.openapi()
    assert "/hidden" not in spec["paths"]
    assert "/full" in spec["paths"]
    assert spec["paths"]["/full"]["get"]["operationId"] == "full_op"


def test_openapi_stream_route_matches_reference(app, rt):
    ref = get_openapi(title=T, version=V, routes=rt.routes)
    assert "/stream" in ref["paths"]
    assert _dump(app.openapi()["paths"]["/stream"]) == _dump(ref["paths"]["/stream"])


# ---------------------------------------------------------------- regression on main.app

def test_main_app_watch_and_insights_paths_unchanged():
    from alr.api.insights import router as insights_router
    from alr.api.main import app as main_app
    from alr.api.watch import router as watch_router

    ref = get_openapi(
        title=main_app.title, version=main_app.version,
        routes=list(watch_router.routes) + list(insights_router.routes),
    )
    ref_paths = {
        p: v for p, v in ref["paths"].items()
        if p == "/watch" or p.startswith("/watch/") or p.startswith("/watch{")
        or p == "/insights" or p.startswith("/insights/")
    }
    assert ref_paths
    got = main_app.openapi()["paths"]
    for p, item in ref_paths.items():
        assert p in got, p
        assert _dump(got[p]) == _dump(item), p


# ---------------------------------------------------------------- structure

def test_signature_and_return():
    assert list(inspect.signature(add_flat_routes).parameters) == ["app", "router"]
    assert add_flat_routes(FastAPI(), APIRouter()) is None


def test_lazy_include_router_comment_present():
    src = Path(common.__file__).read_text()
    assert "# flat routes: include_router is lazy in newer FastAPI" in src


def test_non_api_route_raises_attribute_error():
    rt = APIRouter()
    rt.routes.append(Route("/plain", lambda request: None))
    with pytest.raises(AttributeError):
        add_flat_routes(FastAPI(), rt)

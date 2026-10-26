"""Unit tests for alr.api.errors and alr.api.common (alr/api/CONTRACT.md, test plan 4a).

Every app here is a throwaway FastAPI() with routes defined in the test; alr.api.main is
never imported and no real DB is opened.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from alr.api import common
from alr.api.errors import install_json_errors, path_matches, validation_message
from alr.store import db

BASES = ("/p", "/pX", "/other")


def _add_routes(app: FastAPI) -> None:
    for base in BASES:
        def http_exc():
            raise HTTPException(status_code=418, detail="teapot", headers={"X-Custom": "yes"})

        def typed(n: int):
            return {"n": n}

        def boom():
            raise RuntimeError("kaboom")

        app.add_api_route(f"{base}/http", http_exc, methods=["GET"])
        app.add_api_route(f"{base}/int", typed, methods=["GET"])
        app.add_api_route(f"{base}/boom", boom, methods=["GET"])


def _app(prefixes=("/p",), install_first: bool = False) -> FastAPI:
    app = FastAPI()
    if install_first:
        install_json_errors(app, prefixes)
        _add_routes(app)
    else:
        _add_routes(app)
        install_json_errors(app, prefixes)
    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _snapshot(client: TestClient) -> list:
    out = []
    for path in ("/p/nope", "/p/http", "/p/int?n=abc", "/p/boom",
                 "/pX/nope", "/pX/http", "/pX/int?n=abc", "/pX/boom",
                 "/other/http", "/other/int?n=abc", "/other/boom", "/"):
        r = client.get(path)
        out.append((path, r.status_code, r.headers["content-type"], r.text,
                    r.headers.get("x-custom")))
    return out


# ---------- path_matches ----------

@pytest.mark.parametrize("path,expected", [
    ("/p", True),
    ("/p/x", True),
    ("/p/x/y", True),
    ("/pX", False),
    ("/p-x", False),
    ("/", False),
    ("/q", False),
    ("/P", False),
    ("", False),
])
def test_path_matches_single_prefix(path, expected):
    assert path_matches(path, ("/p",)) is expected


def test_path_matches_second_of_several_prefixes_only():
    prefixes = ("/a", "/b")
    assert path_matches("/b/child", prefixes) is True
    assert path_matches("/b", prefixes) is True
    assert path_matches("/a/child", prefixes) is True
    assert path_matches("/c", prefixes) is False


def test_path_matches_empty_and_generator_prefixes():
    assert path_matches("/p", ()) is False
    assert path_matches("/p", []) is False
    assert path_matches("/b/x", (p for p in ("/a", "/b"))) is True
    assert path_matches("/c", (p for p in ("/a", "/b"))) is False


# ---------- install_json_errors: validation ----------

@pytest.mark.parametrize("bad", [
    (),
    ("",),
    ("watch",),
    ("/watch/",),
    ("/",),
    ("/ok", "bad"),
    ("/ok", "/bad/"),
    "/watch",
])
def test_install_rejects_invalid_prefixes_and_leaves_app_unchanged(bad):
    app = FastAPI()
    before = dict(app.exception_handlers)
    with pytest.raises(ValueError):
        install_json_errors(app, bad)
    assert dict(app.exception_handlers) == before


def test_install_accepts_generator_prefixes():
    app = _app(prefixes=(p for p in ("/p",)))
    r = _client(app).get("/p/nope")
    assert r.status_code == 404
    assert r.json() == {"error": "Not Found"}


# ---------- install_json_errors: handlers ----------

def test_install_registers_exactly_three_handlers_in_errors_module():
    app = FastAPI()
    before = set(app.exception_handlers)
    install_json_errors(app, ("/p",))
    added = set(app.exception_handlers) - before
    assert {StarletteHTTPException, RequestValidationError, Exception} <= set(app.exception_handlers)
    assert added <= {StarletteHTTPException, RequestValidationError, Exception}
    for key in (StarletteHTTPException, RequestValidationError, Exception):
        assert app.exception_handlers[key].__module__ == "alr.api.errors"


@pytest.mark.parametrize("path", ["/p/nope", "/p/missing/deeper"])
def test_matching_path_404_is_json_error(path):
    r = _client(_app()).get(path)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"error": "Not Found"}


@pytest.mark.parametrize("path", ["/pX/nope", "/other/nope", "/nope"])
def test_unmatched_path_404_uses_default_detail(path):
    r = _client(_app()).get(path)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_matching_path_http_exception_keeps_headers():
    r = _client(_app()).get("/p/http")
    assert r.status_code == 418
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"error": "teapot"}
    assert r.headers["x-custom"] == "yes"


@pytest.mark.parametrize("path", ["/pX/http", "/other/http"])
def test_default_path_http_exception_keeps_headers(path):
    r = _client(_app()).get(path)
    assert r.status_code == 418
    assert r.json() == {"detail": "teapot"}
    assert "error" not in r.json()
    assert r.headers["x-custom"] == "yes"


def test_matching_path_validation_error_is_single_error_key():
    r = _client(_app()).get("/p/int?n=abc")
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert list(body) == ["error"]
    assert isinstance(body["error"], str)
    assert body["error"].startswith("query.n: ")


@pytest.mark.parametrize("path", ["/pX/int?n=abc", "/other/int?n=abc"])
def test_default_path_validation_error_has_detail_list(path):
    r = _client(_app()).get(path)
    assert r.status_code == 422
    body = r.json()
    assert "error" not in body
    assert isinstance(body["detail"], list) and body["detail"]


def test_matching_path_missing_param_message_names_location():
    r = _client(_app()).get("/p/int")
    assert r.status_code == 422
    assert list(r.json()) == ["error"]
    assert r.json()["error"].startswith("query.n: ")


def test_matching_path_unexpected_exception_is_json_500():
    r = _client(_app()).get("/p/boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"error": "internal error"}


@pytest.mark.parametrize("path", ["/pX/boom", "/other/boom"])
def test_default_path_unexpected_exception_is_plain_text_500(path):
    r = _client(_app()).get(path)
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "Internal Server Error"


def test_root_path_uses_defaults():
    r = _client(_app()).get("/")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_install_order_independent_of_route_registration():
    before = _snapshot(_client(_app(install_first=False)))
    after = _snapshot(_client(_app(install_first=True)))
    assert before == after


def test_second_install_replaces_prefixes():
    app = FastAPI()
    _add_routes(app)
    install_json_errors(app, ("/p",))
    install_json_errors(app, ("/other",))
    client = _client(app)
    old = client.get("/p/http")
    assert old.json() == {"detail": "teapot"}
    assert client.get("/p/boom").text == "Internal Server Error"
    new = client.get("/other/http")
    assert new.json() == {"error": "teapot"}
    assert client.get("/other/boom").json() == {"error": "internal error"}


def test_earlier_registered_handlers_are_overwritten_and_never_called():
    app = FastAPI()
    called = []

    @app.exception_handler(StarletteHTTPException)
    async def _h(request, exc):
        called.append("http")
        raise AssertionError("must be overwritten")

    @app.exception_handler(RequestValidationError)
    async def _v(request, exc):
        called.append("validation")
        raise AssertionError("must be overwritten")

    @app.exception_handler(Exception)
    async def _e(request, exc):
        called.append("exception")
        raise AssertionError("must be overwritten")

    _add_routes(app)
    install_json_errors(app, ("/p",))
    client = _client(app)
    assert client.get("/p/http").status_code == 418
    assert client.get("/p/int?n=abc").status_code == 422
    assert client.get("/p/boom").status_code == 500
    assert client.get("/other/http").status_code == 418
    assert client.get("/other/int?n=abc").status_code == 422
    assert client.get("/other/boom").status_code == 500
    assert called == []


# ---------- validation_message ----------

def _exc(errors):
    return RequestValidationError(errors)


def test_validation_message_multi_part_loc():
    exc = _exc([{"loc": ("body", "items", 0, "price"), "msg": "bad price", "type": "x"}])
    assert validation_message(exc) == "body.items.0.price: bad price"


def test_validation_message_empty_loc_is_msg_only():
    assert validation_message(_exc([{"loc": (), "msg": "oops", "type": "x"}])) == "oops"
    assert validation_message(_exc([{"msg": "no loc key", "type": "x"}])) == "no loc key"


def test_validation_message_missing_msg_is_invalid():
    assert validation_message(_exc([{"loc": ("query", "n"), "type": "x"}])) == "query.n: invalid"
    assert validation_message(_exc([{"type": "x"}])) == "invalid"


def test_validation_message_joins_several_errors_in_order():
    exc = _exc([
        {"loc": ("query", "a"), "msg": "first", "type": "x"},
        {"loc": (), "msg": "second", "type": "x"},
        {"loc": ("path", "b"), "msg": "third", "type": "x"},
    ])
    assert validation_message(exc) == "query.a: first; second; path.b: third"


def test_validation_message_no_errors():
    assert validation_message(_exc([])) == "invalid request"


# ---------- add_flat_routes ----------

class _Out(BaseModel):
    name: str


def test_add_flat_routes_copies_routes_in_order_and_serves_them():
    router = APIRouter()

    @router.get("/a", response_model=_Out)
    def a():
        return {"name": "a", "extra": 1}

    @router.post("/b", status_code=201)
    def b():
        return {"ok": True}

    @router.api_route("/c/{x}", methods=["PUT", "GET"], status_code=202)
    def c(x: str):
        return {"x": x}

    app = FastAPI()
    assert common.add_flat_routes(app, router) is None

    src = [r for r in router.routes if isinstance(r, APIRoute)]
    got = [r for r in app.routes if isinstance(r, APIRoute)]
    assert [r.path for r in got] == [r.path for r in src] == ["/a", "/b", "/c/{x}"]
    for s, g in zip(src, got):
        assert g.methods == s.methods
        assert g.status_code == s.status_code
        assert g.response_model == s.response_model
    assert got[0].response_model is _Out
    assert got[1].status_code == 201
    assert got[2].methods == {"PUT", "GET"}

    client = TestClient(app)
    assert client.get("/a").json() == {"name": "a"}
    r = client.post("/b")
    assert (r.status_code, r.json()) == (201, {"ok": True})
    r = client.put("/c/zz")
    assert (r.status_code, r.json()) == (202, {"x": "zz"})
    assert client.get("/c/q").status_code == 202


# ---------- get_con ----------

class _FakeCon:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


@pytest.fixture
def fake_connect(monkeypatch):
    made = []

    def connect(*args, **kwargs):
        con = _FakeCon()
        made.append(con)
        return con

    monkeypatch.setattr(db, "connect", connect)
    return made


def test_get_con_yields_connection_and_closes_on_exhaustion(fake_connect):
    gen = common.get_con()
    con = next(gen)
    assert con is fake_connect[0]
    assert con.closed == 0
    with pytest.raises(StopIteration):
        next(gen)
    assert con.closed == 1


def test_get_con_closes_on_generator_close(fake_connect):
    gen = common.get_con()
    con = next(gen)
    assert con.closed == 0
    gen.close()
    assert con.closed == 1


def test_get_con_closes_when_exception_thrown_in(fake_connect):
    gen = common.get_con()
    con = next(gen)
    with pytest.raises(RuntimeError, match="boom"):
        gen.throw(RuntimeError("boom"))
    assert con.closed == 1


def test_get_con_opens_one_connection_per_call(fake_connect):
    g1, g2 = common.get_con(), common.get_con()
    c1, c2 = next(g1), next(g2)
    assert c1 is not c2
    assert len(fake_connect) == 2
    g1.close()
    assert (c1.closed, c2.closed) == (1, 0)
    g2.close()
    assert c2.closed == 1

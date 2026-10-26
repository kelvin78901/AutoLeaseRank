# Contract: unified JSON errors + shared API helpers (alr/api)

This is a refactor contract. It follows `docs/reviews/insights_review.md` (F1-F4 and
"Recommended structure"). **The observable HTTP behaviour of `alr.api.main.app` must stay
byte-for-byte the same**: every status code, body, content-type and header, on every path.
The only things that change are where the code lives and the order-independence of the error
handlers.

This document supersedes the error-handler sections of `alr/watch/CONTRACT.md` (lines 95-97, 105)
and `alr/insights/CONTRACT.md` (lines 178-184, 217-220).

## Behaviour being preserved (today's chained handlers, flattened)

For a request path `P`, "matching" means `P == p or P.startswith(p + "/")` for `p` in
`("/watch", "/insights")`. `P` is `request.url.path`, as it is today.

| Exception raised | Matching path | Any other path |
|---|---|---|
| `starlette.exceptions.HTTPException` (FastAPI's `HTTPException` subclasses it; routing 404/405 raise it too) | `JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=getattr(exc, "headers", None))` | `await fastapi.exception_handlers.http_exception_handler(request, exc)` → `{"detail": ...}`, or a bodyless `Response` for 204/304 |
| `fastapi.exceptions.RequestValidationError` | `JSONResponse({"error": validation_message(exc)}, status_code=422)` | `await fastapi.exception_handlers.request_validation_exception_handler(request, exc)` → 422 `{"detail": [...]}` |
| any other `Exception` | `JSONResponse({"error": "internal error"}, status_code=500)` | `PlainTextResponse("Internal Server Error", status_code=500)` |

Lookalikes such as `/watchx`, `/insightsX` and `/insights-x` are **not** matching paths. Neither is
`/`, nor any path under the static mount. A matching path that has no route (`/insights`,
`/insights/nope`) gives 404 `{"error": "Not Found"}`, as it does today.

## New module `alr/api/errors.py`

Imports: stdlib, `fastapi` and `starlette` only. **No `alr` imports**, and no `..`-relative imports.
No module-level mutable state (there is no global prefix registry; the prefixes live in the
closures that `install_json_errors` creates).

```python
from __future__ import annotations

from typing import Iterable

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError


def validation_message(exc: RequestValidationError) -> str:
    """One human-readable line for a validation error, used as the "error" value in a 422.

    This is watch._validation_message moved here unchanged. For each err in exc.errors(), in order:
      loc = ".".join(str(p) for p in err.get("loc", ()))
      msg = err.get("msg", "invalid")
      part = f"{loc}: {msg}" if loc else msg
    Returns "; ".join(parts), or "invalid request" when there are no parts (exc.errors() == []).
    Never raises for a well-formed RequestValidationError.
    """
    ...


def path_matches(path: str, prefixes: Iterable[str]) -> bool:
    """True iff some p in prefixes has path == p or path.startswith(p + "/").

    Pure. It iterates prefixes once and does no validation (install_json_errors validates).
    An empty iterable gives False. Matching is case-sensitive, and the query string is not part
    of path. Examples with prefixes ("/p",): "/p" -> True; "/p/x" -> True; "/pX", "/p-x", "/",
    "/q" -> False.
    """
    ...


def install_json_errors(app: FastAPI, prefixes: Iterable[str]) -> None:
    """Make errors on the prefixed paths render as {"error": <str>}; leave every other path on
    FastAPI/Starlette defaults.

    1. prefixes = tuple(prefixes). Raise ValueError (registering nothing, so app is unchanged)
       if the tuple is empty, or if any prefix fails p.startswith("/") or ends with "/". So
       "", "watch", "/" and "/watch/" are all rejected. A bare str argument such as "/watch" is
       split into characters and rejected, because "w" does not start with "/".
    2. Register exactly three handlers with app.add_exception_handler, one each for
       starlette.exceptions.HTTPException, fastapi.exceptions.RequestValidationError and
       Exception. Each is an `async def (request, exc)` closure defined inside this function
       (so its __module__ is "alr.api.errors"). Each branches on
       path_matches(request.url.path, prefixes) exactly as the table in this contract says.
    3. It never reads app.exception_handlers and never chains, so it overwrites any handler that
       was already registered for those three keys. A second call replaces the first call's
       prefixes; it does not merge them. The order relative to add_api_route/include_router does
       not matter.

    Must be called before the app serves its first request: Starlette builds the middleware
    stack once (copying exception_handlers), so later calls have no effect on a running app.
    The Exception handler keeps today's comment: it runs inside ServerErrorMiddleware, which
    re-raises after responding, so the error is still logged either way.
    """
    ...
```

Handler details (these are the existing bodies; do not change them):
- The HTTP branch for a matching path does **not** special-case 204/304. The bodyless-status case
  only exists on the default branch, through `http_exception_handler`. This is how it works today;
  keep it.
- The validation branch passes no headers.
- The default branch for `Exception` must not call or await anything else. It returns the
  `PlainTextResponse` directly.

## New module `alr/api/common.py`

Imports: stdlib (`collections.abc`, `typing`), `fastapi`, and `from ..store import db`. `duckdb`
may be imported only under `if TYPE_CHECKING:`, for the annotation.

```python
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI

from ..store import db

if TYPE_CHECKING:
    import duckdb


def get_con() -> Iterator[duckdb.DuckDBPyConnection]:
    """FastAPI generator dependency: one DuckDB connection per request.

    con = db.connect()   # attribute lookup on the db module at call time, so
                         # monkeypatching alr.store.db.connect takes effect
    try:
        yield con
    finally:
        con.close()
    It is the single shared dependency for /watch and /insights. watch.get_con and
    insights.get_insights_con are this same object, so one dependency_overrides key covers both.
    """
    ...


def add_flat_routes(app: FastAPI, router: APIRouter) -> None:
    """Copy each route of router onto app, in router.routes order:

    for r in router.routes:  # flat routes: include_router is lazy in newer FastAPI
        app.add_api_route(r.path, r.endpoint, methods=sorted(r.methods),
                          status_code=r.status_code, response_model=r.response_model)
    This is the exact loop from main.py today, including the comment. It returns None. It
    assumes every route is an APIRoute (true for the watch and insights routers). Anything
    else raises AttributeError; do not add handling for it.
    """
    ...
```

## Changes to existing files (owned by `api_errors_impl`)

`alr/api/watch.py`
- Delete `get_con`, `_is_watch_path`, `_validation_message` and `install_error_handlers`.
- Add `from .common import get_con`. The routes keep `Depends(get_con)`, and
  `alr.api.watch.get_con is alr.api.common.get_con`.
- Drop the imports that are now unused (`FastAPI`, `Request`, `http_exception_handler`,
  `request_validation_exception_handler`, `RequestValidationError`, `JSONResponse`,
  `PlainTextResponse`, and `from ..store import db` if nothing else uses it). Keep
  `StarletteHTTPException` for `_not_found`.
- `get_snapshot` and every route body stay unchanged.

`alr/api/insights.py`
- Delete `get_insights_con`, `_is_insights_path`, `_default_unexpected`,
  `install_insights_error_handlers`, and `from .watch import _validation_message`. The file must
  not import `.watch` / `alr.api.watch` at all, by any form of import.
- Add `from .common import get_con`. Routes use `Depends(get_con)`. Keep a back-compat alias at
  module level: `get_insights_con = get_con` (same object; `tests/test_insights_api.py` imports
  it).
- Drop the imports that are now unused (`FastAPI`, `Request`, the two fastapi default handlers,
  `RequestValidationError`, `JSONResponse`, `PlainTextResponse`, `db`). Keep
  `StarletteHTTPException`.
- `get_insights_snapshot`, the route bodies and `response_model`s stay unchanged. Update the
  module docstring: errors under /insights are rendered by `alr.api.errors`, as configured in
  main.py.

`alr/api/main.py`
- Imports: `from .common import add_flat_routes`, `from .errors import install_json_errors`,
  `from .insights import router as insights_router`, `from .watch import router as watch_router`.
- Replace the two copy loops and the two install calls with exactly:
  ```
  add_flat_routes(app, watch_router)
  add_flat_routes(app, insights_router)
  install_json_errors(app, ("/watch", "/insights"))
  ```
  Keep the same position: after `add_middleware`, before everything else, and so before the
  static `app.mount("/")` at the end. There must be exactly one `install_json_errors` call in
  the codebase.
- Nothing else in main.py changes.

`alr/watch/CONTRACT.md` and `alr/insights/CONTRACT.md`
- In each error-handler section, add one line: "Superseded by alr/api/CONTRACT.md: errors are
  installed once via `alr.api.errors.install_json_errors`." Edit nothing else.

The existing `tests/test_watch_api.py` and `tests/test_insights_api.py` must pass **without edits**.
That is the main check that behaviour has not changed.

## Test plan

Rules for both files: offline only. Never touch the real `data/` DB. Use `TestClient` without
`with`, so the startup hook never runs. Clear `dependency_overrides` in `finally`. Each test
builds its own app, or restores whatever it monkeypatched. The cases below say what to test.
Take the exact expected values from the table and docstrings above.

### a. `tests/test_api_errors.py` (owned by `test_api_errors_unit`)
Unit tests that use throwaway `FastAPI()` apps whose routes are defined inside the test (for
example a route that raises `HTTPException`, one with an `int` query param, and one that raises
`RuntimeError`). Do **not** import `alr.api.main`.
- `path_matches`: the exact prefix; a child path; lookalikes (`/pX`, `/p-x`); `"/"`; an unrelated
  path; several prefixes, where only the second one matches; an empty prefix iterable; a generator
  as `prefixes`.
- `install_json_errors` ValueError: an empty tuple; `""`; no leading slash; a trailing slash; `"/"`;
  a valid prefix mixed with an invalid one; a bare str. After a ValueError, `app.exception_handlers`
  is unchanged.
- For a matching path, a lookalike and an unrelated path, each of 404 (an unknown child path, and
  a raised `HTTPException`), 422 and 500 (`raise_server_exceptions=False`): check the status, the
  content-type, and the body shape (`["error"]` only, vs `"detail"` without `"error"`, vs
  `text/plain` "Internal Server Error"). A custom header on a raised `HTTPException` is kept on both
  the matching path and the default path.
- Order independence: installing before vs after the routes are added gives identical responses.
- A second install (before the first request) replaces the prefixes: the old prefix gets the
  defaults and the new one gets JSON.
- A handler registered earlier for one of the three keys (by `app.exception_handler`) is
  overwritten and never called.
- Exactly the three keys are set, and each handler's `__module__ == "alr.api.errors"`.
- `validation_message`: built from a `RequestValidationError` with known errors. Cover a
  multi-part loc, an empty loc (msg only), a missing msg, several errors joined, and the
  empty-errors case.
- `add_flat_routes`: a throwaway `APIRouter` with several routes (with differing methods,
  `status_code` and `response_model`). The app's added `APIRoute`s keep the order, path, methods,
  status_code and response_model, and the routes are served.
- `get_con`: monkeypatch `alr.store.db.connect` with a fake that returns an object with a
  `close()` that records the call. Drive the generator: the yielded object is the fake, it is not
  closed while suspended, and it is closed after `close()`/exhaustion and also when an exception
  is thrown in. Never open a real DB.

### b. `tests/test_api_error_paths.py` (owned by `test_api_error_paths`)
Tests against `alr.api.main.app`. Use dependency overrides as the existing API tests do
(`mem_con` / `insights_con`, `insights_listings`, `enriched_listings`), and
`raise_server_exceptions=False` for the 500 cases.
- `/top_deals?budget=abc` → FastAPI-default 422: a `"detail"` key and no `"error"` key.
- Unrelated-path 500: `/health` with `alr.api.main.S.listings` monkeypatched to an object whose
  `__len__` raises → 500 `text/plain` "Internal Server Error".
- `/insights/segments` with a raising `get_insights_snapshot` override → 500 JSON `{"error": ...}`.
- An `/insights` con route (for example `/insights/dropping`) with a raising `get_con` override
  (a raising generator) → 500 JSON `{"error": ...}`.
- Lookalikes `/insightsX`, `/insightsfoo`, `/insights-x` → default 404 with no `"error"` key.
- `/insights` and `/insights/nope` → 404 `{"error": ...}` (single key).
- `/listing/does-not-exist` → `"detail"` and no `"error"`.
- `/watch/abc` → 422 `{"error": ...}` (single key).
- Structural checks:
  - `ast` over `alr/api/insights.py` source: there is no `Import`/`ImportFrom` of `alr.api.watch`,
    and no relative import whose module is `watch`.
  - `ast` over `watch.py` and `insights.py`: neither defines an exception handler (no
    `exception_handler` / `add_exception_handler` call, and no `install_*` function).
  - `watch.get_con is common.get_con is insights.get_insights_con`.
  - `app.exception_handlers` entries for `StarletteHTTPException`, `RequestValidationError` and
    `Exception` all have `__module__ == "alr.api.errors"`.

## Out of scope
Changing any error shape or message, adding prefixes, `{prefix: formatter}` maps, logging
changes, the per-request `setup()` DDL in `db.connect` (watches_review L2), and the
comparables re-scan (insights_review F5).

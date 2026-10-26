# Review: flat-route fix (`add_flat_routes`)

Scope: `git diff 33ab78afe8b8` covers `alr/api/common.py` (+31/-2), `alr/api/CONTRACT.md` (+128)
and `tests/test_api_flat_routes.py` (+363, new). Installed FastAPI: 0.138.1, inspected read-only
in `.venv/lib/python3.13/site-packages/fastapi/routing.py`.

## 1. Keyword coverage

`include_router` in 0.138.1 is lazy (`routing.py:3080-3096`). It appends an `_IncludedRouter`
and makes no `add_api_route` call. The reference set is therefore the parameters of
`APIRouter.add_api_route` (`routing.py:2673-2704`), which are also the fields that
`_EffectiveRouteContext.from_api_route` copies from each route (`routing.py:1394-1436`).
`inspect.signature` returns 25 keyword parameters after `path` and `endpoint`. `add_flat_routes`
passes all 25 explicitly, and none is missing.

- `route_class_override=type(r)` is kept. `test_custom_route_class_kept` checks it with a custom
  subclass.
- `generate_unique_id_function=r.generate_unique_id_function` is passed as is (it can be a
  placeholder) and is kept. `strict_content_type` is also kept.
- It calls `app.router.add_api_route`, not `app.add_api_route`. This is correct:
  `FastAPI.add_api_route` has no `route_class_override`, `callbacks` or `strict_content_type`
  parameter. The contract states this.
- `response_model` becomes `Default(None)` when `r.stream_item_type` is set, so the new route
  infers the item type again. This is the root cause of the lost item type, not a symptom
  patch. `test_stream_item_type_kept` and `test_openapi_stream_route_matches_reference` cover it.
- Copying: `tags`, `dependencies` and `responses` are copied with `list()` or `dict()`.
  `callbacks` is passed as is, but `add_api_route` builds a new list
  (`self.callbacks.copy().extend(...)`). A probe confirmed that the app route's `tags`,
  `dependencies`, `responses` and `callbacks` are new objects.

## 2. Dependencies

- Router-level dependencies run once, not twice. `APIRouter.add_api_route` folds them into
  `r.dependencies` when the route is defined. `app.router.add_api_route` then prepends only
  `app.router.dependencies`. A probe showed 2 dependencies on both the app route and the router
  route.
- App-level `FastAPI(dependencies=...)` works as it does under `include_router`. The lazy path
  uses `[*parent_router.dependencies, *route.dependencies]`, and `add_api_route` uses
  `self.dependencies.copy() + dependencies`. The order is the same.
- I probed with `FastAPI(dependencies=[app_dep], default_response_class=PlainTextResponse,
  deprecated=True, generate_unique_id_function=...)` and compared `add_flat_routes` against
  `include_router`:
  - The call order matched: `app → router → route → endpoint`.
  - Status and content type matched.
  - The operation IDs and `deprecated` matched.
  - The full `openapi()` output was identical.
- `dependency_overrides_provider` is `app`, so `app.dependency_overrides` works. The test
  `test_dependency_overrides_apply` covers this.

## 3. Constraints

- The signature `(app, router)` is unchanged. The function returns `None`. The comment
  `# flat routes: include_router is lazy in newer FastAPI` is still on the `for` line.
- `main.py`, `watch.py`, `insights.py`, `alr/adapters`, `alr/store`, `alr/scheduler.py` and
  `alr/pipeline` have no diff.
- No dependency was added. There are no `TODO(macc)` markers or hidden stubs.
- `main.app.openapi()` is byte-identical. I rebuilt `alr.api.main` with the old loop patched in
  and compared `json.dumps(sort_keys=True)`: the output is identical (19610 bytes). This
  confirms the upstream claim.

## 4. Tests (`tests/test_api_flat_routes.py`)

- **Dependency execution.** The tests make real requests through `TestClient`:
  - The order `router → route → endpoint` is checked.
  - A route with only the router dependency runs that dependency alone.
  - A 401 from a router dependency blocks the endpoint.
  - An override test passes.
- **OpenAPI equality.** The tests compare against `get_openapi(routes=rt.routes)` and against
  `include_router`. The `include_router` comparison drops the stream route, as the contract
  documents. They also check that the hidden route is absent from the schema but still served.
- **`/watch` and `/insights` regression.** Each path item in `main.app` is compared with a
  `get_openapi` reference built live from the real routers, and the test asserts the reference
  is not empty.
- **No golden files and no real DB.** All references are computed at test time. Building the
  OpenAPI schema does not touch `data/`. Every throwaway app is in memory.
- **Response behaviour.** Content-type, `response_model_exclude_none` and `exclude`, the custom
  status code, the multi-method route plus a 405, and the `AttributeError` for a route that is
  not an `APIRoute` are all asserted.

### Gaps (non-blocking)

- No test covers app-level `FastAPI(dependencies=...)` or other app-level settings.
  `add_flat_routes` behaves correctly here (see the probe in section 2), but a test would guard
  contract note 2.
- `test_containers_are_copies` does not check `callbacks`. The result is a new list anyway.
- The `main.app` regression test compares against `get_openapi` over the routers, not against the
  output of the old loop. It guards against future drift, but the old code would also have
  passed it. The byte-identical check above was done by hand and is not in the suite.
- `openapi_extra` (a dict) and `response_model_include`/`exclude` are aliased, not copied. This
  matches the contract table and `include_router`, which also uses `route.openapi_extra`
  directly. FastAPI only reads these values, so this is not a defect.

## 5. Full suite

`PYTHONPATH=. /Users/kelvin/Downloads/autoleaserank/.venv/bin/python -m pytest -q tests` on
9abd32e gave **236 passed, 10 warnings in 2.07s**. The warnings are the documented
`Duplicate Operation ID multi_multi_post` from the route with several methods and the starlette
`httpx` deprecation warning.

## Summary

The fix addresses the root cause: every route setting is now forwarded, and the new routes use
the same merge logic as `include_router`. The code matches the contract exactly. I found no
correctness defects and no broken constraints.

VERDICT: APPROVE

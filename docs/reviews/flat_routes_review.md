# Review: flat-route fix (`add_flat_routes`), as merged

Scope: `git diff 33ab78afe8b8` at the merge commit 8158e62. It covers `alr/api/common.py`
(+31/-2), `alr/api/CONTRACT.md` (+128), `tests/test_api_flat_routes.py` (+363, new) and this
file. The tree at 8158e62 is byte-identical to the gated tree at 52e2743 (both are tree
`894233c`). So the merged result includes the later test fix, which added
`operation_id="multi_op"` to `/multi`. The installed FastAPI is 0.138.1, and I inspected it
read-only with `inspect`.

## 1. Keyword coverage

- **`include_router` is lazy in 0.138.1.** It appends an `_IncludedRouter` built from a
  `_RouterIncludeContext` and makes no `add_api_route` call. When a route is resolved,
  `_EffectiveRouteContext.from_api_route` passes these to `_populate_api_route_state`:
  - `response_model`, `status_code`, `tags`, `dependencies`, `summary`, `description`
  - `response_description`, `responses`, `deprecated`, `methods`, `operation_id`
  - the six `response_model_*` options
  - `include_in_schema`, `response_class`, `name`, `dependency_overrides_provider`
  - `callbacks`, `openapi_extra`, `generate_unique_id_function`, `strict_content_type`
- **All 25 forwarded.** `add_flat_routes` passes all 25 keyword parameters of
  `APIRouter.add_api_route` explicitly. That covers every field above plus
  `route_class_override`.
- **One field is not forwarded: `dependency_overrides_provider`.** It is not an `add_api_route`
  parameter, and `app.router` sets it to `app`. The contract names this omission and gives
  that reason, which is sound. `test_dependency_overrides_apply` proves overrides still work.
- **`route_class_override=type(r)` is kept.** `test_custom_route_class_kept` checks it with a
  custom subclass.
- **`generate_unique_id_function` and `strict_content_type` are kept.** Both are passed as is,
  even when they are placeholders. `app.router.add_api_route` then resolves them with
  `get_value_or_default` against the app's router. `include_router` does the same with the
  included router and then the parent router.
- **`app.router.add_api_route` is the right call.** `FastAPI.add_api_route` lacks
  `route_class_override`, `callbacks` and `strict_content_type`. The contract documents this.
- **Streamed routes.** `response_model=Default(None)` is passed when `r.stream_item_type` is set,
  so the new route infers the item type again. This fixes the root cause.
- **Copies vs aliases.**
  - `tags`, `dependencies` and `responses` are copied with `list()`/`dict()`, and
    `add_api_route` copies them again.
  - `callbacks` is passed as is, but `add_api_route` always builds a new list
    (`self.callbacks.copy()` plus extend). My probe confirmed `app_route.callbacks is not r.callbacks`.
  - `openapi_extra` (a dict) and `response_model_include`/`exclude` are aliased. The contract
    table specifies this, `include_router`'s `from_api_route` passes `route.openapi_extra` the
    same way, and FastAPI only reads these values. This is not a defect.

## 2. Dependencies

- **Router-level dependencies run once.** `APIRouter.add_api_route` had already folded them into
  `r.dependencies`, and `app.router.add_api_route` only prepends `app.router.dependencies`.
  `test_dependency_order` asserts exactly `["router", "route", "endpoint"]`, so a duplicate
  would fail it.
- **App-level dependencies match `include_router`.** I probed
  `FastAPI(dependencies=[app_dep])` with `add_flat_routes` against the same app with
  `include_router`, under 12 hash seeds. Both gave `['app', 'router', 'route', 'endpoint']`
  every time.

## 3. Constraints

- The signature `(app, router)`, the `None` return and the
  `# flat routes: include_router is lazy in newer FastAPI` comment on the `for` line are all
  unchanged. Tests assert all three.
- These paths have no diff since 33ab78afe8b8: `main.py`, `watch.py`, `insights.py`,
  `alr/adapters`, `alr/store`, `alr/scheduler.py` and `alr/pipeline`.
- No dependency was added. There are no `TODO(macc)` markers and no hidden stubs.
- Byte-identical `main.app.openapi()` comes from the upstream integrate handoff. I did not
  re-derive it.

## 4. Tests (`tests/test_api_flat_routes.py`)

- **Dependency execution.** Real `TestClient` requests check four things:
  - the router → route → endpoint order;
  - a route with only the router dependency;
  - a 401 from a router dependency, where the endpoint never runs;
  - `dependency_overrides`.
- **OpenAPI equality.** The app's `openapi()` is compared with `get_openapi(routes=rt.routes)`
  and with an app that uses `include_router`. The stream route is dropped from the second
  comparison, as the contract explains. The hidden route is absent from the schema but still
  served. The stream route's path item matches the reference.
- **`/watch` and `/insights` regression.** Each path item in `main.app` is compared with a
  `get_openapi` reference built at test time from the real routers. The test asserts the
  reference is not empty.
- **No golden files, no real DB.** All references are computed at test time. Importing
  `alr.api.main` and calling `openapi()` never runs `_load()`/`db.connect()`, which only happen
  in the startup hook, and the tests never start the app. Every other app is a throwaway.

### The flakiness fix, and hash and set ordering

- **Root cause.** The cause is upstream FastAPI, not `add_flat_routes`.
  `generate_unique_id` uses `list(route.methods)[0]`, and `route.methods` is a set of strings, so
  the ID depends on the hash seed. The lazy `include_router` rebuilds that set from the original
  set's iteration order. That can change the iteration order, and so the unique ID, compared
  with the router's own route.
- **Measured on 200 seeds.** For `methods=["GET","POST","PUT"]`:
  - `add_flat_routes`, which uses `sorted(...)`, gave the same unique ID as the router's route
    200/200 times.
  - `include_router` differed 21/200 times.

  So the old `test_openapi_equals_include_router` failure came from the include side.
- **The fix is right for this test.** The explicit `operation_id="multi_op"` removes
  `unique_id` from the `/multi` operation IDs on both sides. `/multi` has no body, so no
  `Body_<unique_id>` schema name depends on it either.
- **No other order dependence in the file.**
  - `/multi` is the only route with several methods. The callback route is POST-only, and
    every other route has one method.
  - The `set()` comparisons do not depend on order.
  - `response_model_exclude={"secret"}` has one element.
  - `_dump` uses `sort_keys=True`, and the remaining OpenAPI lists (tags, `required`,
    parameters) follow declaration order.
  - The watch and insights routers declare no multi-method routes (grep for
    `api_route|methods=` under `alr/`).
- **Probe result.** I ran `pytest -q tests/test_api_flat_routes.py` with 20 random
  `PYTHONHASHSEED` values. All 20 runs gave `23 passed, 10 warnings`. The warnings include the
  expected `Duplicate Operation ID multi_op`, because GET and POST now share the explicit ID on
  both apps.

### Gaps and notes (non-blocking)

- **The contract's OpenAPI invariant over-claims slightly.** It says "for any `APIRouter`", but
  that fails for a multi-method route with no `operation_id` whose methods are declared
  unsorted.
  - **Measured on 200 seeds.** For `methods=["PUT","GET","POST"]` / `["POST","GET"]`,
    `add_flat_routes` matched the router's unique ID in 158/200 seeds, and `include_router` in
    179/200.
  - **Why it can't be fixed in `add_flat_routes`.** The original insertion order is not kept on
    the route.
  - **No reachable impact.** No router under `alr/` has such a route.
  - **Suggested fix.** The contract text could add "routes with several methods need an
    explicit `operation_id` for the equality to be deterministic".
- **No test for app-level settings.** Nothing tests app-level `FastAPI(dependencies=...)`. It
  behaves correctly (section 2 probe), but contract note 2 is not guarded.
- **`test_containers_are_copies` does not check `callbacks`.** The value is a new list anyway.
- **The `main.app` regression is a drift guard, not a before/after diff.** It compares against
  `get_openapi` over the routers, not the old loop's output. The old code would also have
  passed it.

## 5. Full suite

I did not re-run it, as instructed. The scheduler's full gate on 52e2743 PASSED, and 8158e62
has the identical tree. The integrate handoff reports 236 passed (213 existing + 23 new).

## Summary

The fix addresses the root cause: every `add_api_route` setting is forwarded, and the routes are
merged the same way `include_router` merges them. The code matches the contract table exactly.
The flaky test was fixed correctly, and nothing else in the test file depends on hash or set
order (20/20 seeded runs passed). I found no correctness defects and no broken constraints.

VERDICT: APPROVE

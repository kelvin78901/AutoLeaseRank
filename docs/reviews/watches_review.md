# Review: watches (saved searches), round 2

Scope: `git diff 776bae5..HEAD` (watch_contract, watch_matcher, test_watch_matcher, watch_store,
test_watch_store, watch_api, test_watch_api, integrate_watches), checked against
`alr/watch/CONTRACT.md` and the original requirements. Round 2 also re-checks the round-1 fixes
in `git diff ef05366..HEAD` (`alr/api/watch.py`, `alr/watch/models.py`, `tests/test_watch_api.py`).

Test run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests` gives **59 passed**, 3 warnings.
The warnings are the pre-existing FastAPI `on_event` deprecation. Round 1 had 53 passed; the 6 new
tests are the M1/M2/L1 tests. I also ran offline probes with `TestClient`, a `:memory:` DB and
overridden dependencies. Their results are quoted below.

## Requirement checklist

| Requirement | Status | Evidence |
|---|---|---|
| Watch fields: name, created_at, make?, model?, max_effective_monthly, min_score 0–99, max_miles_per_month? | OK | `alr/watch/models.py:15-46` |
| Persisted in DuckDB via production connect/setup path | OK | `alr/store/db.py:25-59` (`connect` → `setup`; `watches` table + `watch_id_seq`) |
| Matching over scored snapshot, score = `personalized_score`, all set criteria, score desc, limit | OK | `alr/watch/matcher.py:9-42` |
| POST 201 / GET list / GET one / DELETE 204·404 / GET matches?limit | OK | `alr/api/watch.py:46-85` |
| `/watch` errors JSON `{"error": str}` | OK, now including 5xx | `alr/api/watch.py:102-124` |
| Offline tests for store, matcher, API | OK | `tests/test_watch_{store,matcher,api}.py`, `tests/conftest.py` |
| No changes to `alr/adapters`, `alr/scheduler.py`, `alr/pipeline` | OK | `git diff 776bae5 --stat` touches none of them |
| No new deps | OK | no `pyproject`/`requirements` changes |

## Round-1 findings: status

### M1 (was Medium): unhandled errors on `/watch*` were plain text. **Resolved.**
`alr/api/watch.py:118-124` registers an `Exception` handler. Starlette installs it on
`ServerErrorMiddleware`, which sends the response and then re-raises, so the error is still logged.
- Probe: when `get_con` raises `duckdb.IOException`, GET `/watch/1/matches`, DELETE `/watch/1` and
  POST `/watch` each return `500 {"error":"internal error"}`.
- Non-`/watch` paths: a route inserted ahead of the static mount that raises `RuntimeError` gives
  `500 text/plain 'Internal Server Error'`. That is byte-identical to Starlette's default with
  `debug=False`, and `app.debug` is False (`alr/api/main.py:33`). The existing format is unchanged.
- Covered by `test_unhandled_db_error_is_json_500`.

This is a root-cause fix, because it catches every unexpected exception rather than special-casing
DuckDB.

### M2 (was Medium): `max_effective_monthly` accepted `inf`. **Resolved.**
`alr/watch/models.py:20` now has `Field(gt=0, allow_inf_nan=False)`. Probe: JSON `1e999`, `-1e999`,
`"inf"` and `"Infinity"` each return `422 {"error":"body.max_effective_monthly: Input should be a
finite number"}`, and `500` still returns 201. Covered by
`test_post_non_finite_max_effective_monthly_is_422` (`Infinity`, `-Infinity`, `NaN`).

### L1 (was Low): `_is_watch_path` matched by prefix. **Resolved.**
`alr/api/watch.py:88-90` now checks `path == "/watch" or path.startswith("/watch/")`. Probe:
`/watchx` and `/watches/1` return `404 {"detail":"Not Found"}`, and `/watch/` returns
`404 {"error":"Not Found"}`, which is correct because it is a watch path. Covered by
`test_watch_prefix_lookalikes_keep_default_format`.

## Specific checks (re-verified)

- **SQL is parameterized.** OK. Every value goes through `?` binds (`alr/store/watches.py:21-44`).
  The only f-string piece is `_COLS`, a module constant.
- **Router precedes the static "/" mount.** OK. Routes are added at `alr/api/main.py:35-37`, before
  `app.mount("/", ...)`. Guarded by `test_watch_routes_registered_before_static_mount`.
- **Existing endpoints' error format unchanged.** OK for 4xx, which delegate to
  `fastapi.exception_handlers.*`, and for 5xx, which is identical to the default (see M1). Covered
  by `test_non_watch_errors_keep_default_format` and `test_watch_prefix_lookalikes_keep_default_format`.
- **Existing `db.connect()` callers unaffected.** OK. The signature is `connect(path=None)`. With no
  argument it opens `DB_PATH` and runs a superset of the old DDL. Callers are unchanged. Covered by
  `test_connect_without_args_opens_db_path`.
- **Tests never touch the real DB file.** OK.
  - `mem_con` uses `db.connect(":memory:")`, and the file-backed tests use `tmp_path`.
  - `DB_PATH` is monkeypatched where the default is exercised.
  - API tests override `get_con` and `get_snapshot`, and never enter `TestClient` as a context
    manager, so the startup `_load()` never runs.
  - The new M1 test overrides `get_con` with a raising generator.
- **DuckDB connection closed per request in production.** OK. `get_con` is a generator dependency
  with `con.close()` in `finally` (`alr/api/watch.py:29-34`).

## Remaining findings (non-blocking)

### L2 (Low): `get_con` runs the full `setup()` DDL on every request
`alr/api/watch.py:30` → `alr/store/db.py:28`. That is about six idempotent `CREATE ... IF NOT
EXISTS` statements per request, and it takes the write path even for GETs. Fixing it would need
a contract change, because `get_con` is specified as yielding `db.connect()`.

### L3 (Low): route copying in `main.py` drops route metadata
`alr/api/main.py:35-37` copies `path, endpoint, methods, status_code, response_model`, and drops
`name`, `tags`, `dependencies`, `response_class` and `responses`. Nothing is lost today. A future
router-level `dependencies=[...]`, such as auth, would be lost silently. The workaround for lazy
`include_router` is documented inline.

### L4 (Low): no test pins the non-`/watch` 500 shape
The new `Exception` handler is global, and its non-watch branch reimplements Starlette's default.
I verified that branch by probe only. A test that a non-watch route raising gives
`500 text/plain` would guard against regressions.

### I1 (Info): the `Exception` handler hides the debug traceback page
If `FastAPI(debug=True)` is ever set, the handler's non-watch branch still returns the plain
`Internal Server Error`, not Starlette's traceback page. This has no effect today (`debug=False`).

### I2 (Info): per-request recompute of the whole-snapshot rank
`/watch/{id}/matches` calls `scored_snapshot` on every request. That is the same cost profile as
`/listing/{key}`, and it is what the contract specifies. A memoised snapshot would help at the
~15-20k target scale.

### I3 (Info): DuckDB config conflict risk (pre-existing)
`alr/adapters/swapalease.py:114` opens `DB_PATH` read-only. With `ALR_INPROCESS_SCHEDULER=1`, a
concurrent `get_con()` can hit DuckDB's "different configuration" error. That error now surfaces
as the JSON 500 from M1 instead of plain text.

## Root-cause vs symptom, stubs, ownership

- **Root-cause fixes.** The M1/M2/L1 fixes address the causes: a catch-all handler, a field
  constraint, and an exact path predicate. None is a per-case patch. The earlier root-cause work
  stands: `db.connect(path)` with `setup()` split out, the `include_router` workaround with a guard
  test, and UTC reads without pytz.
- **Hidden stubs.** None. There is no `TODO(macc)`, no `pass` body, and no skip or xfail in the diff.
- **Ownership.** Round-2 changes touch only `alr/api/watch.py`, `alr/watch/models.py` and
  `tests/test_watch_api.py`, all within the watch_api and test_watch_api scope. Forbidden paths
  are untouched.
- **Missing tests (non-blocking).** The non-watch 500 shape (L4). A negative
  `max_miles_per_month` and a non-int `min_score` such as `40.5`; pydantic rejects both, but no
  test pins either.

## Verdict rationale

All tests pass (59). Every contract signature and behaviour is implemented. All three round-1
Medium/Low items are fixed at the root and covered by tests. Probes show the `/watch` error shape
is JSON for 4xx and 5xx, while other paths keep their default formats. The remaining items are
hardening or informational, not contract violations.

VERDICT: APPROVE

# Review: watches (saved searches)

Scope: `git diff 776bae5..HEAD` (watch_contract, watch_matcher, test_watch_matcher, watch_store,
test_watch_store, watch_api, test_watch_api, integrate_watches), checked against
`alr/watch/CONTRACT.md` and the original requirements.

Test run: `PYTHONPATH=. .venv/bin/python -m pytest -q tests` gives **53 passed**, 3 warnings
(pre-existing FastAPI `on_event` deprecation). I also ran offline probes with `TestClient`, an
in-memory DB and overridden dependencies; their results are quoted below.

## Requirement checklist

| Requirement | Status | Evidence |
|---|---|---|
| Watch fields: name, created_at, make?, model?, max_effective_monthly, min_score 0–99, max_miles_per_month? | OK | `alr/watch/models.py:15-46` |
| Persisted in DuckDB via production connect/setup path | OK | `alr/store/db.py:25-59` (`connect` → `setup`; `watches` table + `watch_id_seq`) |
| Matching over scored snapshot, score = `personalized_score`, all set criteria, score desc, limit | OK | `alr/watch/matcher.py:9-42`; `scored_snapshot` is the same call as `main._scored_in_context` |
| POST 201 / GET list / GET one / DELETE 204·404 / GET matches?limit | OK | `alr/api/watch.py:46-85` |
| `/watch` errors JSON `{"error": str}` | OK for 4xx (see M1 for 5xx) | `alr/api/watch.py:101-115` |
| Offline tests for store, matcher, API | OK | `tests/test_watch_{store,matcher,api}.py`, `tests/conftest.py` |
| No changes to `alr/adapters`, `alr/scheduler.py`, `alr/pipeline` | OK | `git diff --stat` touches none of them |
| No new deps | OK | no `pyproject`/`requirements` changes; only fastapi/starlette/pydantic/duckdb used |

## Specific checks

- **SQL is parameterized.** OK. Every value goes through `?` binds (`alr/store/watches.py:21-44`).
  The only f-string piece is `_COLS`, which is a module constant. No user input reaches SQL text.
- **Router precedes the static "/" mount.** OK. Watch routes are added at `alr/api/main.py:35-37`,
  at import time and before `app.mount("/", ...)` at `alr/api/main.py:275`. `web/` exists in the
  repo, so `test_watch_routes_registered_before_static_mount` really runs its ordering assertion
  instead of passing vacuously.
- **Existing endpoints' error format unchanged.** OK. The handlers delegate to
  `fastapi.exception_handlers.*` for non-`/watch` paths (`alr/api/watch.py:104-115`).
  `test_non_watch_errors_keep_default_format` covers `/listing/...` → `{"detail": ...}`.
- **Existing `db.connect()` callers unaffected.** OK. The signature is now
  `connect(path=None)`, and with no argument it opens `DB_PATH` and runs the same DDL as before,
  plus the new `watches` table and sequence. Callers checked: `alr/api/main.py:50,264`,
  `scripts/train_ltr.py:22`, `alr/pipeline/run.py:52`, `alr/rank/retrain.py:16`. None changed.
  `test_connect_without_args_opens_db_path` covers the no-arg path against a tmp file.
- **Tests never touch the real DB file.** OK.
  - `mem_con` uses `db.connect(":memory:")`.
  - The file-backed store tests use `tmp_path`, and `DB_PATH` is monkeypatched where the default
    is exercised.
  - The API tests override `get_con` and `get_snapshot` and use `TestClient(app)` without `with`,
    so the startup `_load()` never runs.
  - Importing `alr.api.main` does no DB I/O.
- **DuckDB connection closed per request in production.** OK. `get_con` is a generator
  dependency with `con.close()` in `finally` (`alr/api/watch.py:29-34`).

## Findings

Severity: High = contract or correctness violation (blocking); Medium = real defect, not blocking;
Low/Info = hardening or notes.

### M1 (Medium): unhandled errors on `/watch*` return plain text, not `{"error": str}`
`alr/api/watch.py:101-115`. Only `StarletteHTTPException` and `RequestValidationError` are mapped.
Any other exception becomes Starlette's default 500. Probe: a `get_con` that raises
`duckdb.IOException("Could not set lock on file")` gives `500 text/plain "Internal Server Error"`.
This failure is realistic: an out-of-process writer (`scripts/train_ltr.py`, a crawler container)
holds the DuckDB file lock, and `get_con` opens `DB_PATH` on every request. The original
requirement says "all /watch errors JSON". The contract only names HTTPException and validation
errors, so this is not a contract violation and does not block.
Fix: register an `Exception` handler that returns `{"error": "internal error"}` with status 500
for `/watch` paths, and possibly map `duckdb.IOException` to 503.

### M2 (Medium): `max_effective_monthly` accepts `inf`, and the response then shows `null`
`alr/watch/models.py:20`. `Field(gt=0)` allows infinity, because pydantic's `allow_inf_nan`
defaults to True and `inf > 0`. Probe: `POST /watch {"name":"x","max_effective_monthly":"inf"}`
returns 201 with `"max_effective_monthly": null`. A JSON number like `1e999` also parses to inf.
The stored watch then serialises a required float as `null`, so `Watch(**resp.json())` fails on
the client. Matching still works, since inf acts as "no cap". NaN is already rejected by `gt=0`.
Fix: `Field(gt=0, allow_inf_nan=False)`, plus a parametrized 422 case in
`test_post_invalid_body_is_422_error`. This is an edge case the contract does not rule out, so it
does not block.

### L1 (Low): `_is_watch_path` matches by prefix, so `/watchx` and `/watches/1` get the `{"error"}` shape
`alr/api/watch.py:88-89`. `startswith("/watch")` also catches unrelated paths that fall through
to the static mount. Probe: `GET /watchx` gives `404 {"error":"Not Found"}`, where it should be
`{"detail":...}`. Nothing real lives under those paths today, so the impact is cosmetic. A tighter
check: `p == "/watch" or p.startswith("/watch/")`.

### L2 (Low): `get_con` runs the full `setup()` DDL on every request
`alr/api/watch.py:30` → `alr/store/db.py:28`. Each `/watch` request runs about six
`CREATE ... IF NOT EXISTS` statements, a write transaction, before any work. This is correct
and idempotent, but it is wasted work, and it takes the write path even for GETs.
Optional fix: run `setup` once at startup and have `get_con` call `duckdb.connect` and skip
`setup`. That change would need a contract update, because `get_con` is specified as yielding
`db.connect()`.

### L3 (Low): route copying in `main.py` drops route metadata
`alr/api/main.py:35-37`. The loop copies `path, endpoint, methods, status_code, response_model`.
It drops `name`, `tags`, `dependencies`, `response_class` and `responses`. The handoff explains
why: FastAPI 0.138.1 `include_router` is lazy. That root cause is real, and the workaround is
documented inline. It works today because none of those attributes are set on `watch_router`.
Anyone who later adds router-level `dependencies` (auth, for example) would silently lose them.
Suggest a comment at `alr/api/watch.py:26` (`router = APIRouter()`) pointing at the loop, or
copying `dependencies`/`name`/`tags` too.

### I1 (Info): per-request recompute of the whole-snapshot rank
`alr/api/watch.py` `/matches` calls `scored_snapshot(S.listings, S.scorer)` on every request.
This is the same cost profile as the existing `/listing/{key}` (`main._scored_in_context`) and is
exactly what the contract specifies, so it is acceptable. At the target scale (~15-20k listings)
it will be noticeably slower than the `/watch` CRUD endpoints. A memoised scored snapshot that
`_load()` invalidates would remove the cost.

### I2 (Info): DuckDB config conflict risk (not introduced by this change)
`alr/adapters/swapalease.py:114` opens `DB_PATH` with `read_only=True`. With
`ALR_INPROCESS_SCHEDULER=1`, a concurrent read-write `get_con()` in the same process can raise
DuckDB's "different configuration" error, which turns into the M1 500. `/vehicle` had the same
exposure before this change. The per-request watch connections only widen the window.

## Root-cause vs symptom, stubs, ownership

- **Root-cause fixes.** `db.connect` gained a `path` and `setup()` is split out, so tests use the
  production DDL rather than a copy. The `include_router` workaround addresses a real FastAPI
  behaviour, and a test guards it. `created_at` is read with `AT TIME ZONE 'UTC'` to avoid the
  pytz dependency, and the reason is documented at `alr/store/watches.py:7-8`. I found no
  symptom-only patches.
- **Hidden stubs.** None. No `TODO(macc)`, `pass` bodies, or skipped/xfail tests in the diff.
- **Ownership.** The diff stays within the watch feature, `alr/store/db.py`, `alr/api/main.py`,
  README and tests. It also includes macc scaffolding: `CLAUDE.md`, `OPERATOR.md`, `rules.md`.
  The forbidden paths are untouched.
- **Missing tests (non-blocking).**
  - 500 shape on `/watch` (M1).
  - `inf` rejection (M2).
  - A prefix-collision path such as `/watchx` (L1).
  - `max_miles_per_month` negative, and a non-int `min_score` like `40.5`. Pydantic handles both,
    but neither is pinned by a test.

## Verdict rationale

The suite is green, all contract signatures and semantics are implemented as specified, the SQL
is parameterized, the router ordering and error-format isolation are correct and tested, and
tests are fully offline. The findings are hardening items outside the contract's letter: M1, M2
and the Low items. None is a contract violation or a failing test.

VERDICT: APPROVE

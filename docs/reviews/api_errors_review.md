# Review: unified error handling refactor (review_api_errors)

Scope: `git diff 5e93417..cf86336`, which covers the tasks api_errors_contract, api_errors_impl,
test_api_error_paths, test_api_errors_unit and integrate_api_errors. I checked it against
`alr/api/CONTRACT.md` and against `docs/reviews/insights_review.md` F1-F4 and its
"Recommended structure".

Files changed: `alr/api/{CONTRACT.md,common.py,errors.py,insights.py,main.py,watch.py}`,
`alr/{watch,insights}/CONTRACT.md` (one line each), and two new test files,
`tests/test_api_errors.py` and `tests/test_api_error_paths.py`.

## Verification performed
- Full suite: `PYTHONPATH=. .venv/bin/python -m pytest -q tests` gave **213 passed**. I ran it
  myself on cf86336.
- **Differential probe** (in-process, offline, `TestClient` without `with`). I loaded the
  pre-refactor `alr/api/{watch,insights,main}.py` from `git show 5e93417:...` as separate modules
  and built the old app next to the HEAD app. I patched `db.connect()` with no path to raise, so
  nothing could reach the real DB. Connections were in-memory DuckDB.
  - Requests: 32 requests × 4 modes (normal / `get_con` raises / snapshot raises /
    `S.listings` raises), 128 in total.
  - Paths covered: /watch (GET, POST, DELETE, PUT-405, bad id, bad limit, bad body, non-JSON
    body, trailing slash); /insights (bare, trailing slash, unknown child, every route, bad
    params, POST-405); the lookalikes /watchx, /watch-x, /insightsX, /insights-x and
    /insightsfoo; and the unrelated paths /health (GET and POST-405), /top_deals 422,
    /listing 404 and 500, /stats 500, /nope and the static `/`.
  - Result: **0 differences** in status, headers (except `date`) or body.
- Handlers on the HEAD app: `HTTPException`, `RequestValidationError` and `Exception` all come
  from `alr.api.errors`. `WebSocketRequestValidationError` keeps FastAPI's default.
- Route order on the HEAD app: the 5 /watch routes, then the 5 /insights routes, then the main.py
  routes, with `Mount('/')` last. This is the same as before.

## Checklist
| Check | Result | Evidence |
|---|---|---|
| Exactly one handler set, registered by `install_json_errors`, called once in main.py | OK | `alr/api/errors.py:31-60`; `alr/api/main.py:45` is the only call in the repo (grep) |
| Install order no longer matters (F1, F2) | OK | No chaining and no read of `app.exception_handlers` (`errors.py:41-60`). Tested by `tests/test_api_errors.py:216-219,222-233,236-264` |
| insights.py does not import watch | OK | `alr/api/insights.py:11-24`; AST test at `tests/test_api_error_paths.py:114-124` |
| `get_con` shared; back-compat aliases are the same object | OK | `alr/api/common.py:15-20`, `watch.py:18`, `insights.py:24,29`; `tests/test_api_error_paths.py:138-139` |
| Route helper keeps the order and runs before the static mount | OK | `common.py:23-26` is the exact old loop; `main.py:43-44`, mount at `main.py:282` |
| No leftover handlers or duplicated code in watch.py / insights.py (F3) | OK | All handler, path-test, `_validation_message` and `_default_unexpected` code was deleted. No unused imports are left (`Response` and `StarletteHTTPException` are still used, at `watch.py:29,54`) |
| HTTP behaviour unchanged for /watch, /insights, lookalikes and unrelated 404/422/500 | OK | Differential probe above: 0 diffs |
| F4 gaps covered | OK | `/top_deals?budget=abc` default 422 (`test_api_error_paths.py:44-47`); unrelated 500 is plain text (`:50-59`); /insights 500 when the snapshot fails (`:62-73`) and when the con fails (`:76-87`); lookalike 404s (`:90-93`) |
| Tests never touch the real DB or the network | OK | Unit tests monkeypatch `db.connect` (`test_api_errors.py:355-365`) and build throwaway apps. App tests override `common.get_con` and both snapshots, never use `with TestClient`, and the routes they hit without overrides (`/health`, `/listing`) use only the monkeypatched `S.listings` |
| No new dependencies | OK | No packaging files appear in the diff |
| No pre-existing files under tests/ modified | OK | `git diff --name-status 5e93417 -- tests/` lists only the two new files (A) |
| Ownership and stubs | OK | Each task stayed within the files the contract assigned it. No `TODO(macc)` and no hidden stubs |

Root cause vs symptom: this is a root-cause fix. The order dependency (F1/F2) is gone by design:
there is one installer that overwrites and never chains, and its prefixes are held in closures
with no global registry. It does not just add a comment about the ordering.

## Findings (none blocking)
- **L1 (low, test strength)**: `tests/test_api_errors.py:126-134`. The test "exactly the three
  keys are set" is weak. `FastAPI()` already registers `HTTPException`, `RequestValidationError`
  and `WebSocketRequestValidationError`, so `added` is only `{Exception}`. An implementation that
  also overwrote `WebSocketRequestValidationError` would still pass. Suggestion: assert that the
  `WebSocketRequestValidationError` handler is unchanged, or compare the full key-to-module map
  before and after. Today's behaviour is correct (I checked it in the probe).
- **L2 (low, docs)**: Some superseded prose is left. `alr/watch/CONTRACT.md:106` still says
  `install_error_handlers(app)` is called in main.py. `alr/insights/CONTRACT.md:182` cites
  `alr.api.watch._validation_message`, which no longer exists. `alr/insights/CONTRACT.md:218-220`
  still describes the old handler bodies and has no "Superseded" line. The ranges listed at
  `alr/api/CONTRACT.md:9-10` (watch 95-97 and 105, insights 178-184 and 217-220) have each moved
  down one line, because of the inserted note. The contract asked for exactly one added line per
  section, so this is consistent with it, but a reader could be misled.
- **L3 (low, test brittleness)**: `tests/test_api_error_paths.py:120` rejects any `ImportFrom`
  whose last module component is `watch`. That would also flag a legitimate
  `from ..watch import models` (the `alr.watch` package, not `alr.api.watch`). It does not fail
  today, but it is stricter than the contract.

VERDICT: APPROVE
